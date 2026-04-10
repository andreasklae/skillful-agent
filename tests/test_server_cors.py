"""Regression tests for the server HTTP API."""

from __future__ import annotations

import io
import tempfile
import types
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server import create_app


# ── Fakes ───────────────────────────────────────────────────────────────


class _FakeThreadMessage:
    def __init__(self, *, role: str, content: str, sender: str | None = None):
        self.id = "msg-1"
        self.timestamp = datetime(2026, 4, 9, tzinfo=timezone.utc)
        self.role = types.SimpleNamespace(value=role)
        self.content = content
        self.source_context = types.SimpleNamespace(origin="ui", sender=sender)


class _FakeThread:
    def __init__(self, name: str, status: str = "active"):
        self.name = name
        self.status = types.SimpleNamespace(value=status)
        self.archived = False
        self.participants = ["user"]
        self.created_at = datetime(2026, 4, 9, tzinfo=timezone.utc)
        self.messages: list[_FakeThreadMessage] = []
        self._inbound_listeners: list = []
        self._outbound_listeners: list = []

    def send(self, content, source_context=None):
        msg = _FakeThreadMessage(role="participant", content=content, sender=getattr(source_context, "sender", None))
        self.messages.append(msg)
        return msg

    def reply(self, content):
        msg = _FakeThreadMessage(role="agent", content=content)
        self.messages.append(msg)
        return msg

    def subscribe_inbound(self, handler):
        self._inbound_listeners.append(handler)

    def subscribe_outbound(self, handler):
        self._outbound_listeners.append(handler)


class _FakeThreadRegistry:
    def __init__(self):
        self.threads: dict[str, _FakeThread] = {
            "main": _FakeThread("main"),
        }

    def create(self, name, participants=None, source_context=None):
        t = _FakeThread(name)
        t.participants = participants or []
        self.threads[name] = t
        return t

    def get(self, name):
        if name not in self.threads:
            raise KeyError(f"Thread '{name}' not found.")
        return self.threads[name]

    def active(self):
        return [t for t in self.threads.values() if not t.archived]

    def archive(self, name):
        self.threads[name].archived = True

    def summary(self):
        return ""

    async def subscribe(self):
        if False:
            yield None


class _FakeAgent:
    def __init__(self) -> None:
        self._skills: dict[str, types.SimpleNamespace] = {
            "existing": types.SimpleNamespace(
                name="existing",
                description="existing skill",
                path=Path("/tmp/existing/SKILL.md"),
                scripts=["run.py"],
                references=["guide.md"],
                assets=["icon.svg"],
            )
        }
        self.message_log: list = []
        self.context_window: list = []
        self.thread_registry = _FakeThreadRegistry()
        self.skills_dir = Path(tempfile.mkdtemp(prefix="skills-dir-"))

    async def enqueue_run(
        self, prompt: str, *, files=None, source="api", metadata=None, coalesce_key=None
    ) -> str:
        return "run-1"

    async def subscribe_run(self, run_id):
        yield {
            "type": "run_queued",
            "run_id": run_id,
            "source": "api",
            "prompt_preview": "hello",
            "metadata": {"origin": "http"},
        }
        yield {
            "type": "agent_event",
            "run_id": run_id,
            "source": "api",
            "prompt_preview": "hello",
            "metadata": {"origin": "http"},
            "event": {"type": "text_delta", "content": "hello"},
        }
        yield {
            "type": "agent_event",
            "run_id": run_id,
            "source": "api",
            "prompt_preview": "hello",
            "metadata": {"origin": "http"},
            "event": {
                "type": "run_complete",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        }

    async def subscribe_all_runs(self):
        yield {
            "type": "run_started",
            "run_id": "run-bg-1",
            "source": "thread",
            "prompt_preview": "new message in 'researcher'",
            "metadata": {"thread_name": "researcher"},
        }

    def receive_thread_message(self, thread_name, content, source_context=None, *, allow_create=False):
        try:
            thread = self.thread_registry.get(thread_name)
        except KeyError:
            if not allow_create:
                raise
            thread = self.thread_registry.create(thread_name)
        return thread.send(content, source_context)

    def add_skill_dir(self, skill_dir):
        name = Path(skill_dir).name
        self._skills[name] = types.SimpleNamespace(
            name=name,
            description="uploaded skill",
            path=Path(skill_dir) / "SKILL.md",
            scripts=[],
            references=[],
            assets=[],
        )
        return name


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture()
def fake_agent() -> _FakeAgent:
    return _FakeAgent()


@pytest.fixture()
def client(fake_agent: _FakeAgent) -> TestClient:
    app = create_app(agent=fake_agent)
    return TestClient(app)


# ── Tests ───────────────────────────────────────────────────────────────


def test_health_preflight_returns_cors_headers(client: TestClient):
    response = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"


def test_health_returns_integer_fields(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "status": "ok",
        "skills": 1,
        "message_log_size": 0,
        "context_window_size": 0,
    }
    assert isinstance(payload["skills"], int)


def test_skill_archive_upload_registers_live_skill(client: TestClient, fake_agent: _FakeAgent):
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w") as archive:
        archive.writestr(
            "uploaded-skill/SKILL.md",
            "---\nname: uploaded-skill\ndescription: test skill\n---\n\n# Uploaded skill\n",
        )

    response = client.post(
        "/skills/upload",
        files={"file": ("uploaded-skill.zip", archive_buffer.getvalue(), "application/zip")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["skill_name"] == "uploaded-skill"
    assert payload["registered_skills"] == ["existing", "uploaded-skill"]


def test_run_endpoint_streams_queued_run_events(client: TestClient):
    with client.stream("POST", "/run", json={"prompt": "hello", "files": []}) as response:
        body = b"".join(response.iter_raw()).decode()

    assert response.status_code == 200
    assert "event: run_queued" in body
    assert "event: text_delta" in body
    assert '"content": "hello"' in body


def test_runs_subscribe_streams_global_run_events(client: TestClient):
    with client.stream("GET", "/runs/subscribe") as response:
        body = b"".join(response.iter_raw()).decode()

    assert response.status_code == 200
    assert "event: run_started" in body
    assert '"run_id": "run-bg-1"' in body


def test_list_threads(client: TestClient):
    response = client.get("/threads")
    assert response.status_code == 200
    payload = response.json()
    names = [t["name"] for t in payload]
    assert "main" in names


def test_get_thread(client: TestClient):
    response = client.get("/threads/main")
    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "main"
    assert payload["status"] == "active"


def test_get_thread_not_found(client: TestClient):
    response = client.get("/threads/nonexistent")
    assert response.status_code == 404


def test_post_thread_message(client: TestClient, fake_agent: _FakeAgent):
    response = client.post(
        "/threads/main/messages",
        json={"content": "hello", "sender": "frontend"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["content"] == "hello"
    assert payload["role"] == "participant"
    assert payload["thread_name"] == "main"


def test_post_thread_message_creates_new_thread(client: TestClient, fake_agent: _FakeAgent):
    response = client.post(
        "/threads/new-thread/messages",
        json={"content": "hi", "sender": "webhook"},
    )
    assert response.status_code == 200
    assert "new-thread" in fake_agent.thread_registry.threads


def test_list_skills_returns_live_registry(client: TestClient):
    response = client.get("/skills")

    assert response.status_code == 200
    payload = response.json()
    assert payload == [
        {
            "name": "existing",
            "description": "existing skill",
            "path": "/tmp/existing/SKILL.md",
            "scripts": ["run.py"],
            "references": ["guide.md"],
            "assets": ["icon.svg"],
        }
    ]
