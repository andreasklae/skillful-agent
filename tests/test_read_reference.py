"""Tests for the path-based read_reference tool.

read_reference takes a path relative to the skill's references/ directory,
supports subfolders, and is jailed to that directory (no escape). It is no
longer gated by a flat allowlist of top-level filenames.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from skill_agent.models import Skill


class _StubRunner:
    def __init__(self) -> None:
        self.registered: dict = {}

    def tool(self, description: str = ""):
        def decorator(fn):
            self.registered[fn.__name__] = fn
            return fn
        return decorator


def _build_skill(tmp_path: Path) -> Skill:
    skill_dir = tmp_path / "chess"
    refs = skill_dir / "references" / "patterns" / "mating-patterns"
    refs.mkdir(parents=True)
    (skill_dir / "references" / "index.md").write_text("# Index\n", encoding="utf-8")
    (refs / "back-rank-mate.md").write_text("# Back-Rank Mate\nbody\n", encoding="utf-8")
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text("---\nname: chess\ndescription: x\n---\nbody", encoding="utf-8")
    return Skill(name="chess", description="x", body="body", path=skill_md)


def _make_ctx(skill: Skill):
    deps = SimpleNamespace(skills={skill.name: skill}, tool_log=[])
    return SimpleNamespace(deps=deps)


def _read_reference(tmp_path):
    from skill_agent.skill_tools import register_skill_tools

    runner = _StubRunner()
    register_skill_tools(runner, ())
    return runner.registered["read_reference"]


def test_reads_top_level_file(tmp_path):
    skill = _build_skill(tmp_path)
    fn = _read_reference(tmp_path)
    out = fn(_make_ctx(skill), "chess", "index.md")
    assert "# Index" in out


def test_reads_nested_file(tmp_path):
    skill = _build_skill(tmp_path)
    fn = _read_reference(tmp_path)
    out = fn(_make_ctx(skill), "chess", "patterns/mating-patterns/back-rank-mate.md")
    assert "Back-Rank Mate" in out


def test_tolerates_leading_references_prefix(tmp_path):
    skill = _build_skill(tmp_path)
    fn = _read_reference(tmp_path)
    out = fn(_make_ctx(skill), "chess", "references/index.md")
    assert "# Index" in out


def test_path_jail_blocks_escape(tmp_path):
    skill = _build_skill(tmp_path)
    fn = _read_reference(tmp_path)
    out = fn(_make_ctx(skill), "chess", "../SKILL.md")
    assert "escapes" in out.lower()


def test_missing_file_reports_path(tmp_path):
    skill = _build_skill(tmp_path)
    fn = _read_reference(tmp_path)
    out = fn(_make_ctx(skill), "chess", "patterns/nope.md")
    assert "no reference file" in out.lower()


def test_unknown_skill(tmp_path):
    skill = _build_skill(tmp_path)
    fn = _read_reference(tmp_path)
    out = fn(_make_ctx(skill), "nosuch", "index.md")
    assert "not found" in out.lower()
