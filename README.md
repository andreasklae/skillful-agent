# Skill Agent SDK

A pydantic-ai based SDK for building AI agents that discover and use skills via progressive disclosure. Includes a FastAPI server, thread-based inter-agent communication, and subagent spawning.

## How it works

The agent loads skill **descriptions** into its system prompt, but only fetches the full instructions when it decides a skill is relevant. This keeps the context window lean regardless of how many skills are registered.

```
skills/                          skill_agent/ (the SDK)
  my_skill/                        agent.py          — agent loop, run queue, event stream
    SKILL.md                       models.py         — data models and event types
    scripts/                       messages.py       — Message model, SourceContext hierarchy
    references/                    threads.py        — Thread, ThreadRegistry, ThreadMessage
    assets/                        thread_tools.py   — read_thread, reply_to_thread, spawn_agent
                                   skill_tools.py    — use_skill, run_script, manage_todos, etc.
                                   context_tools.py  — compress_message, retrieve_message, etc.
                                   registry.py       — discovers SKILL.md files on disk
                                   user_prompt_files.py — file attachments (images, PDFs, text)

server/                          HTTP API (FastAPI)
  routes/
    runs.py                      POST /run, GET /runs/subscribe
    threads.py                   GET/POST /threads, GET /threads/subscribe
    skills.py                    GET /skills, POST /skills/upload
    health.py                    GET /health
  services/
    sse.py                       SSE formatting
```

## Quick start

```bash
uv sync

# Set your API key in .env
echo 'API_KEY=your-key-here' > .env

# Run the example CLI agent
uv run Example.py

# Run the HTTP server
uv sync --extra server
uv run run_server.py
```

## Usage

```python
import asyncio
from pathlib import Path

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from skill_agent import Agent, TextDeltaEvent, ToolCallEvent, TodoUpdateEvent

model = OpenAIChatModel("gpt-4o", provider=OpenAIProvider(api_key="your-key"))
agent = Agent(model=model, skills_dir=Path("skills"))


# ── Blocking ──────────────────────────────────────────────────────────
result = agent.run("What is the speed of light?")

print(result.answer)
print(result.activated_skills)    # which skills were loaded
print(result.usage.input_tokens)  # token usage

tool_calls = [e for e in result.events if isinstance(e, ToolCallEvent)]
todo_states = [e for e in result.events if isinstance(e, TodoUpdateEvent)]


# ── Streaming ─────────────────────────────────────────────────────────
async def stream_to_cli():
    async for event in agent.run_stream("What is the speed of light?"):
        if isinstance(event, TextDeltaEvent):
            print(event.content, end="", flush=True)
        elif isinstance(event, ToolCallEvent):
            print(f"\n[tool] {event.name}: {event.activity or ''}")
        elif isinstance(event, TodoUpdateEvent):
            for item in event.items:
                print(f"  - [{item.status}] {item.content}")
    print()

asyncio.run(stream_to_cli())
```

The agent remembers the conversation across calls on the same instance. Call `clear_conversation()` to start fresh.

## Attaching files

```python
# Text files (.csv, .json, .py, .md, etc.) are inlined in the prompt
result = agent.run("Summarise the dataset.", files=[Path("data/sample.csv")])

# Images (.jpg, .png, .webp, …) are sent as vision inputs
result = agent.run("What is in this photo?", files=[Path("photos/IMG_001.jpg")])

# PDFs — install the extra first: uv sync --extra pdf
result = agent.run("Summarise this contract.", files=[Path("contract.pdf")])
```

### On-demand file reads

Set `AgentConfig.user_file_roots` to let the agent request files itself during a run:

```python
agent = Agent(
    model=model,
    skills_dir=Path("skills"),
    config=AgentConfig(user_file_roots=[Path("data/workspace")]),
)
```

## Thread system

Every agent has a `thread_registry`. All inter-agent and external communication flows through named threads.

**The main thread** (`"main"`) is the primary user conversation. It mirrors the agent's context window and is always present.

**Subagent threads** are created by the agent via the `spawn_agent` tool. They are bidirectional channels: the parent posts via `reply_to_thread`, the subagent posts back via `thread.send()`, and each side is notified automatically.

```python
registry = agent.thread_registry

main = registry.get("main")
for msg in main.messages:
    print(f"[{msg.role.value}] {msg.content[:80]}")
```

### Message event logs

Every `ThreadMessage` has an `events` field — a list of serialized `AgentEvent` dicts for the run that produced the message. This includes tool calls, todo updates, skill loads, and token usage.

```python
for msg in main.messages:
    if msg.events:
        tool_calls = [e for e in msg.events if e["type"] == "tool_call"]
        final_todos = next(
            (e["items"] for e in reversed(msg.events) if e["type"] == "todo_update"),
            [],
        )
        usage = next(
            (e["usage"] for e in msg.events if e["type"] == "run_complete"),
            None,
        )
```

| `events[*].type` | Key fields |
|---|---|
| `tool_call` | `name`, `args`, `activity` |
| `tool_result` | `name` |
| `todo_update` | `items[]` — full todo list snapshot |
| `text_delta` | `content` — one streaming token |
| `run_complete` | `usage.input_tokens`, `usage.output_tokens` |
| `client_function_request` | `requests[]` |

`events` is empty on participant (inbound) messages.

## Run queue

Runs are processed sequentially from an async queue. Multiple sources can enqueue concurrently — user prompts, subagent reply notifications, external thread messages.

```python
# Queue without waiting for completion
run_id = await agent.enqueue_run("Summarise the latest research")

# Subscribe to a specific run's events
async for envelope in agent.subscribe_run(run_id):
    print(envelope["type"], envelope.get("event", {}).get("type"))

# Subscribe to all runs (background monitoring)
async for envelope in agent.subscribe_all_runs():
    print(envelope["run_id"], envelope["source"])
```

## Message stores

- **`agent.message_log`** — append-only, full content. Source of truth for the entire conversation.
- **`agent.context_window`** — mutable working set for the model. Entries can be compressed.

Auto-compression triggers when `input_tokens` exceeds `AgentConfig.context_compression_threshold` (default 100k).

## HTTP server

```bash
uv sync --extra server
uv run run_server.py
```

### Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/run` | Queue a run; returns SSE stream of its events |
| `GET` | `/runs/subscribe` | SSE stream of all runs (background + foreground) |
| `GET` | `/threads` | List active threads |
| `GET` | `/threads/subscribe` | SSE stream of all thread activity |
| `GET` | `/threads/{name}` | Full thread with messages and per-message event logs |
| `POST` | `/threads/{name}/messages` | Send a message to a thread (creates thread if missing) |
| `GET` | `/skills` | List registered skills |
| `POST` | `/skills/upload` | Upload a skill as a `.zip` archive |
| `GET` | `/health` | Health check |

### SSE — run stream (`POST /run`)

```
event: run_queued
data: {"type":"run_queued","run_id":"...","source":"api","prompt_preview":"..."}

event: run_started
data: {"type":"run_started","run_id":"...",...}

event: tool_call
data: {"type":"agent_event","run_id":"...","event":{"type":"tool_call","name":"use_skill","args":{...},"activity":"Loading skill"}}

event: todo_update
data: {"type":"agent_event","run_id":"...","event":{"type":"todo_update","items":[...]}}

event: text_delta
data: {"type":"agent_event","run_id":"...","event":{"type":"text_delta","content":"Hello"}}

event: run_complete
data: {"type":"agent_event","run_id":"...","event":{"type":"run_complete","usage":{"input_tokens":1200,"output_tokens":85}}}
```

### SSE — thread stream (`GET /threads/subscribe`)

```
event: thread_message
data: {"id":"...","timestamp":"...","role":"agent","content":"...","thread_name":"main","events":[...]}
```

The `events` array carries the full activity log for that turn.

### JavaScript example

```javascript
// Live run stream
const source = new EventSource('/runs/subscribe')
source.addEventListener('text_delta',   e => appendText(JSON.parse(e.data).event.content))
source.addEventListener('todo_update',  e => renderTodos(JSON.parse(e.data).event.items))
source.addEventListener('tool_call',    e => logTool(JSON.parse(e.data).event))
source.addEventListener('run_complete', e => console.log('done', JSON.parse(e.data).event.usage))

// Thread activity (main + subagent threads)
const threads = new EventSource('/threads/subscribe')
threads.addEventListener('thread_message', e => {
  const msg = JSON.parse(e.data)
  // msg.thread_name, msg.role, msg.content, msg.events[]
  renderThreadMessage(msg)
})
```

## Built-in tools

| Tool | File | Purpose |
|---|---|---|
| `use_skill` | skill_tools.py | Load a skill's full instructions by name |
| `manage_todos` | skill_tools.py | Plan and track an internal task list |
| `read_reference` | skill_tools.py | Read a doc from a skill's `references/` directory |
| `run_script` | skill_tools.py | Run a Python script from a skill's `scripts/` directory |
| `call_client_function` | skill_tools.py | Request execution of a client-declared function |
| `read_user_file` | skill_tools.py | *(Conditional)* Read files under `AgentConfig.user_file_roots` |
| `compress_message` | context_tools.py | Compress a context window message to a summary |
| `retrieve_message` | context_tools.py | Restore a compressed message from the log |
| `compress_all` | context_tools.py | Replace entire context window with a summary |
| `read_thread` | thread_tools.py | Read full thread contents by name |
| `reply_to_thread` | thread_tools.py | Send one message to a thread (triggers subagent run) |
| `archive_thread` | thread_tools.py | Archive a thread (removes from active list) |
| `spawn_agent` | thread_tools.py | Spawn a subagent wired to a new thread |

## Skill structure

```
skill-name/
├── SKILL.md                # YAML frontmatter (name, description) + markdown body
├── client_functions.json   # Client-side function declarations (optional)
├── permissions.yaml        # Client-controlled permission rules (optional, agent cannot overwrite)
├── scripts/                # Python scripts runnable via run_script
├── references/             # Docs readable via read_reference
└── assets/                 # Templates, icons, etc.
```

## Client-side functions

Skills can declare functions that execute on the client, not inside the agent. The agent calls them via `call_client_function`; the SDK emits a `ClientFunctionRequestEvent`; the client handles execution.

`permissions.yaml` gates write operations. The agent can create this file but never overwrite it.

## Configuration

```python
from skill_agent import Agent, AgentConfig

agent = Agent(
    model=model,
    skills_dir=Path("skills"),
    config=AgentConfig(
        max_tokens=4096,
        max_turns=64,
        system_prompt_extra="You are a helpful assistant.",
        user_file_roots=[Path("data/workspace")],
        max_user_file_read_chars=15000,
        context_compression_threshold=100_000,
    ),
)
```

## Testing

```bash
uv run pytest tests/ -v   # 70 tests
```
