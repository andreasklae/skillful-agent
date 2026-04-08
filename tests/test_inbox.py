import asyncio
import uuid

from skill_agent.inbox import Inbox, InboxItem, Thread, ThreadStatus
from skill_agent.messages import UIContext, SubAgentContext


def _ui_ctx(sender: str = "user") -> UIContext:
    return UIContext(sender=sender)


def test_create_item_auto_generates_thread_id():
    inbox = Inbox()
    item = inbox.create_item(
        content="hello",
        subject="greeting",
        source_context=_ui_ctx(),
        notify=False,
    )
    assert isinstance(item, InboxItem)
    uuid.UUID(item.thread_id)
    assert item.read is False
    assert item.content == "hello"


def test_create_item_with_explicit_thread_id():
    inbox = Inbox()
    item = inbox.create_item(
        content="msg",
        subject="subj",
        thread_id="t-1",
        source_context=_ui_ctx(),
        notify=False,
    )
    assert item.thread_id == "t-1"


def test_write_to_thread_requires_existing_thread():
    inbox = Inbox()
    try:
        inbox.write_to_thread(
            thread_id="nonexistent",
            content="msg",
            source_context=_ui_ctx(),
            notify=False,
        )
        assert False, "Should have raised"
    except KeyError:
        pass


def test_write_to_thread_appends_to_existing():
    inbox = Inbox()
    inbox.create_item(
        content="first", subject="topic", thread_id="t-1",
        source_context=_ui_ctx(), notify=False,
    )
    inbox.write_to_thread(
        thread_id="t-1", content="second",
        source_context=_ui_ctx(), notify=False,
    )
    thread_items = inbox.read_thread("t-1")
    assert len(thread_items) == 2


def test_read_inbox_returns_unread_and_marks_read():
    inbox = Inbox()
    inbox.create_item(
        content="a", subject="s1",
        source_context=_ui_ctx(), notify=False,
    )
    inbox.create_item(
        content="b", subject="s2",
        source_context=_ui_ctx(), notify=True,
    )
    unread = inbox.read_inbox()
    assert len(unread) == 2
    assert all(item.read for item in inbox.items)
    assert len(inbox.read_inbox()) == 0


def test_get_thread_returns_thread_wrapper():
    inbox = Inbox()
    inbox.create_item(
        content="msg", subject="subj", thread_id="t-1",
        source_context=_ui_ctx(), notify=False,
    )
    thread = inbox.get_thread("t-1")
    assert isinstance(thread, Thread)
    assert thread.thread_id == "t-1"
    assert thread.status == ThreadStatus.in_progress


def test_thread_status_updates():
    inbox = Inbox()
    inbox.create_item(
        content="msg", subject="subj", thread_id="t-1",
        source_context=_ui_ctx(), notify=False,
    )
    thread = inbox.get_thread("t-1")
    assert thread.status == ThreadStatus.in_progress
    inbox.write_to_thread(
        thread_id="t-1", content="update",
        source_context=_ui_ctx(), notify=False,
        status=ThreadStatus.waiting_for_response,
    )
    assert inbox.get_thread("t-1").status == ThreadStatus.waiting_for_response


def test_dismiss_item():
    inbox = Inbox()
    item = inbox.create_item(
        content="msg", subject="subj",
        source_context=_ui_ctx(), notify=True,
    )
    inbox.dismiss_item(item.id)
    assert inbox.pending_notifications() is False


def test_delete_thread():
    inbox = Inbox()
    inbox.create_item(
        content="msg", subject="subj", thread_id="t-1",
        source_context=_ui_ctx(), notify=False,
    )
    inbox.delete_thread("t-1")
    assert len([i for i in inbox.items if i.thread_id == "t-1"]) == 0


def test_pending_notifications():
    inbox = Inbox()
    inbox.create_item(
        content="a", subject="s",
        source_context=_ui_ctx(), notify=False,
    )
    assert inbox.pending_notifications() is False
    inbox.create_item(
        content="b", subject="s",
        source_context=_ui_ctx(), notify=True,
    )
    assert inbox.pending_notifications() is True


def test_subscribe_yields_new_items():
    inbox = Inbox()

    async def _test():
        items_received = []

        async def consumer():
            async for item in inbox.subscribe():
                items_received.append(item)
                if len(items_received) == 2:
                    break

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0.01)
        inbox.create_item(
            content="a", subject="s1",
            source_context=_ui_ctx(), notify=False,
        )
        inbox.create_item(
            content="b", subject="s2",
            source_context=_ui_ctx(), notify=False,
        )
        await asyncio.wait_for(task, timeout=2.0)
        assert len(items_received) == 2
        assert items_received[0].content == "a"

    asyncio.run(_test())
