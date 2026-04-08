# Agent Layer Upgrade — Design Specification

## Overview

Upgrade the skill-agent SDK to support structured message logging, context window management, subagent spawning, and a unified inbox/thread system. All stores are in-memory. No database persistence, no new UI surfaces — infrastructure only.

Approach B: new modules alongside existing code, with tool registration extracted into focused files to keep each module readable.

## Clarifications from design discussion

- `solve()` in the original spec maps to the existing `run()`/`run_stream()` API. No new entry point.
- `SubAgent` is a subclass of `Agent`, shares the parent's model, does not stream events to consumers — communicates exclusively via inbox.
- Context compression auto-trigger uses `input_tokens` reported by pydantic-ai after each model call. Reactive (triggers on next turn), no new dependencies.

---

## 1. Message Model & SourceContext

**File: `skill_agent/messages.py`**

### MessageType enum

```python
class MessageType(str, Enum):
    user = "user"
    agent = "agent"
    tool_call = "tool_call"
    tool_result = "tool_result"
    reasoning = "reasoning"
    subagent = "subagent"
    system = "system"
```

### Message

```python
class Message(BaseModel):
    id: str                          # auto-generated UUID
    timestamp: datetime              # auto-set to utcnow
    type: MessageType
    source_context: SourceContext | None = None
    content: Any                     # payload — varies by type
    summary: str | None = None       # populated when compressed
```

Tool call messages store only the tool name + a short human-readable description. Arguments and full results are not stored on the tool_call message. The result lives in a separate `tool_result` message.

### SourceContext hierarchy

```python
class SourceContext(BaseModel):
    origin: str                      # channel: "ui", "email", "subagent", "slack"
    sender: str | None = None
    interaction_id: str              # auto-generated UUID if not provided

class UIContext(SourceContext):
    origin: str = "ui"

class EmailContext(SourceContext):
    origin: str = "email"
    subject: str
    thread_id: str | None = None
    reply_to: str | None = None

class SubAgentContext(SourceContext):
    origin: str = "subagent"
    subagent_id: str
    parent_interaction_id: str
```

Designed for extension — new channels (Slack, SMS) are added as subclasses without modifying existing ones.

---

## 2. Dual Message Stores

Both stores live on the Agent instance.

### `message_log: list[Message]`

Append-only. Every message and step is written here in full at the moment it occurs. Never modified after append. Source of truth.

### `context_window: list[Message]`

The live working list. Entries can be compressed (content replaced with summary) or removed entirely. This is what gets serialized into pydantic-ai's `message_history` before each model call.

### Relationship to pydantic-ai

The existing `self._conversation_messages` (pydantic-ai's raw ModelMessage list) becomes internal plumbing. Before each model call, `context_window` is serialized into pydantic-ai's message_history format:
- Normal messages: serialized with full content
- Compressed messages (summary set, content cleared): serialized as summary text only

### Subagent message logs

Subagent message logs are stored as named forks of the parent's log, linked by `thread_id`. The parent stores archived logs in `self.subagent_logs: dict[str, list[Message]]`. While a subagent is alive, the parent accesses its live log via the SubAgent instance. On completion, the log is copied to the archive.

---

## 3. Context Management Tools

**File: `skill_agent/context_tools.py`**

Exports `register_context_tools(runner)` which registers three tools. Available to both main agent and all subagents.

### compress_message(id: str, summary: str)

- Finds the message in `context_window` by ID
- Sets `summary` to the provided string, sets `content` to `None`
- Full content remains in `message_log`
- Returns confirmation or error if ID not found

### retrieve_message(id: str)

- Looks up the full message from `message_log` by ID
- Finds the compressed entry in `context_window` and restores original content, clears summary
- If the message was removed entirely from `context_window`, re-inserts at original position (by timestamp ordering)
- Returns the restored content or error if not found

### compress_all(summary: str, instruction: str)

- Replaces the entire `context_window` with a single `system` type Message containing:
  - ID range covered (first and last message IDs)
  - The summary
  - The instruction (enough detail for cold resume without reading full log)
- Full content stays in `message_log`
- After compression, a `Message` of type `system` is appended to both stores with content stating context was compressed. This appears as a normal agent message in the conversation, not as a stream event.

### Auto-trigger

After each model call, the runtime checks `input_tokens` from pydantic-ai's usage report against `AgentConfig.context_compression_threshold`. If exceeded, the runtime calls `compress_all` directly — not as a model-suggested tool call, but as a runtime-initiated operation. The model is asked to provide the summary and instruction via a system message injected into the next turn: "Context window exceeded threshold. Provide a summary and resumption instruction, then call compress_all." If the model fails to call compress_all within that turn, the runtime forces compression with a generic summary derived from the message_log (first and last messages, active todos, most recent tool calls). This guarantees compression happens regardless of model cooperation.

---

## 4. Inbox & Thread System

**File: `skill_agent/inbox.py`**

### InboxItem

```python
class InboxItem(BaseModel):
    id: str                          # auto-UUID
    timestamp: datetime              # auto-now
    thread_id: str                   # groups related items
    subject: str                     # brief status line
    content: str
    source_context: SourceContext
    notify: bool                     # if true, triggers follow-up run
    read: bool = False
```

### Thread

Not a separate stored model for items — but carries metadata. A `Thread` is a wrapper around all `InboxItem` objects sharing a `thread_id`, returned as a sorted list. The Inbox provides `get_thread(thread_id)` which returns a `Thread` wrapper that scopes operations (write, read) to that thread.

**Thread status:** Every thread has a `status: ThreadStatus` field — an enum with values `in_progress`, `waiting_for_response`, `done`. Status is set explicitly by the agent or subagent via `write_to_thread` (which accepts an optional `status` parameter), or automatically: spawning a subagent sets the thread to `in_progress`; `delete_thread` sets it to `done` before archiving. The status is visible on `read_inbox()` output alongside the subject, so the agent can triage without opening threads.

### Inbox class

First-class object exposed as `Agent.inbox`. Agent tools and external callers use the same methods.

```
Inbox
  items: list[InboxItem]
  create_item(content, subject, thread_id?, source_context, notify) -> InboxItem
  write_to_thread(thread_id, content, subject?, source_context, notify) -> InboxItem
  read_inbox() -> list[InboxItem]          # unread items, marks them read
  read_thread(thread_id) -> list[InboxItem]
  get_thread(thread_id) -> Thread          # scoped wrapper
  dismiss_item(id)
  delete_thread(thread_id)
  pending_notifications() -> bool
  subscribe() -> AsyncGenerator[InboxItem] # readable stream for UI
```

`create_item` auto-generates a `thread_id` if not provided (starts a new thread). `write_to_thread` requires an existing thread.

`subscribe()` yields new items as they arrive via `asyncio.Queue` internally.

### Inbox notification behavior

- `notify: True` item arrives during a running `run()`/`run_stream()`: written to inbox silently
- After the current run completes: runtime checks `inbox.pending_notifications()`, triggers `run("inbox updated")` if any exist
- No run active when item arrives: notification fires immediately
- `notify: False` items never trigger a run

### Thread read compression (automatic)

When the agent's `read_thread` tool is called, the inbox checks `context_window` for previous reads of that same `thread_id`. Older reads are compressed to `{thread_id, subject}`. Only the latest read stays in full. Happens inside the tool handler.

---

## 5. Inbox Tools

**File: `skill_agent/inbox_tools.py`**

Exports `register_inbox_tools(runner)` and `register_spawn_tools(runner)`.

### Agent inbox tools

- **`read_inbox()`** — returns unread items with subjects only, marks them as read, logs to message_log
- **`read_thread(thread_id)`** — returns full thread contents, triggers automatic compression of previous reads of that thread in context_window
- **`write_to_thread(thread_id, content, notify, status?)`** — writes a new item to a thread. Target inbox is resolved automatically: if `thread_id` matches an active subagent in `_active_subagents`, the item is written to that subagent's `inbox`. Otherwise it is written to the agent's own inbox (for self-threads like external conversations). This resolution is transparent to the model — it just provides a `thread_id`.
- **`forward_thread_item(item_id, to_subagent_id)`** — forwards an inbox item to another agent's inbox without loading content into the forwarding agent's context window. `to_subagent_id` is the thread_id that identifies the target subagent; resolved via `_active_subagents` to get the SubAgent instance, then the item is written to that subagent's `inbox`. Forwarding agent sees only the subject. Recipient receives the item with original source_context preserved and a forwarding note appended. Returns error if `to_subagent_id` does not match an active subagent.
- **`dismiss_inbox_item(id)`** — marks an item dismissed without processing
- **`delete_thread(thread_id)`** — deletes thread and archives the associated subagent log. Subagent detects deletion and winds down gracefully.

---

## 6. SubAgent

**File: `skill_agent/subagent.py`**

### SubAgent(Agent)

Subclass of Agent. Shares parent's model. Does not stream events — communicates via inbox.

**Constructor:**

```python
SubAgent(
    parent: Agent,
    instructions: str,
    system_prompt: str,
    tools: list,              # explicit tools for this subagent
    skills: list[str],        # skill names resolved from parent's registry
    thread_id: str,           # thread in parent's inbox
)
```

**Always-available tools** (not required in `tools` parameter):
- compress_message, retrieve_message, compress_all
- manage_todos
- read_inbox, read_thread, write_to_thread, forward_thread_item, dismiss_inbox_item, delete_thread

All other tools must be explicitly passed. SubAgent does not get `spawn_subagent` unless explicitly included.

### Lifecycle

1. Starts with `instructions` as initial prompt via internal `run()`
2. Between steps, checks own inbox for new messages
3. Writes to parent's inbox via `write_to_thread` scoped to its `thread_id`
4. Winds down when its thread is deleted by parent (inbox check returns sentinel, loop exits)
5. All status/progress posted to parent thread

### Notification discipline

Subagents must use `notify` judiciously. Guidelines baked into the subagent's system prompt:
- `notify: True` — only for: final result delivery, blocking errors that require parent intervention, or explicit requests for input
- `notify: False` — for: progress updates, intermediate findings, status changes
- Thread status should be updated on every write: `in_progress` while working, `waiting_for_response` when blocked on parent/external input, `done` when finished

### Async execution

The subagent loop runs as an `asyncio.Task` (via `asyncio.create_task`). The parent tracks it in `self._subagent_tasks: dict[str, asyncio.Task]`.

---

## 7. Spawn Tool

Registered alongside inbox tools in `skill_agent/inbox_tools.py`.

```python
spawn_subagent(
    instructions: str,
    system_prompt: str,
    tools: list[str],
    skills: list[str],
    blocking: bool = False,
    singleton: bool = False,
    singleton_id: str | None = None,
) -> str  # returns thread_id
```

- Creates a thread in parent's inbox with subject derived from instructions
- Instantiates SubAgent with parent's model, specified tools/skills, new thread_id
- `blocking=False`: launches as asyncio.Task, returns thread_id immediately
- `blocking=True`: parent's run awaits subagent completion, receives final result as tool return
- `singleton=True` requires `singleton_id` — an explicit string identity (e.g. `"researcher"`, `"librarian"`). The Agent tracks active singletons in `self._singleton_subagents: dict[str, str]` mapping `singleton_id` to `thread_id`. On spawn, if the `singleton_id` is already active, returns the existing `thread_id` immediately without spawning. On `delete_thread`, the entry is removed from `_singleton_subagents`. This avoids fragile string comparison of system prompts — identity is explicit and intentional.

---

## 8. Agent Integration Changes

### New Agent attributes

```python
self.inbox = Inbox()
self.message_log: list[Message] = []
self.context_window: list[Message] = []
self.subagent_logs: dict[str, list[Message]] = {}
self._active_subagents: dict[str, SubAgent] = {}
self._subagent_tasks: dict[str, asyncio.Task] = {}
self._singleton_subagents: dict[str, str] = {}  # singleton_id -> thread_id
self._running: bool = False
```

### _event_stream changes

- At entry: append user Message to message_log and context_window
- On tool call/result: append Message to both stores
- On text delta: accumulate; append full agent response as single Message on completion
- On run complete: check input_tokens against threshold for auto-compression; check inbox.pending_notifications() and schedule follow-up run if needed
- Before model call: serialize context_window into pydantic-ai message_history format

### _RunDeps additions

```python
inbox: Inbox                                    # reference to Agent.inbox (same object)
message_log: list[Message]                      # reference to Agent.message_log (same list)
context_window: list[Message]                   # reference to Agent.context_window (same list)
active_subagents: dict[str, SubAgent]           # reference to Agent._active_subagents (same dict)
context_compression_threshold: int
```

All collection fields in `_RunDeps` are **references** to the same objects on the Agent instance, not copies. This ensures subagents spawned mid-run are immediately visible to tools within that run, and message/context writes from tools update the Agent's canonical state directly.

### AgentConfig additions

```python
context_compression_threshold: int = 100_000  # input_tokens threshold
```

---

## 9. Tool Registration Refactor

Extract existing tools from `_create_runner` into focused files:

| File | Function | Tools |
|---|---|---|
| `skill_tools.py` | `register_skill_tools(runner)` | use_skill, register_skill, scaffold_skill, manage_todos, read_reference, run_script, write_skill_file, read_user_file, call_client_function |
| `context_tools.py` | `register_context_tools(runner)` | compress_message, retrieve_message, compress_all |
| `inbox_tools.py` | `register_inbox_tools(runner)` | read_inbox, read_thread, write_to_thread, forward_thread_item, dismiss_inbox_item, delete_thread |
| `inbox_tools.py` | `register_spawn_tools(runner)` | spawn_subagent |

`_create_runner` in `agent.py` becomes a slim coordinator:
1. Creates the pydantic-ai Agent with system prompt
2. Calls each `register_*_tools(runner)` function
3. Returns the runner

---

## 10. File Layout

```
skill_agent/
  __init__.py            # public API exports (add new types)
  agent.py               # Agent class, _create_runner (slim), _event_stream, system prompt
  models.py              # existing models + AgentConfig.context_compression_threshold
  messages.py            # Message, MessageType, SourceContext, UIContext, EmailContext, SubAgentContext
  inbox.py               # Inbox, InboxItem, Thread, ThreadStatus
  context_tools.py       # register_context_tools: compress_message, retrieve_message, compress_all
  inbox_tools.py         # register_inbox_tools + register_spawn_tools
  skill_tools.py         # register_skill_tools (extracted from current _create_runner)
  subagent.py            # SubAgent(Agent)
  registry.py            # unchanged
  user_prompt_files.py   # unchanged
  system_prompt.md       # updated to mention new tools
```

## Out of Scope

- Database persistence — all stores are in-memory
- Any UI beyond the existing stream interface
- Skill implementation — this is infrastructure only
