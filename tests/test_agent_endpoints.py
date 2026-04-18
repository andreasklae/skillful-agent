"""Tests for the new /agent/* management endpoints.

Covers:
  - POST /agent/reset
  - POST /agent/configure  (happy path + invalid path rejection)
  - GET  /agent/snapshot
  - POST /agent/load
  - snapshot -> load round-trip
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from skill_agent.messages import Message, MessageType
from skill_agent.models import TodoItem


# ── /agent/reset ───────────────────────────────────────────────────────


def test_reset_returns_ok(client: TestClient) -> None:
    resp = client.post("/agent/reset")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_reset_clears_message_log(client: TestClient, fake_agent) -> None:
    fake_agent.message_log.append(
        Message(type=MessageType.user, content="hello")
    )
    assert len(fake_agent.message_log) == 1

    resp = client.post("/agent/reset")
    assert resp.status_code == 200
    assert len(fake_agent.message_log) == 0


def test_reset_clears_context_window(client: TestClient, fake_agent) -> None:
    fake_agent.context_window.append(
        Message(type=MessageType.agent, content="world")
    )
    resp = client.post("/agent/reset")
    assert resp.status_code == 200
    assert len(fake_agent.context_window) == 0


def test_reset_clears_todos(client: TestClient, fake_agent) -> None:
    fake_agent._deps.todo_list.append(TodoItem(id=1, content="do something"))
    resp = client.post("/agent/reset")
    assert resp.status_code == 200
    assert len(fake_agent._deps.todo_list) == 0


def test_reset_recreates_main_thread(client: TestClient, fake_agent) -> None:
    # Create an extra thread, then reset; only main should remain.
    fake_agent.thread_registry.create(name="side-thread")
    assert "side-thread" in fake_agent.thread_registry.threads

    client.post("/agent/reset")
    assert "main" in fake_agent.thread_registry.threads
    assert "side-thread" not in fake_agent.thread_registry.threads


# ── /agent/configure ──────────────────────────────────────────────────


def test_configure_returns_current_state_when_no_changes(
    client: TestClient, fake_agent, skills_tmp: Path
) -> None:
    resp = client.post("/agent/configure", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert "skills_dir" in data
    assert "user_file_roots" in data
    assert "registered_skills" in data


def test_configure_updates_skills_dir(
    client: TestClient, fake_agent, skills_tmp: Path
) -> None:
    resp = client.post(
        "/agent/configure",
        json={"skills_dir": str(skills_tmp)},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert Path(data["skills_dir"]).resolve() == skills_tmp.resolve()


def test_configure_rejects_nonexistent_skills_dir(
    client: TestClient,
) -> None:
    resp = client.post(
        "/agent/configure",
        json={"skills_dir": "/nonexistent/path/that/does/not/exist"},
    )
    assert resp.status_code == 400


def test_configure_updates_user_file_roots(
    client: TestClient, fake_agent, workspace_tmp: Path
) -> None:
    resp = client.post(
        "/agent/configure",
        json={"user_file_roots": [str(workspace_tmp)]},
    )
    assert resp.status_code == 200
    data = resp.json()
    resolved_returned = {Path(r).resolve() for r in data["user_file_roots"]}
    assert workspace_tmp.resolve() in resolved_returned


def test_configure_rejects_nonexistent_user_file_root(
    client: TestClient,
) -> None:
    resp = client.post(
        "/agent/configure",
        json={"user_file_roots": ["/nonexistent/workspace"]},
    )
    assert resp.status_code == 400


# ── /agent/snapshot ────────────────────────────────────────────────────


def test_snapshot_returns_expected_keys(client: TestClient) -> None:
    resp = client.get("/agent/snapshot")
    assert resp.status_code == 200
    data = resp.json()
    assert set(data.keys()) >= {"message_log", "context_window", "todos", "thread_registry"}


def test_snapshot_reflects_message_log(client: TestClient, fake_agent) -> None:
    fake_agent.message_log.append(
        Message(type=MessageType.user, content="snapshot test")
    )
    resp = client.get("/agent/snapshot")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["message_log"]) == 1
    assert data["message_log"][0]["content"] == "snapshot test"


def test_snapshot_reflects_todos(client: TestClient, fake_agent) -> None:
    fake_agent._deps.todo_list.append(TodoItem(id=1, content="todo item"))
    resp = client.get("/agent/snapshot")
    data = resp.json()
    assert len(data["todos"]) == 1
    assert data["todos"][0]["content"] == "todo item"


def test_snapshot_includes_main_thread(client: TestClient) -> None:
    resp = client.get("/agent/snapshot")
    data = resp.json()
    assert "main" in data["thread_registry"]


# ── /agent/load ────────────────────────────────────────────────────────


def test_load_empty_payload_succeeds(client: TestClient) -> None:
    resp = client.post("/agent/load", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["restored"]["message_log_size"] == 0
    assert data["restored"]["context_window_size"] == 0


def test_load_restores_message_log(client: TestClient, fake_agent) -> None:
    msg = Message(type=MessageType.user, content="loaded message")
    payload = {
        "message_log": [msg.model_dump(mode="json")],
        "context_window": [],
        "todos": [],
    }
    resp = client.post("/agent/load", json=payload)
    assert resp.status_code == 200
    assert resp.json()["restored"]["message_log_size"] == 1
    assert len(fake_agent.message_log) == 1
    assert fake_agent.message_log[0].content == "loaded message"


def test_load_restores_todos(client: TestClient, fake_agent) -> None:
    todo = TodoItem(id=5, content="restore me")
    payload = {
        "message_log": [],
        "context_window": [],
        "todos": [todo.model_dump(mode="json")],
    }
    resp = client.post("/agent/load", json=payload)
    assert resp.status_code == 200
    assert len(fake_agent._deps.todo_list) == 1
    assert fake_agent._deps.todo_list[0].content == "restore me"
    # _next_todo_id should be max_id + 1
    assert fake_agent._deps._next_todo_id == 6


# ── snapshot → load round-trip ─────────────────────────────────────────


def test_snapshot_load_round_trip(client: TestClient, fake_agent) -> None:
    """Send messages + todos, capture snapshot, reset, reload, verify sizes match."""
    # Populate state
    fake_agent.message_log.append(Message(type=MessageType.user, content="turn 1"))
    fake_agent.message_log.append(Message(type=MessageType.agent, content="reply 1"))
    fake_agent.context_window.append(Message(type=MessageType.user, content="turn 1"))
    fake_agent.context_window.append(Message(type=MessageType.agent, content="reply 1"))
    fake_agent._deps.todo_list.append(TodoItem(id=1, content="task A"))
    fake_agent._deps.todo_list.append(TodoItem(id=2, content="task B"))

    # Capture snapshot
    snap_resp = client.get("/agent/snapshot")
    assert snap_resp.status_code == 200
    snapshot = snap_resp.json()

    # Reset
    reset_resp = client.post("/agent/reset")
    assert reset_resp.status_code == 200
    assert len(fake_agent.message_log) == 0
    assert len(fake_agent._deps.todo_list) == 0

    # Reload
    load_resp = client.post("/agent/load", json=snapshot)
    assert load_resp.status_code == 200
    restored = load_resp.json()["restored"]

    assert restored["message_log_size"] == 2
    assert restored["context_window_size"] == 2
    assert len(fake_agent._deps.todo_list) == 2
    assert fake_agent._deps.todo_list[0].content == "task A"
    assert fake_agent._deps.todo_list[1].content == "task B"
