"""Structured message model and source context hierarchy.

Every message or step in the agent's conversation is represented as a
Message object. Messages flow into two stores: an append-only message_log
(source of truth) and a mutable context_window (what the model sees).

SourceContext describes where an inbox item came from. Extend by adding
new subclasses for additional channels (Slack, SMS, etc.).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class MessageType(str, Enum):
    """The role or purpose of a message in the conversation."""

    user = "user"
    agent = "agent"
    tool_call = "tool_call"
    tool_result = "tool_result"
    reasoning = "reasoning"
    subagent = "subagent"
    system = "system"


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Message(BaseModel):
    """One message or step in the agent's conversation."""

    id: str = Field(default_factory=_uuid)
    timestamp: datetime = Field(default_factory=_now)
    type: MessageType
    source_context: "SourceContext | None" = None
    content: Any = None
    summary: str | None = None


class SourceContext(BaseModel):
    """Base class describing the origin of an inbox item or message."""

    origin: str
    sender: str | None = None
    interaction_id: str = Field(default_factory=_uuid)


class UIContext(SourceContext):
    """Message originated from a UI interaction."""

    origin: str = "ui"


class EmailContext(SourceContext):
    """Message originated from an email channel."""

    origin: str = "email"
    subject: str
    thread_id: str | None = None
    reply_to: str | None = None


class SubAgentContext(SourceContext):
    """Message originated from a subagent."""

    origin: str = "subagent"
    subagent_id: str
    parent_interaction_id: str


Message.model_rebuild()
