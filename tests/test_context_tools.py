from skill_agent.messages import Message, MessageType
from skill_agent.context_tools import (
    compress_message_impl,
    retrieve_message_impl,
    compress_all_impl,
    build_generic_summary,
)


def _make_messages(n: int = 3) -> tuple[list[Message], list[Message]]:
    """Return (message_log, context_window) with n messages.

    Log and window hold separate Message objects with the same IDs,
    matching the real system where message_log keeps originals and
    context_window has mutable copies.
    """
    log: list[Message] = []
    window: list[Message] = []
    for i in range(n):
        msg_id = f"msg-id-{i}"
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc)
        log.append(Message(id=msg_id, timestamp=ts, type=MessageType.user, content=f"msg-{i}"))
        window.append(Message(id=msg_id, timestamp=ts, type=MessageType.user, content=f"msg-{i}"))
    return log, window


def test_compress_message_sets_summary_and_clears_content():
    log, window = _make_messages(3)
    target_id = window[1].id
    result = compress_message_impl(window, target_id, "summary of msg-1")
    assert "compressed" in result.lower()
    assert window[1].summary == "summary of msg-1"
    assert window[1].content is None
    assert log[1].content == "msg-1"


def test_compress_message_not_found():
    _, window = _make_messages(1)
    result = compress_message_impl(window, "nonexistent-id", "summary")
    assert "not found" in result.lower()


def test_retrieve_message_restores_content():
    log, window = _make_messages(3)
    target_id = window[1].id
    compress_message_impl(window, target_id, "summary")
    assert window[1].content is None
    retrieve_message_impl(log, window, target_id)
    assert window[1].content == "msg-1"
    assert window[1].summary is None


def test_retrieve_message_reinserts_if_removed():
    log, window = _make_messages(3)
    target_id = window[1].id
    window.pop(1)
    assert len(window) == 2
    retrieve_message_impl(log, window, target_id)
    assert len(window) == 3
    assert window[1].id == target_id
    assert window[1].content == "msg-1"


def test_compress_all_replaces_window():
    log, window = _make_messages(5)
    first_id = window[0].id
    last_id = window[-1].id
    compress_all_impl(log, window, "everything summarized", "resume by doing X")
    assert len(window) == 2
    assert window[0].type == MessageType.system
    assert first_id in window[0].content
    assert last_id in window[0].content
    assert "everything summarized" in window[0].content
    assert "resume by doing X" in window[0].content
    assert window[1].type == MessageType.system
    assert "compressed" in window[1].content.lower()
    assert len(log) == 7  # 5 original + 2 system


def test_build_generic_summary_with_messages():
    log = [
        Message(type=MessageType.user, content="What is Python?"),
        Message(type=MessageType.tool_call, content={"tool": "use_skill", "description": "loading"}),
        Message(type=MessageType.agent, content="Python is a programming language."),
    ]
    summary, instruction = build_generic_summary(log, [])
    assert "Python" in summary
    assert "use_skill" in summary
    assert instruction


def test_build_generic_summary_empty_log():
    summary, instruction = build_generic_summary([], [])
    assert "compressed" in summary.lower()
    assert instruction
