"""Core agent loop with progressive skill disclosure.

The agent works like this:

    1. On init, it scans skills_dir and builds a system prompt listing
       skill names and descriptions (not full instructions).

    2. When run() or run_stream() is called, the LLM starts the loop:
         a. Calls manage_todos to plan its approach.
         b. Calls use_skill to load the full instructions for a skill.
         c. Uses read_reference / run_script to access bundled resources.
         d. Repeats until it produces a final text answer.

    3. Every meaningful step emits a typed AgentEvent. These are the
       same events whether you use run() or run_stream() — the difference
       is only in how the caller receives them.

Public API
──────────
    agent.run(prompt, files=[...])        → AgentResult   (blocking, collects all events)
    agent.run_stream(prompt, files=[...]) → AsyncGenerator[AgentEvent, ...]  (live stream)

Optional ``files=`` attaches local paths (text inlined, images as vision parts, PDF as
extracted text if the ``[pdf]`` extra is installed). With ``AgentConfig.user_file_roots``,
the model can call ``read_user_file`` for on-demand reads under those directories.

Conversation state is kept on the agent between ``run`` / ``run_stream`` calls.
Call ``agent.clear_conversation()`` to start a new thread.
"""

import asyncio
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncGenerator
from uuid import uuid4

from pydantic_ai import Agent as PydanticAgent, RunContext
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
)
from pydantic_ai.run import AgentRunResultEvent
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from .models import (
    AgentConfig,
    AgentEvent,
    AgentResult,
    ClientFunctionRequest,
    ClientFunctionRequestEvent,
    RunCompleteEvent,
    Skill,
    TextDeltaEvent,
    TodoItem,
    TodoStatus,
    TodoUpdateEvent,
    TokenUsage,
    ToolCallEvent,
    ToolCallRecord,
    ToolResultEvent,
)
from .messages import Message, MessageType
from .threads import ThreadRegistry
from .user_prompt_files import build_user_message, resolve_allowed_user_path

logger = logging.getLogger(__name__)


def _preview_text(value: str | list[Any], *, limit: int = 120) -> str:
    text = value if isinstance(value, str) else str(value)
    return text.replace("\n", "\\n")[:limit]


# ── Per-run state ──────────────────────────────────────────────────────
#
# Mutable state shared across tool calls. ``todo_list`` persists across
# ``run`` / ``run_stream`` on the same agent; other fields reset each run.
# pydantic-ai passes deps via RunContext.deps.

@dataclass
class _RunDeps:
    skills: dict[str, Skill]
    # Thread-based infrastructure (references to Agent instance attributes)
    thread_registry: ThreadRegistry = field(default_factory=ThreadRegistry)
    message_log: list[Message] = field(default_factory=list)
    context_window: list[Message] = field(default_factory=list)
    context_compression_threshold: int = 100_000
    _agent_ref: Any = field(default=None)
    _singleton_agents: dict[str, str] = field(default_factory=dict)
    # Original fields
    activated_skills: list[str] = field(default_factory=list)
    tool_log: list[ToolCallRecord] = field(default_factory=list)
    todo_list: list[TodoItem] = field(default_factory=list)
    _next_todo_id: int = 1
    user_file_roots: tuple[Path, ...] = field(default_factory=tuple)
    max_user_file_read_chars: int = 15000
    user_skills_dirs: tuple[Path, ...] = field(default_factory=tuple)
    pending_client_requests: list[ClientFunctionRequest] = field(default_factory=list)


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


# ── Agent ─────────────────────────────────────────────────────────────

class Agent:
    """Skill-based AI agent. Initialize once, call run() or run_stream() many times.

    The agent discovers skills from a directory on init. No manual wiring needed —
    just drop a skill folder in and it gets picked up automatically.

    Basic usage:
        agent = Agent(model=model, skills_dir=Path("skills"))

        # Blocking — waits for the full answer, returns AgentResult
        result = agent.run("What is the speed of light?")
        print(result.answer)

        # Streaming — yields typed events as they happen (async)
        async for event in agent.run_stream("What is the speed of light?"):
            if isinstance(event, TextDeltaEvent):
                print(event.content, end="", flush=True)

        # Later prompts on the same agent see prior turns unless you call clear_conversation().
    """

    def __init__(
        self,
        *,
        model: Model,
        skills_dir: Path,
        config: AgentConfig | None = None,
    ) -> None:
        cfg = config or AgentConfig()
        self._model = model
        self._skills_dir = Path(skills_dir).expanduser().resolve()
        self._native_skills_dir = Path(__file__).resolve().parent.parent / "native-skills"

        self._config = cfg

        # Thread-based infrastructure
        self.thread_registry = ThreadRegistry()
        self.thread_registry.create(name="main", participants=["user"])
        self.message_log: list[Message] = []
        self.context_window: list[Message] = []
        self.subagent_logs: dict[str, list[Message]] = {}
        self._singleton_agents: dict[str, str] = {}
        self._running: bool = False
        self._run_queue: asyncio.Queue[_QueuedRun] | None = None
        self._run_worker_task: asyncio.Task | None = None
        self._queued_runs: dict[str, _QueuedRun] = {}
        self._global_run_subscribers: list[asyncio.Queue[dict[str, Any] | None]] = []
        self._queued_run_keys: set[str] = set()
        self._auto_thread_run_counts: dict[str, int] = {}

        roots = tuple(Path(p).expanduser().resolve() for p in cfg.user_file_roots)

        # _deps holds mutable per-run state; references to Agent instance attributes
        self._deps = _RunDeps(
            skills={},
            thread_registry=self.thread_registry,
            message_log=self.message_log,
            context_window=self.context_window,
            context_compression_threshold=cfg.context_compression_threshold,
            _agent_ref=self,
            _singleton_agents=self._singleton_agents,
            user_file_roots=roots,
            max_user_file_read_chars=cfg.max_user_file_read_chars,
            user_skills_dirs=(self._skills_dir,),
        )

        self._reload_skills(rebuild_runner=False)
        self._runner = _create_runner(model, self._system_prompt, roots)
        self._model_settings = ModelSettings(max_tokens=cfg.max_tokens)
        self._usage_limits = UsageLimits(
            request_limit=cfg.max_turns if cfg.max_turns is not None else None,
        )

        # pydantic-ai ModelMessage list; updated after each completed run
        self._conversation_messages: list[Any] = []

    # ── Public API ────────────────────────────────────────────────────

    def clear_conversation(self) -> None:
        """Drop all remembered turns, todo list, message stores, and threads."""
        self._conversation_messages.clear()
        self._deps.todo_list.clear()
        self._deps._next_todo_id = 1
        self.message_log.clear()
        self.context_window.clear()
        self.thread_registry = ThreadRegistry()
        self.thread_registry.create(name="main", participants=["user"])
        self._deps.thread_registry = self.thread_registry
        self._singleton_agents.clear()
        self._auto_thread_run_counts.clear()
        logger.info("conversation_cleared")

    @property
    def skills_dir(self) -> Path:
        return self._skills_dir

    def receive_thread_message(
        self,
        thread_name: str,
        content: str,
        source_context: Any = None,
        *,
        allow_create: bool = False,
    ):
        """Send a participant message into a named thread.

        If the thread does not exist and allow_create is True, creates it first.
        Otherwise raises KeyError. Inbound listeners handle notification.
        """
        logger.info(
            "thread_message_received thread=%s sender=%s content=%s allow_create=%s",
            thread_name,
            getattr(source_context, "sender", None),
            content[:200],
            allow_create,
        )
        try:
            thread = self.thread_registry.get(thread_name)
        except KeyError:
            if not allow_create:
                raise
            thread = self.thread_registry.create(name=thread_name)
            self._register_thread_notification(thread)

        msg = thread.send(content, source_context)
        return msg

    def refresh_skills(self) -> list[str]:
        """Reload native and user skills from disk and rebuild the runner."""
        return self._reload_skills(rebuild_runner=True)

    def add_skill_dir(self, skill_dir: Path | str) -> str:
        """Register a skill directory already present on disk and rebuild runtime state."""
        from .registry import _parse_skill

        resolved = Path(skill_dir).expanduser().resolve()
        skill_md = resolved / "SKILL.md"
        if not skill_md.exists():
            raise ValueError(f"No SKILL.md found at {resolved}")

        if not self._is_relative_to(resolved, self._skills_dir):
            raise ValueError(f"Skill directory must stay inside {self._skills_dir}")

        parsed = _parse_skill(resolved)
        if parsed is None:
            raise ValueError(f"Could not parse SKILL.md at {resolved}")

        self.refresh_skills()
        return parsed.name

    async def enqueue_run(
        self,
        prompt: str,
        *,
        files: Sequence[Path | str] | None = None,
        source: str = "api",
        metadata: dict[str, Any] | None = None,
        coalesce_key: str | None = None,
    ) -> str:
        """Queue a run and return its run_id."""
        user_message = self._prepare_user_message(prompt, files)
        logger.info(
            "enqueue_run source=%s prompt_preview=%s files=%s",
            source,
            _preview_text(user_message),
            [str(f) for f in files] if files else [],
        )
        return await self.enqueue_run_message(
            user_message,
            source=source,
            metadata=metadata,
            coalesce_key=coalesce_key,
        )

    async def enqueue_run_message(
        self,
        user_message: str | list[Any],
        *,
        source: str,
        metadata: dict[str, Any] | None = None,
        coalesce_key: str | None = None,
    ) -> str:
        """Queue an already-prepared prompt payload and return its run_id."""
        self._ensure_run_worker()
        assert self._run_queue is not None

        if coalesce_key and coalesce_key in self._queued_run_keys:
            for existing in self._queued_runs.values():
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
                self._auto_thread_run_counts[thread_name] = 0
        job = _QueuedRun(
            run_id=run_id,
            user_message=user_message,
            source=source,
            prompt_preview=self._prompt_preview(user_message),
            metadata=metadata or {},
            coalesce_key=coalesce_key,
        )
        self._queued_runs[run_id] = job
        if coalesce_key:
            self._queued_run_keys.add(coalesce_key)
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

        await self._publish_run_envelope(
            job,
            {
                "type": "run_queued",
                "run_id": run_id,
                "source": source,
                "prompt_preview": job.prompt_preview,
                "metadata": job.metadata,
            },
        )
        await self._run_queue.put(job)
        return run_id

    async def subscribe_run(self, run_id: str) -> AsyncGenerator[dict[str, Any], None]:
        """Yield lifecycle and agent events for a single queued run."""
        job = self._queued_runs.get(run_id)
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

    async def subscribe_all_runs(self) -> AsyncGenerator[dict[str, Any], None]:
        """Yield envelopes for every queued run, including background ones."""
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._global_run_subscribers.append(queue)
        logger.info("global_run_subscription_open subscribers=%s", len(self._global_run_subscribers))
        try:
            while True:
                envelope = await queue.get()
                if envelope is None:
                    break
                yield envelope
        finally:
            if queue in self._global_run_subscribers:
                self._global_run_subscribers.remove(queue)
            logger.info("global_run_subscription_closed subscribers=%s", len(self._global_run_subscribers))

    def _register_thread_notification(self, thread: Any) -> None:
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
            self._queue_thread_follow_up(thread_name)

        thread.subscribe_inbound(on_inbound_message)

    def _queue_thread_follow_up(self, thread_name: str) -> None:
        """Queue an agent run in response to a new inbound message on a thread."""
        logger.info("thread_notification thread=%s", thread_name)
        print(f"[AGENT] queue_thread_follow_up  thread={thread_name!r}", flush=True)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("Thread notification without a running event loop; skipping auto-queue.")
            print(f"[AGENT] queue_thread_follow_up  SKIPPED (no event loop)  thread={thread_name!r}", flush=True)
            return

        count = self._auto_thread_run_counts.get(thread_name, 0)
        coalesce_key = f"thread_notification:{thread_name}"
        already_queued = coalesce_key in self._queued_run_keys
        print(
            f"[AGENT] queue_thread_follow_up  thread={thread_name!r}  count={count}"
            f"  already_queued={already_queued}  queued_run_keys={list(self._queued_run_keys)}",
            flush=True,
        )

        if count >= 10:
            logger.warning(
                "thread_follow_up_suppressed thread=%s count=%s reason=max_auto_runs",
                thread_name,
                count,
            )
            print(f"[AGENT] queue_thread_follow_up  SUPPRESSED (count={count})  thread={thread_name!r}", flush=True)
            return

        loop.create_task(
            self.enqueue_run_message(
                f"new message in '{thread_name}'",
                source="thread",
                metadata={"thread_name": thread_name},
                coalesce_key=coalesce_key,
            )
        )
        print(f"[AGENT] queue_thread_follow_up  task_created  thread={thread_name!r}", flush=True)

    def run(self, prompt: str, *, files: Sequence[Path | str] | None = None) -> AgentResult:
        """Run the agent and wait for the full answer.

        Internally this drives the same _event_stream as run_stream, but
        collects all events before returning. The AgentResult includes
        the full event timeline, so you can inspect it after the fact:

            result = agent.run("question")
            todos   = [e for e in result.events if isinstance(e, TodoUpdateEvent)]
            tools   = [e for e in result.events if isinstance(e, ToolCallEvent)]
            answer  = result.answer  # or join TextDeltaEvents yourself

        Pass ``files=`` with paths to attach text (read as UTF-8), images (vision), or
        PDFs (text extraction; requires ``pip install 'skill-agent[pdf]'``).
        """
        user_message = self._prepare_user_message(prompt, files)

        self._reset_run_state()
        logger.info("sync_run_start prompt_preview=%s", _preview_text(user_message))
        return asyncio.run(self._collect_run(user_message))

    def run_stream(
        self, prompt: str, *, files: Sequence[Path | str] | None = None
    ) -> AsyncGenerator[AgentEvent, None]:
        """Run the agent and stream typed events as they happen.

        Returns an async generator — iterate it with `async for`. Each
        yielded object is a typed AgentEvent with a `type` literal field
        that makes it easy to route to the right part of your UI or output.

        CLI example:
            async for event in agent.run_stream("question"):
                if isinstance(event, TextDeltaEvent):
                    print(event.content, end="", flush=True)

        FastAPI SSE example:
            async def generate():
                async for event in agent.run_stream(prompt):
                    yield f"event: {event.type}\\ndata: {event.model_dump_json()}\\n\\n"

        JavaScript EventSource example:
            source.addEventListener("text_delta",  e => appendAnswer(e.data))
            source.addEventListener("todo_update", e => renderTodos(e.data))
            source.addEventListener("tool_call",   e => logTool(e.data))
        """
        user_message = self._prepare_user_message(prompt, files)

        self._reset_run_state()
        logger.info("direct_stream_start prompt_preview=%s", _preview_text(user_message))

        # Return the async generator directly. The caller is responsible
        # for iterating it inside an async context.
        return self._event_stream(user_message)

    # ── Internal helpers ──────────────────────────────────────────────

    def _prepare_user_message(
        self, prompt: str, files: Sequence[Path | str] | None
    ) -> str | list[Any]:
        msg = build_user_message(
            prompt,
            files,
            max_text_file_chars=self._config.max_attached_text_file_chars,
        )
        if isinstance(msg, str):
            if not msg.strip():
                raise ValueError("Prompt cannot be empty unless you attach files.")
        elif not msg or (isinstance(msg[0], str) and not str(msg[0]).strip() and len(msg) == 1):
            raise ValueError("Prompt cannot be empty unless you attach files.")
        return msg

    def _reset_run_state(self) -> None:
        """Clear per-run bookkeeping. Todo list and IDs persist across turns; clear via clear_conversation()."""
        self._deps.activated_skills.clear()
        self._deps.tool_log.clear()
        self._deps.pending_client_requests.clear()

    @property
    def current_todos(self) -> list[TodoItem]:
        """Copy of the live task list (persists across ``run`` / ``run_stream`` until cleared)."""
        return list(self._deps.todo_list)

    async def _collect_run(self, user_message: str | list[Any]) -> AgentResult:
        """Collect all events from _event_stream and build an AgentResult."""
        events: list[AgentEvent] = []
        logger.info("collect_run_start prompt_preview=%s", _preview_text(user_message))
        async for event in self._event_stream(user_message):
            events.append(event)
        logger.info("collect_run_end prompt_preview=%s events=%s", _preview_text(user_message), len(events))
        return self._build_result(events)

    def _build_result(self, events: list[AgentEvent]) -> AgentResult:
        """Assemble an AgentResult from the collected event list and run state."""
        # Reconstruct the answer from text delta events
        answer = "".join(e.content for e in events if isinstance(e, TextDeltaEvent))

        # Pull token usage from the RunCompleteEvent (last event in the list)
        usage = next(
            (e.usage for e in events if isinstance(e, RunCompleteEvent)),
            TokenUsage(),
        )

        return AgentResult(
            answer=answer,
            activated_skills=list(self._deps.activated_skills),
            tool_log=list(self._deps.tool_log),
            todo_list=list(self._deps.todo_list),
            usage=usage,
            events=events,
        )

    async def _event_stream(self, user_message: str | list[Any]) -> AsyncGenerator[AgentEvent, None]:
        """The core async generator that both run() and run_stream() rely on.

        Translates raw pydantic-ai stream events into typed AgentEvent objects:
          FunctionToolCallEvent   → ToolCallEvent
          FunctionToolResultEvent → ToolResultEvent  (+ TodoUpdateEvent for manage_todos)
          PartStartEvent (text)   → TextDeltaEvent (initial chunk in TextPart.content)
          PartDeltaEvent          → TextDeltaEvent
          AgentRunResultEvent     → RunCompleteEvent
        """
        # Inject active thread summary into user message if there are non-main threads
        thread_summary = self.thread_registry.summary()
        if thread_summary:
            prefix = f"[{thread_summary}]\n\n"
            if isinstance(user_message, str):
                user_message = prefix + user_message
            elif isinstance(user_message, list) and user_message and isinstance(user_message[0], str):
                user_message = [prefix + user_message[0]] + user_message[1:]

        # Log the user message to both stores
        user_content = user_message if isinstance(user_message, str) else str(user_message)
        user_msg = Message(type=MessageType.user, content=user_content)
        self.message_log.append(user_msg)
        self.context_window.append(user_msg)
        self._running = True
        answer_chunks: list[str] = []
        run_events: list[Any] = []  # collect yielded AgentEvents to attach to thread message
        logger.info(
            "event_stream_start prompt_preview=%s message_history=%s",
            _preview_text(user_message),
            len(self._conversation_messages),
        )

        # Sanitize history: pydantic-ai sets content=None on assistant messages that
        # contain only tool calls and no text. Some models (e.g. gpt-5.4-mini) reject
        # null content with a 400 error. Ensure every ModelResponse has at least one
        # TextPart so the serialized content field is never null.
        hist: list | None = None
        if self._conversation_messages:
            import dataclasses
            from pydantic_ai.messages import ToolCallPart
            hist = []
            for msg in self._conversation_messages:
                if isinstance(msg, ModelResponse):
                    has_text = any(isinstance(p, TextPart) for p in msg.parts)
                    has_tool_calls = any(isinstance(p, ToolCallPart) for p in msg.parts)
                    if not has_text and has_tool_calls:
                        # Add an empty text part so OpenAI gets content="" not null
                        patched_parts = list(msg.parts) + [TextPart(content="")]
                        msg = dataclasses.replace(msg, parts=patched_parts)
                hist.append(msg)

        async for raw in self._runner.run_stream_events(
            user_message,
            deps=self._deps,
            model_settings=self._model_settings,
            usage_limits=self._usage_limits,
            message_history=hist,
        ):
            # The LLM is calling a tool
            if isinstance(raw, FunctionToolCallEvent):
                args = dict(raw.part.args_as_dict())
                act = args.pop("activity", None)
                if isinstance(act, str):
                    act = act.strip() or None
                else:
                    act = None
                logger.debug(
                    "tool_call  %-20s  args=%s",
                    raw.part.tool_name,
                    json.dumps(args, ensure_ascii=False, default=str)[:400],
                )
                _ev = ToolCallEvent(
                    name=raw.part.tool_name,
                    args=args,
                    activity=act,
                )
                run_events.append(_ev)
                yield _ev
                tool_msg = Message(
                    type=MessageType.tool_call,
                    content={"tool": raw.part.tool_name, "description": act or raw.part.tool_name},
                )
                self.message_log.append(tool_msg)
                self.context_window.append(tool_msg)

            # A tool call just finished
            elif isinstance(raw, FunctionToolResultEvent):
                logger.debug(
                    "tool_result %-20s  result=%s",
                    raw.result.tool_name,
                    str(raw.result.content)[:400],
                )
                _ev = ToolResultEvent(name=raw.result.tool_name)
                run_events.append(_ev)
                yield _ev
                result_msg = Message(
                    type=MessageType.tool_result,
                    content={"tool": raw.result.tool_name},
                )
                self.message_log.append(result_msg)
                self.context_window.append(result_msg)

                # manage_todos modifies the todo list in _deps — emit the new state
                if raw.result.tool_name == "manage_todos":
                    _todo_ev = TodoUpdateEvent(items=list(self._deps.todo_list))
                    run_events.append(_todo_ev)
                    yield _todo_ev

                # call_client_function queues requests in _deps — emit and clear them
                if raw.result.tool_name == "call_client_function" and self._deps.pending_client_requests:
                    _cf_ev = ClientFunctionRequestEvent(
                        requests=list(self._deps.pending_client_requests),
                    )
                    run_events.append(_cf_ev)
                    yield _cf_ev
                    self._deps.pending_client_requests.clear()

            # First chunk of streamed text often arrives on part start, not only in deltas
            elif isinstance(raw, PartStartEvent):
                if isinstance(raw.part, TextPart) and raw.part.content:
                    answer_chunks.append(raw.part.content)
                    _ev = TextDeltaEvent(content=raw.part.content)
                    run_events.append(_ev)
                    yield _ev

            # The model is streaming its answer token by token
            elif isinstance(raw, PartDeltaEvent):
                if isinstance(raw.delta, TextPartDelta):
                    answer_chunks.append(raw.delta.content_delta)
                    _ev = TextDeltaEvent(content=raw.delta.content_delta)
                    run_events.append(_ev)
                    yield _ev

            # The run is complete — emit final token usage
            elif isinstance(raw, AgentRunResultEvent):
                self._conversation_messages[:] = list(raw.result.all_messages())

                # Log the full agent response as a single message
                full_answer = "".join(answer_chunks)
                run_usage = raw.result.usage()
                input_tokens = run_usage.input_tokens or 0

                _complete_ev = RunCompleteEvent(
                    usage=TokenUsage(
                        input_tokens=input_tokens,
                        output_tokens=run_usage.output_tokens or 0,
                    ),
                )
                run_events.append(_complete_ev)

                # Serialize all events collected this run for thread attachment
                serialized_run_events = [e.model_dump(mode="json") for e in run_events]

                if full_answer:
                    agent_msg = Message(type=MessageType.agent, content=full_answer)
                    self.message_log.append(agent_msg)
                    self.context_window.append(agent_msg)
                    # Append to main thread with full event log attached
                    try:
                        self.thread_registry.get("main").reply(
                            full_answer,
                            events=serialized_run_events,
                        )
                    except KeyError:
                        pass

                # Auto-compression check
                threshold = self._deps.context_compression_threshold
                if input_tokens > threshold and len(self.context_window) > 1:
                    from .context_tools import compress_all_impl, build_generic_summary
                    summary, instruction = build_generic_summary(
                        self.message_log, self._deps.todo_list
                    )
                    compress_all_impl(
                        self.message_log, self.context_window, summary, instruction
                    )

                yield _complete_ev
                logger.info(
                    "event_stream_complete prompt_preview=%s input_tokens=%s output_tokens=%s answer_chars=%s",
                    _preview_text(user_message),
                    input_tokens,
                    run_usage.output_tokens or 0,
                    len(full_answer),
                )

        self._running = False
        logger.info("event_stream_end prompt_preview=%s", _preview_text(user_message))

    def _reload_skills(self, *, rebuild_runner: bool) -> list[str]:
        from .registry import discover_skills

        native_skills = (
            discover_skills(self._native_skills_dir)
            if self._native_skills_dir.is_dir()
            else {}
        )
        user_skills = discover_skills(self._skills_dir)
        if not user_skills and not native_skills:
            raise RuntimeError(f"No skills found in {self._skills_dir}. Add at least one SKILL.md.")

        all_skills = {**native_skills, **user_skills}
        self._skills = all_skills
        self._deps.skills = all_skills
        self._system_prompt = self._build_runtime_system_prompt(all_skills)
        logger.info(
            "skills_reloaded total=%s user=%s native=%s rebuild_runner=%s",
            len(all_skills),
            len(user_skills),
            len(native_skills),
            rebuild_runner,
        )

        roots = tuple(Path(p).expanduser().resolve() for p in self._config.user_file_roots)
        if rebuild_runner:
            self._runner = _create_runner(self._model, self._system_prompt, roots)

        return sorted(all_skills)

    @staticmethod
    def _is_relative_to(path: Path, parent: Path) -> bool:
        try:
            path.relative_to(parent)
            return True
        except ValueError:
            return False

    def _build_runtime_system_prompt(self, skills: dict[str, Skill]) -> str:
        prompt = _build_system_prompt(skills, self._config.system_prompt_extra)
        roots = tuple(Path(p).expanduser().resolve() for p in self._config.user_file_roots)
        if roots:
            listed = ", ".join(str(r) for r in roots)
            prompt += (
                "\n\n## User file access\n"
                f"Additional files may live under: {listed}. "
                "Call `read_user_file` with a path relative to one of these roots, "
                "or an absolute path that stays inside them."
            )
        return prompt

    def _ensure_run_worker(self) -> None:
        if self._run_queue is None:
            self._run_queue = asyncio.Queue()
            logger.info("run_queue_initialized")
        if self._run_worker_task is None or self._run_worker_task.done():
            self._run_worker_task = asyncio.create_task(self._run_queue_worker())
            logger.info("run_worker_started")

    async def _run_queue_worker(self) -> None:
        assert self._run_queue is not None
        while True:
            job = await self._run_queue.get()
            logger.info(
                "run_worker_picked run_id=%s source=%s remaining_queue=%s prompt_preview=%s",
                job.run_id,
                job.source,
                self._run_queue.qsize(),
                job.prompt_preview,
            )
            print(
                f"[WORKER] picked  run_id={job.run_id}  source={job.source!r}"
                f"  queue_remaining={self._run_queue.qsize()}  preview={job.prompt_preview!r}",
                flush=True,
            )
            try:
                # Release the coalesce key as soon as we start executing the run.
                # This allows a new notification that arrives *during* this run to
                # queue up immediately, rather than being dropped as a duplicate.
                if job.coalesce_key:
                    self._queued_run_keys.discard(job.coalesce_key)
                    logger.info(
                        "run_worker_coalesce_key_released run_id=%s key=%s",
                        job.run_id,
                        job.coalesce_key,
                    )
                    print(f"[WORKER] coalesce_key_released  key={job.coalesce_key!r}  run_id={job.run_id}", flush=True)

                if job.source == "thread":
                    thread_name = job.metadata.get("thread_name")
                    if isinstance(thread_name, str):
                        self._auto_thread_run_counts[thread_name] = self._auto_thread_run_counts.get(thread_name, 0) + 1
                        logger.info(
                            "thread_follow_up_started run_id=%s thread=%s count=%s",
                            job.run_id,
                            thread_name,
                            self._auto_thread_run_counts[thread_name],
                        )
                await self._publish_run_envelope(
                    job,
                    {
                        "type": "run_started",
                        "run_id": job.run_id,
                        "source": job.source,
                        "prompt_preview": job.prompt_preview,
                        "metadata": job.metadata,
                    },
                )
                self._reset_run_state()
                async for event in self._event_stream(job.user_message):
                    logger.debug("run_worker_event run_id=%s type=%s", job.run_id, event.type)
                    await self._publish_run_envelope(
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
                await self._publish_run_envelope(
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
                    self._auto_thread_run_counts.clear()
                    print(f"[WORKER] auto_thread_run_counts_cleared  (source={job.source!r})", flush=True)
                if job.coalesce_key:
                    self._queued_run_keys.discard(job.coalesce_key)
                print(
                    f"[WORKER] finalized  run_id={job.run_id}  source={job.source!r}"
                    f"  auto_counts={dict(self._auto_thread_run_counts)}",
                    flush=True,
                )
                for subscriber in list(job.subscribers):
                    subscriber.put_nowait(None)
                self._run_queue.task_done()
                logger.info(
                    "run_worker_finalized run_id=%s source=%s completed=%s history_events=%s",
                    job.run_id,
                    job.source,
                    job.completed,
                    len(job.history),
                )

    async def _publish_run_envelope(self, job: _QueuedRun, envelope: dict[str, Any]) -> None:
        job.history.append(envelope)
        logger.debug(
            "publish_envelope run_id=%s type=%s run_subscribers=%s global_subscribers=%s",
            job.run_id,
            envelope["type"],
            len(job.subscribers),
            len(self._global_run_subscribers),
        )
        for subscriber in list(job.subscribers):
            subscriber.put_nowait(envelope)
        for subscriber in list(self._global_run_subscribers):
            subscriber.put_nowait(envelope)

    @staticmethod
    def _prompt_preview(user_message: str | list[Any]) -> str:
        if isinstance(user_message, str):
            return user_message[:200]
        return str(user_message)[:200]


# ── System prompt ──────────────────────────────────────────────────────
#
# The system prompt lists skill names and descriptions only — not their
# full bodies. The LLM must call use_skill to load the full instructions.
# This keeps the prompt lean regardless of how many skills are registered.

_SYSTEM_PROMPT_TEMPLATE_PATH = Path(__file__).with_name("system_prompt.md")


def _build_system_prompt(skills: dict[str, Skill], extra: str | None) -> str:
    template = _SYSTEM_PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8").strip()
    skill_lines = "\n".join(
        f"  - **{name}**: {skill.description}" for name, skill in skills.items()
    )
    skills_section = (
        "## Available skills (call `use_skill` to load full instructions)\n"
        f"{skill_lines}"
    )
    lines = template.splitlines()
    try:
        first_heading = next(i for i, line in enumerate(lines) if line.startswith("## "))
    except StopIteration:
        prompt = f"{template}\n\n{skills_section}"
    else:
        head = "\n".join(lines[:first_heading]).rstrip()
        tail = "\n".join(lines[first_heading:])
        prompt = f"{head}\n\n{skills_section}\n\n{tail}"
    if extra:
        prompt += f"\n\n{extra}"
    return prompt


# ── Runner factory ─────────────────────────────────────────────────────
#
# Builds the underlying pydantic-ai Agent with all built-in tools.
# Tool registration is delegated to focused modules.

def _create_runner(
    model: Model,
    system_prompt: str,
    user_file_roots: tuple[Path, ...],
) -> PydanticAgent[_RunDeps, str]:
    """Build the pydantic-ai runner and register all tools."""
    from .skill_tools import register_skill_tools
    from .context_tools import register_context_tools
    from .thread_tools import register_thread_tools

    runner: PydanticAgent[_RunDeps, str] = PydanticAgent(
        model=model,
        system_prompt=system_prompt,
        deps_type=_RunDeps,
        output_type=str,
    )

    register_skill_tools(runner, user_file_roots)
    register_context_tools(runner)
    register_thread_tools(runner)

    return runner
