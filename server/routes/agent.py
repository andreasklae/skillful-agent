"""Agent management endpoints: reset, configure, snapshot, load."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from server.dependencies import get_agent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"])


# ── Request / response models ──────────────────────────────────────────


class ResetResponse(BaseModel):
    status: str


class ConfigureRequest(BaseModel):
    skills_dir: str | None = None
    user_file_roots: list[str] | None = None


class ConfigureResponse(BaseModel):
    skills_dir: str
    user_file_roots: list[str]
    registered_skills: list[str]


class SnapshotResponse(BaseModel):
    message_log: list[dict[str, Any]]
    context_window: list[dict[str, Any]]
    todos: list[dict[str, Any]]
    thread_registry: dict[str, Any]


class LoadRequest(BaseModel):
    message_log: list[dict[str, Any]] = []
    context_window: list[dict[str, Any]] = []
    todos: list[dict[str, Any]] = []
    thread_registry: dict[str, Any] | None = None


class LoadResponse(BaseModel):
    status: str
    restored: dict[str, int]


# ── Endpoints ──────────────────────────────────────────────────────────


@router.post("/reset", response_model=ResetResponse)
async def reset_agent(agent=Depends(get_agent)) -> ResetResponse:
    """Clear message_log, context_window, todo list, and thread registry.

    Drops all threads except a freshly-created 'main' thread.
    """
    agent.clear_conversation()
    logger.info("HTTP /agent/reset completed")
    return ResetResponse(status="ok")


@router.post("/configure", response_model=ConfigureResponse)
async def configure_agent(
    req: ConfigureRequest,
    agent=Depends(get_agent),
) -> ConfigureResponse:
    """Dynamically update skills_dir and/or user_file_roots without restarting.

    - If skills_dir changes: fully reloads the skill registry from the new path
      and rebuilds the runner.
    - If user_file_roots changes: updates the allowed roots for read_user_file
      and write_user_file and rebuilds the runner so the new roots take effect.
    """
    if req.skills_dir is not None:
        new_dir = Path(req.skills_dir).expanduser().resolve()
        if not new_dir.is_dir():
            raise HTTPException(
                status_code=400,
                detail=f"skills_dir does not exist or is not a directory: {new_dir}",
            )
        try:
            agent.set_skills_dir(new_dir)
            logger.info("HTTP /agent/configure skills_dir updated to %s", new_dir)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    if req.user_file_roots is not None:
        resolved_roots: list[Path] = []
        for raw in req.user_file_roots:
            p = Path(raw).expanduser().resolve()
            if not p.exists():
                raise HTTPException(
                    status_code=400,
                    detail=f"user_file_roots entry does not exist: {p}",
                )
            resolved_roots.append(p)
        agent.set_user_file_roots(resolved_roots)
        logger.info(
            "HTTP /agent/configure user_file_roots updated to %s",
            [str(r) for r in resolved_roots],
        )

    registered_skills = sorted(agent._skills.keys())
    return ConfigureResponse(
        skills_dir=str(agent.skills_dir),
        user_file_roots=[str(r) for r in agent._config.user_file_roots],
        registered_skills=registered_skills,
    )


@router.get("/snapshot", response_model=SnapshotResponse)
async def snapshot_agent(agent=Depends(get_agent)) -> SnapshotResponse:
    """Return current message_log, context_window, todos, and thread registry as JSON.

    The output can be passed back to POST /agent/load to restore this state.
    Messages are serialized via model_dump(mode='json') so they round-trip through load.
    """
    from skill_agent.messages import Message
    from skill_agent.models import TodoItem

    message_log_data = [m.model_dump(mode="json") for m in agent.message_log]
    context_window_data = [m.model_dump(mode="json") for m in agent.context_window]
    todos_data = [t.model_dump(mode="json") for t in agent._deps.todo_list]

    # Serialize thread registry: capture thread names, statuses, and message histories.
    # pydantic_ai internal conversation messages (_conversation_messages) are NOT
    # included here — they contain opaque model-specific objects that cannot be
    # trivially serialised. See load endpoint for the documented gap.
    thread_registry_data: dict[str, Any] = {}
    for name, thread in agent.thread_registry.threads.items():
        thread_registry_data[name] = {
            "name": thread.name,
            "status": thread.status.value,
            "archived": thread.archived,
            "participants": thread.participants,
            "created_at": thread.created_at.isoformat(),
            "messages": [
                {
                    "id": m.id,
                    "timestamp": m.timestamp.isoformat(),
                    "role": m.role.value,
                    "content": m.content,
                    "events": m.events,
                }
                for m in thread.messages
            ],
        }

    logger.info(
        "HTTP /agent/snapshot message_log=%s context_window=%s todos=%s threads=%s",
        len(message_log_data),
        len(context_window_data),
        len(todos_data),
        len(thread_registry_data),
    )
    return SnapshotResponse(
        message_log=message_log_data,
        context_window=context_window_data,
        todos=todos_data,
        thread_registry=thread_registry_data,
    )


@router.post("/load", response_model=LoadResponse)
async def load_agent(
    req: LoadRequest,
    agent=Depends(get_agent),
) -> LoadResponse:
    """Restore agent state from a snapshot produced by GET /agent/snapshot.

    Restores:
      - message_log (Message objects re-hydrated from dicts)
      - context_window (same)
      - todo list (TodoItem objects)
      - thread registry thread messages (participant/agent message history)

    Known limitation: the pydantic-ai internal conversation history
    (_conversation_messages) that the LLM sees as prior turns is NOT
    restored, because those are opaque pydantic-ai ModelRequest/ModelResponse
    objects with no stable public serialisation format. As a result, after
    load the agent's internal LLM context starts fresh; message_log and
    context_window are restored for the UI layer and for tool access, but
    the model will not have verbatim memory of prior turns. A summary prompt
    injected by the caller before the first new turn is the recommended
    workaround.
    """
    from skill_agent.messages import Message, MessageType
    from skill_agent.models import TodoItem, TodoStatus
    from skill_agent.threads import Thread, ThreadMessage, ThreadRole, ThreadStatus

    # Restore message_log
    restored_log: list[Message] = []
    for entry in req.message_log:
        try:
            restored_log.append(Message.model_validate(entry))
        except Exception:
            # Skip entries that can't be rehydrated rather than aborting the whole load.
            logger.warning("Skipping unhydratable message_log entry: %s", entry)

    # Restore context_window
    restored_ctx: list[Message] = []
    for entry in req.context_window:
        try:
            restored_ctx.append(Message.model_validate(entry))
        except Exception:
            logger.warning("Skipping unhydratable context_window entry: %s", entry)

    # Restore todos
    restored_todos: list[TodoItem] = []
    max_id = 0
    for entry in req.todos:
        try:
            item = TodoItem.model_validate(entry)
            restored_todos.append(item)
            if item.id > max_id:
                max_id = item.id
        except Exception:
            logger.warning("Skipping unhydratable todo entry: %s", entry)

    # Apply to agent
    agent.message_log.clear()
    agent.message_log.extend(restored_log)
    agent.context_window.clear()
    agent.context_window.extend(restored_ctx)
    agent._deps.todo_list.clear()
    agent._deps.todo_list.extend(restored_todos)
    agent._deps._next_todo_id = max_id + 1

    # Restore thread registry messages (best-effort; skips threads that fail to parse)
    if req.thread_registry:
        for thread_name, thread_data in req.thread_registry.items():
            if thread_name == "main":
                # Main thread is always present; just restore its messages
                try:
                    main_thread = agent.thread_registry.get("main")
                    main_thread.messages.clear()
                    for msg_data in thread_data.get("messages", []):
                        try:
                            main_thread.messages.append(ThreadMessage.model_validate(msg_data))
                        except Exception:
                            logger.warning("Skipping unhydratable main thread message: %s", msg_data)
                except KeyError:
                    pass
            else:
                # Non-main threads: create if missing, restore messages
                try:
                    participants = thread_data.get("participants", ["user"])
                    try:
                        thread = agent.thread_registry.get(thread_name)
                    except KeyError:
                        thread = agent.thread_registry.create(
                            name=thread_name,
                            participants=participants,
                        )
                    thread.messages.clear()
                    for msg_data in thread_data.get("messages", []):
                        try:
                            thread.messages.append(ThreadMessage.model_validate(msg_data))
                        except Exception:
                            logger.warning(
                                "Skipping unhydratable thread message in %s: %s",
                                thread_name,
                                msg_data,
                            )
                except Exception as exc:
                    logger.warning("Could not restore thread '%s': %s", thread_name, exc)

    logger.info(
        "HTTP /agent/load restored message_log=%s context_window=%s todos=%s",
        len(restored_log),
        len(restored_ctx),
        len(restored_todos),
    )
    return LoadResponse(
        status="ok",
        restored={
            "message_log_size": len(restored_log),
            "context_window_size": len(restored_ctx),
        },
    )
