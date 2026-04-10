"""Thread-based communication system.

A Thread is a bidirectional message channel between the agent and any
participant — a human via UI, an email sender, another agent instance,
a webhook, anything. The agent does not need to know what is on the
other side. It only knows the thread name and can send messages to it.

Every agent has a special "main" thread for the primary user conversation.
All other threads require explicit tool calls to interact with.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, AsyncGenerator, Callable

from pydantic import BaseModel, ConfigDict, Field

from .messages import SourceContext

logger = logging.getLogger(__name__)


# ── Enums ───────────────────────────────────────────────────────────────


class ThreadRole(str, Enum):
    """Who authored a thread message."""

    agent = "agent"
    participant = "participant"


class ThreadStatus(str, Enum):
    """Lifecycle status of a thread."""

    active = "active"
    waiting = "waiting"  # agent is waiting for participant response
    done = "done"  # conversation concluded, pending archive


# ── ThreadMessage ───────────────────────────────────────────────────────


class ThreadMessage(BaseModel):
    """One message in a thread.

    `events` carries the structured activity log for the run that produced this
    message — tool calls, todo updates, skill loads, token usage, etc. Each
    entry is a serialized AgentEvent dict (has a `type` discriminator field).
    For participant messages (inbound) this is empty. For agent replies and
    subagent posts it contains the full event timeline for that turn.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    role: ThreadRole
    content: str
    source_context: SourceContext | None = None
    events: list[dict] = Field(
        default_factory=list,
        description="Serialized AgentEvent objects produced during the run that generated this message.",
    )


# ── ThreadEvent (for registry-level subscriptions) ──────────────────────


class ThreadEvent(BaseModel):
    """Emitted when any thread receives a new message."""

    thread_name: str
    message: ThreadMessage


# ── Thread ──────────────────────────────────────────────────────────────


class Thread(BaseModel):
    """A bidirectional message channel.

    Listeners are runtime-only (not serialized). Use subscribe_inbound()
    and subscribe_outbound() to register callbacks.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    messages: list[ThreadMessage] = Field(default_factory=list)
    status: ThreadStatus = ThreadStatus.active
    archived: bool = False
    participants: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source_context: SourceContext | None = None

    # Runtime-only, not serialized
    _inbound_listeners: list[Callable] = []
    _outbound_listeners: list[Callable] = []
    _registry_callback: Callable | None = None

    def model_post_init(self, _context: Any) -> None:
        # Pydantic shares class-level mutables across instances; ensure per-instance lists
        self._inbound_listeners = []
        self._outbound_listeners = []
        self._registry_callback = None

    def send(
        self,
        content: str,
        source_context: SourceContext | None = None,
        events: list[dict] | None = None,
    ) -> ThreadMessage:
        """Append a participant-authored message and fire inbound listeners."""
        msg = ThreadMessage(
            role=ThreadRole.participant,
            content=content,
            source_context=source_context,
            events=events or [],
        )
        self.messages.append(msg)
        print(
            f"[THREAD] send  thread={self.name!r}  inbound_listeners={len(self._inbound_listeners)}"
            f"  content={content[:120]!r}",
            flush=True,
        )
        logger.info(
            "thread_send name=%s msg_id=%s content=%s",
            self.name,
            msg.id,
            content[:200],
        )
        if self._registry_callback is not None:
            self._registry_callback(self.name, msg)
        for i, listener in enumerate(self._inbound_listeners):
            print(f"[THREAD] send  firing inbound_listener[{i}]  thread={self.name!r}", flush=True)
            listener(msg)
        return msg

    def reply(self, content: str, events: list[dict] | None = None) -> ThreadMessage:
        """Append an agent-authored message and fire outbound listeners."""
        msg = ThreadMessage(
            role=ThreadRole.agent,
            content=content,
            events=events or [],
        )
        self.messages.append(msg)
        print(
            f"[THREAD] reply  thread={self.name!r}  outbound_listeners={len(self._outbound_listeners)}"
            f"  content={content[:120]!r}",
            flush=True,
        )
        logger.info(
            "thread_reply name=%s msg_id=%s content=%s",
            self.name,
            msg.id,
            content[:200],
        )
        if self._registry_callback is not None:
            self._registry_callback(self.name, msg)
        for i, listener in enumerate(self._outbound_listeners):
            print(f"[THREAD] reply  firing outbound_listener[{i}]  thread={self.name!r}", flush=True)
            listener(msg)
        return msg

    def subscribe_inbound(self, handler: Callable) -> None:
        """Register a handler fired when send() is called (participant → agent)."""
        self._inbound_listeners.append(handler)
        print(f"[THREAD] subscribe_inbound  thread={self.name!r}  total_inbound={len(self._inbound_listeners)}", flush=True)

    def subscribe_outbound(self, handler: Callable) -> None:
        """Register a handler fired when reply() is called (agent → participant)."""
        self._outbound_listeners.append(handler)
        print(f"[THREAD] subscribe_outbound  thread={self.name!r}  total_outbound={len(self._outbound_listeners)}", flush=True)

    def summary(self) -> str:
        """One-line summary for the active thread list."""
        last_msg = self.messages[-1] if self.messages else None
        if last_msg:
            age = datetime.now(timezone.utc) - last_msg.timestamp
            secs = int(age.total_seconds())
            if secs < 60:
                age_str = f"{secs}s ago"
            elif secs < 3600:
                age_str = f"{secs // 60}m ago"
            else:
                age_str = f"{secs // 3600}h ago"
            return f"{self.name} [{self.status.value}] — last message {age_str}"
        return f"{self.name} [{self.status.value}] — no messages"


# ── ThreadRegistry ──────────────────────────────────────────────────────


class ThreadRegistry(BaseModel):
    """Manages all threads for an agent."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    threads: dict[str, Thread] = Field(default_factory=dict)

    # Runtime-only subscriber queues for SSE
    _subscribers: list[asyncio.Queue[ThreadEvent]] = []

    def model_post_init(self, _context: Any) -> None:
        self._subscribers = []

    def create(
        self,
        name: str,
        participants: list[str] | None = None,
        source_context: SourceContext | None = None,
    ) -> Thread:
        """Create a new thread. Raises ValueError if name already exists."""
        if name in self.threads:
            raise ValueError(f"Thread '{name}' already exists.")
        thread = Thread(
            name=name,
            participants=participants or [],
            source_context=source_context,
        )
        thread._registry_callback = self._on_thread_message
        self.threads[name] = thread
        logger.info(
            "thread_created name=%s participants=%s",
            name,
            thread.participants,
        )
        return thread

    def get(self, name: str) -> Thread:
        """Look up a thread by name. Raises KeyError if not found."""
        if name not in self.threads:
            raise KeyError(f"Thread '{name}' not found.")
        return self.threads[name]

    def active(self) -> list[Thread]:
        """Return all non-archived threads."""
        return [t for t in self.threads.values() if not t.archived]

    def archive(self, name: str) -> None:
        """Archive a thread. It stays queryable but leaves the active list."""
        thread = self.get(name)
        thread.archived = True
        thread.status = ThreadStatus.done
        logger.info("thread_archived name=%s", name)

    def summary(self) -> str:
        """Compact multi-line summary of active non-main threads for context injection."""
        active_threads = [t for t in self.active() if t.name != "main"]
        if not active_threads:
            return ""
        lines = [t.summary() for t in active_threads]
        return "Active threads:\n" + "\n".join(f"- {line}" for line in lines)

    async def subscribe(self) -> AsyncGenerator[ThreadEvent, None]:
        """Yield ThreadEvent objects as messages arrive in any thread."""
        queue: asyncio.Queue[ThreadEvent] = asyncio.Queue()
        self._subscribers.append(queue)
        logger.info("thread_registry_subscribe_open subscribers=%s", len(self._subscribers))
        try:
            while True:
                event = await queue.get()
                yield event
        finally:
            if queue in self._subscribers:
                self._subscribers.remove(queue)
            logger.info(
                "thread_registry_subscribe_closed subscribers=%s",
                len(self._subscribers),
            )

    def _on_thread_message(self, thread_name: str, message: ThreadMessage) -> None:
        """Callback fired by Thread.send() and Thread.reply()."""
        event = ThreadEvent(thread_name=thread_name, message=message)
        for queue in self._subscribers:
            queue.put_nowait(event)
