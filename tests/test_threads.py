"""Tests for Thread, ThreadMessage, ThreadRegistry, and listener mechanisms."""

from skill_agent.threads import (
    Thread,
    ThreadEvent,
    ThreadMessage,
    ThreadRegistry,
    ThreadRole,
    ThreadStatus,
)
from skill_agent.messages import UIContext


def test_thread_send_appends_participant_message():
    thread = Thread(name="test")
    msg = thread.send("hello from participant")
    assert msg.role == ThreadRole.participant
    assert msg.content == "hello from participant"
    assert len(thread.messages) == 1
    assert thread.messages[0] is msg


def test_thread_reply_appends_agent_message():
    thread = Thread(name="test")
    msg = thread.reply("hello from agent")
    assert msg.role == ThreadRole.agent
    assert msg.content == "hello from agent"
    assert len(thread.messages) == 1


def test_thread_send_fires_inbound_listeners():
    received = []
    thread = Thread(name="test")
    thread.subscribe_inbound(lambda msg: received.append(msg))
    thread.send("ping")
    assert len(received) == 1
    assert received[0].content == "ping"
    assert received[0].role == ThreadRole.participant


def test_thread_reply_fires_outbound_listeners():
    received = []
    thread = Thread(name="test")
    thread.subscribe_outbound(lambda msg: received.append(msg))
    thread.reply("pong")
    assert len(received) == 1
    assert received[0].content == "pong"
    assert received[0].role == ThreadRole.agent


def test_thread_send_does_not_fire_outbound():
    received = []
    thread = Thread(name="test")
    thread.subscribe_outbound(lambda msg: received.append(msg))
    thread.send("hello")
    assert received == []


def test_thread_reply_does_not_fire_inbound():
    received = []
    thread = Thread(name="test")
    thread.subscribe_inbound(lambda msg: received.append(msg))
    thread.reply("hello")
    assert received == []


def test_thread_summary_with_messages():
    thread = Thread(name="research")
    thread.send("start research")
    summary = thread.summary()
    assert "research" in summary
    assert "active" in summary


def test_thread_summary_without_messages():
    thread = Thread(name="empty")
    assert "no messages" in thread.summary()


def test_thread_send_with_source_context():
    thread = Thread(name="test")
    ctx = UIContext(sender="user-1")
    msg = thread.send("hello", source_context=ctx)
    assert msg.source_context is not None
    assert msg.source_context.sender == "user-1"


# ── ThreadRegistry tests ───────────────────────────────────────────


def test_registry_create_and_get():
    reg = ThreadRegistry()
    thread = reg.create("test-thread", participants=["alice"])
    assert thread.name == "test-thread"
    assert reg.get("test-thread") is thread


def test_registry_create_duplicate_raises():
    reg = ThreadRegistry()
    reg.create("dup")
    try:
        reg.create("dup")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_registry_get_missing_raises():
    reg = ThreadRegistry()
    try:
        reg.get("missing")
        assert False, "Should have raised KeyError"
    except KeyError:
        pass


def test_registry_active_excludes_archived():
    reg = ThreadRegistry()
    reg.create("active-thread")
    reg.create("archived-thread")
    reg.archive("archived-thread")
    active = reg.active()
    names = [t.name for t in active]
    assert "active-thread" in names
    assert "archived-thread" not in names


def test_registry_archive_sets_done():
    reg = ThreadRegistry()
    reg.create("t")
    reg.archive("t")
    thread = reg.get("t")
    assert thread.archived is True
    assert thread.status == ThreadStatus.done


def test_registry_summary_excludes_main():
    reg = ThreadRegistry()
    reg.create("main")
    reg.create("side-thread")
    reg.get("side-thread").send("hello")
    summary = reg.summary()
    assert "main" not in summary
    assert "side-thread" in summary


def test_registry_summary_empty_when_no_non_main_threads():
    reg = ThreadRegistry()
    reg.create("main")
    assert reg.summary() == ""


def test_registry_notifies_subscribers_on_send():
    """Registry callback fires when a thread created through it receives messages."""
    events: list[ThreadEvent] = []
    reg = ThreadRegistry()
    thread = reg.create("test")

    # Manually simulate what subscribe() does — just capture events via callback
    original_cb = thread._registry_callback
    def capture(name, msg):
        events.append(ThreadEvent(thread_name=name, message=msg))
        if original_cb:
            original_cb(name, msg)
    thread._registry_callback = capture

    thread.send("hello")
    thread.reply("hi back")

    assert len(events) == 2
    assert events[0].thread_name == "test"
    assert events[0].message.role == ThreadRole.participant
    assert events[1].message.role == ThreadRole.agent


def test_thread_instances_have_independent_listeners():
    """Each thread instance has its own listener lists (not shared class-level)."""
    t1 = Thread(name="t1")
    t2 = Thread(name="t2")
    received = []
    t1.subscribe_inbound(lambda msg: received.append("t1"))
    t2.send("ping")
    assert received == []  # t2's send should not trigger t1's listener
