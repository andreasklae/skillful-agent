"""Tests that Agent has message_log, context_window, and thread_registry wired up."""
from skill_agent.messages import Message, MessageType
from skill_agent.threads import ThreadRegistry


def test_rundeps_has_new_fields():
    from skill_agent.agent import _RunDeps

    deps = _RunDeps(
        skills={},
        thread_registry=ThreadRegistry(),
        message_log=[],
        context_window=[],
        context_compression_threshold=100_000,
    )
    assert isinstance(deps.thread_registry, ThreadRegistry)
    assert deps.message_log == []
    assert deps.context_window == []
    assert deps.context_compression_threshold == 100_000
