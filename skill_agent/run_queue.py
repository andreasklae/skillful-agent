"""Run queue and SSE subscription helpers.

These were extracted from ``agent.py`` to keep the ``Agent`` class itself focused
on the agent loop and conversation state. Each function takes an ``Agent``
instance as its first argument and mutates the same instance attributes the
``Agent`` class owns (``_run_queue``, ``_queued_runs``, ``_queued_run_keys``,
``_auto_thread_run_counts``, ``_global_run_subscribers``, ``_run_worker_task``).
The split is purely organizational — the public API on ``Agent`` is unchanged.

Public functions are imported and called from thin wrappers on the ``Agent``
class. Tests can still construct an Agent via ``__new__`` and stamp the queue
attributes directly; the names are unchanged.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncGenerator
from uuid import uuid4

if TYPE_CHECKING:
    from .agent import Agent


logger = logging.getLogger(__name__)


@dataclass
class _QueuedRun:
    run_id: str
    user_message: str | list[Any]
    source: str
    prompt_preview: str
    metadata: dict[str, Any] = field(default_factory=dict)
    coalesce_key: str | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    subscribers: list[asyncio.Queue[dict[str, Any] | None]] = field(default_factory=list)
    completed: bool = False


def _prompt_preview(user_message: str | list[Any]) -> str:
    if isinstance(user_message, str):
        return user_message[:200]
    return str(user_message)[:200]


def _preview_text(value: str | list[Any], *, limit: int = 120) -> str:
    text = value if isinstance(value, str) else str(value)
    return text.replace("\n", "\\n")[:limit]


async def enqueue_run(
    agent: "Agent",
    prompt: str,
    *,
    files: Sequence[Path | str] | None = None,
    source: str = "api",
    metadata: dict[str, Any] | None = None,
    coalesce_key: str | None = None,
) -> str:
    """Queue a run and return its run_id."""
    user_message = agent._prepare_user_message(prompt, files)
    logger.info(
        "enqueue_run source=%s prompt_preview=%s files=%s",
        source,
        _preview_text(user_message),
        [str(f) for f in files] if files else [],
    )
    return await enqueue_run_message(
        agent,
        user_message,
        source=source,
        metadata=metadata,
        coalesce_key=coalesce_key,
    )


async def enqueue_run_message(
    agent: "Agent",
    user_message: str | list[Any],
    *,
    source: str,
    metadata: dict[str, Any] | None = None,
    coalesce_key: str | None = None,
) -> str:
    """Queue an already-prepared prompt payload and return its run_id."""
    ensure_run_worker(agent)
    assert agent._run_queue is not None

    if coalesce_key and coalesce_key in agent._queued_run_keys:
        for existing in agent._queued_runs.values():
            if existing.coalesce_key == coalesce_key and not existing.completed:
                logger.info(
                    "enqueue_run_coalesced source=%s existing_run_id=%s key=%s prompt_preview=%s",
                    source,
                    existing.run_id,
                    coalesce_key,
                    existing.prompt_preview,
                )
                print(
                    f"[QUEUE] enqueue_COALESCED  source={source!r}  key={coalesce_key!r}"
                    f"  existing_run_id={existing.run_id}",
                    flush=True,
                )
                return existing.run_id

    run_id = str(uuid4())
    if source != "thread":
        thread_name = (metadata or {}).get("thread_name")
        if isinstance(thread_name, str):
            agent._auto_thread_run_counts[thread_name] = 0
    job = _QueuedRun(
        run_id=run_id,
        user_message=user_message,
        source=source,
        prompt_preview=_prompt_preview(user_message),
        metadata=metadata or {},
        coalesce_key=coalesce_key,
    )
    agent._queued_runs[run_id] = job
    if coalesce_key:
        agent._queued_run_keys.add(coalesce_key)
    logger.info(
        "run_queued run_id=%s source=%s key=%s prompt_preview=%s",
        run_id,
        source,
        coalesce_key,
        job.prompt_preview,
    )
    print(
        f"[QUEUE] enqueue_run  run_id={run_id}  source={source!r}  key={coalesce_key!r}"
        f"  preview={job.prompt_preview!r}",
        flush=True,
    )

    await publish_run_envelope(
        agent,
        job,
        {
            "type": "run_queued",
            "run_id": run_id,
            "source": source,
            "prompt_preview": job.prompt_preview,
            "metadata": job.metadata,
        },
    )
    await agent._run_queue.put(job)
    return run_id


async def subscribe_run(
    agent: "Agent", run_id: str
) -> AsyncGenerator[dict[str, Any], None]:
    """Yield lifecycle and agent events for a single queued run."""
    job = agent._queued_runs.get(run_id)
    if job is None:
        raise KeyError(f"Run '{run_id}' not found.")
    logger.info("run_subscription_open run_id=%s completed=%s", run_id, job.completed)

    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    history = list(job.history)
    if job.completed:
        for envelope in history:
            yield envelope
        return

    job.subscribers.append(queue)
    try:
        for envelope in history:
            yield envelope
        while True:
            envelope = await queue.get()
            if envelope is None:
                break
            yield envelope
    finally:
        if queue in job.subscribers:
            job.subscribers.remove(queue)
        logger.info("run_subscription_closed run_id=%s", run_id)


async def subscribe_all_runs(
    agent: "Agent",
) -> AsyncGenerator[dict[str, Any], None]:
    """Yield envelopes for every queued run, including background ones."""
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    agent._global_run_subscribers.append(queue)
    logger.info(
        "global_run_subscription_open subscribers=%s",
        len(agent._global_run_subscribers),
    )
    try:
        while True:
            envelope = await queue.get()
            if envelope is None:
                break
            yield envelope
    finally:
        if queue in agent._global_run_subscribers:
            agent._global_run_subscribers.remove(queue)
        logger.info(
            "global_run_subscription_closed subscribers=%s",
            len(agent._global_run_subscribers),
        )


def register_thread_notification(agent: "Agent", thread: Any) -> None:
    """Register an inbound listener on a non-main thread to trigger agent runs."""
    thread_name = thread.name
    if thread_name == "main":
        return

    print(f"[AGENT] register_thread_notification  thread={thread_name!r}", flush=True)

    def on_inbound_message(msg: Any) -> None:
        print(
            f"[AGENT] inbound_listener_fired  thread={thread_name!r}"
            f"  content={str(msg.content)[:120]!r}",
            flush=True,
        )
        queue_thread_follow_up(agent, thread_name)

    thread.subscribe_inbound(on_inbound_message)


def queue_thread_follow_up(agent: "Agent", thread_name: str) -> None:
    """Queue an agent run in response to a new inbound message on a thread."""
    logger.info("thread_notification thread=%s", thread_name)
    print(f"[AGENT] queue_thread_follow_up  thread={thread_name!r}", flush=True)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("Thread notification without a running event loop; skipping auto-queue.")
        print(
            f"[AGENT] queue_thread_follow_up  SKIPPED (no event loop)  thread={thread_name!r}",
            flush=True,
        )
        return

    count = agent._auto_thread_run_counts.get(thread_name, 0)
    coalesce_key = f"thread_notification:{thread_name}"
    already_queued = coalesce_key in agent._queued_run_keys
    print(
        f"[AGENT] queue_thread_follow_up  thread={thread_name!r}  count={count}"
        f"  already_queued={already_queued}  queued_run_keys={list(agent._queued_run_keys)}",
        flush=True,
    )

    if count >= 10:
        logger.warning(
            "thread_follow_up_suppressed thread=%s count=%s reason=max_auto_runs",
            thread_name,
            count,
        )
        print(
            f"[AGENT] queue_thread_follow_up  SUPPRESSED (count={count})  thread={thread_name!r}",
            flush=True,
        )
        return

    loop.create_task(
        enqueue_run_message(
            agent,
            f"new message in '{thread_name}'",
            source="thread",
            metadata={"thread_name": thread_name},
            coalesce_key=coalesce_key,
        )
    )
    print(f"[AGENT] queue_thread_follow_up  task_created  thread={thread_name!r}", flush=True)


def ensure_run_worker(agent: "Agent") -> None:
    if agent._run_queue is None:
        agent._run_queue = asyncio.Queue()
        logger.info("run_queue_initialized")
    if agent._run_worker_task is None or agent._run_worker_task.done():
        agent._run_worker_task = asyncio.create_task(run_queue_worker(agent))
        logger.info("run_worker_started")


async def run_queue_worker(agent: "Agent") -> None:
    assert agent._run_queue is not None
    while True:
        job = await agent._run_queue.get()
        logger.info(
            "run_worker_picked run_id=%s source=%s remaining_queue=%s prompt_preview=%s",
            job.run_id,
            job.source,
            agent._run_queue.qsize(),
            job.prompt_preview,
        )
        print(
            f"[WORKER] picked  run_id={job.run_id}  source={job.source!r}"
            f"  queue_remaining={agent._run_queue.qsize()}  preview={job.prompt_preview!r}",
            flush=True,
        )
        try:
            # Release the coalesce key as soon as we start executing the run.
            # This allows a new notification that arrives *during* this run to
            # queue up immediately, rather than being dropped as a duplicate.
            if job.coalesce_key:
                agent._queued_run_keys.discard(job.coalesce_key)
                logger.info(
                    "run_worker_coalesce_key_released run_id=%s key=%s",
                    job.run_id,
                    job.coalesce_key,
                )
                print(
                    f"[WORKER] coalesce_key_released  key={job.coalesce_key!r}  run_id={job.run_id}",
                    flush=True,
                )

            if job.source == "thread":
                thread_name = job.metadata.get("thread_name")
                if isinstance(thread_name, str):
                    agent._auto_thread_run_counts[thread_name] = (
                        agent._auto_thread_run_counts.get(thread_name, 0) + 1
                    )
                    logger.info(
                        "thread_follow_up_started run_id=%s thread=%s count=%s",
                        job.run_id,
                        thread_name,
                        agent._auto_thread_run_counts[thread_name],
                    )
            await publish_run_envelope(
                agent,
                job,
                {
                    "type": "run_started",
                    "run_id": job.run_id,
                    "source": job.source,
                    "prompt_preview": job.prompt_preview,
                    "metadata": job.metadata,
                },
            )
            agent._reset_run_state()
            async for event in agent._event_stream(job.user_message):
                logger.debug("run_worker_event run_id=%s type=%s", job.run_id, event.type)
                await publish_run_envelope(
                    agent,
                    job,
                    {
                        "type": "agent_event",
                        "run_id": job.run_id,
                        "source": job.source,
                        "prompt_preview": job.prompt_preview,
                        "metadata": job.metadata,
                        "event": event.model_dump(mode="json"),
                    },
                )
        except Exception as exc:
            logger.exception("Queued run %s failed", job.run_id)
            await publish_run_envelope(
                agent,
                job,
                {
                    "type": "run_error",
                    "run_id": job.run_id,
                    "source": job.source,
                    "prompt_preview": job.prompt_preview,
                    "metadata": job.metadata,
                    "error": str(exc),
                },
            )
        finally:
            job.completed = True
            if job.source != "thread":
                # A real user-initiated run completed — reset flood guards so
                # subagent back-and-forth can start fresh on the next user message.
                agent._auto_thread_run_counts.clear()
                print(
                    f"[WORKER] auto_thread_run_counts_cleared  (source={job.source!r})",
                    flush=True,
                )
            if job.coalesce_key:
                agent._queued_run_keys.discard(job.coalesce_key)
            print(
                f"[WORKER] finalized  run_id={job.run_id}  source={job.source!r}"
                f"  auto_counts={dict(agent._auto_thread_run_counts)}",
                flush=True,
            )
            for subscriber in list(job.subscribers):
                subscriber.put_nowait(None)
            agent._run_queue.task_done()
            logger.info(
                "run_worker_finalized run_id=%s source=%s completed=%s history_events=%s",
                job.run_id,
                job.source,
                job.completed,
                len(job.history),
            )


async def publish_run_envelope(
    agent: "Agent", job: _QueuedRun, envelope: dict[str, Any]
) -> None:
    job.history.append(envelope)
    logger.debug(
        "publish_envelope run_id=%s type=%s run_subscribers=%s global_subscribers=%s",
        job.run_id,
        envelope["type"],
        len(job.subscribers),
        len(agent._global_run_subscribers),
    )
    for subscriber in list(job.subscribers):
        subscriber.put_nowait(envelope)
    for subscriber in list(agent._global_run_subscribers):
        subscriber.put_nowait(envelope)
