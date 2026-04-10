from pathlib import Path

from skill_agent.agent import Agent, _RunDeps
from skill_agent.threads import ThreadRegistry


def _write_skill(root: Path, name: str, description: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    return skill_dir


def test_reload_skills_updates_registry_and_runner(monkeypatch, tmp_path):
    user_skills_dir = tmp_path / "skills"
    native_skills_dir = tmp_path / "native-skills"
    _write_skill(user_skills_dir, "beta", "user skill")
    _write_skill(native_skills_dir, "alpha", "native skill")

    created_runners: list[tuple[object, str, tuple[Path, ...]]] = []

    def fake_create_runner(model, system_prompt, user_file_roots):
        created_runners.append((model, system_prompt, user_file_roots))
        return "runner"

    monkeypatch.setattr("skill_agent.agent._create_runner", fake_create_runner)

    agent = Agent.__new__(Agent)
    agent._model = object()
    agent._skills_dir = user_skills_dir
    agent._native_skills_dir = native_skills_dir
    agent._config = type(
        "Cfg",
        (),
        {"system_prompt_extra": None, "user_file_roots": (), "max_tokens": None, "max_turns": None},
    )()
    agent._deps = _RunDeps(
        skills={},
        thread_registry=ThreadRegistry(),
        message_log=[],
        context_window=[],
        context_compression_threshold=100_000,
    )

    registered = agent._reload_skills(rebuild_runner=True)

    assert registered == ["alpha", "beta"]
    assert sorted(agent._skills) == ["alpha", "beta"]
    assert sorted(agent._deps.skills) == ["alpha", "beta"]
    assert created_runners and created_runners[0][0] is agent._model
    assert "alpha" in created_runners[0][1]
    assert "beta" in created_runners[0][1]
    assert agent._runner == "runner"


def test_add_skill_dir_rejects_paths_outside_skills_root(tmp_path):
    inside_root = tmp_path / "skills"
    outside_root = tmp_path / "outside"
    _write_skill(outside_root, "rogue", "outside")

    agent = Agent.__new__(Agent)
    agent._skills_dir = inside_root.resolve()

    try:
        agent.add_skill_dir(outside_root / "rogue")
    except ValueError as exc:
        assert "must stay inside" in str(exc)
    else:
        raise AssertionError("Expected add_skill_dir to reject paths outside the skills root")
