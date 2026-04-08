from skill_agent.inbox import Inbox, ThreadStatus
from skill_agent.messages import Message, MessageType, UIContext
from skill_agent.inbox_tools import (
    read_inbox_impl,
    read_thread_impl,
    write_to_thread_impl,
    dismiss_inbox_item_impl,
    delete_thread_impl,
)


def _ctx() -> UIContext:
    return UIContext(sender="test")


def test_read_inbox_returns_subjects_and_status():
    inbox = Inbox()
    inbox.create_item(content="c1", subject="s1", source_context=_ctx(), notify=False, thread_id="t-1")
    inbox.create_item(content="c2", subject="s2", source_context=_ctx(), notify=True, thread_id="t-2")
    log: list[Message] = []
    result = read_inbox_impl(inbox, log)
    assert "s1" in result
    assert "s2" in result
    assert "in_progress" in result
    assert len(log) == 1
    assert log[0].type == MessageType.tool_result


def test_read_thread_returns_full_contents():
    inbox = Inbox()
    inbox.create_item(content="first", subject="topic", source_context=_ctx(), notify=False, thread_id="t-1")
    inbox.write_to_thread(thread_id="t-1", content="second", source_context=_ctx(), notify=False)
    log: list[Message] = []
    window: list[Message] = []
    result = read_thread_impl(inbox, log, window, "t-1")
    assert "first" in result
    assert "second" in result


def test_write_to_thread_resolves_own_inbox():
    own_inbox = Inbox()
    own_inbox.create_item(content="init", subject="s", source_context=_ctx(), notify=False, thread_id="t-1")
    active_subagents: dict = {}
    log: list[Message] = []
    result = write_to_thread_impl(
        own_inbox=own_inbox,
        active_subagents=active_subagents,
        message_log=log,
        thread_id="t-1",
        content="update",
        notify=False,
        source_context=_ctx(),
    )
    assert "t-1" in result


def test_dismiss_inbox_item():
    inbox = Inbox()
    item = inbox.create_item(content="c", subject="s", source_context=_ctx(), notify=True)
    dismiss_inbox_item_impl(inbox, item.id)
    assert inbox.pending_notifications() is False


def test_delete_thread():
    inbox = Inbox()
    inbox.create_item(content="c", subject="s", source_context=_ctx(), notify=False, thread_id="t-1")
    deleted, result = delete_thread_impl(inbox, "t-1", {}, {})
    assert len(deleted) == 1
    assert len(inbox.read_thread("t-1")) == 0


def test_singleton_cleanup_on_delete():
    inbox = Inbox()
    inbox.create_item(content="c", subject="s", source_context=_ctx(), notify=False, thread_id="t-1")
    singletons = {"researcher": "t-1"}
    delete_thread_impl(inbox, "t-1", {}, singletons)
    assert "researcher" not in singletons
