"""Tests for gaps identified during review:
1. Auto-compression fallback triggers directly (no model cooperation needed)
2. write_to_thread routes to subagent inbox vs self inbox
3. Singleton re-spawn after delete
4. _RunDeps fields are references, not copies
"""

from skill_agent.inbox import Inbox, ThreadStatus
from skill_agent.messages import Message, MessageType, UIContext
from skill_agent.context_tools import compress_all_impl, build_generic_summary
from skill_agent.inbox_tools import write_to_thread_impl, delete_thread_impl


# ── 1. Auto-compression fallback ─────────────────────────────────────


def test_auto_compression_forces_without_model():
    """The runtime calls compress_all_impl directly — no model tool call needed.
    Simulate the exact code path from _event_stream: build_generic_summary + compress_all_impl.
    """
    log = [
        Message(type=MessageType.user, content="Tell me about Python"),
        Message(type=MessageType.tool_call, content={"tool": "use_skill", "description": "loading wikipedia"}),
        Message(type=MessageType.tool_result, content={"tool": "use_skill"}),
        Message(type=MessageType.agent, content="Python is a programming language created by Guido van Rossum."),
    ]
    window = [Message(id=m.id, timestamp=m.timestamp, type=m.type, content=m.content) for m in log]

    # Simulate: input_tokens > threshold, runtime forces compression
    summary, instruction = build_generic_summary(log, [])
    result = compress_all_impl(log, window, summary, instruction)

    # Window should be compressed to summary + notification
    assert len(window) == 2
    assert window[0].type == MessageType.system
    assert "Python" in window[0].content  # summary mentions content
    assert "use_skill" in window[0].content  # summary mentions tools
    assert window[1].type == MessageType.system
    assert "compressed" in window[1].content.lower()

    # Original log entries still intact (4 original + 2 new system messages)
    assert len(log) == 6
    assert log[0].content == "Tell me about Python"  # untouched


# ── 2. write_to_thread routing ───────────────────────────────────────


class _FakeSubagent:
    """Minimal stand-in with its own inbox."""
    def __init__(self) -> None:
        self.inbox = Inbox()
        # Create a thread the subagent can receive on
        self.inbox.create_item(
            content="init", subject="sa-thread",
            source_context=UIContext(sender="spawn"), notify=False,
            thread_id="sa-1",
        )


def test_write_to_thread_routes_to_subagent_inbox():
    """When thread_id matches an active subagent, the message goes to that subagent's inbox."""
    own_inbox = Inbox()
    subagent = _FakeSubagent()
    active_subagents = {"sa-1": subagent}
    log: list[Message] = []

    result = write_to_thread_impl(
        own_inbox=own_inbox,
        active_subagents=active_subagents,
        message_log=log,
        thread_id="sa-1",
        content="instruction for subagent",
        notify=True,
        source_context=UIContext(sender="parent"),
    )

    assert "sa-1" in result
    # Message landed in the subagent's inbox, not the parent's
    sa_items = subagent.inbox.read_thread("sa-1")
    assert any("instruction for subagent" in item.content for item in sa_items)
    # Parent inbox should NOT have this message
    assert not any("instruction for subagent" in item.content for item in own_inbox.items)


def test_write_to_thread_falls_back_to_own_inbox():
    """When thread_id doesn't match a subagent, message goes to own inbox."""
    own_inbox = Inbox()
    own_inbox.create_item(
        content="init", subject="my-thread",
        source_context=UIContext(), notify=False, thread_id="t-own",
    )
    active_subagents: dict = {}
    log: list[Message] = []

    result = write_to_thread_impl(
        own_inbox=own_inbox,
        active_subagents=active_subagents,
        message_log=log,
        thread_id="t-own",
        content="self-note",
        notify=False,
        source_context=UIContext(sender="agent"),
    )

    assert "t-own" in result
    items = own_inbox.read_thread("t-own")
    assert any("self-note" in item.content for item in items)


def test_write_to_thread_nonexistent_returns_error():
    """Writing to a thread that doesn't exist in any inbox returns an error."""
    own_inbox = Inbox()
    log: list[Message] = []

    result = write_to_thread_impl(
        own_inbox=own_inbox,
        active_subagents={},
        message_log=log,
        thread_id="ghost-thread",
        content="hello",
        notify=False,
    )

    assert "not found" in result.lower()


# ── 3. Singleton re-spawn after delete ───────────────────────────────


def test_singleton_respawn_after_delete():
    """After deleting a singleton's thread, a new one can be spawned with the same id."""
    inbox = Inbox()
    singletons: dict[str, str] = {"researcher": "t-old"}

    # Create and delete the old thread
    inbox.create_item(
        content="old task", subject="research",
        source_context=UIContext(), notify=False, thread_id="t-old",
    )
    delete_thread_impl(inbox, "t-old", {}, singletons)

    # Singleton mapping should be cleared
    assert "researcher" not in singletons

    # Simulate re-spawn: a new singleton can now be registered
    singletons["researcher"] = "t-new"
    inbox.create_item(
        content="new task", subject="research v2",
        source_context=UIContext(), notify=False, thread_id="t-new",
    )

    assert singletons["researcher"] == "t-new"
    assert inbox.get_thread("t-new").items[0].subject == "research v2"


# ── 4. _RunDeps fields are references ────────────────────────────────


def test_rundeps_fields_are_references_not_copies():
    """_RunDeps collection fields must be the same objects as Agent's attributes,
    so mutations through deps are visible on the Agent and vice versa."""
    from skill_agent.agent import _RunDeps

    # Simulate what Agent.__init__ does
    shared_log: list[Message] = []
    shared_window: list[Message] = []
    shared_inbox = Inbox()
    shared_subagents: dict = {}

    deps = _RunDeps(
        skills={},
        inbox=shared_inbox,
        message_log=shared_log,
        context_window=shared_window,
        active_subagents=shared_subagents,
        context_compression_threshold=100_000,
    )

    # Mutate through deps — should be visible via the original references
    deps.message_log.append(Message(type=MessageType.user, content="hello"))
    assert len(shared_log) == 1
    assert shared_log[0].content == "hello"

    # Mutate through the original — should be visible via deps
    shared_window.append(Message(type=MessageType.agent, content="hi"))
    assert len(deps.context_window) == 1
    assert deps.context_window[0].content == "hi"

    # Inbox is the same object
    assert deps.inbox is shared_inbox

    # Subagents dict is the same object
    deps.active_subagents["sa-1"] = "fake"
    assert shared_subagents["sa-1"] == "fake"
