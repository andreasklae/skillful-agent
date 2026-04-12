# Skill Agent SDK

A pydantic-ai based SDK for building AI agents that discover and use skills via progressive disclosure. Agents load skill descriptions into their system prompt but only fetch full instructions on demand, keeping the context window lean. Includes a FastAPI server, thread-based inter-agent communication, subagent spawning, dual message stores (log + context window), and auto-compression.

## Core Concepts

**Progressive disclosure** — The agent starts with only skill names and descriptions in its system prompt. When it calls `use_skill`, the full instructions are loaded. This scales skills to hundreds without context bloat.

**Threads** — All communication (user → agent, agent → subagent, external → agent) flows through named threads. Each thread has an event log. The `"main"` thread mirrors the agent's context window.

**Dual stores** — `message_log` is append-only (source of truth). `context_window` is mutable and fed to the model; entries can be compressed when tokens exceed a threshold.

**Subagents** — Spawned via `spawn_agent` tool. Bidirectional: parent sends via `reply_to_thread`, subagent posts back via `thread.send()`. Notifications automatically trigger the parent to process the response.

## Architecture

```
skills/                          skill_agent/ (the SDK)
  my_skill/                        agent.py              agent loop, run queue, event stream
    SKILL.md                       models.py             Pydantic models, event types
    scripts/                       messages.py           Message, SourceContext hierarchy
    references/                    threads.py            Thread, ThreadRegistry, ThreadMessage
    assets/                        thread_tools.py       read_thread, reply_to_thread, spawn_agent
                                   skill_tools.py        use_skill, run_script, manage_todos
                                   context_tools.py      compress_message, retrieve_message
                                   registry.py           SKILL.md discovery + parsing
                                   user_prompt_files.py  file attachments (images, PDFs)

native-skills/                   Built-in skills (bundled with SDK)
  learner/                         Meta-skill for acquiring new skills

server/                          HTTP API (FastAPI)
  routes/
    runs.py                      POST /run, GET /runs/subscribe
    threads.py                   GET/POST /threads, GET /threads/subscribe
    skills.py                    GET /skills, POST /skills/upload
    health.py                    GET /health
  services/
    sse.py                       SSE envelope formatting
```

## Quick Start

```bash
uv sync

# Set your API key in .env
echo 'API_KEY=your-key-here' > .env

# Run the example CLI agent
uv run Example.py

# Or run the HTTP server
uv sync --extra server
uv run run_server.py
```

## Installation & Setup

**Core SDK**
```bash
uv sync
```

**With optional features**
```bash
uv sync --extra pdf      # PDF text extraction (pdfplumber)
uv sync --extra examples # Example skill dependencies (wikipedia-api)
uv sync --extra server   # FastAPI server (uvicorn, fastapi, azure identity)
```

**Run tests**
```bash
uv run pytest tests/ -v   # 70 tests
```

## Usage — Basic

```python
from pathlib import Path
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from skill_agent import Agent, TextDeltaEvent, ToolCallEvent, TodoUpdateEvent

model = OpenAIChatModel("gpt-4o", provider=OpenAIProvider(api_key="your-key"))
agent = Agent(model=model, skills_dir=Path("skills"))

# Blocking call — returns when done
result = agent.run("What is the speed of light?")
print(result.answer)
print(result.activated_skills)    # List of skill names used
print(result.usage.input_tokens)  # Token usage
```

**The agent maintains conversation state across `run()` calls on the same instance.** Call `agent.clear_conversation()` to reset.

## Usage — Streaming

```python
import asyncio

async def stream_to_cli():
    async for event in agent.run_stream("What is the speed of light?"):
        if isinstance(event, TextDeltaEvent):
            print(event.content, end="", flush=True)
        elif isinstance(event, ToolCallEvent):
            print(f"\n[tool] {event.name}: {event.activity or ''}")
        elif isinstance(event, TodoUpdateEvent):
            for item in event.items:
                print(f"  - [{item.status}] {item.content}")

asyncio.run(stream_to_cli())
```

**Event types**: `TextDeltaEvent`, `ToolCallEvent`, `TodoUpdateEvent`, `ToolResultEvent`, `RunCompleteEvent`, `ClientFunctionRequestEvent`, `SkillLoadedEvent`.

## File Attachments

**Pass files to a run** — they are inlined into the prompt message.

```python
from pathlib import Path

# Text files are inlined as strings
result = agent.run("Summarise this data.", files=[Path("data.csv")])

# Images are sent as vision inputs
result = agent.run("Describe this photo.", files=[Path("photo.jpg")])

# PDFs extracted as text (requires: uv sync --extra pdf)
result = agent.run("Summarise this contract.", files=[Path("contract.pdf")])
```

**On-demand reads** — let the agent request files during a run via the `read_user_file` tool.

```python
from skill_agent import AgentConfig

agent = Agent(
    model=model,
    skills_dir=Path("skills"),
    config=AgentConfig(user_file_roots=[Path("workspace")]),
)

# Agent can now call read_user_file("data.csv") during execution
result = agent.run("Analyze all CSVs in the workspace")
```

## Threads & Communication

Every agent has a `thread_registry` for all communication — user prompts, subagent replies, external messages.

**Main thread** (`"main"`) — User conversation. Mirrors the agent's context window. Always present.

**Subagent threads** — Created by agent via `spawn_agent` tool. Bidirectional: parent calls `reply_to_thread()`, subagent posts back via `thread.send()`. Notifications are automatic.

```python
registry = agent.thread_registry

# Access main thread
main = registry.get("main")
for msg in main.messages:
    print(f"[{msg.role.value}] {msg.content[:80]}")

# List all threads
for name, thread in registry.items():
    print(f"{name}: {len(thread.messages)} messages")
```

### Message Event Logs

Every `ThreadMessage.events` is an activity log for the run that produced it:

```python
for msg in main.messages:
    if msg.events:
        # Find tool calls in this message's run
        tool_calls = [e for e in msg.events if e["type"] == "tool_call"]
        
        # Get final todo list from this run
        final_todos = next(
            (e["items"] for e in reversed(msg.events) if e["type"] == "todo_update"),
            [],
        )
        
        # Get token usage for this run
        usage = next(
            (e["usage"] for e in msg.events if e["type"] == "run_complete"),
            None,
        )
```

| Event type | Key fields | Notes |
|---|---|---|
| `text_delta` | `content` | One streaming token |
| `tool_call` | `name`, `args`, `activity` | When a tool is invoked |
| `tool_result` | `name` | Tool execution complete |
| `todo_update` | `items[]` | Full todo list snapshot |
| `skill_loaded` | `name` | Skill instructions fetched |
| `run_complete` | `usage.input_tokens`, `usage.output_tokens` | Final tokens for run |
| `client_function_request` | `requests[]` | Client-side functions needed |

**Note:** `events` is empty on participant (inbound) messages — only agent-generated messages have event logs.

## Run Queue & Subscriptions

Runs are processed sequentially. Multiple sources can enqueue concurrently — user prompts, subagent notifications, external messages.

```python
# Queue a run without blocking
run_id = await agent.enqueue_run("Summarise the latest research")

# Subscribe to a specific run's events
async for envelope in agent.subscribe_run(run_id):
    event_type = envelope.get("event", {}).get("type")
    print(f"{envelope['type']}: {event_type}")

# Subscribe to all runs (live monitoring)
async for envelope in agent.subscribe_all_runs():
    print(f"Run {envelope['run_id']} from {envelope['source']}")
```

**Run sources**: `"api"` (external), `"thread"` (subagent notification), `"user"` (direct call).

## Message Stores

The agent maintains **two** message stores:

- **`agent.message_log`** — Append-only, full content. Source of truth.
- **`agent.context_window`** — Mutable working set sent to the model. Can be compressed.

**Auto-compression** triggers when `input_tokens` exceeds `AgentConfig.context_compression_threshold` (default: 100k tokens). Older messages are summarized and replaced with a single compressed entry.

```python
# Manually compress a message
await agent.compress_message(message_index=0)

# Replace entire context window with summary
await agent.compress_all()

# Retrieve an archived message from log
original = await agent.retrieve_message(message_index=2)
```

## HTTP Server

```bash
uv sync --extra server
uv run run_server.py
```

Launches a FastAPI app on `http://localhost:8000`.

### Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/run` | Queue a run with prompt; stream events as SSE |
| `GET` | `/runs/subscribe` | SSE stream of all run lifecycle events |
| `GET` | `/threads` | List all active threads |
| `GET` | `/threads/{name}` | Fetch a thread with all messages + event logs |
| `GET` | `/threads/subscribe` | SSE stream of thread messages (all threads) |
| `POST` | `/threads/{name}/messages` | Send message to thread (creates if missing) |
| `GET` | `/skills` | List registered skills (name, description) |
| `POST` | `/skills/upload` | Upload skill as `.zip` archive |
| `GET` | `/health` | Health check |

### Run Stream (SSE)

```bash
curl -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{"prompt": "What is the speed of light?"}'
```

Response (Server-Sent Events):

```
event: run_queued
data: {"type":"run_queued","run_id":"uuid","source":"api","prompt_preview":"..."}

event: run_started
data: {"type":"run_started","run_id":"uuid"}

event: agent_event
data: {"type":"agent_event","run_id":"uuid","event":{"type":"tool_call","name":"use_skill","args":{...}}}

event: agent_event
data: {"type":"agent_event","run_id":"uuid","event":{"type":"text_delta","content":"The speed"}}

event: agent_event
data: {"type":"agent_event","run_id":"uuid","event":{"type":"run_complete","usage":{"input_tokens":1200,"output_tokens":85}}}
```

### Thread Stream (SSE)

```bash
curl http://localhost:8000/threads/subscribe
```

Response:

```
event: thread_message
data: {
  "id":"msg-uuid",
  "timestamp":"2026-04-12T...",
  "thread_name":"main",
  "role":"agent",
  "content":"...",
  "events":[...]
}
```

## Frontend Integration Guide

A complete walkthrough for building a chat UI with the server.

### Basic Chat Interface

**1. Queue a run and stream results**

```javascript
async function submitPrompt(userMessage) {
  const response = await fetch('/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt: userMessage })
  })

  // response.body is a ReadableStream (SSE)
  const reader = response.body.getReader()
  const decoder = new TextDecoder()

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    const chunk = decoder.decode(value)
    const lines = chunk.split('\n')

    for (const line of lines) {
      if (line.startsWith('event: ')) {
        const eventType = line.slice(7)
        continue
      }
      if (line.startsWith('data: ')) {
        const data = JSON.parse(line.slice(6))
        handleEvent(data)
      }
    }
  }
}

function handleEvent(envelope) {
  if (envelope.type === 'run_queued') {
    console.log('Run queued:', envelope.run_id)
    showLoadingIndicator()
  }

  if (envelope.type === 'run_started') {
    console.log('Run started')
  }

  if (envelope.type === 'agent_event') {
    const event = envelope.event
    if (event.type === 'text_delta') {
      appendToChat(event.content)  // Stream text as it arrives
    } else if (event.type === 'tool_call') {
      showToolIndicator(event.name, event.activity)
    } else if (event.type === 'run_complete') {
      hideLoadingIndicator()
      console.log('Tokens:', event.usage)
    }
  }
}
```

**2. Live run monitoring (background)**

```javascript
// Monitor all runs across the app (for status bars, activity feeds, etc.)
const runMonitor = new EventSource('/runs/subscribe')

runMonitor.addEventListener('run_queued', e => {
  const { run_id, source } = JSON.parse(e.data)
  updateActivityFeed(`Run ${run_id} queued from ${source}`)
})

runMonitor.addEventListener('agent_event', e => {
  const { run_id, event } = JSON.parse(e.data)
  if (event.type === 'run_complete') {
    updateActivityFeed(`Run ${run_id} complete (${event.usage.output_tokens} tokens)`)
  }
})
```

**3. Multi-thread chat (subagents)**

```javascript
// Listen for all thread activity across the app
const threadMonitor = new EventSource('/threads/subscribe')

threadMonitor.addEventListener('thread_message', e => {
  const msg = JSON.parse(e.data)
  console.log(`[${msg.thread_name}] ${msg.role}: ${msg.content}`)

  // Each message has an activity log
  if (msg.events) {
    const toolCalls = msg.events.filter(e => e.type === 'tool_call')
    const usage = msg.events.find(e => e.type === 'run_complete')?.usage
    renderThreadMessage(msg, { toolCalls, usage })
  }
})

// Read a specific thread's history
async function loadThreadHistory(threadName) {
  const res = await fetch(`/threads/${threadName}`)
  const { messages } = await res.json()
  return messages
}

// Send a message to a thread (triggers subagent if it's a subagent thread)
async function replyToThread(threadName, content) {
  await fetch(`/threads/${threadName}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content })
  })
}
```

**4. List & upload skills**

```javascript
// Show available skills in a dropdown or UI
async function loadSkills() {
  const res = await fetch('/skills')
  const { skills } = await res.json()
  return skills  // Array of { name, description }
}

// Upload a new skill (drag-and-drop)
async function uploadSkill(zipFile) {
  const formData = new FormData()
  formData.append('file', zipFile)
  
  const res = await fetch('/skills/upload', {
    method: 'POST',
    body: formData
  })
  const { skill, message } = await res.json()
  console.log(`Uploaded skill: ${skill.name}`)
}
```

**5. Health checks**

```javascript
async function checkServerHealth() {
  try {
    const res = await fetch('/health')
    const { status, version } = await res.json()
    return status === 'ok'
  } catch {
    return false
  }
}

// Poll for readiness on app startup
async function waitForServer(maxAttempts = 10) {
  for (let i = 0; i < maxAttempts; i++) {
    if (await checkServerHealth()) return true
    await new Promise(r => setTimeout(r, 500))
  }
  throw new Error('Server did not become ready')
}
```

### React Example: Complete Chat Component

```jsx
import { useState, useEffect, useRef } from 'react'

export function ChatUI() {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [skills, setSkills] = useState([])

  // Load available skills on mount
  useEffect(() => {
    fetch('/skills')
      .then(r => r.json())
      .then(({ skills }) => setSkills(skills))
  }, [])

  async function handleSubmit(e) {
    e.preventDefault()
    if (!input.trim()) return

    // Add user message to UI
    setMessages(prev => [...prev, { role: 'user', content: input }])
    setInput('')
    setIsLoading(true)

    const response = await fetch('/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt: input })
    })

    let currentResponse = ''

    const reader = response.body.getReader()
    const decoder = new TextDecoder()

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      const chunk = decoder.decode(value)
      for (const line of chunk.split('\n')) {
        if (!line.startsWith('data: ')) continue

        const data = JSON.parse(line.slice(6))
        if (data.type === 'agent_event') {
          const event = data.event
          if (event.type === 'text_delta') {
            currentResponse += event.content
            // Update UI in real-time
            setMessages(prev => {
              const last = prev[prev.length - 1]
              if (last?.role === 'agent') {
                return [...prev.slice(0, -1), { ...last, content: currentResponse }]
              }
              return [...prev, { role: 'agent', content: currentResponse }]
            })
          } else if (event.type === 'tool_call') {
            console.log(`Using tool: ${event.name}`)
          } else if (event.type === 'run_complete') {
            console.log(`Tokens: ${event.usage.input_tokens} → ${event.usage.output_tokens}`)
          }
        }
      }
    }

    setIsLoading(false)
  }

  return (
    <div className="chat-container">
      <div className="skills-bar">
        <p>Available skills:</p>
        {skills.map(s => (
          <span key={s.name} title={s.description}>
            {s.name}
          </span>
        ))}
      </div>

      <div className="messages">
        {messages.map((msg, i) => (
          <div key={i} className={`message ${msg.role}`}>
            {msg.content}
          </div>
        ))}
      </div>

      <form onSubmit={handleSubmit}>
        <input
          value={input}
          onChange={e => setInput(e.target.value)}
          placeholder="Ask the agent..."
          disabled={isLoading}
        />
        <button type="submit" disabled={isLoading}>
          Send
        </button>
      </form>
    </div>
  )
}
```

### Common Patterns

**Streaming text with typing effect**
```javascript
async function streamTextWithDelay(text) {
  for (const char of text) {
    document.getElementById('output').textContent += char
    await new Promise(r => setTimeout(r, 10))  // 10ms per char
  }
}
```

**Display tool usage**
```javascript
function showToolUsage(event) {
  const toolName = event.name
  const activity = event.activity || 'executing'
  return `🔧 ${toolName}: ${activity}`
}
```

**Display token usage**
```javascript
function formatTokens(usage) {
  return `${usage.input_tokens} → ${usage.output_tokens} (${
    usage.input_tokens + usage.output_tokens
  } total)`
}
```

**Error handling**
```javascript
async function safeFetch(url, options = {}) {
  try {
    const res = await fetch(url, options)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return await res.json()
  } catch (err) {
    console.error(`Request failed: ${err.message}`)
    throw err
  }
}
```

### Client Example (JavaScript)

```javascript
// Subscribe to run events
const runStream = new EventSource('/runs/subscribe')
runStream.addEventListener('agent_event', e => {
  const { run_id, event } = JSON.parse(e.data)
  if (event.type === 'text_delta') {
    document.body.innerHTML += event.content
  } else if (event.type === 'tool_call') {
    console.log('Tool:', event.name, event.args)
  } else if (event.type === 'run_complete') {
    console.log('Done. Tokens:', event.usage)
  }
})

// Subscribe to thread messages
const threadStream = new EventSource('/threads/subscribe')
threadStream.addEventListener('thread_message', e => {
  const msg = JSON.parse(e.data)
  console.log(`[${msg.thread_name}] ${msg.role}: ${msg.content}`)
})
```

## Built-in Tools

All tools are automatically registered. The agent calls them during execution.

| Tool | Purpose |
|---|---|
| `use_skill(name)` | Load a skill's full instructions (progressive disclosure) |
| `manage_todos(action, ...)` | Plan and track internal task list (`add`, `update`, `complete`) |
| `read_reference(skill, path)` | Read a document from skill's `references/` directory |
| `run_script(skill, script, **kwargs)` | Execute Python script from skill's `scripts/` directory |
| `read_user_file(path)` | Read file from `AgentConfig.user_file_roots` (if configured) |
| `call_client_function(name, **kwargs)` | Request client-side function execution |
| `read_thread(name)` | Fetch full thread with all messages |
| `reply_to_thread(name, content)` | Send message to thread; triggers subagent run |
| `archive_thread(name)` | Mark thread as archived (hidden from active list) |
| `spawn_agent(skill_dir, config)` | Spawn a subagent wired to a new thread |
| `compress_message(index)` | Summarize a context window message |
| `retrieve_message(index)` | Restore a message from the full log |
| `compress_all()` | Replace entire context window with a summary |

## Skill Structure

Each skill is a directory with a `SKILL.md` file and optional bundled resources.

```
my-skill/
├── SKILL.md                  # Required. YAML frontmatter + markdown body
├── client_functions.json     # Optional. Functions executed on client
├── permissions.yaml          # Optional. Write permissions (agent cannot overwrite)
├── scripts/                  # Optional. Python scripts (runnable via run_script)
├── references/               # Optional. Documentation (readable via read_reference)
└── assets/                   # Optional. Templates, icons, etc.
```

### SKILL.md Format

```yaml
---
name: skill-name
description: >
  One-line description shown in system prompt.
  Only this is loaded initially (progressive disclosure).
---

# Skill Name

Full markdown instructions. Loaded only when `use_skill` is called.

## Sections

- Reference documentation
- Examples
- Constraints
- Integration notes
```

### Client Functions

Skills can declare functions that run on the client, not the agent. Example:

```json
{
  "functions": [
    {
      "name": "open_file_dialog",
      "description": "Open file picker on client",
      "parameters": {
        "type": "object",
        "properties": {
          "filter": { "type": "string" }
        }
      }
    }
  ]
}
```

Agent calls via `call_client_function("open_file_dialog", filter="*.csv")`. SDK emits `ClientFunctionRequestEvent`; client handles execution.

### Permissions

`permissions.yaml` gates write operations. Agent can create but never overwrite.

```yaml
allow:
  - path: scripts/
  - path: references/data.json

deny:
  - path: permissions.yaml  # Agent cannot modify this
  - path: SKILL.md          # Usually locked down
```

## Configuration

```python
from skill_agent import Agent, AgentConfig
from pathlib import Path

agent = Agent(
    model=model,
    skills_dir=Path("skills"),
    config=AgentConfig(
        # Agent behavior
        max_tokens=4096,              # Max tokens per run
        max_turns=64,                 # Max agentic loops per run
        
        # System prompt
        system_prompt_extra="...",    # Extra context appended to system prompt
        
        # File access (optional)
        user_file_roots=[Path("data")],  # Directories agent can read
        max_user_file_read_chars=15000,  # Max chars per file read
        
        # Context window (auto-compression)
        context_compression_threshold=100_000,  # Trigger compression at this token count
    ),
)
```

## Testing

```bash
uv run pytest tests/ -v
```

70 tests covering:
- Progressive skill disclosure
- Thread communication & subagent spawning
- Message store & compression
- Run queue
- Server endpoints (FastAPI)
- Event serialization
