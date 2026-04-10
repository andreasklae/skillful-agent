"""Tests for thread tools: read_thread, reply_to_thread, archive_thread."""

from skill_agent.threads import ThreadRegistry, ThreadStatus
from skill_agent.thread_tools import (
    read_thread_impl,
    reply_to_thread_impl,
    archive_thread_impl,
)
from skill_agent.messages import Message, MessageType, UIContext


def _make_registry_with_thread() -> ThreadRegistry:
    reg = ThreadRegistry()
    reg.create("main", participants=["user"])
    reg.create("researcher", participants=["subagent"])
    t = reg.get("researcher")
    t.send("Starting research", UIContext(sender="subagent"))
    t.reply("Good, proceed")
    return reg


def test_read_thread_returns_messages():
    reg = _make_registry_with_thread()
    log: list[Message] = []
    window: list[Message] = []

    result = read_thread_impl(reg, log, window, "researcher")

    assert "researcher" in result
    assert "Starting research" in result
    assert "Good, proceed" in result
    assert "2 messages" in result


def test_read_thread_not_found():
    reg = ThreadRegistry()
    result = read_thread_impl(reg, [], [], "missing")
    assert "not found" in result.lower()


def test_read_thread_compresses_previous_reads():
    reg = _make_registry_with_thread()
    log: list[Message] = []
    window: list[Message] = []

    # First read
    read_thread_impl(reg, log, window, "researcher")
    assert len(window) == 1
    assert window[0].summary is None

    # Second read — first should be compressed
    read_thread_impl(reg, log, window, "researcher")
    assert window[0].summary is not None
    assert "previous read" in window[0].summary
    assert window[0].content is None


def test_reply_to_thread():
    reg = _make_registry_with_thread()
    log: list[Message] = []

    result = reply_to_thread_impl(reg, log, "researcher", "Keep going")

    assert "Replied" in result
    thread = reg.get("researcher")
    assert thread.messages[-1].content == "Keep going"
    assert thread.messages[-1].role.value == "agent"


def test_reply_to_main_blocked():
    reg = ThreadRegistry()
    reg.create("main")
    result = reply_to_thread_impl(reg, [], "main", "hello")
    assert "Cannot reply to the main thread" in result


def test_reply_to_thread_not_found():
    reg = ThreadRegistry()
    result = reply_to_thread_impl(reg, [], "missing", "hello")
    assert "not found" in result.lower()


def test_reply_fires_outbound_listeners():
    reg = _make_registry_with_thread()
    received = []
    reg.get("researcher").subscribe_outbound(lambda msg: received.append(msg))

    reply_to_thread_impl(reg, [], "researcher", "New instruction")

    assert len(received) == 1
    assert received[0].content == "New instruction"


def test_archive_thread():
    reg = _make_registry_with_thread()
    result = archive_thread_impl(reg, "researcher")

    assert "Archived" in result
    thread = reg.get("researcher")
    assert thread.archived is True
    assert thread.status == ThreadStatus.done


def test_archive_main_blocked():
    reg = ThreadRegistry()
    reg.create("main")
    result = archive_thread_impl(reg, "main")
    assert "Cannot archive" in result


def test_archive_not_found():
    reg = ThreadRegistry()
    result = archive_thread_impl(reg, "missing")
    assert "not found" in result.lower()
