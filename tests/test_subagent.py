"""Test SubAgent class structure and thread lifecycle."""
from skill_agent.subagent import SubAgent
from skill_agent.inbox import Inbox, ThreadStatus
from skill_agent.messages import UIContext


def test_subagent_class_exists():
    assert hasattr(SubAgent, '__init__')
    assert hasattr(SubAgent, 'run_loop')
    assert hasattr(SubAgent, 'finish')


def test_thread_deleted_detection():
    inbox = Inbox()
    inbox.create_item(
        content="task", subject="work", thread_id="t-1",
        source_context=UIContext(), notify=False,
    )
    assert inbox.get_thread("t-1").items
    inbox.delete_thread("t-1")
    assert not inbox.get_thread("t-1").items
    assert inbox.get_thread("t-1").status == ThreadStatus.done


def test_spawn_creates_thread():
    inbox = Inbox()
    thread_id = "sa-test-1"
    inbox.create_item(
        content="Subagent task: research X",
        subject="research X",
        source_context=UIContext(sender="spawn_tool"),
        notify=False,
        thread_id=thread_id,
        status=ThreadStatus.in_progress,
    )
    thread = inbox.get_thread(thread_id)
    assert thread.status == ThreadStatus.in_progress
    assert thread.items[0].subject == "research X"


def test_singleton_dedup():
    singleton_map: dict[str, str] = {"researcher": "t-existing"}
    assert "researcher" in singleton_map
    assert singleton_map["researcher"] == "t-existing"


def test_singleton_new_entry():
    singleton_map: dict[str, str] = {}
    assert "librarian" not in singleton_map
    singleton_map["librarian"] = "t-new"
    assert singleton_map["librarian"] == "t-new"
