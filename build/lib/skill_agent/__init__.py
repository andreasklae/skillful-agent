"""Skill-based agent SDK with progressive skill disclosure.

Public API:
    Agent             — Create with model + skills_dir, call run() or run_stream()
    AgentEvent        — Discriminated union of all event types
    TodoUpdateEvent   — Todo list state after each manage_todos call
    ToolCallEvent     — Tool invocation (name, args, optional activity)
    ToolResultEvent   — Tool completion
    TextDeltaEvent    — Answer token from the model
    RunCompleteEvent  — Final event (token usage); conversation memory lives on Agent
    Skill             — Skill metadata model (name, description, body, resources)
    AgentConfig       — Optional configuration (max_tokens, max_turns, etc.)
    AgentResult       — Typed return value from agent.run()

Usage:
    from pathlib import Path
    from skill_agent import Agent

    agent = Agent(model=model, skills_dir=Path("skills"))

    # Blocking
    result = agent.run("Who invented the telephone?")
    print(result.answer)

    # Streaming (async)
    async for event in agent.run_stream("Who invented the telephone?"):
        if isinstance(event, TextDeltaEvent):
            print(event.content, end="", flush=True)
"""

from .agent import Agent
from .user_prompt_files import build_user_message
from .models import (
    AgentConfig,
    AgentEvent,
    AgentResult,
    ClientFunction,
    ClientFunctionParam,
    ClientFunctionRequest,
    ClientFunctionRequestEvent,
    RunCompleteEvent,
    Skill,
    TextDeltaEvent,
    TodoItem,
    TodoStatus,
    TodoUpdateEvent,
    ToolCallEvent,
    ToolResultEvent,
)

__all__ = [
    "Agent",
    "build_user_message",
    "AgentConfig",
    "AgentEvent",
    "AgentResult",
    "ClientFunction",
    "ClientFunctionParam",
    "ClientFunctionRequest",
    "ClientFunctionRequestEvent",
    "RunCompleteEvent",
    "Skill",
    "TextDeltaEvent",
    "TodoItem",
    "TodoStatus",
    "TodoUpdateEvent",
    "ToolCallEvent",
    "ToolResultEvent",
]
