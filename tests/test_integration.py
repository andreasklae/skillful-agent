"""Integration test: verify all new components are wired up on Agent."""
import importlib


def test_all_new_modules_importable():
    modules = [
        "skill_agent.messages",
        "skill_agent.inbox",
        "skill_agent.context_tools",
        "skill_agent.inbox_tools",
        "skill_agent.skill_tools",
        "skill_agent.subagent",
    ]
    for mod in modules:
        importlib.import_module(mod)


def test_public_api_exports():
    from skill_agent import (
        Message, MessageType, SourceContext, UIContext, EmailContext, SubAgentContext,
        Inbox, InboxItem, Thread, ThreadStatus,
        SubAgent,
    )
    assert Message is not None
    assert Inbox is not None
    assert SubAgent is not None


def test_agent_config_has_new_field():
    from skill_agent import AgentConfig
    cfg = AgentConfig()
    assert hasattr(cfg, 'context_compression_threshold')
    assert cfg.context_compression_threshold == 100_000
