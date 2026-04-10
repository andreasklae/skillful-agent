from __future__ import annotations

import asyncio

import pytest

from skill_agent.agent import Agent, _RunDeps
from skill_agent.threads import ThreadRegistry
from skill_agent.models import RunCompleteEvent, TextDeltaEvent, TokenUsage


def _make_queue_agent() -> Agent:
    agent = Agent.__new__(Agent)
    agent.thread_registry = ThreadRegistry()
    agent.thread_registry.create(name="main", participants=["user"])
    agent._deps = _RunDeps(
        skills={},
        thread_registry=agent.thread_registry,
        message_log=[],
        context_window=[],
        context_compression_threshold=100_000,
    )
    agent.message_log = []
    agent.context_window = []
    agent._conversation_messages = []
    agent._running = False
    agent._run_queue = None
    agent._run_worker_task = None
    agent._queued_runs = {}
    agent._global_run_subscribers = []
    agent._queued_run_keys = set()
    agent._auto_thread_run_counts = {}
    return agent


@pytest.mark.anyio
async def test_queued_runs_publish_lifecycle_and_agent_events(monkeypatch):
    agent = _make_queue_agent()

    async def fake_event_stream(user_message):
        yield TextDeltaEvent(content=str(user_message))
        yield RunCompleteEvent(usage=TokenUsage(input_tokens=1, output_tokens=1))

    monkeypatch.setattr(agent, "_event_stream", fake_event_stream)

    first_run = await agent.enqueue_run_message("first", source="api")
    second_run = await agent.enqueue_run_message("second", source="thread")

    first_events = [event async for event in agent.subscribe_run(first_run)]
    second_events = [event async for event in agent.subscribe_run(second_run)]

    assert [event["type"] for event in first_events[:2]] == ["run_queued", "run_started"]
    assert first_events[2]["event"]["type"] == "text_delta"
    assert first_events[2]["event"]["content"] == "first"
    assert first_events[3]["event"]["type"] == "run_complete"

    assert [event["type"] for event in second_events[:2]] == ["run_queued", "run_started"]
    assert second_events[2]["event"]["content"] == "second"


@pytest.mark.anyio
async def test_thread_notification_coalesces_while_queued_not_running(monkeypatch):
    """Two notifications enqueued before the first is picked up should coalesce."""
    agent = _make_queue_agent()

    # Pause the worker so both notifications are enqueued before execution starts.
    agent._run_queue = asyncio.Queue()

    first_run = await agent.enqueue_run_message(
        "new message in 'researcher'",
        source="thread",
        coalesce_key="thread_notification:researcher",
    )
    second_run = await agent.enqueue_run_message(
        "new message in 'researcher'",
        source="thread",
        coalesce_key="thread_notification:researcher",
    )

    assert first_run == second_run


@pytest.mark.anyio
async def test_thread_notification_queues_new_run_while_previous_running(monkeypatch):
    """A notification that arrives while the first run is already executing must queue a new run."""
    agent = _make_queue_agent()
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_event_stream(user_message):
        started.set()
        await release.wait()
        yield RunCompleteEvent(usage=TokenUsage(input_tokens=1, output_tokens=1))

    monkeypatch.setattr(agent, "_event_stream", fake_event_stream)

    first_run = await agent.enqueue_run_message(
        "new message in 'researcher'",
        source="thread",
        coalesce_key="thread_notification:researcher",
    )
    await started.wait()
    # First run is now executing — coalesce key has been released. A second
    # notification should queue a brand-new run, not be dropped.
    second_run = await agent.enqueue_run_message(
        "new message in 'researcher'",
        source="thread",
        coalesce_key="thread_notification:researcher",
    )

    assert first_run != second_run

    release.set()
    events = [event async for event in agent.subscribe_run(first_run)]
    assert events[-1]["event"]["type"] == "run_complete"


@pytest.mark.anyio
async def test_thread_notification_runs_are_suppressed_after_limit():
    agent = _make_queue_agent()
    agent._auto_thread_run_counts["researcher"] = 10

    agent._queue_thread_follow_up("researcher")
    await asyncio.sleep(0)

    assert agent._queued_runs == {}
