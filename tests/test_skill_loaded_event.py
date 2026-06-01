"""Tests for SkillLoadedEvent.

Verifies that:
  1. SkillLoadedEvent is present in the AgentEvent union and serialises correctly.
  2. use_skill queues a SkillLoadedEvent in _deps.pending_skill_loaded.
  3. The event is cleared from pending after being emitted (no duplicate events).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from skill_agent.models import (
    AgentEvent,
    SkillLoadedEvent,
    Skill,
)


# ── Model-level tests ──────────────────────────────────────────────────


def test_skill_loaded_event_fields() -> None:
    ev = SkillLoadedEvent(name="learner", source="/path/to/SKILL.md")
    assert ev.type == "skill_loaded"
    assert ev.name == "learner"
    assert ev.source == "/path/to/SKILL.md"


def test_skill_loaded_event_serialises() -> None:
    ev = SkillLoadedEvent(name="learner", source="/path/SKILL.md")
    data = ev.model_dump(mode="json")
    assert data["type"] == "skill_loaded"
    assert data["name"] == "learner"


def test_skill_loaded_event_in_agent_event_union() -> None:
    """The discriminated union must accept skill_loaded typed dicts."""
    from pydantic import TypeAdapter

    ta = TypeAdapter(AgentEvent)
    ev = ta.validate_python(
        {"type": "skill_loaded", "name": "demo", "source": "<builtin>"}
    )
    assert isinstance(ev, SkillLoadedEvent)


# ── Tool-handler tests ────────────────────────────────────────────────


@dataclass
class _StubDeps:
    skills: dict = field(default_factory=dict)
    activated_skills: list = field(default_factory=list)
    tool_log: list = field(default_factory=list)
    todo_list: list = field(default_factory=list)
    _next_todo_id: int = 1
    user_file_roots: tuple = field(default_factory=tuple)
    max_user_file_read_chars: int = 15000
    max_user_file_write_bytes: int = 2 * 1024 * 1024
    user_skills_dirs: tuple = field(default_factory=tuple)
    pending_client_requests: list = field(default_factory=list)
    pending_skill_loaded: list = field(default_factory=list)


@dataclass
class _StubCtx:
    deps: _StubDeps


def _make_tools(roots=()):
    """Register skill tools on a stub runner and return the tool registry."""
    registered: dict[str, Any] = {}

    class _StubRunner:
        def tool(self, description=""):
            def decorator(fn):
                registered[fn.__name__] = fn
                return fn
            return decorator

    from skill_agent.skill_tools import register_skill_tools
    register_skill_tools(_StubRunner(), roots)
    return registered


def _make_skill(name: str, path: Path | None = None) -> Skill:
    return Skill(
        name=name,
        description=f"Skill {name}",
        body="## Instructions\nDo the thing.",
        path=path,
    )


def test_use_skill_queues_skill_loaded_event(tmp_path: Path) -> None:
    """Calling use_skill should append a SkillLoadedEvent to pending_skill_loaded."""
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text("---\nname: demo\ndescription: test\n---\n# Demo\n")
    skill = _make_skill("demo", path=skill_md)

    tools = _make_tools()
    use_skill_fn = tools.get("use_skill")
    assert use_skill_fn is not None

    deps = _StubDeps(skills={"demo": skill})
    ctx = _StubCtx(deps=deps)
    result = use_skill_fn(ctx, skill_name="demo")

    assert "## Skill: demo" in result
    assert len(deps.pending_skill_loaded) == 1
    ev = deps.pending_skill_loaded[0]
    assert isinstance(ev, SkillLoadedEvent)
    assert ev.name == "demo"
    assert ev.source == str(skill_md)


def test_use_skill_source_is_builtin_when_no_path() -> None:
    """When skill has no path, source should be '<builtin>'."""
    skill = _make_skill("no-path-skill", path=None)
    tools = _make_tools()
    use_skill_fn = tools["use_skill"]

    deps = _StubDeps(skills={"no-path-skill": skill})
    ctx = _StubCtx(deps=deps)
    use_skill_fn(ctx, skill_name="no-path-skill")

    assert len(deps.pending_skill_loaded) == 1
    assert deps.pending_skill_loaded[0].source == "<builtin>"


def test_use_skill_unknown_skill_does_not_queue_event() -> None:
    """Calling use_skill with an unknown name should not emit SkillLoadedEvent."""
    tools = _make_tools()
    use_skill_fn = tools["use_skill"]

    deps = _StubDeps(skills={})
    ctx = _StubCtx(deps=deps)
    result = use_skill_fn(ctx, skill_name="ghost")

    assert "not found" in result.lower()
    assert len(deps.pending_skill_loaded) == 0


def test_pending_skill_loaded_separate_per_call() -> None:
    """Two successive use_skill calls each queue exactly one event each."""
    skill_a = _make_skill("skill-a")
    skill_b = _make_skill("skill-b")
    tools = _make_tools()
    use_skill_fn = tools["use_skill"]

    deps = _StubDeps(skills={"skill-a": skill_a, "skill-b": skill_b})
    ctx = _StubCtx(deps=deps)

    use_skill_fn(ctx, skill_name="skill-a")
    assert len(deps.pending_skill_loaded) == 1

    # Simulate the agent clearing pending between tool results (as _event_stream does)
    deps.pending_skill_loaded.clear()

    use_skill_fn(ctx, skill_name="skill-b")
    assert len(deps.pending_skill_loaded) == 1
    assert deps.pending_skill_loaded[0].name == "skill-b"
