"""Skill-based agent SDK with progressive skill disclosure.

Public API:
    Agent             — Create with model + skills_dir, call solve() or solve_stream()
    Skill             — Skill metadata model (name, description, body, resources)
    AgentConfig       — Optional configuration (max_tokens, max_turns, etc.)
    AgentResult       — Typed return value from agent.solve()

Usage:
    from pathlib import Path
    from skill_agent import Agent

    agent  = Agent(model=model, skills_dir=Path("skills"))
    result = agent.solve("Who invented the telephone?")
    print(result.answer)
"""

from .agent import Agent
from .models import AgentConfig, AgentResult, Skill, TodoItem, TodoStatus

__all__ = [
    "Agent",
    "Skill",
    "AgentConfig",
    "AgentResult",
    "TodoItem",
    "TodoStatus",
]
