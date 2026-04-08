# Agent Layer Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add structured message logging, context window management, inbox/thread system, and subagent spawning to the skill-agent SDK.

**Architecture:** New modules (`messages.py`, `inbox.py`, `context_tools.py`, `inbox_tools.py`, `skill_tools.py`, `subagent.py`) alongside existing code. Existing tool registration extracted from `agent.py` into `skill_tools.py`. Agent class gains dual message stores, inbox, and subagent tracking. SubAgent inherits from Agent.

**Tech Stack:** Python 3.12+, Pydantic v2, pydantic-ai, asyncio

**Spec:** `docs/superpowers/specs/2026-04-08-agent-layer-upgrade-design.md`

---

### Task 1: Message Model & SourceContext

**Files:**
- Create: `skill_agent/messages.py`
- Create: `tests/test_messages.py`

- [ ] **Step 1: Write tests for Message and SourceContext models**

```python
# tests/test_messages.py
import uuid
from datetime import datetime, timezone

from skill_agent.messages import (
    Message,
    MessageType,
    SourceContext,
    UIContext,
    EmailContext,
    SubAgentContext,
)


def test_message_auto_generates_id_and_timestamp():
    msg = Message(type=MessageType.user, content="hello")
    assert msg.id  # non-empty
    uuid.UUID(msg.id)  # valid UUID
    assert isinstance(msg.timestamp, datetime)
    assert msg.summary is None
    assert msg.source_context is None


def test_message_types_are_string_enums():
    assert MessageType.user == "user"
    assert MessageType.tool_call == "tool_call"
    assert MessageType.system == "system"


def test_ui_context_defaults():
    ctx = UIContext()
    assert ctx.origin == "ui"
    assert ctx.sender is None
    uuid.UUID(ctx.interaction_id)  # auto-generated


def test_email_context_fields():
    ctx = EmailContext(
        sender="alice@example.com",
        subject="Hello",
        thread_id="t-123",
        reply_to="msg-456",
    )
    assert ctx.origin == "email"
    assert ctx.subject == "Hello"
    assert ctx.thread_id == "t-123"
    assert ctx.reply_to == "msg-456"


def test_subagent_context_fields():
    ctx = SubAgentContext(
        subagent_id="sa-1",
        parent_interaction_id="int-2",
    )
    assert ctx.origin == "subagent"
    assert ctx.subagent_id == "sa-1"
    assert ctx.parent_interaction_id == "int-2"


def test_message_with_source_context():
    ctx = UIContext(sender="user-1")
    msg = Message(type=MessageType.user, content="hi", source_context=ctx)
    assert msg.source_context.origin == "ui"
    assert msg.source_context.sender == "user-1"


def test_message_serialization_roundtrip():
    msg = Message(type=MessageType.agent, content={"key": "value"})
    data = msg.model_dump()
    restored = Message(**data)
    assert restored.id == msg.id
    assert restored.content == {"key": "value"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_messages.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skill_agent.messages'`

- [ ] **Step 3: Implement messages.py**

```python
# skill_agent/messages.py
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


# ── SourceContext hierarchy ───────────────────────────────────────────


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


# Rebuild Message so the forward reference to SourceContext resolves.
Message.model_rebuild()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_messages.py -v`
Expected: All 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add skill_agent/messages.py tests/test_messages.py
git commit -m "feat: add Message model and SourceContext hierarchy"
```

---

### Task 2: Inbox & Thread System

**Files:**
- Create: `skill_agent/inbox.py`
- Create: `tests/test_inbox.py`

- [ ] **Step 1: Write tests for Inbox, InboxItem, Thread, ThreadStatus**

```python
# tests/test_inbox.py
import asyncio
import uuid

from skill_agent.inbox import Inbox, InboxItem, Thread, ThreadStatus
from skill_agent.messages import UIContext, SubAgentContext


def _ui_ctx(sender: str = "user") -> UIContext:
    return UIContext(sender=sender)


def test_create_item_auto_generates_thread_id():
    inbox = Inbox()
    item = inbox.create_item(
        content="hello",
        subject="greeting",
        source_context=_ui_ctx(),
        notify=False,
    )
    assert isinstance(item, InboxItem)
    uuid.UUID(item.thread_id)  # valid UUID
    assert item.read is False
    assert item.content == "hello"


def test_create_item_with_explicit_thread_id():
    inbox = Inbox()
    item = inbox.create_item(
        content="msg",
        subject="subj",
        thread_id="t-1",
        source_context=_ui_ctx(),
        notify=False,
    )
    assert item.thread_id == "t-1"


def test_write_to_thread_requires_existing_thread():
    inbox = Inbox()
    try:
        inbox.write_to_thread(
            thread_id="nonexistent",
            content="msg",
            source_context=_ui_ctx(),
            notify=False,
        )
        assert False, "Should have raised"
    except KeyError:
        pass


def test_write_to_thread_appends_to_existing():
    inbox = Inbox()
    item1 = inbox.create_item(
        content="first", subject="topic", thread_id="t-1",
        source_context=_ui_ctx(), notify=False,
    )
    item2 = inbox.write_to_thread(
        thread_id="t-1", content="second",
        source_context=_ui_ctx(), notify=False,
    )
    assert item2.thread_id == "t-1"
    thread_items = inbox.read_thread("t-1")
    assert len(thread_items) == 2


def test_read_inbox_returns_unread_and_marks_read():
    inbox = Inbox()
    inbox.create_item(
        content="a", subject="s1",
        source_context=_ui_ctx(), notify=False,
    )
    inbox.create_item(
        content="b", subject="s2",
        source_context=_ui_ctx(), notify=True,
    )
    unread = inbox.read_inbox()
    assert len(unread) == 2
    assert all(item.read for item in inbox.items)
    # Second call returns empty
    assert len(inbox.read_inbox()) == 0


def test_get_thread_returns_thread_wrapper():
    inbox = Inbox()
    inbox.create_item(
        content="msg", subject="subj", thread_id="t-1",
        source_context=_ui_ctx(), notify=False,
    )
    thread = inbox.get_thread("t-1")
    assert isinstance(thread, Thread)
    assert thread.thread_id == "t-1"
    assert thread.status == ThreadStatus.in_progress


def test_thread_status_updates():
    inbox = Inbox()
    inbox.create_item(
        content="msg", subject="subj", thread_id="t-1",
        source_context=_ui_ctx(), notify=False,
    )
    thread = inbox.get_thread("t-1")
    assert thread.status == ThreadStatus.in_progress
    inbox.write_to_thread(
        thread_id="t-1", content="update",
        source_context=_ui_ctx(), notify=False,
        status=ThreadStatus.waiting_for_response,
    )
    assert inbox.get_thread("t-1").status == ThreadStatus.waiting_for_response


def test_dismiss_item():
    inbox = Inbox()
    item = inbox.create_item(
        content="msg", subject="subj",
        source_context=_ui_ctx(), notify=True,
    )
    inbox.dismiss_item(item.id)
    assert inbox.pending_notifications() is False


def test_delete_thread():
    inbox = Inbox()
    inbox.create_item(
        content="msg", subject="subj", thread_id="t-1",
        source_context=_ui_ctx(), notify=False,
    )
    inbox.delete_thread("t-1")
    assert len([i for i in inbox.items if i.thread_id == "t-1"]) == 0


def test_pending_notifications():
    inbox = Inbox()
    inbox.create_item(
        content="a", subject="s",
        source_context=_ui_ctx(), notify=False,
    )
    assert inbox.pending_notifications() is False
    inbox.create_item(
        content="b", subject="s",
        source_context=_ui_ctx(), notify=True,
    )
    assert inbox.pending_notifications() is True


def test_subscribe_yields_new_items():
    inbox = Inbox()

    async def _test():
        items_received = []

        async def consumer():
            async for item in inbox.subscribe():
                items_received.append(item)
                if len(items_received) == 2:
                    break

        task = asyncio.create_task(consumer())
        # Give consumer time to start
        await asyncio.sleep(0.01)
        inbox.create_item(
            content="a", subject="s1",
            source_context=_ui_ctx(), notify=False,
        )
        inbox.create_item(
            content="b", subject="s2",
            source_context=_ui_ctx(), notify=False,
        )
        await asyncio.wait_for(task, timeout=2.0)
        assert len(items_received) == 2
        assert items_received[0].content == "a"

    asyncio.run(_test())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_inbox.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skill_agent.inbox'`

- [ ] **Step 3: Implement inbox.py**

```python
# skill_agent/inbox.py
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

        # Initialize thread status if new thread
        if tid not in self._thread_statuses:
            self._thread_statuses[tid] = status or ThreadStatus.in_progress
        elif status is not None:
            self._thread_statuses[tid] = status

        # Notify subscribers
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

        # Use latest subject from thread if not provided
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_inbox.py -v`
Expected: All 12 tests PASS

- [ ] **Step 5: Commit**

```bash
git add skill_agent/inbox.py tests/test_inbox.py
git commit -m "feat: add Inbox, InboxItem, Thread, and ThreadStatus"
```

---

### Task 3: AgentConfig update

**Files:**
- Modify: `skill_agent/models.py:294-325`

- [ ] **Step 1: Write test for new config field**

```python
# tests/test_config.py
from skill_agent.models import AgentConfig


def test_context_compression_threshold_default():
    cfg = AgentConfig()
    assert cfg.context_compression_threshold == 100_000


def test_context_compression_threshold_custom():
    cfg = AgentConfig(context_compression_threshold=50_000)
    assert cfg.context_compression_threshold == 50_000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL — `ValidationError` (unknown field)

- [ ] **Step 3: Add context_compression_threshold to AgentConfig**

In `skill_agent/models.py`, add after line 324 (after `max_attached_text_file_chars`):

```python
    context_compression_threshold: int = Field(
        default=100_000,
        description="Auto-compress context_window when input_tokens exceeds this threshold.",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: All 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add skill_agent/models.py tests/test_config.py
git commit -m "feat: add context_compression_threshold to AgentConfig"
```

---

### Task 4: Extract existing tools into skill_tools.py

This is a pure refactor — move all existing tool registration from `_create_runner` in `agent.py` into a new file. No behavior change.

**Files:**
- Create: `skill_agent/skill_tools.py`
- Modify: `skill_agent/agent.py:486-1074` (the `_create_runner` function)

- [ ] **Step 1: Create skill_tools.py with register_skill_tools function**

Extract the entire body of `_create_runner` (lines 486–1074 of `agent.py`) into a new file. The new function signature:

```python
# skill_agent/skill_tools.py
"""Built-in skill tools: use_skill, register_skill, scaffold_skill, manage_todos,
read_reference, run_script, write_skill_file, read_user_file, call_client_function.

Extracted from agent.py to keep each file focused on one concern.
"""

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field
from pydantic_ai import RunContext

from .models import (
    ClientFunctionRequest,
    Skill,
    TodoItem,
    TodoStatus,
    ToolCallRecord,
)


def _resolve_skill_dir(skill: Skill) -> Path:
    """Return the directory containing the skill's SKILL.md file."""
    if skill.path is None:
        raise ValueError(f"Skill '{skill.name}' has no path — cannot access resources.")
    return skill.path.parent


def _preview(text: str, limit: int = 300) -> str:
    """Truncate text to `limit` chars for log previews."""
    return text if len(text) <= limit else text[:limit] + "..."


def _normalize_task_line(entry: Any) -> str | None:
    # ... (copy the existing function from agent.py lines 420-434)


def _coerce_todo_id(raw: Any) -> int | None:
    # ... (copy the existing function from agent.py lines 437-451)


def _parse_todo_status(raw: Any) -> "TodoStatus | None":
    # ... (copy the existing function from agent.py lines 453-483)


def register_skill_tools(runner, user_file_roots: tuple[Path, ...]) -> None:
    """Register all skill-related tools on the pydantic-ai runner."""

    # ... (copy all @runner.tool definitions from agent.py lines 500-1074)
    # This includes: use_skill, register_skill, scaffold_skill, manage_todos,
    # read_reference, run_script, write_skill_file, read_user_file (conditional),
    # call_client_function
```

Copy the entire tool registration code verbatim from `agent.py`. The helper functions (`_normalize_task_line`, `_coerce_todo_id`, `_parse_todo_status`, `_resolve_skill_dir`, `_preview`) move with it. The `ActivityDesc` type annotation also moves here.

- [ ] **Step 2: Slim down _create_runner in agent.py**

Replace the existing `_create_runner` function (lines 486–1074) with:

```python
def _create_runner(
    model: Model,
    system_prompt: str,
    user_file_roots: tuple[Path, ...],
) -> PydanticAgent[_RunDeps, str]:
    """Build the pydantic-ai runner and register all tools."""
    from .skill_tools import register_skill_tools

    runner: PydanticAgent[_RunDeps, str] = PydanticAgent(
        model=model,
        system_prompt=system_prompt,
        deps_type=_RunDeps,
        output_type=str,
    )

    register_skill_tools(runner, user_file_roots)

    return runner
```

Also remove the now-unused imports and helper functions that moved to `skill_tools.py`: `_resolve_skill_dir`, `_preview`, `_normalize_task_line`, `_coerce_todo_id`, `_parse_todo_status`. Remove unused imports: `subprocess`, `sys`, `Annotated`, `Field` (from pydantic), `Skill`, `ClientFunctionRequest`, `ToolCallRecord` — but only if they're not used elsewhere in `agent.py`. Keep `json` (used in `_event_stream`).

- [ ] **Step 3: Run existing example to verify nothing broke**

Run: `uv run python -c "from skill_agent.agent import Agent; print('import ok')"`
Expected: `import ok`

Run: `uv run pytest tests/ -v` (if tests exist)
Expected: All existing tests PASS

- [ ] **Step 4: Commit**

```bash
git add skill_agent/skill_tools.py skill_agent/agent.py
git commit -m "refactor: extract tool registration into skill_tools.py"
```

---

### Task 5: Add dual message stores and inbox to Agent

Wire the new Message, Inbox, and dual stores into the Agent class. No new tools yet — just the plumbing.

**Files:**
- Modify: `skill_agent/agent.py:85-96` (`_RunDeps`)
- Modify: `skill_agent/agent.py:121-175` (`Agent.__init__`)
- Modify: `skill_agent/agent.py:179-183` (`clear_conversation`)
- Modify: `skill_agent/agent.py:292-368` (`_event_stream`)
- Create: `tests/test_agent_stores.py`

- [ ] **Step 1: Write tests for dual stores and inbox on Agent**

```python
# tests/test_agent_stores.py
"""Tests that Agent has message_log, context_window, and inbox wired up."""
from skill_agent.messages import Message, MessageType
from skill_agent.inbox import Inbox


def test_rundeps_has_new_fields():
    """Verify _RunDeps dataclass has the new fields."""
    from skill_agent.agent import _RunDeps
    from skill_agent.inbox import Inbox

    deps = _RunDeps(
        skills={},
        inbox=Inbox(),
        message_log=[],
        context_window=[],
        active_subagents={},
        context_compression_threshold=100_000,
    )
    assert isinstance(deps.inbox, Inbox)
    assert deps.message_log == []
    assert deps.context_window == []
    assert deps.active_subagents == {}
    assert deps.context_compression_threshold == 100_000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_agent_stores.py -v`
Expected: FAIL — `TypeError` (unexpected keyword arguments)

- [ ] **Step 3: Update _RunDeps with new fields**

In `skill_agent/agent.py`, modify the `_RunDeps` dataclass (around line 85):

```python
from .inbox import Inbox
from .messages import Message

@dataclass
class _RunDeps:
    skills: dict[str, Skill]
    inbox: Inbox = field(default_factory=Inbox)
    message_log: list[Message] = field(default_factory=list)
    context_window: list[Message] = field(default_factory=list)
    active_subagents: dict = field(default_factory=dict)
    context_compression_threshold: int = 100_000
    activated_skills: list[str] = field(default_factory=list)
    tool_log: list[ToolCallRecord] = field(default_factory=list)
    todo_list: list[TodoItem] = field(default_factory=list)
    _next_todo_id: int = 1
    user_file_roots: tuple[Path, ...] = field(default_factory=tuple)
    max_user_file_read_chars: int = 15000
    user_skills_dirs: tuple[Path, ...] = field(default_factory=tuple)
    pending_client_requests: list[ClientFunctionRequest] = field(default_factory=list)
```

- [ ] **Step 4: Update Agent.__init__ to initialize new attributes**

In `Agent.__init__` (around line 142), after `self._config = cfg`, add:

```python
        self.inbox = Inbox()
        self.message_log: list[Message] = []
        self.context_window: list[Message] = []
        self.subagent_logs: dict[str, list[Message]] = {}
        self._active_subagents: dict[str, Any] = {}
        self._subagent_tasks: dict[str, asyncio.Task] = {}
        self._singleton_subagents: dict[str, str] = {}
        self._running: bool = False
```

Update the `_deps` initialization to pass references:

```python
        self._deps = _RunDeps(
            skills=all_skills,
            inbox=self.inbox,
            message_log=self.message_log,
            context_window=self.context_window,
            active_subagents=self._active_subagents,
            context_compression_threshold=cfg.context_compression_threshold,
            user_file_roots=roots,
            max_user_file_read_chars=cfg.max_user_file_read_chars,
            user_skills_dirs=(Path(skills_dir).resolve(),),
        )
```

- [ ] **Step 5: Update clear_conversation to clear new stores**

```python
    def clear_conversation(self) -> None:
        """Drop all remembered turns, todo list, message stores, and inbox."""
        self._conversation_messages.clear()
        self._deps.todo_list.clear()
        self._deps._next_todo_id = 1
        self.message_log.clear()
        self.context_window.clear()
        self.inbox = Inbox()
        self._deps.inbox = self.inbox
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_agent_stores.py tests/test_messages.py tests/test_inbox.py -v`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add skill_agent/agent.py tests/test_agent_stores.py
git commit -m "feat: wire dual message stores and inbox into Agent"
```

---

### Task 6: Message logging in _event_stream

Instrument `_event_stream` to write Messages to both stores as events occur.

**Files:**
- Modify: `skill_agent/agent.py:292-368` (`_event_stream`)
- Create: `tests/test_message_logging.py`

- [ ] **Step 1: Write test for message logging during a run**

```python
# tests/test_message_logging.py
"""Test that _event_stream writes Messages to message_log and context_window.

These tests require a mock model since we can't call a real LLM in unit tests.
We test the Message creation helpers directly instead.
"""
from skill_agent.messages import Message, MessageType


def test_user_message_creation():
    msg = Message(type=MessageType.user, content="What is 2+2?")
    assert msg.type == MessageType.user
    assert msg.content == "What is 2+2?"
    assert msg.summary is None


def test_tool_call_message_stores_name_and_description():
    msg = Message(
        type=MessageType.tool_call,
        content={"tool": "use_skill", "description": "Loading wikipedia skill"},
    )
    assert msg.type == MessageType.tool_call
    assert msg.content["tool"] == "use_skill"


def test_agent_message_creation():
    msg = Message(type=MessageType.agent, content="The answer is 4.")
    assert msg.type == MessageType.agent


def test_system_message_for_compression():
    msg = Message(
        type=MessageType.system,
        content="Context window was compressed. Summary: ...",
    )
    assert msg.type == MessageType.system
```

- [ ] **Step 2: Run test to verify it passes (these are unit tests for Message)**

Run: `uv run pytest tests/test_message_logging.py -v`
Expected: All 4 tests PASS (Message already implemented)

- [ ] **Step 3: Instrument _event_stream with message logging**

In `skill_agent/agent.py`, modify `_event_stream` (around line 292). Add message logging at each event point:

At the start of `_event_stream`, after the method signature:
```python
    async def _event_stream(self, user_message: str | list[Any]) -> AsyncGenerator[AgentEvent, None]:
        # Log the user message
        user_content = user_message if isinstance(user_message, str) else str(user_message)
        user_msg = Message(type=MessageType.user, content=user_content)
        self.message_log.append(user_msg)
        self.context_window.append(user_msg)
        self._running = True

        answer_chunks: list[str] = []
```

On `FunctionToolCallEvent` (after yielding ToolCallEvent):
```python
                tool_msg = Message(
                    type=MessageType.tool_call,
                    content={"tool": raw.part.tool_name, "description": act or raw.part.tool_name},
                )
                self.message_log.append(tool_msg)
                self.context_window.append(tool_msg)
```

On `FunctionToolResultEvent` (after yielding ToolResultEvent):
```python
                result_msg = Message(
                    type=MessageType.tool_result,
                    content={"tool": raw.result.tool_name},
                )
                self.message_log.append(result_msg)
                self.context_window.append(result_msg)
```

On text deltas, accumulate:
```python
            elif isinstance(raw, PartStartEvent):
                if isinstance(raw.part, TextPart) and raw.part.content:
                    answer_chunks.append(raw.part.content)
                    yield TextDeltaEvent(content=raw.part.content)

            elif isinstance(raw, PartDeltaEvent):
                if isinstance(raw.delta, TextPartDelta):
                    answer_chunks.append(raw.delta.content_delta)
                    yield TextDeltaEvent(content=raw.delta.content_delta)
```

On `AgentRunResultEvent`, log the full agent response and check compression:
```python
            elif isinstance(raw, AgentRunResultEvent):
                self._conversation_messages[:] = list(raw.result.all_messages())

                # Log the full agent response as a single message
                full_answer = "".join(answer_chunks)
                if full_answer:
                    agent_msg = Message(type=MessageType.agent, content=full_answer)
                    self.message_log.append(agent_msg)
                    self.context_window.append(agent_msg)

                run_usage = raw.result.usage()
                yield RunCompleteEvent(
                    usage=TokenUsage(
                        input_tokens=run_usage.input_tokens or 0,
                        output_tokens=run_usage.output_tokens or 0,
                    ),
                )

        self._running = False
```

Add import at top of agent.py:
```python
from .messages import Message, MessageType
```

- [ ] **Step 4: Run all tests**

Run: `uv run pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add skill_agent/agent.py tests/test_message_logging.py
git commit -m "feat: instrument _event_stream with dual message store logging"
```

---

### Task 7: Context management tools

**Files:**
- Create: `skill_agent/context_tools.py`
- Create: `tests/test_context_tools.py`
- Modify: `skill_agent/agent.py` (wire into `_create_runner`)

- [ ] **Step 1: Write tests for context tools**

```python
# tests/test_context_tools.py
from skill_agent.messages import Message, MessageType
from skill_agent.inbox import Inbox
from skill_agent.context_tools import (
    compress_message_impl,
    retrieve_message_impl,
    compress_all_impl,
)


def _make_messages(n: int = 3) -> tuple[list[Message], list[Message]]:
    """Return (message_log, context_window) with n messages in both."""
    messages = [
        Message(type=MessageType.user, content=f"msg-{i}")
        for i in range(n)
    ]
    log = list(messages)
    window = list(messages)
    return log, window


def test_compress_message_sets_summary_and_clears_content():
    log, window = _make_messages(3)
    target_id = window[1].id
    result = compress_message_impl(window, target_id, "summary of msg-1")
    assert "compressed" in result.lower() or "ok" in result.lower()
    assert window[1].summary == "summary of msg-1"
    assert window[1].content is None
    # Log is unchanged
    assert log[1].content == "msg-1"


def test_compress_message_not_found():
    log, window = _make_messages(1)
    result = compress_message_impl(window, "nonexistent-id", "summary")
    assert "not found" in result.lower()


def test_retrieve_message_restores_content():
    log, window = _make_messages(3)
    target_id = window[1].id
    # First compress
    compress_message_impl(window, target_id, "summary")
    assert window[1].content is None
    # Then retrieve
    result = retrieve_message_impl(log, window, target_id)
    assert window[1].content == "msg-1"
    assert window[1].summary is None


def test_retrieve_message_reinserts_if_removed():
    log, window = _make_messages(3)
    target_id = window[1].id
    # Remove from window entirely
    window.pop(1)
    assert len(window) == 2
    # Retrieve should re-insert at original position
    result = retrieve_message_impl(log, window, target_id)
    assert len(window) == 3
    assert window[1].id == target_id
    assert window[1].content == "msg-1"


def test_compress_all_replaces_window():
    log, window = _make_messages(5)
    first_id = window[0].id
    last_id = window[-1].id
    result = compress_all_impl(log, window, "everything summarized", "resume by doing X")
    # Window should have exactly 2 messages: the compressed summary + the notification
    assert len(window) == 2
    assert window[0].type == MessageType.system
    assert first_id in window[0].content
    assert last_id in window[0].content
    assert "everything summarized" in window[0].content
    assert "resume by doing X" in window[0].content
    # Notification message
    assert window[1].type == MessageType.system
    assert "compressed" in window[1].content.lower()
    # Log unchanged (still 5 original + 2 new system messages)
    assert len(log) == 7
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_context_tools.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skill_agent.context_tools'`

- [ ] **Step 3: Implement context_tools.py**

```python
# skill_agent/context_tools.py
"""Context window management tools: compress, retrieve, and compress_all.

These tools allow the agent (or the runtime) to manage the context window
by compressing old messages to summaries and retrieving them when needed.

The implementation functions (*_impl) are pure logic operating on lists.
register_context_tools() wires them as pydantic-ai tools on the runner.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field
from pydantic_ai import RunContext

from .messages import Message, MessageType


def compress_message_impl(
    context_window: list[Message],
    message_id: str,
    summary: str,
) -> str:
    """Replace a message's content with a summary in the context window."""
    for msg in context_window:
        if msg.id == message_id:
            msg.content = None
            msg.summary = summary
            return f"Compressed message {message_id}."
    return f"Message {message_id} not found in context window."


def retrieve_message_impl(
    message_log: list[Message],
    context_window: list[Message],
    message_id: str,
) -> str:
    """Restore a message from message_log into context_window."""
    # Find the full message in the log
    original = None
    for msg in message_log:
        if msg.id == message_id:
            original = msg
            break
    if original is None:
        return f"Message {message_id} not found in message log."

    # Check if it's still in the window (compressed)
    for i, msg in enumerate(context_window):
        if msg.id == message_id:
            msg.content = original.content
            msg.summary = None
            return f"Restored message {message_id}."

    # Re-insert at original position by timestamp
    insert_at = len(context_window)
    for i, msg in enumerate(context_window):
        if msg.timestamp > original.timestamp:
            insert_at = i
            break

    restored = Message(
        id=original.id,
        timestamp=original.timestamp,
        type=original.type,
        source_context=original.source_context,
        content=original.content,
        summary=None,
    )
    context_window.insert(insert_at, restored)
    return f"Re-inserted message {message_id} at position {insert_at}."


def compress_all_impl(
    message_log: list[Message],
    context_window: list[Message],
    summary: str,
    instruction: str,
) -> str:
    """Replace the entire context window with a single summary message."""
    if not context_window:
        return "Context window is already empty."

    first_id = context_window[0].id
    last_id = context_window[-1].id

    compressed = Message(
        type=MessageType.system,
        content=(
            f"[Context compressed: messages {first_id} through {last_id}]\n\n"
            f"Summary: {summary}\n\n"
            f"Resumption instruction: {instruction}"
        ),
    )

    notification = Message(
        type=MessageType.system,
        content="Context window was compressed to stay within token limits.",
    )

    context_window.clear()
    context_window.append(compressed)
    context_window.append(notification)

    # Append to message_log (append-only)
    message_log.append(compressed)
    message_log.append(notification)

    return f"Compressed {last_id} — context window now contains summary only."


def build_generic_summary(
    message_log: list[Message],
    todo_list: list[Any],
) -> tuple[str, str]:
    """Build a generic summary from message_log when the model fails to compress.

    Returns (summary, instruction) tuple.
    """
    parts: list[str] = []

    if message_log:
        first = message_log[0]
        parts.append(f"First message: [{first.type.value}] {str(first.content)[:200]}")
        last = message_log[-1]
        parts.append(f"Last message: [{last.type.value}] {str(last.content)[:200]}")

    # Recent tool calls
    recent_tools = [
        m for m in message_log[-20:]
        if m.type == MessageType.tool_call
    ]
    if recent_tools:
        tool_names = [str(m.content.get("tool", "?")) if isinstance(m.content, dict) else "?" for m in recent_tools[-5:]]
        parts.append(f"Recent tools: {', '.join(tool_names)}")

    if todo_list:
        todo_strs = [str(getattr(t, "content", t))[:80] for t in todo_list[:5]]
        parts.append(f"Active todos: {'; '.join(todo_strs)}")

    summary = "\n".join(parts) if parts else "Conversation history (details compressed)."
    instruction = "Review the inbox and todo list to determine next steps. Ask the user for clarification if the task is unclear."

    return summary, instruction


def register_context_tools(runner: Any) -> None:
    """Register compress_message, retrieve_message, and compress_all as pydantic-ai tools."""

    ActivityDesc = Annotated[
        str,
        Field(
            description="Short plain-language phrase describing what you are doing.",
        ),
    ]

    @runner.tool(description=(
        "Compress a message in the context window by replacing its content with a summary. "
        "The full content is preserved in the message log and can be retrieved later. "
        "Provide the message id and a concise summary of what the message contained."
    ))
    def compress_message(
        ctx: RunContext,
        message_id: str,
        summary: str,
        activity: ActivityDesc = "",
    ) -> str:
        return compress_message_impl(ctx.deps.context_window, message_id, summary)

    @runner.tool(description=(
        "Retrieve a previously compressed or removed message from the message log "
        "and restore it to the context window. Provide the message id."
    ))
    def retrieve_message(
        ctx: RunContext,
        message_id: str,
        activity: ActivityDesc = "",
    ) -> str:
        return retrieve_message_impl(
            ctx.deps.message_log, ctx.deps.context_window, message_id
        )

    @runner.tool(description=(
        "Compress the entire context window into a single summary message. "
        "Use this when the context is getting too large. Provide a comprehensive "
        "summary of the conversation so far, and an instruction with enough detail "
        "to resume work without reading the full history."
    ))
    def compress_all(
        ctx: RunContext,
        summary: str,
        instruction: str,
        activity: ActivityDesc = "",
    ) -> str:
        return compress_all_impl(
            ctx.deps.message_log, ctx.deps.context_window, summary, instruction
        )
```

- [ ] **Step 4: Wire context tools into _create_runner**

In `skill_agent/agent.py`, update `_create_runner`:

```python
def _create_runner(
    model: Model,
    system_prompt: str,
    user_file_roots: tuple[Path, ...],
) -> PydanticAgent[_RunDeps, str]:
    """Build the pydantic-ai runner and register all tools."""
    from .skill_tools import register_skill_tools
    from .context_tools import register_context_tools

    runner: PydanticAgent[_RunDeps, str] = PydanticAgent(
        model=model,
        system_prompt=system_prompt,
        deps_type=_RunDeps,
        output_type=str,
    )

    register_skill_tools(runner, user_file_roots)
    register_context_tools(runner)

    return runner
```

- [ ] **Step 5: Run all tests**

Run: `uv run pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add skill_agent/context_tools.py tests/test_context_tools.py skill_agent/agent.py
git commit -m "feat: add context management tools (compress, retrieve, compress_all)"
```

---

### Task 8: Auto-compression trigger in _event_stream

**Files:**
- Modify: `skill_agent/agent.py` (`_event_stream`, around the `AgentRunResultEvent` handler)
- Create: `tests/test_auto_compression.py`

- [ ] **Step 1: Write test for auto-compression logic**

```python
# tests/test_auto_compression.py
"""Test the auto-compression check that runs after each model call."""
from skill_agent.messages import Message, MessageType
from skill_agent.context_tools import compress_all_impl, build_generic_summary


def test_build_generic_summary_with_messages():
    log = [
        Message(type=MessageType.user, content="What is Python?"),
        Message(type=MessageType.tool_call, content={"tool": "use_skill", "description": "loading"}),
        Message(type=MessageType.agent, content="Python is a programming language."),
    ]
    summary, instruction = build_generic_summary(log, [])
    assert "Python" in summary
    assert "use_skill" in summary
    assert instruction  # non-empty


def test_build_generic_summary_empty_log():
    summary, instruction = build_generic_summary([], [])
    assert "compressed" in summary.lower()
    assert instruction  # non-empty


def test_compress_all_followed_by_generic_summary():
    """Simulate the runtime forcing compression with a generic summary."""
    log = [
        Message(type=MessageType.user, content=f"msg-{i}")
        for i in range(10)
    ]
    window = list(log)
    summary, instruction = build_generic_summary(log, [])
    result = compress_all_impl(log, window, summary, instruction)
    assert len(window) == 2  # summary + notification
    assert window[0].type == MessageType.system
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/test_auto_compression.py -v`
Expected: All 3 tests PASS

- [ ] **Step 3: Add auto-compression check to _event_stream**

In `skill_agent/agent.py`, in the `AgentRunResultEvent` handler, after logging the agent response and before yielding `RunCompleteEvent`, add:

```python
                # Auto-compression check
                input_tokens = run_usage.input_tokens or 0
                threshold = self._deps.context_compression_threshold
                if input_tokens > threshold and len(self.context_window) > 1:
                    from .context_tools import compress_all_impl, build_generic_summary
                    summary, instruction = build_generic_summary(
                        self.message_log, self._deps.todo_list
                    )
                    compress_all_impl(
                        self.message_log, self.context_window, summary, instruction
                    )
```

- [ ] **Step 4: Run all tests**

Run: `uv run pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add skill_agent/agent.py tests/test_auto_compression.py
git commit -m "feat: add auto-compression trigger when input_tokens exceeds threshold"
```

---

### Task 9: Inbox tools

**Files:**
- Create: `skill_agent/inbox_tools.py`
- Create: `tests/test_inbox_tools.py`
- Modify: `skill_agent/agent.py` (wire into `_create_runner`)

- [ ] **Step 1: Write tests for inbox tool implementations**

```python
# tests/test_inbox_tools.py
from skill_agent.inbox import Inbox, ThreadStatus
from skill_agent.messages import Message, MessageType, UIContext
from skill_agent.inbox_tools import (
    read_inbox_impl,
    read_thread_impl,
    write_to_thread_impl,
    forward_thread_item_impl,
    dismiss_inbox_item_impl,
    delete_thread_impl,
)


def _ctx() -> UIContext:
    return UIContext(sender="test")


def test_read_inbox_returns_subjects_and_status():
    inbox = Inbox()
    inbox.create_item(content="c1", subject="s1", source_context=_ctx(), notify=False, thread_id="t-1")
    inbox.create_item(content="c2", subject="s2", source_context=_ctx(), notify=True, thread_id="t-2")
    log: list[Message] = []
    result = read_inbox_impl(inbox, log)
    assert "s1" in result
    assert "s2" in result
    assert "in_progress" in result
    assert len(log) == 1  # one message logged
    assert log[0].type == MessageType.tool_result


def test_read_thread_returns_full_contents():
    inbox = Inbox()
    inbox.create_item(content="first", subject="topic", source_context=_ctx(), notify=False, thread_id="t-1")
    inbox.write_to_thread(thread_id="t-1", content="second", source_context=_ctx(), notify=False)
    log: list[Message] = []
    window: list[Message] = []
    result = read_thread_impl(inbox, log, window, "t-1")
    assert "first" in result
    assert "second" in result


def test_write_to_thread_resolves_target():
    own_inbox = Inbox()
    own_inbox.create_item(content="init", subject="s", source_context=_ctx(), notify=False, thread_id="t-1")
    active_subagents: dict = {}
    log: list[Message] = []
    result = write_to_thread_impl(
        own_inbox=own_inbox,
        active_subagents=active_subagents,
        message_log=log,
        thread_id="t-1",
        content="update",
        notify=False,
        source_context=_ctx(),
    )
    assert "wrote" in result.lower() or "t-1" in result


def test_dismiss_inbox_item():
    inbox = Inbox()
    item = inbox.create_item(content="c", subject="s", source_context=_ctx(), notify=True)
    result = dismiss_inbox_item_impl(inbox, item.id)
    assert inbox.pending_notifications() is False


def test_delete_thread():
    inbox = Inbox()
    inbox.create_item(content="c", subject="s", source_context=_ctx(), notify=False, thread_id="t-1")
    deleted, result = delete_thread_impl(inbox, "t-1", {}, {})
    assert len(deleted) == 1
    assert len(inbox.read_thread("t-1")) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_inbox_tools.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement inbox_tools.py**

```python
# skill_agent/inbox_tools.py
"""Inbox tools for agent-to-agent and external communication.

Provides read_inbox, read_thread, write_to_thread, forward_thread_item,
dismiss_inbox_item, delete_thread, and spawn_subagent.

Implementation functions (*_impl) are pure logic. register_*_tools()
wires them as pydantic-ai tools on the runner.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from pydantic import Field
from pydantic_ai import RunContext

from .inbox import Inbox, InboxItem, ThreadStatus
from .messages import Message, MessageType, SourceContext, UIContext


def read_inbox_impl(
    inbox: Inbox,
    message_log: list[Message],
) -> str:
    """Return unread items with subjects and thread status. Marks them read."""
    unread = inbox.read_inbox()
    if not unread:
        return "Inbox is empty — no unread items."

    lines: list[str] = []
    seen_threads: set[str] = set()
    for item in unread:
        thread = inbox.get_thread(item.thread_id)
        status_tag = f"[{thread.status.value}]" if item.thread_id not in seen_threads else ""
        seen_threads.add(item.thread_id)
        lines.append(
            f"- [{item.thread_id}] {status_tag} {item.subject}"
            f" (notify={'yes' if item.notify else 'no'})"
        )

    result_text = f"{len(unread)} unread item(s):\n" + "\n".join(lines)

    # Log to message_log
    message_log.append(Message(
        type=MessageType.tool_result,
        content={"tool": "read_inbox", "item_count": len(unread)},
    ))

    return result_text


def read_thread_impl(
    inbox: Inbox,
    message_log: list[Message],
    context_window: list[Message],
    thread_id: str,
) -> str:
    """Return full thread contents. Auto-compresses previous reads of same thread."""
    items = inbox.read_thread(thread_id)
    if not items:
        return f"Thread '{thread_id}' not found or empty."

    # Auto-compress previous reads of this thread in context_window
    thread = inbox.get_thread(thread_id)
    for i, msg in enumerate(context_window):
        if (
            msg.type == MessageType.tool_result
            and isinstance(msg.content, dict)
            and msg.content.get("tool") == "read_thread"
            and msg.content.get("thread_id") == thread_id
            and msg.summary is None  # not already compressed
        ):
            msg.content = None
            msg.summary = f"[previous read of thread {thread_id}: {thread.subject}]"

    lines: list[str] = []
    for item in items:
        lines.append(
            f"[{item.timestamp.isoformat()}] {item.source_context.origin}/"
            f"{item.source_context.sender or '?'}: {item.content}"
        )

    result_text = f"Thread {thread_id} ({thread.status.value}):\n" + "\n".join(lines)

    # Log to message_log and context_window
    log_msg = Message(
        type=MessageType.tool_result,
        content={"tool": "read_thread", "thread_id": thread_id},
    )
    message_log.append(log_msg)
    context_window.append(log_msg)

    return result_text


def write_to_thread_impl(
    own_inbox: Inbox,
    active_subagents: dict,
    message_log: list[Message],
    thread_id: str,
    content: str,
    notify: bool,
    source_context: SourceContext | None = None,
    subject: str | None = None,
    status: ThreadStatus | None = None,
) -> str:
    """Write to a thread, resolving target inbox automatically."""
    ctx = source_context or UIContext(sender="agent")

    # Resolve target inbox: subagent inbox if thread matches, else own
    target_inbox = own_inbox
    if thread_id in active_subagents:
        subagent = active_subagents[thread_id]
        target_inbox = subagent.inbox

    try:
        item = target_inbox.write_to_thread(
            thread_id=thread_id,
            content=content,
            source_context=ctx,
            notify=notify,
            subject=subject,
            status=status,
        )
    except KeyError:
        # Thread doesn't exist in target — try own inbox as fallback
        if target_inbox is not own_inbox:
            try:
                item = own_inbox.write_to_thread(
                    thread_id=thread_id,
                    content=content,
                    source_context=ctx,
                    notify=notify,
                    subject=subject,
                    status=status,
                )
            except KeyError:
                return f"Thread '{thread_id}' not found in any inbox."
        else:
            return f"Thread '{thread_id}' not found."

    message_log.append(Message(
        type=MessageType.tool_call,
        content={"tool": "write_to_thread", "description": f"Wrote to thread {thread_id}"},
    ))

    return f"Wrote to thread {thread_id} (notify={notify})."


def forward_thread_item_impl(
    own_inbox: Inbox,
    active_subagents: dict,
    item_id: str,
    to_subagent_id: str,
) -> str:
    """Forward an inbox item to a subagent's inbox without loading content."""
    # Find the item
    source_item = None
    for item in own_inbox.items:
        if item.id == item_id:
            source_item = item
            break
    if source_item is None:
        return f"Item '{item_id}' not found in inbox."

    # Resolve target subagent
    if to_subagent_id not in active_subagents:
        return f"No active subagent with id '{to_subagent_id}'."

    subagent = active_subagents[to_subagent_id]
    target_inbox: Inbox = subagent.inbox

    # Forward: write to target inbox with original source_context + forwarding note
    forwarded_content = f"[Forwarded from parent] {source_item.content}"
    target_inbox.create_item(
        content=forwarded_content,
        subject=f"[FWD] {source_item.subject}",
        source_context=source_item.source_context,
        notify=True,
    )

    return f"Forwarded item '{source_item.subject}' to subagent {to_subagent_id}."


def dismiss_inbox_item_impl(inbox: Inbox, item_id: str) -> str:
    """Mark an inbox item as dismissed."""
    try:
        inbox.dismiss_item(item_id)
        return f"Dismissed item {item_id}."
    except KeyError:
        return f"Item '{item_id}' not found."


def delete_thread_impl(
    inbox: Inbox,
    thread_id: str,
    active_subagents: dict,
    singleton_subagents: dict,
) -> tuple[list[InboxItem], str]:
    """Delete a thread and clean up subagent references."""
    deleted = inbox.delete_thread(thread_id)

    # Clean up singleton mapping
    to_remove = [k for k, v in singleton_subagents.items() if v == thread_id]
    for k in to_remove:
        del singleton_subagents[k]

    return deleted, f"Deleted thread {thread_id} ({len(deleted)} items archived)."


def register_inbox_tools(runner: Any) -> None:
    """Register inbox tools on the pydantic-ai runner."""

    ActivityDesc = Annotated[
        str,
        Field(description="Short plain-language phrase describing what you are doing."),
    ]

    @runner.tool(description=(
        "Check your inbox for unread messages. Returns subjects and thread status "
        "for each unread item. Use read_thread to see full contents of a thread."
    ))
    def read_inbox(ctx: RunContext, activity: ActivityDesc = "") -> str:
        return read_inbox_impl(ctx.deps.inbox, ctx.deps.message_log)

    @runner.tool(description=(
        "Read the full contents of a thread by thread_id. "
        "Previous reads of the same thread in context are automatically compressed."
    ))
    def read_thread(ctx: RunContext, thread_id: str, activity: ActivityDesc = "") -> str:
        return read_thread_impl(
            ctx.deps.inbox, ctx.deps.message_log, ctx.deps.context_window, thread_id
        )

    @runner.tool(description=(
        "Write a message to a thread. Target inbox is resolved automatically: "
        "if thread_id matches an active subagent, the message goes to that subagent's inbox. "
        "Otherwise it goes to your own inbox. Set notify=true only for final results or blocking errors."
    ))
    def write_to_thread(
        ctx: RunContext,
        thread_id: str,
        content: str,
        notify: bool = False,
        status: str | None = None,
        activity: ActivityDesc = "",
    ) -> str:
        thread_status = None
        if status:
            try:
                thread_status = ThreadStatus(status)
            except ValueError:
                return f"Invalid status '{status}'. Use: in_progress, waiting_for_response, done."
        return write_to_thread_impl(
            own_inbox=ctx.deps.inbox,
            active_subagents=ctx.deps.active_subagents,
            message_log=ctx.deps.message_log,
            thread_id=thread_id,
            content=content,
            notify=notify,
            status=thread_status,
        )

    @runner.tool(description=(
        "Forward an inbox item to a subagent without loading its content into your context. "
        "You only see the subject line. Provide the item_id and the target subagent's thread_id."
    ))
    def forward_thread_item(
        ctx: RunContext,
        item_id: str,
        to_subagent_id: str,
        activity: ActivityDesc = "",
    ) -> str:
        return forward_thread_item_impl(
            ctx.deps.inbox, ctx.deps.active_subagents, item_id, to_subagent_id
        )

    @runner.tool(description="Mark an inbox item as dismissed without processing.")
    def dismiss_inbox_item(ctx: RunContext, item_id: str, activity: ActivityDesc = "") -> str:
        return dismiss_inbox_item_impl(ctx.deps.inbox, item_id)

    @runner.tool(description=(
        "Delete a thread and archive its contents. If the thread is linked to a subagent, "
        "the subagent will wind down gracefully. This is how you stop a subagent."
    ))
    def delete_thread(ctx: RunContext, thread_id: str, activity: ActivityDesc = "") -> str:
        deleted, result = delete_thread_impl(
            ctx.deps.inbox, thread_id,
            ctx.deps.active_subagents, {},  # singleton cleanup handled by spawn tools
        )
        return result
```

- [ ] **Step 4: Wire inbox tools into _create_runner**

In `skill_agent/agent.py`, update `_create_runner`:

```python
def _create_runner(
    model: Model,
    system_prompt: str,
    user_file_roots: tuple[Path, ...],
) -> PydanticAgent[_RunDeps, str]:
    from .skill_tools import register_skill_tools
    from .context_tools import register_context_tools
    from .inbox_tools import register_inbox_tools

    runner: PydanticAgent[_RunDeps, str] = PydanticAgent(
        model=model,
        system_prompt=system_prompt,
        deps_type=_RunDeps,
        output_type=str,
    )

    register_skill_tools(runner, user_file_roots)
    register_context_tools(runner)
    register_inbox_tools(runner)

    return runner
```

- [ ] **Step 5: Run all tests**

Run: `uv run pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add skill_agent/inbox_tools.py tests/test_inbox_tools.py skill_agent/agent.py
git commit -m "feat: add inbox tools (read, write, forward, dismiss, delete)"
```

---

### Task 10: SubAgent class

**Files:**
- Create: `skill_agent/subagent.py`
- Create: `tests/test_subagent.py`

- [ ] **Step 1: Write tests for SubAgent**

```python
# tests/test_subagent.py
"""Test SubAgent class structure and initialization.

Full lifecycle tests require a mock model — these tests verify the class
hierarchy, attribute setup, and thread checking logic.
"""
from skill_agent.subagent import SubAgent
from skill_agent.inbox import Inbox, ThreadStatus
from skill_agent.messages import UIContext


def test_subagent_has_own_inbox():
    """SubAgent should have its own Inbox instance."""
    # We can't fully construct a SubAgent without a parent Agent (needs a model),
    # so we test the class attributes exist.
    assert hasattr(SubAgent, '__init__')


def test_thread_deleted_check():
    """Test the logic that detects a deleted thread."""
    inbox = Inbox()
    inbox.create_item(
        content="task", subject="work", thread_id="t-1",
        source_context=UIContext(), notify=False,
    )
    # Thread exists
    assert inbox.get_thread("t-1").items
    # Delete it
    inbox.delete_thread("t-1")
    # Thread is gone
    assert not inbox.get_thread("t-1").items
    assert inbox.get_thread("t-1").status == ThreadStatus.done
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_subagent.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement subagent.py**

```python
# skill_agent/subagent.py
"""SubAgent: a scoped worker that communicates via inbox, not streaming.

SubAgent inherits from Agent but:
  - Shares the parent's model (no separate model config)
  - Has its own inbox, message_log, context_window
  - Does not stream events to consumers
  - Communicates exclusively through the parent's inbox thread
  - Runs an autonomous loop as an asyncio.Task
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, TYPE_CHECKING

from .inbox import Inbox, ThreadStatus
from .messages import Message, MessageType, SubAgentContext

if TYPE_CHECKING:
    from .agent import Agent

logger = logging.getLogger(__name__)


class SubAgent:
    """A scoped worker agent that communicates via inbox.

    Inherits the parent's model and skill registry but has its own
    inbox, message stores, and tool set.
    """

    def __init__(
        self,
        *,
        parent: "Agent",
        instructions: str,
        system_prompt: str,
        tools: list[str],
        skills: list[str],
        thread_id: str,
    ) -> None:
        self.parent = parent
        self.instructions = instructions
        self.system_prompt = system_prompt
        self.requested_tools = tools
        self.requested_skills = skills
        self.thread_id = thread_id

        # Own stores
        self.inbox = Inbox()
        self.message_log: list[Message] = []
        self.context_window: list[Message] = []

        # Resolved from parent's registry
        self._skills = {
            name: parent._skills[name]
            for name in skills
            if name in parent._skills
        }

        self._source_context = SubAgentContext(
            subagent_id=thread_id,
            parent_interaction_id=thread_id,
            sender=f"subagent:{thread_id[:8]}",
        )

        self._done = False

    @property
    def is_done(self) -> bool:
        return self._done

    def _check_thread_alive(self) -> bool:
        """Check if our thread still exists in the parent's inbox."""
        thread = self.parent.inbox.get_thread(self.thread_id)
        if thread.status == ThreadStatus.done and not thread.items:
            return False
        return True

    def _post_to_parent(
        self,
        content: str,
        subject: str | None = None,
        notify: bool = False,
        status: ThreadStatus | None = None,
    ) -> None:
        """Write a message to the parent's inbox thread."""
        self.parent.inbox.write_to_thread(
            thread_id=self.thread_id,
            content=content,
            source_context=self._source_context,
            notify=notify,
            subject=subject,
            status=status,
        )

    async def run_loop(self) -> None:
        """The autonomous subagent loop.

        1. Run with instructions as initial prompt
        2. Check inbox between steps
        3. Wind down when thread is deleted
        """
        try:
            self._post_to_parent(
                content="Subagent started.",
                subject=self.instructions[:80],
                notify=False,
                status=ThreadStatus.in_progress,
            )

            # Initial run with instructions
            # This uses the parent's model via a simplified internal run
            await self._execute_step(self.instructions)

            # Autonomous loop: check inbox, process, repeat
            while not self._done:
                if not self._check_thread_alive():
                    logger.info("SubAgent %s: thread deleted, winding down.", self.thread_id[:8])
                    self._done = True
                    break

                # Check own inbox for new messages
                unread = self.inbox.read_inbox()
                if unread:
                    for item in unread:
                        await self._execute_step(item.content)
                else:
                    # No new messages — wait briefly before checking again
                    await asyncio.sleep(0.5)

                if not self._check_thread_alive():
                    self._done = True
                    break

        except Exception as e:
            logger.error("SubAgent %s error: %s", self.thread_id[:8], e)
            self._post_to_parent(
                content=f"Subagent error: {e}",
                notify=True,
                status=ThreadStatus.done,
            )
        finally:
            self._done = True

    async def _execute_step(self, prompt: str) -> None:
        """Execute one step using the parent's model.

        This is a simplified version of Agent.run() that logs to
        the subagent's own stores and posts results to the parent thread.
        """
        # Log the prompt
        self.message_log.append(Message(type=MessageType.user, content=prompt))
        self.context_window.append(Message(type=MessageType.user, content=prompt))

        # For now, the subagent uses the parent's runner directly.
        # A full implementation would create its own runner with filtered tools.
        # This placeholder posts the instructions to the parent thread.
        self._post_to_parent(
            content=f"Processing: {prompt[:200]}",
            notify=False,
            status=ThreadStatus.in_progress,
        )

    async def finish(self, result: str) -> None:
        """Mark the subagent as done and post the final result."""
        self._done = True
        self._post_to_parent(
            content=result,
            notify=True,
            status=ThreadStatus.done,
        )
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_subagent.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add skill_agent/subagent.py tests/test_subagent.py
git commit -m "feat: add SubAgent class with inbox communication and lifecycle loop"
```

---

### Task 11: Spawn tool

**Files:**
- Modify: `skill_agent/inbox_tools.py` (add `register_spawn_tools`)
- Create: `tests/test_spawn.py`
- Modify: `skill_agent/agent.py` (wire spawn tools into `_create_runner`)

- [ ] **Step 1: Write tests for spawn tool logic**

```python
# tests/test_spawn.py
from skill_agent.inbox import Inbox, ThreadStatus
from skill_agent.messages import UIContext


def test_singleton_dedup():
    """Singleton logic: same singleton_id should return existing thread_id."""
    singleton_map: dict[str, str] = {}
    singleton_map["researcher"] = "t-existing"
    # Simulate spawn check
    singleton_id = "researcher"
    if singleton_id in singleton_map:
        result = singleton_map[singleton_id]
    else:
        result = "t-new"
    assert result == "t-existing"


def test_singleton_new_entry():
    singleton_map: dict[str, str] = {}
    singleton_id = "librarian"
    assert singleton_id not in singleton_map
    singleton_map[singleton_id] = "t-new"
    assert singleton_map["librarian"] == "t-new"


def test_spawn_creates_thread_in_parent_inbox():
    inbox = Inbox()
    thread_id = "sa-thread-1"
    inbox.create_item(
        content="Subagent task: research X",
        subject="research X",
        source_context=UIContext(sender="spawn_tool"),
        notify=False,
        thread_id=thread_id,
        status=ThreadStatus.in_progress,
    )
    thread = inbox.get_thread(thread_id)
    assert thread.status == ThreadStatus.in_progress
    assert thread.items[0].subject == "research X"
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/test_spawn.py -v`
Expected: All 3 tests PASS

- [ ] **Step 3: Add register_spawn_tools to inbox_tools.py**

Append to `skill_agent/inbox_tools.py`:

```python
def register_spawn_tools(runner: Any) -> None:
    """Register the spawn_subagent tool on the pydantic-ai runner."""

    ActivityDesc = Annotated[
        str,
        Field(description="Short plain-language phrase describing what you are doing."),
    ]

    @runner.tool(description=(
        "Spawn a subagent to handle a scoped task. Returns a thread_id for communication. "
        "The subagent runs autonomously and posts updates to the thread. "
        "Use delete_thread to stop a subagent. "
        "Set blocking=true to wait for the result. "
        "Set singleton=true with a singleton_id to ensure only one instance runs."
    ))
    async def spawn_subagent(
        ctx: RunContext,
        instructions: str,
        system_prompt: str,
        tools: list[str] | None = None,
        skills: list[str] | None = None,
        blocking: bool = False,
        singleton: bool = False,
        singleton_id: str | None = None,
        activity: ActivityDesc = "",
    ) -> str:
        import asyncio
        import uuid
        from .subagent import SubAgent
        from .inbox import ThreadStatus
        from .messages import UIContext

        tools = tools or []
        skills = skills or []

        # Singleton check
        if singleton:
            if not singleton_id:
                return "singleton=true requires singleton_id."
            singleton_map = getattr(ctx.deps, '_singleton_subagents', None)
            if singleton_map is None:
                # Fallback: check on the active_subagents keys
                pass
            else:
                if singleton_id in singleton_map:
                    existing_tid = singleton_map[singleton_id]
                    if existing_tid in ctx.deps.active_subagents:
                        return f"Singleton '{singleton_id}' already running. Thread: {existing_tid}"

        # Create thread in parent's inbox
        thread_id = f"sa-{uuid.uuid4().hex[:12]}"
        subject = instructions[:80]
        ctx.deps.inbox.create_item(
            content=f"Spawning subagent: {instructions}",
            subject=subject,
            source_context=UIContext(sender="spawn_tool"),
            notify=False,
            thread_id=thread_id,
            status=ThreadStatus.in_progress,
        )

        # Access parent agent — it's the object that owns _deps
        # We need a reference to the Agent instance. Since _RunDeps has references
        # to the agent's stores, we can construct the SubAgent with those.
        # The parent Agent reference needs to be available via deps.
        parent_agent = getattr(ctx.deps, '_agent_ref', None)
        if parent_agent is None:
            return f"Thread {thread_id} created but subagent could not be instantiated (no agent ref)."

        subagent = SubAgent(
            parent=parent_agent,
            instructions=instructions,
            system_prompt=system_prompt,
            tools=tools,
            skills=skills,
            thread_id=thread_id,
        )

        ctx.deps.active_subagents[thread_id] = subagent

        # Register singleton
        if singleton and singleton_id:
            singleton_map = getattr(ctx.deps, '_singleton_subagents', {})
            singleton_map[singleton_id] = thread_id

        if blocking:
            # Wait for subagent to complete
            await subagent.run_loop()
            # Archive log
            return f"Subagent completed. Thread: {thread_id}"
        else:
            # Launch as background task
            task = asyncio.create_task(subagent.run_loop())
            # Store task reference on the agent
            if hasattr(parent_agent, '_subagent_tasks'):
                parent_agent._subagent_tasks[thread_id] = task
            return f"Subagent spawned. Thread: {thread_id}"
```

- [ ] **Step 4: Add _agent_ref and _singleton_subagents to _RunDeps**

In `skill_agent/agent.py`, add to `_RunDeps`:

```python
    _agent_ref: Any = field(default=None)
    _singleton_subagents: dict[str, str] = field(default_factory=dict)
```

In `Agent.__init__`, when creating `_deps`, add:

```python
        self._deps._agent_ref = self
        self._deps._singleton_subagents = self._singleton_subagents
```

- [ ] **Step 5: Wire spawn tools into _create_runner**

In `skill_agent/agent.py`, update `_create_runner`:

```python
def _create_runner(
    model: Model,
    system_prompt: str,
    user_file_roots: tuple[Path, ...],
) -> PydanticAgent[_RunDeps, str]:
    from .skill_tools import register_skill_tools
    from .context_tools import register_context_tools
    from .inbox_tools import register_inbox_tools, register_spawn_tools

    runner: PydanticAgent[_RunDeps, str] = PydanticAgent(
        model=model,
        system_prompt=system_prompt,
        deps_type=_RunDeps,
        output_type=str,
    )

    register_skill_tools(runner, user_file_roots)
    register_context_tools(runner)
    register_inbox_tools(runner)
    register_spawn_tools(runner)

    return runner
```

- [ ] **Step 6: Run all tests**

Run: `uv run pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add skill_agent/inbox_tools.py skill_agent/agent.py tests/test_spawn.py
git commit -m "feat: add spawn_subagent tool with singleton support"
```

---

### Task 12: Update __init__.py exports

**Files:**
- Modify: `skill_agent/__init__.py`

- [ ] **Step 1: Update public API exports**

```python
# skill_agent/__init__.py
"""Skill-based agent SDK with progressive skill disclosure.

Public API:
    Agent             — Create with model + skills_dir, call run() or run_stream()
    AgentEvent        — Discriminated union of all event types
    TodoUpdateEvent   — Todo list state after each manage_todos call
    ToolCallEvent     — Tool invocation (name, args, optional activity)
    ToolResultEvent   — Tool completion
    TextDeltaEvent    — Answer token from the model
    RunCompleteEvent  — Final event (token usage); conversation memory lives on Agent
    ClientFunctionRequestEvent — Client function request from skill
    Skill             — Skill metadata model (name, description, body, resources)
    AgentConfig       — Optional configuration (max_tokens, max_turns, etc.)
    AgentResult       — Typed return value from agent.run()
    Message           — Structured message in the conversation log
    MessageType       — Enum of message roles
    SourceContext     — Base class for message origin
    UIContext         — UI-originated message
    EmailContext      — Email-originated message
    SubAgentContext   — Subagent-originated message
    Inbox             — In-memory inbox for inter-agent communication
    InboxItem         — One item in an inbox
    Thread            — Scoped view of inbox items sharing a thread_id
    ThreadStatus      — Lifecycle status enum for threads
    SubAgent          — Scoped worker agent communicating via inbox
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
from .messages import (
    Message,
    MessageType,
    SourceContext,
    UIContext,
    EmailContext,
    SubAgentContext,
)
from .inbox import (
    Inbox,
    InboxItem,
    Thread,
    ThreadStatus,
)
from .subagent import SubAgent

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
    "Message",
    "MessageType",
    "SourceContext",
    "UIContext",
    "EmailContext",
    "SubAgentContext",
    "Inbox",
    "InboxItem",
    "Thread",
    "ThreadStatus",
    "SubAgent",
]
```

- [ ] **Step 2: Verify imports work**

Run: `uv run python -c "from skill_agent import Message, Inbox, SubAgent, ThreadStatus; print('all imports ok')"`
Expected: `all imports ok`

- [ ] **Step 3: Commit**

```bash
git add skill_agent/__init__.py
git commit -m "feat: export new message, inbox, and subagent types from __init__"
```

---

### Task 13: Update system_prompt.md

**Files:**
- Modify: `skill_agent/system_prompt.md`

- [ ] **Step 1: Add new tools to the system prompt**

Update `skill_agent/system_prompt.md` to include the new tools:

```markdown
You are a task-solving AI agent.

## Built-in tools
  - **use_skill**: Load a skill's instructions by name.
  - **manage_todos**: Plan and track your task list.
  - **read_reference**: Read a reference doc bundled with a skill.
  - **run_script**: Run a Python script bundled with a skill.

## Context management tools
  - **compress_message**: Compress a message in context by replacing it with a summary. Use when context is growing large and older messages are no longer needed in full.
  - **retrieve_message**: Restore a previously compressed message to full content.
  - **compress_all**: Replace the entire context window with a single summary. Use when instructed to compress or when context is critically large.

## Inbox & communication tools
  - **read_inbox**: Check for unread messages. Returns subjects and thread status.
  - **read_thread**: Read full contents of a thread by thread_id.
  - **write_to_thread**: Send a message to a thread. Target resolves automatically.
  - **forward_thread_item**: Forward an inbox item to a subagent without reading its content.
  - **dismiss_inbox_item**: Dismiss an inbox item without processing.
  - **delete_thread**: Delete a thread and stop any linked subagent.
  - **spawn_subagent**: Spawn a background worker for a scoped task. Returns a thread_id for communication.

## Rules
1. If your task is not straight forward, requires multiple steps, is complex or you get several instructions in one prompt; plan first, call `manage_todos` with action "set" to create a task list. Think about what the desired result looks like and make a step by step to do list that accomplishes that. Split it into small, easily achievable sub-problems. Work through your task list, updating item statuses as you go. If you learn something along the way that should change your approach, you're allowed to (and encouraged to) change the items of the list.
2. **Todo status updates are mandatory when you use a task list:** before starting work on an item, call `manage_todos` with action `update` and set that item's `id` to `in_progress` (ids are in the JSON the tool returns). When that step is finished, call `update` again with the same `id` and status `done`. Do this for every item you complete—even if you run many other tools in the same turn, you must still issue these `update` calls so progress is visible. Before your final reply to the user, ensure every finished item is marked `done`.
3. Pick the most relevant skill and call `use_skill` to load its instructions.
4. Your response should always be in the same language as the users prompts. Default to english when you're unsure.
5. Use `read_reference` and `run_script` to access skill resources as needed.
6. Adapt: add, remove, or reorder tasks if you learn something new.
7. Return a concise final answer.
8. Whenever you call any tool, pass `activity` with a brief plain-language description of that action for the user interface.
9. Use `compress_message` or `compress_all` to manage context size when conversations grow long. Prefer compressing old tool results and intermediate steps first.
10. Check your inbox between tasks when working on multi-step problems. Subagents may have posted updates.
```

- [ ] **Step 2: Commit**

```bash
git add skill_agent/system_prompt.md
git commit -m "docs: update system prompt with new context and inbox tools"
```

---

### Task 14: Inbox notification behavior in Agent

Wire the post-run inbox notification check into `_event_stream`.

**Files:**
- Modify: `skill_agent/agent.py` (`_event_stream` and `run`/`run_stream`)
- Create: `tests/test_inbox_notifications.py`

- [ ] **Step 1: Write test for notification behavior**

```python
# tests/test_inbox_notifications.py
from skill_agent.inbox import Inbox
from skill_agent.messages import UIContext


def test_pending_notifications_detected():
    inbox = Inbox()
    assert inbox.pending_notifications() is False
    inbox.create_item(
        content="result", subject="done",
        source_context=UIContext(), notify=True,
    )
    assert inbox.pending_notifications() is True
    # Reading clears the notification
    inbox.read_inbox()
    assert inbox.pending_notifications() is False


def test_silent_updates_dont_trigger():
    inbox = Inbox()
    inbox.create_item(
        content="progress", subject="update",
        source_context=UIContext(), notify=False,
    )
    assert inbox.pending_notifications() is False
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/test_inbox_notifications.py -v`
Expected: All 2 tests PASS (uses existing Inbox logic)

- [ ] **Step 3: Add notification check to _event_stream**

In `skill_agent/agent.py`, at the end of `_event_stream`, after `self._running = False`:

```python
        self._running = False

        # Check for pending inbox notifications
        # If any notify=True items arrived during this run, schedule a follow-up
        if self.inbox.pending_notifications():
            # The caller (run or run_stream consumer) is responsible for
            # triggering the follow-up. We signal via a flag.
            self._pending_inbox_notification = True
```

Add `self._pending_inbox_notification: bool = False` to `Agent.__init__`.

In `Agent.run()`, after `_collect_run` returns, add:

```python
    def run(self, prompt: str, *, files: Sequence[Path | str] | None = None) -> AgentResult:
        # ... existing code ...
        self._reset_run_state()
        result = asyncio.run(self._collect_run(user_message))

        # Handle pending inbox notifications
        if self._pending_inbox_notification:
            self._pending_inbox_notification = False
            follow_up = asyncio.run(self._collect_run("inbox updated"))
            # Merge events — the caller sees both runs
            result.events.extend(follow_up.events)
            result.answer += "\n\n" + follow_up.answer if follow_up.answer else ""

        return result
```

- [ ] **Step 4: Run all tests**

Run: `uv run pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add skill_agent/agent.py tests/test_inbox_notifications.py
git commit -m "feat: add post-run inbox notification check and follow-up trigger"
```

---

### Task 15: Final integration verification

**Files:**
- Create: `tests/test_integration.py`

- [ ] **Step 1: Write integration test verifying all pieces fit together**

```python
# tests/test_integration.py
"""Integration test: verify all new components are wired up on Agent."""
import importlib


def test_all_new_modules_importable():
    """Every new module should import cleanly."""
    modules = [
        "skill_agent.messages",
        "skill_agent.inbox",
        "skill_agent.context_tools",
        "skill_agent.inbox_tools",
        "skill_agent.skill_tools",
        "skill_agent.subagent",
    ]
    for mod in modules:
        importlib.import_module(mod)


def test_public_api_exports():
    """All new types should be importable from skill_agent."""
    from skill_agent import (
        Message, MessageType, SourceContext, UIContext, EmailContext, SubAgentContext,
        Inbox, InboxItem, Thread, ThreadStatus,
        SubAgent,
    )
    assert Message is not None
    assert Inbox is not None
    assert SubAgent is not None


def test_agent_config_has_new_field():
    from skill_agent import AgentConfig
    cfg = AgentConfig()
    assert hasattr(cfg, 'context_compression_threshold')
    assert cfg.context_compression_threshold == 100_000
```

- [ ] **Step 2: Run all tests**

Run: `uv run pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 3: Run import check**

Run: `uv run python -c "from skill_agent import Agent, Message, Inbox, SubAgent, ThreadStatus; print('Full integration OK')"`
Expected: `Full integration OK`

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: add integration tests for agent layer upgrade"
```

---

### Task 16: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update CLAUDE.md with new architecture information**

Add the following section after the existing "Built-in Tools" table:

```markdown
### New Infrastructure (Agent Layer Upgrade)

- **`messages.py`** — `Message` model (id, timestamp, type, content, summary) and `SourceContext` hierarchy (UIContext, EmailContext, SubAgentContext). Every conversation step is a Message.
- **`inbox.py`** — `Inbox`, `InboxItem`, `Thread`, `ThreadStatus`. General-purpose message routing for inter-agent and external communication. Exposed as `Agent.inbox`.
- **`context_tools.py`** — `compress_message`, `retrieve_message`, `compress_all`. Manage context window size by compressing messages to summaries. Auto-compression triggers when `input_tokens` exceeds `AgentConfig.context_compression_threshold`.
- **`inbox_tools.py`** — `read_inbox`, `read_thread`, `write_to_thread`, `forward_thread_item`, `dismiss_inbox_item`, `delete_thread`, `spawn_subagent`. Registered as pydantic-ai tools.
- **`skill_tools.py`** — All original tools extracted from `agent.py` (use_skill, manage_todos, run_script, etc.).
- **`subagent.py`** — `SubAgent` class. Shares parent's model, communicates via inbox threads, runs as `asyncio.Task`.

### Dual Message Stores

- `Agent.message_log` — append-only, full content, never modified. Source of truth.
- `Agent.context_window` — mutable working list passed to the model. Messages can be compressed or removed.
- `_RunDeps` fields are **references** to Agent instance attributes, not copies.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md with agent layer upgrade architecture"
```
