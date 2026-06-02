"""Tests for system-prompt building and disabled-tool stripping."""

from __future__ import annotations

import re

from skill_agent.agent import _build_system_prompt, _strip_disabled_tools_from_template
from skill_agent.models import Skill


_CHESS_DISABLED = frozenset({
    "manage_todos", "register_skill", "scaffold_skill", "write_skill_file",
    "list_skill_files", "call_client_function", "compress_message",
    "retrieve_message", "compress_all", "read_thread", "reply_to_thread",
    "archive_thread", "spawn_agent",
})


def test_full_prompt_lists_skills_and_keeps_core_tools():
    skills = {"chess": Skill(name="chess", description="Play chess.", body="b")}
    p = _build_system_prompt(skills, None)
    assert "use_skill" in p
    assert "read_reference" in p
    assert "**chess**: Play chess." in p


def test_no_stale_run_script_reference():
    skills = {"chess": Skill(name="chess", description="Play chess.", body="b")}
    p = _build_system_prompt(skills, None)
    assert "run_script" not in p


def test_disabled_tools_and_their_rules_are_stripped():
    skills = {"chess": Skill(name="chess", description="Play chess.", body="b")}
    p = _build_system_prompt(skills, "You are white.", disabled_tools=_CHESS_DISABLED)
    for bad in ("manage_todos", "spawn_agent", "reply_to_thread", "compress_all"):
        assert bad not in p, f"{bad} should have been stripped"
    assert "use_skill" in p and "read_reference" in p
    assert "You are white." in p  # system_prompt_extra appended


def test_surviving_rules_are_renumbered_without_gaps():
    p = _build_system_prompt(
        {"chess": Skill(name="chess", description="x", body="b")},
        None,
        disabled_tools=_CHESS_DISABLED,
    )
    numbers = [int(m.group(1)) for m in re.finditer(r"^(\d+)\. ", p, re.MULTILINE)]
    assert numbers, "expected at least one numbered rule to survive"
    assert numbers == list(range(1, len(numbers) + 1)), f"rules not 1..N: {numbers}"


def test_no_runs_of_blank_lines():
    p = _build_system_prompt(
        {"chess": Skill(name="chess", description="x", body="b")},
        None,
        disabled_tools=_CHESS_DISABLED,
    )
    assert "\n\n\n" not in p


def test_empty_sections_dropped():
    # With all thread + context tools disabled, those section headings go away.
    p = _build_system_prompt(
        {"chess": Skill(name="chess", description="x", body="b")},
        None,
        disabled_tools=_CHESS_DISABLED,
    )
    assert "## Thread & communication tools" not in p
    assert "## Context management tools" not in p


def test_mixed_rule_survives_when_one_tool_still_enabled():
    # A rule mentioning both an enabled and a disabled tool must survive.
    template = (
        "Intro.\n\n"
        "## Built-in tools\n"
        "  - **use_skill**: load.\n"
        "  - **manage_todos**: plan.\n\n"
        "## Rules\n"
        "1. Use `use_skill` and `manage_todos` together.\n"
        "2. Only `manage_todos` here.\n"
    )
    out = _strip_disabled_tools_from_template(template, frozenset({"manage_todos"}))
    assert "Use `use_skill` and `manage_todos` together." in out  # mixed rule kept
    assert "Only `manage_todos` here." not in out                  # all-disabled rule dropped
    assert "  - **manage_todos**" not in out                       # tool bullet dropped
