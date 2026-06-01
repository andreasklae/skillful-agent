"""Tests for AgentConfig.disabled_tools."""

from __future__ import annotations

from typing import Any


class _StubRunner:
    """Minimal stand-in for the pydantic-ai runner that just records which
    function names get registered as tools."""

    def __init__(self) -> None:
        self.registered: dict[str, Any] = {}

    def tool(self, description: str = ""):
        def decorator(fn):
            self.registered[fn.__name__] = fn
            return fn
        return decorator


def test_skill_tools_skip_disabled() -> None:
    from skill_agent.skill_tools import register_skill_tools

    runner = _StubRunner()
    register_skill_tools(runner, (), disabled_tools=frozenset({"manage_todos"}))

    assert "use_skill" in runner.registered, "non-disabled tool should still register"
    assert "read_reference" in runner.registered
    assert "manage_todos" not in runner.registered, (
        "manage_todos was in disabled_tools and should not have been registered"
    )


def test_skill_tools_register_everything_by_default() -> None:
    from skill_agent.skill_tools import register_skill_tools

    runner = _StubRunner()
    register_skill_tools(runner, ())

    # Sanity check: tools that should always register.
    for name in ("use_skill", "manage_todos", "list_skill_files", "read_reference"):
        assert name in runner.registered, f"{name} should register without filtering"


def test_thread_tools_skip_disabled() -> None:
    from skill_agent.thread_tools import register_thread_tools

    runner = _StubRunner()
    register_thread_tools(runner, disabled_tools=frozenset({"spawn_agent", "read_thread"}))

    assert "spawn_agent" not in runner.registered
    assert "read_thread" not in runner.registered
    # Other thread tools should still be present.
    assert "reply_to_thread" in runner.registered
    assert "archive_thread" in runner.registered


def test_context_tools_skip_disabled() -> None:
    from skill_agent.context_tools import register_context_tools

    runner = _StubRunner()
    register_context_tools(runner, disabled_tools=frozenset({"compress_all"}))

    assert "compress_message" in runner.registered
    assert "retrieve_message" in runner.registered
    assert "compress_all" not in runner.registered


def test_disable_native_skills_excludes_them(tmp_path) -> None:
    """When AgentConfig.disable_native_skills=True, _reload_skills must
    not merge native skills into the agent's skill set."""
    from pathlib import Path
    from pydantic_ai.models.test import TestModel
    from skill_agent import Agent, AgentConfig

    user_skills_dir = tmp_path / "user_skills"
    (user_skills_dir / "mine").mkdir(parents=True)
    (user_skills_dir / "mine" / "SKILL.md").write_text(
        "---\nname: mine\ndescription: User skill.\n---\nBody.\n",
        encoding="utf-8",
    )

    cfg = AgentConfig(disable_native_skills=True)
    agent = Agent(model=TestModel(), skills_dir=user_skills_dir, config=cfg)

    skill_names = set(agent._skills.keys())
    assert "mine" in skill_names
    # web-search-free is bundled in the SDK's native-skills/.
    assert "web-search-free" not in skill_names, (
        "disable_native_skills=True should exclude SDK-bundled skills"
    )


def test_unknown_disabled_name_is_noop() -> None:
    """Passing a name that doesn't match any tool should not raise. This
    keeps the API permissive for callers that don't track which tools
    currently exist."""
    from skill_agent.skill_tools import register_skill_tools

    runner = _StubRunner()
    register_skill_tools(
        runner,
        (),
        disabled_tools=frozenset({"never_was_a_tool", "manage_todos"}),
    )
    assert "manage_todos" not in runner.registered
    assert "use_skill" in runner.registered
