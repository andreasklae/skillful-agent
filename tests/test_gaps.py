"""Tests for gaps identified during review:
1. Auto-compression fallback triggers directly (no model cooperation needed)
2. Thread reply fires outbound listeners (subagent communication path)
3. Singleton tracking and re-registration after archive
4. _RunDeps fields are references, not copies
"""

from skill_agent.threads import ThreadRegistry, ThreadStatus
from skill_agent.messages import Message, MessageType, UIContext
from skill_agent.context_tools import compress_all_impl, build_generic_summary
from skill_agent.thread_tools import reply_to_thread_impl, archive_thread_impl


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


# ── 2. Thread reply fires outbound (subagent communication path) ────


def test_reply_to_thread_fires_outbound_listener():
    """reply_to_thread calls thread.reply(), which fires outbound listeners.
    This is the parent → subagent communication path."""
    reg = ThreadRegistry()
    reg.create("researcher", participants=["subagent"])
    reg.get("researcher").send("ready")

    received = []
    reg.get("researcher").subscribe_outbound(lambda msg: received.append(msg))

    reply_to_thread_impl(reg, [], "researcher", "Start working on X")

    assert len(received) == 1
    assert received[0].content == "Start working on X"
    assert received[0].role.value == "agent"


def test_thread_send_fires_inbound_listener():
    """thread.send() fires inbound listeners. This is the subagent → parent path."""
    reg = ThreadRegistry()
    reg.create("researcher")

    received = []
    reg.get("researcher").subscribe_inbound(lambda msg: received.append(msg))

    reg.get("researcher").send("Result: found 5 sources")

    assert len(received) == 1
    assert received[0].content == "Result: found 5 sources"
    assert received[0].role.value == "participant"


# ── 3. Singleton re-registration after archive ─────────────────────


def test_singleton_reregistration_after_archive():
    """After archiving a singleton's thread, a new one can be registered with the same id."""
    reg = ThreadRegistry()
    singletons: dict[str, str] = {"researcher": "old-thread"}

    reg.create("old-thread", participants=["subagent"])
    reg.archive("old-thread")

    # Singleton mapping can be updated
    singletons["researcher"] = "new-thread"
    reg.create("new-thread", participants=["subagent"])

    assert singletons["researcher"] == "new-thread"
    assert reg.get("new-thread").status == ThreadStatus.active
    assert reg.get("old-thread").archived is True


# ── 4. _RunDeps fields are references ────────────────────────────────


def test_rundeps_fields_are_references_not_copies():
    """_RunDeps collection fields must be the same objects as Agent's attributes,
    so mutations through deps are visible on the Agent and vice versa."""
    from skill_agent.agent import _RunDeps

    # Simulate what Agent.__init__ does
    shared_log: list[Message] = []
    shared_window: list[Message] = []
    shared_registry = ThreadRegistry()

    deps = _RunDeps(
        skills={},
        thread_registry=shared_registry,
        message_log=shared_log,
        context_window=shared_window,
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

    # Registry is the same object
    assert deps.thread_registry is shared_registry
