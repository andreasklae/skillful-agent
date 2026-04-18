"""Shared test fixtures for skillful-agent server tests.

Provides a minimal fake agent and a TestClient wired to it so tests can
exercise HTTP endpoints without touching any LLM or real skill directories.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from skill_agent.models import AgentConfig
from skill_agent.messages import Message, MessageType
from skill_agent.models import TodoItem
from skill_agent.threads import ThreadRegistry


class FakeAgent:
    """Minimal stand-in for skill_agent.Agent.

    Only implements the surface area exercised by the HTTP endpoints under
    test.  No LLM calls, no skill loading, no async run queue.
    """

    def __init__(self, skills_dir: Path, user_file_roots: list[Path] | None = None) -> None:
        self._skills_dir = skills_dir
        self._config = AgentConfig(
            user_file_roots=user_file_roots or [],
        )
        self._skills: dict = {}
        self.message_log: list[Message] = []
        self.context_window: list[Message] = []
        self.thread_registry = ThreadRegistry()
        self.thread_registry.create(name="main", participants=["user"])

        # _deps stub — only the fields touched by endpoints
        class _DepStub:
            def __init__(self, agent: FakeAgent) -> None:
                self._agent = agent
                self.todo_list: list[TodoItem] = []
                self._next_todo_id: int = 1
                self.user_file_roots: tuple[Path, ...] = tuple(
                    agent._config.user_file_roots
                )
                self.max_user_file_write_bytes: int = 2 * 1024 * 1024

        self._deps = _DepStub(self)

    @property
    def skills_dir(self) -> Path:
        return self._skills_dir

    def clear_conversation(self) -> None:
        self.message_log.clear()
        self.context_window.clear()
        self._deps.todo_list.clear()
        self._deps._next_todo_id = 1
        self.thread_registry = ThreadRegistry()
        self.thread_registry.create(name="main", participants=["user"])

    def set_skills_dir(self, path: Path) -> list[str]:
        self._skills_dir = path
        return []

    def set_user_file_roots(self, roots: list[Path]) -> None:
        self._config = self._config.model_copy(
            update={"user_file_roots": roots}
        )
        self._deps.user_file_roots = tuple(roots)


def _make_skill_dir() -> tempfile.TemporaryDirectory:
    """Create a temporary directory with a minimal SKILL.md."""
    tmp = tempfile.TemporaryDirectory()
    skill_dir = Path(tmp.name) / "demo-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: A demo skill.\n---\n\n# Demo\nHello.\n",
        encoding="utf-8",
    )
    return tmp


@pytest.fixture()
def skills_tmp():
    """Yield a TemporaryDirectory containing one valid skill."""
    tmp = _make_skill_dir()
    yield Path(tmp.name)
    tmp.cleanup()


@pytest.fixture()
def workspace_tmp():
    """Yield a writable TemporaryDirectory for user file roots."""
    tmp = tempfile.TemporaryDirectory()
    yield Path(tmp.name)
    tmp.cleanup()


@pytest.fixture()
def fake_agent(skills_tmp: Path) -> FakeAgent:
    return FakeAgent(skills_dir=skills_tmp)


@pytest.fixture()
def client(fake_agent: FakeAgent) -> TestClient:
    """Return a TestClient wired to a fresh FakeAgent.

    create_app calls init_agent internally when agent is provided, so
    the dependency injector is already wired before TestClient is created.
    """
    from server.app import create_app

    app = create_app(agent=fake_agent)  # type: ignore[arg-type]
    return TestClient(app)
