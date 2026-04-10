"""FastAPI dependency providers for the Agent singleton."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from skill_agent import Agent

_agent: Agent | None = None


def init_agent(agent: Agent) -> None:
    """Store the Agent instance for dependency injection."""
    global _agent
    _agent = agent


def get_agent() -> Agent:
    """FastAPI dependency that provides the Agent singleton."""
    if _agent is None:
        raise RuntimeError("Agent not initialized. Call init_agent() first.")
    return _agent
