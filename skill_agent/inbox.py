"""Inbox and thread system for inter-agent and external communication.

The Inbox is the single entry point for all incoming messages — emails,
subagent updates, forwarded tasks, and any future channels. Both the
main agent and all subagents have their own inbox.

The public interface (Agent.inbox) is the same API the agent's tools call.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import AsyncGenerator

import asyncio

from pydantic import BaseModel, Field

from .messages import SourceContext


class ThreadStatus(str, Enum):
    """Lifecycle status of a thread."""

    in_progress = "in_progress"
    waiting_for_response = "waiting_for_response"
    done = "done"


class InboxItem(BaseModel):
    """One item in an agent's inbox."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    thread_id: str
    subject: str
    content: str
    source_context: SourceContext
    notify: bool
    read: bool = False
    dismissed: bool = False


class Thread:
    """Scoped view of all InboxItem objects sharing a thread_id.

    Not stored separately — constructed on demand by Inbox.get_thread().
    """

    def __init__(self, thread_id: str, inbox: "Inbox") -> None:
        self.thread_id = thread_id
        self._inbox = inbox

    @property
    def items(self) -> list[InboxItem]:
        return sorted(
            [i for i in self._inbox.items if i.thread_id == self.thread_id],
            key=lambda i: i.timestamp,
        )

    @property
    def status(self) -> ThreadStatus:
        return self._inbox._thread_statuses.get(
            self.thread_id, ThreadStatus.in_progress
        )

    @status.setter
    def status(self, value: ThreadStatus) -> None:
        self._inbox._thread_statuses[self.thread_id] = value

    @property
    def subject(self) -> str:
        items = self.items
        return items[-1].subject if items else ""

    def write(
        self,
        content: str,
        source_context: SourceContext,
        notify: bool = False,
        subject: str | None = None,
        status: ThreadStatus | None = None,
    ) -> InboxItem:
        return self._inbox.write_to_thread(
            thread_id=self.thread_id,
            content=content,
            source_context=source_context,
            notify=notify,
            subject=subject,
            status=status,
        )


class Inbox:
    """In-memory inbox for an agent. First-class public API object."""

    def __init__(self) -> None:
        self.items: list[InboxItem] = []
        self._thread_statuses: dict[str, ThreadStatus] = {}
        self._subscribers: list[asyncio.Queue[InboxItem]] = []

    def create_item(
        self,
        content: str,
        subject: str,
        source_context: SourceContext,
        notify: bool,
        thread_id: str | None = None,
        status: ThreadStatus | None = None,
    ) -> InboxItem:
        """Create a new inbox item. Starts a new thread if thread_id is not provided."""
        tid = thread_id or str(uuid.uuid4())
        item = InboxItem(
            thread_id=tid,
            subject=subject,
            content=content,
            source_context=source_context,
            notify=notify,
        )
        self.items.append(item)

        if tid not in self._thread_statuses:
            self._thread_statuses[tid] = status or ThreadStatus.in_progress
        elif status is not None:
            self._thread_statuses[tid] = status

        for queue in self._subscribers:
            queue.put_nowait(item)

        return item

    def write_to_thread(
        self,
        thread_id: str,
        content: str,
        source_context: SourceContext,
        notify: bool,
        subject: str | None = None,
        status: ThreadStatus | None = None,
    ) -> InboxItem:
        """Append to an existing thread. Raises KeyError if thread doesn't exist."""
        if thread_id not in self._thread_statuses:
            raise KeyError(f"Thread '{thread_id}' does not exist.")

        if subject is None:
            existing = [i for i in self.items if i.thread_id == thread_id]
            subject = existing[-1].subject if existing else "update"

        item = InboxItem(
            thread_id=thread_id,
            subject=subject,
            content=content,
            source_context=source_context,
            notify=notify,
        )
        self.items.append(item)

        if status is not None:
            self._thread_statuses[thread_id] = status

        for queue in self._subscribers:
            queue.put_nowait(item)

        return item

    def read_inbox(self) -> list[InboxItem]:
        """Return all unread, non-dismissed items and mark them as read."""
        unread = [i for i in self.items if not i.read and not i.dismissed]
        for item in unread:
            item.read = True
        return unread

    def read_thread(self, thread_id: str) -> list[InboxItem]:
        """Return all items in a thread, sorted by timestamp."""
        return sorted(
            [i for i in self.items if i.thread_id == thread_id],
            key=lambda i: i.timestamp,
        )

    def get_thread(self, thread_id: str) -> Thread:
        """Return a Thread wrapper scoped to the given thread_id."""
        return Thread(thread_id=thread_id, inbox=self)

    def dismiss_item(self, item_id: str) -> None:
        """Mark an item as dismissed without processing."""
        for item in self.items:
            if item.id == item_id:
                item.dismissed = True
                item.read = True
                return
        raise KeyError(f"Item '{item_id}' not found.")

    def delete_thread(self, thread_id: str) -> list[InboxItem]:
        """Delete all items in a thread. Returns the deleted items for archival."""
        deleted = [i for i in self.items if i.thread_id == thread_id]
        self.items = [i for i in self.items if i.thread_id != thread_id]
        self._thread_statuses[thread_id] = ThreadStatus.done
        return deleted

    def pending_notifications(self) -> bool:
        """Return True if any unread, non-dismissed items have notify=True."""
        return any(
            i.notify and not i.read and not i.dismissed
            for i in self.items
        )

    async def subscribe(self) -> AsyncGenerator[InboxItem, None]:
        """Yield new items as they arrive. For UI consumption."""
        queue: asyncio.Queue[InboxItem] = asyncio.Queue()
        self._subscribers.append(queue)
        try:
            while True:
                item = await queue.get()
                yield item
        finally:
            self._subscribers.remove(queue)
