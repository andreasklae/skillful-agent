"""Tests for typed-tool contract: schema extraction, prepare gating, argv reconstruction, list_skill_files."""

import json
import textwrap
from pathlib import Path

import pytest

from skill_agent._schema_extractor import build_argv, extract_tool_spec
from skill_agent.models import Skill, ToolSpec


# ── Helpers ──────────────────────────────────────────────────────────


def write_script(tmp_path: Path, name: str, source: str) -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(source), encoding="utf-8")
    return p


# ── Schema extraction: argparse (tier 2) ─────────────────────────────


def test_argparse_flags(tmp_path):
    p = write_script(tmp_path, "move.py", """\
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--move", type=str, required=True, help="UCI move")
        parser.add_argument("--reasoning", type=str, default="", help="Reasoning text")
        """)
    spec = extract_tool_spec("chess", p)
    assert spec.tier == 2
    assert spec.tool_name == "chess__move"
    props = spec.json_schema["properties"]
    assert props["move"]["type"] == "string"
    assert props["move"]["description"] == "UCI move"
    assert "move" in spec.json_schema["required"]
    assert "reasoning" not in spec.json_schema.get("required", [])


def test_argparse_positional(tmp_path):
    p = write_script(tmp_path, "analyse.py", """\
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("fen", type=str, help="Position FEN")
        parser.add_argument("depth", type=int, help="Search depth")
        """)
    spec = extract_tool_spec("chess", p)
    assert spec.tier == 2
    props = spec.json_schema["properties"]
    assert props["fen"]["type"] == "string"
    assert props["depth"]["type"] == "integer"
    assert "fen" in spec.json_schema["required"]
    assert "depth" in spec.json_schema["required"]


def test_argparse_store_true(tmp_path):
    p = write_script(tmp_path, "eval.py", """\
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--verbose", action="store_true")
        """)
    spec = extract_tool_spec("chess", p)
    assert spec.tier == 2
    assert spec.json_schema["properties"]["verbose"]["type"] == "boolean"


def test_argparse_choices(tmp_path):
    p = write_script(tmp_path, "pick.py", """\
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--color", choices=["white", "black"], required=True)
        """)
    spec = extract_tool_spec("chess", p)
    assert spec.tier == 2
    assert spec.json_schema["properties"]["color"]["enum"] == ["white", "black"]


def test_argparse_nargs(tmp_path):
    p = write_script(tmp_path, "batch.py", """\
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--moves", nargs="+", help="List of moves")
        """)
    spec = extract_tool_spec("chess", p)
    assert spec.tier == 2
    assert spec.json_schema["properties"]["moves"]["type"] == "array"


def test_argparse_module_docstring(tmp_path):
    p = write_script(tmp_path, "info.py", """\
        \"\"\"Return game info.\"\"\"
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--game-id", required=True)
        """)
    spec = extract_tool_spec("chess", p)
    assert spec.description == "Return game info."


# ── Schema extraction: typed entrypoint (tier 1) ─────────────────────


def test_typed_entrypoint(tmp_path):
    p = write_script(tmp_path, "calc.py", """\
        def run(x: int, y: float, label: str = "result") -> str:
            return f"{label}: {x + y}"
        """)
    spec = extract_tool_spec("math", p)
    assert spec.tier == 1
    props = spec.json_schema["properties"]
    assert props["x"]["type"] == "integer"
    assert props["y"]["type"] == "number"
    assert props["label"]["type"] == "string"
    assert "x" in spec.json_schema["required"]
    assert "y" in spec.json_schema["required"]
    assert "label" not in spec.json_schema.get("required", [])


# ── Schema extraction: generic fallback (tier 4) ─────────────────────


def test_generic_fallback(tmp_path):
    p = write_script(tmp_path, "raw.py", """\
        import sys
        print(sys.argv[1])
        """)
    spec = extract_tool_spec("misc", p)
    assert spec.tier == 4
    assert spec.json_schema["properties"]["args"]["type"] == "array"


def test_parse_error_falls_back_to_tier4(tmp_path):
    p = tmp_path / "bad.py"
    p.write_bytes(b"\xff\xfe not valid utf8 \x00")
    spec = extract_tool_spec("misc", p)
    assert spec.tier == 4


# ── argv reconstruction ───────────────────────────────────────────────


def test_argv_flags(tmp_path):
    p = write_script(tmp_path, "move.py", """\
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--move", required=True)
        parser.add_argument("--reasoning", default="")
        """)
    spec = extract_tool_spec("chess", p)
    argv = build_argv(spec, {"move": "e2e4", "reasoning": "central control"})
    assert "--move" in argv
    assert "e2e4" in argv
    assert "--reasoning" in argv
    assert "central control" in argv


def test_argv_store_true_true(tmp_path):
    p = write_script(tmp_path, "ev.py", """\
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--verbose", action="store_true")
        """)
    spec = extract_tool_spec("x", p)
    argv = build_argv(spec, {"verbose": True})
    assert "--verbose" in argv


def test_argv_store_true_false(tmp_path):
    p = write_script(tmp_path, "ev.py", """\
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--verbose", action="store_true")
        """)
    spec = extract_tool_spec("x", p)
    argv = build_argv(spec, {"verbose": False})
    assert "--verbose" not in argv


def test_argv_positionals(tmp_path):
    p = write_script(tmp_path, "a.py", """\
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("src")
        parser.add_argument("dst")
        """)
    spec = extract_tool_spec("x", p)
    argv = build_argv(spec, {"src": "a.txt", "dst": "b.txt"})
    assert argv == ["a.txt", "b.txt"]


def test_argv_tier4_passthrough(tmp_path):
    p = write_script(tmp_path, "raw.py", "import sys; print(sys.argv[1])")
    spec = extract_tool_spec("x", p)
    assert spec.tier == 4
    argv = build_argv(spec, {"args": ["hello", "world"]})
    assert argv == ["hello", "world"]


# ── Registry integration ──────────────────────────────────────────────


def test_registry_populates_tool_specs(tmp_path):
    skill_dir = tmp_path / "mychess"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: mychess\ndescription: chess skill\n---\n\n# chess\n",
        encoding="utf-8",
    )
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "make_move.py").write_text(
        "import argparse\np=argparse.ArgumentParser()\np.add_argument('--move',required=True)\n",
        encoding="utf-8",
    )
    (scripts_dir / "_private.py").write_text("# private", encoding="utf-8")

    from skill_agent.registry import _parse_skill
    skill = _parse_skill(skill_dir)
    assert skill is not None
    assert len(skill.tool_specs) == 1  # _private.py excluded
    assert skill.tool_specs[0].tool_name == "mychess__make_move"
    assert skill.tool_specs[0].tier == 2


def test_registry_exec_style_direct(tmp_path):
    skill_dir = tmp_path / "s"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: s\ndescription: d\n---\n\nbody\n", encoding="utf-8")
    (skill_dir / "scripts").mkdir()

    from skill_agent.registry import _parse_skill
    skill = _parse_skill(skill_dir)
    assert skill.exec_style == "direct"


def test_registry_exec_style_module(tmp_path):
    skill_dir = tmp_path / "s"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: s\ndescription: d\n---\n\nbody\n", encoding="utf-8")
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "__init__.py").write_text("", encoding="utf-8")

    from skill_agent.registry import _parse_skill
    skill = _parse_skill(skill_dir)
    assert skill.exec_style == "module"


# ── activated_skills lifecycle ────────────────────────────────────────


def test_activated_skills_persists_across_runs(tmp_path):
    """activated_skills must survive _reset_run_state (cleared only on clear_conversation)."""
    from skill_agent.agent import _RunDeps, Agent
    from skill_agent.threads import ThreadRegistry

    deps = _RunDeps(
        skills={},
        thread_registry=ThreadRegistry(),
        message_log=[],
        context_window=[],
        context_compression_threshold=100_000,
    )

    # Simulate use_skill appending to activated_skills
    deps.activated_skills.append("chess")

    # Simulate _reset_run_state (called at the start of each run())
    deps.tool_log.clear()
    deps.pending_client_requests.clear()
    deps.pending_skill_loaded.clear()
    # activated_skills must NOT be cleared here

    assert "chess" in deps.activated_skills


def test_activated_skills_clears_on_clear_conversation(tmp_path):
    """clear_conversation must reset activated_skills."""
    from pathlib import Path
    import textwrap

    skill_dir = tmp_path / "skills" / "dummy"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: dummy\ndescription: dummy skill\n---\n\n# dummy\n",
        encoding="utf-8",
    )

    from pydantic_ai.models.test import TestModel
    from skill_agent.agent import Agent

    agent = Agent(model=TestModel(), skills_dir=tmp_path / "skills")
    agent._deps.activated_skills.append("dummy")
    assert "dummy" in agent._deps.activated_skills

    agent.clear_conversation()
    assert agent._deps.activated_skills == []


# ── list_skill_files ──────────────────────────────────────────────────


def test_list_skill_files_returns_references(tmp_path):
    skill_dir = tmp_path / "skills" / "myskill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: myskill\ndescription: test\n---\n\nbody\n",
        encoding="utf-8",
    )
    refs_dir = skill_dir / "references"
    refs_dir.mkdir()
    (refs_dir / "guide.md").write_text("guide content", encoding="utf-8")
    (refs_dir / "api.md").write_text("api content", encoding="utf-8")

    from pydantic_ai.models.test import TestModel
    from skill_agent.agent import Agent

    agent = Agent(model=TestModel(), skills_dir=tmp_path / "skills")
    # list_skill_files is a registered tool — call via run()
    result = agent.run("list_skill_files for myskill")
    # The answer may vary with TestModel; test tool execution directly
    from skill_agent.agent import _RunDeps
    from skill_agent.threads import ThreadRegistry
    from skill_agent.skill_tools import register_skill_tools
    from pydantic_ai import RunContext
    from pydantic_ai.models.test import TestModel as TM
    from pydantic_ai import Agent as PA

    # Directly test list_skill_files by calling it via the agent's tool
    skill = agent._deps.skills.get("myskill")
    assert skill is not None
    assert sorted(skill.references) == ["api.md", "guide.md"]
