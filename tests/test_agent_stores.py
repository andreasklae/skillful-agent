"""Tests that Agent has message_log, context_window, and inbox wired up."""
from skill_agent.messages import Message, MessageType
from skill_agent.inbox import Inbox


def test_rundeps_has_new_fields():
    from skill_agent.agent import _RunDeps

    deps = _RunDeps(
        skills={},
        inbox=Inbox(),
        message_log=[],
        context_window=[],
        active_subagents={},
        context_compression_threshold=100_000,
    )
    assert isinstance(deps.inbox, Inbox)
    assert deps.message_log == []
    assert deps.context_window == []
    assert deps.active_subagents == {}
    assert deps.context_compression_threshold == 100_000
