# Skill Agent SDK

A pydantic-ai based SDK for building AI agents that discover and use skills via progressive disclosure.

## How it works

The agent loads skill **descriptions** into its system prompt, but only fetches the full instructions when it decides a skill is relevant. This keeps the context window lean regardless of how many skills are registered.

```
skills/                          skill_agent/ (the SDK)
  my_skill/                        agent.py    — agent loop + built-in tools
    SKILL.md                       models.py   — all data models and event types
    scripts/                       registry.py — discovers SKILL.md files on disk
    references/
    assets/
```

At runtime:

```
Agent(model, skills_dir)
  └─ run(prompt, files=[...])        → AgentResult
  └─ run_stream(prompt, files=[...]) → AsyncGenerator[AgentEvent, ...]
  └─ clear_conversation() — forget prior turns (optional)
```

`files=` is optional: attach local paths so the model sees text (inlined), images (vision), or PDF text (see below).

The agent remembers the conversation across `run` / `run_stream` calls on the same instance. Call `clear_conversation()` when you want a fresh thread.

## Quick start

```bash
uv sync

# Set your API key in .env
echo 'API_KEY=your-key-here' > .env

uv run Example.py
```

## Usage

```python
import asyncio
from pathlib import Path

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from skill_agent import Agent, TextDeltaEvent, ToolCallEvent, TodoUpdateEvent

# Any pydantic-ai compatible model works (OpenAI, Anthropic, Azure, Gemini, etc.)
model = OpenAIChatModel("gpt-4o", provider=OpenAIProvider(api_key="your-key"))

agent = Agent(model=model, skills_dir=Path("skills"))


# ── Blocking ──────────────────────────────────────────────────────────
# Waits for the full answer. Returns AgentResult with the complete event timeline.

result = agent.run("What is the speed of light?")

print(result.answer)
print(result.activated_skills)   # which skills were loaded
print(result.usage.input_tokens) # token usage

# Filter the event timeline by type
tool_calls = [e for e in result.events if isinstance(e, ToolCallEvent)]
todo_states = [e for e in result.events if isinstance(e, TodoUpdateEvent)]


# ── Streaming ─────────────────────────────────────────────────────────
# Yields typed events in real time. The caller decides what to do with them.

async def stream_to_cli():
    async for event in agent.run_stream("What is the speed of light?"):
        if isinstance(event, TextDeltaEvent):
            print(event.content, end="", flush=True)
        elif isinstance(event, ToolCallEvent):
            print(f"[tool] {event.name}")
        elif isinstance(event, TodoUpdateEvent):
            for item in event.items:
                print(f"  - {item.content} ({item.status})")
    print()

asyncio.run(stream_to_cli())

# Same agent instance remembers context for a follow-up:
# async for event in agent.run_stream("And in miles per hour?"):
#     ...
# agent.clear_conversation()  # start over when needed
```

## Attaching files

You can pass local file paths alongside the text prompt. The SDK turns them into the shape pydantic-ai expects for your model (text in the prompt string, images as `BinaryContent` parts).

```python
from pathlib import Path

# Text files (e.g. .csv, .json, .xml, .txt, .py, .md) are read as UTF-8 and appended after your prompt.
result = agent.run(
    "Summarise the dataset.",
    files=[Path("data/sample.csv")],
)

# Images (.jpg, .png, .webp, …) are sent as vision inputs for multimodal models.
result = agent.run(
    "What is in this photo?",
    files=[Path("photos/IMG_001.jpg")],
)

# Streaming with files works the same way.
async for event in agent.run_stream("Analyse both", files=[Path("a.txt"), Path("b.png")]):
    ...
```

**PDFs:** text is extracted with [pdfplumber](https://github.com/jsvine/pdfplumber). Install the optional extra:

```bash
uv sync --extra pdf
# or: pip install 'skill-agent[pdf]'
```

**Limits:** `AgentConfig.max_attached_text_file_chars` truncates inlined text and PDF extraction (default large cap; set `None` for no limit).

**Empty prompt:** if you only attach files, a text-only run still needs content from those files; for image-only runs the SDK adds a short default line so the model knows to look at the images.

### On-demand file reads (`read_user_file`)

If you set `AgentConfig.user_file_roots` to one or more directories, the agent also gets a **`read_user_file`** tool: the model can request UTF-8 text from paths that stay under those roots (relative paths or allowed absolutes). Use this when you prefer not to load everything into the first prompt.

```python
from skill_agent import Agent, AgentConfig

agent = Agent(
    model=model,
    skills_dir=Path("skills"),
    config=AgentConfig(
        user_file_roots=[Path("data/workspace")],
        max_user_file_read_chars=15000,
    ),
)
```

The system prompt is extended automatically to mention the allowed roots.

For advanced use, the same attachment logic is available as `build_user_message` from `skill_agent` if you need to assemble a user message yourself before calling pydantic-ai directly.

## Event types

Every event has a `type` string literal field, making them easy to route in any consumer — a CLI printer, a FastAPI SSE endpoint, or a JavaScript `EventSource`.

| Event | `type` | Contents |
|---|---|---|
| `TodoUpdateEvent` | `"todo_update"` | Full current todo list |
| `ToolCallEvent` | `"tool_call"` | Tool name, args, optional `activity` (UI-oriented) |
| `ToolResultEvent` | `"tool_result"` | Tool name |
| `TextDeltaEvent` | `"text_delta"` | Incremental answer text (streamed chunk; not necessarily a single model token) |
| `RunCompleteEvent` | `"run_complete"` | Final token usage (`usage`) |
| `ClientFunctionRequestEvent` | `"client_function_request"` | One or more client function requests (see below) |

Serialize any event with `event.model_dump_json()` (Python) — same shapes you would put in an SSE `data:` line.

### Example JSON payloads

Illustrative only; real `args` come from the model. `status` on todo items is always `"pending"`, `"in_progress"`, or `"done"`.

**`todo_update`**

```json
{
  "type": "todo_update",
  "items": [
    { "id": 1, "content": "Load the relevant skill", "status": "done" },
    { "id": 2, "content": "Run the lookup script", "status": "in_progress" }
  ]
}
```

**`tool_call`** — `use_skill`

```json
{
  "type": "tool_call",
  "name": "use_skill",
  "args": { "skill_name": "wikipedia_lookup" },
  "activity": "Loading the lookup skill"
}
```

**`tool_call`** — `manage_todos`

```json
{
  "type": "tool_call",
  "name": "manage_todos",
  "args": {
    "action": "set",
    "payload": { "items": ["Plan step one", "Plan step two"] }
  },
  "activity": "Planning the steps"
}
```

**`tool_call`** — `read_reference`

```json
{
  "type": "tool_call",
  "name": "read_reference",
  "args": { "skill_name": "wikipedia_lookup", "filename": "api_notes.md" },
  "activity": "Opening bundled reference doc"
}
```

**`tool_call`** — `run_script`

```json
{
  "type": "tool_call",
  "name": "run_script",
  "args": {
    "skill_name": "wikipedia_lookup",
    "filename": "search.py",
    "args": "{\"query\": \"example\"}"
  },
  "activity": "Running bundled script"
}
```

**`tool_result`**

```json
{
  "type": "tool_result",
  "name": "run_script"
}
```

**`text_delta`**

```json
{
  "type": "text_delta",
  "content": "Here is a short answer based on the script output."
}
```

**`run_complete`**

```json
{
  "type": "run_complete",
  "usage": { "input_tokens": 1200, "output_tokens": 340 }
}
```

**`client_function_request`**

```json
{
  "type": "client_function_request",
  "requests": [
    {
      "name": "confirm_action",
      "args": { "action": "delete all records", "details": "This cannot be undone." },
      "skill_name": "my_skill",
      "awaits_user": true
    }
  ]
}
```

### Handling client function requests in your UI

When a skill declares a `client_functions.json`, the agent will emit `ClientFunctionRequestEvent`
events on the stream. **Your client is responsible for handling them** — the SDK only delivers the
request; it does not execute the function or render any UI.

The standard pattern:

```python
from skill_agent.models import ClientFunctionRequestEvent

state = {}

async for event in agent.run_stream(prompt):
    if event.type == "client_function_request":
        for req in event.requests:
            result = handle_client_function(req.name, req.args)
            if req.awaits_user:
                # Agent has stopped — send the result as the next user message
                state["pending_response"] = result

# After the stream ends, resume if there's a pending response
if "pending_response" in state:
    async for event in agent.run_stream(state["pending_response"]):
        ...  # handle normally
```

If `req.awaits_user` is `False`, the event is informational — the agent continues on its own.

**In a web UI**, translate `client_function_request` events into whatever your interface needs:
a modal dialog, a toast notification, a confirmation button, or a side-panel. The response goes
back as the user's next message.

**In a streaming API (FastAPI SSE)**, forward the event to the frontend and wait for the user's
response before resuming the agent.

---

### FastAPI SSE example

```python
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from skill_agent import Agent

app = FastAPI()
agent = Agent(model=model, skills_dir=Path("skills"))

@app.get("/stream")
async def stream(prompt: str):
    async def generate():
        async for event in agent.run_stream(prompt):
            yield f"event: {event.type}\ndata: {event.model_dump_json()}\n\n"
    return StreamingResponse(generate(), media_type="text/event-stream")
```

### JavaScript EventSource example

```javascript
const source = new EventSource(`/stream?prompt=your+question`)

source.addEventListener("text_delta",  e => appendToAnswer(JSON.parse(e.data).content))
source.addEventListener("todo_update", e => renderTodoList(JSON.parse(e.data).items))
source.addEventListener("tool_call",   e => logToolCall(JSON.parse(e.data)))
source.addEventListener("run_complete", () => source.close())
```

## Client-side functions

Skills can expose functions that execute on the **client**, not inside the agent. The agent requests them via a validated tool call; the SDK emits a `ClientFunctionRequestEvent` on the stream; the client handles execution however it wants (prompting the user, calling an API, updating local state, etc.).

This is a general-purpose escape hatch for anything that belongs outside the agent loop.

### Declaring a client function

Add a `client_functions.json` file to your skill directory:

```json
[
  {
    "name": "confirm_action",
    "description": "Ask the user to confirm a potentially destructive action before proceeding.",
    "awaits_user": true,
    "parameters": [
      { "name": "action",  "type": "string", "description": "Human-readable description of the action.", "required": true },
      { "name": "details", "type": "string", "description": "Additional context for the user.",           "required": false }
    ]
  }
]
```

Fields:

| Field | Type | Description |
|---|---|---|
| `name` | string | Unique function name within the skill |
| `description` | string | Shown to the agent so it knows when to call this |
| `awaits_user` | bool | If `true`, the agent stops and waits for the user's next message after calling |
| `parameters` | array | Declared schema — the SDK validates the agent's call against this |

### How the agent calls it

After loading the skill, the agent sees the declared functions in the `use_skill` response. When it wants to invoke one it calls the built-in `call_client_function` tool:

```
call_client_function(
    skill_name="my_skill",
    function_name="confirm_action",
    args={"action": "delete all records", "details": "This cannot be undone."}
)
```

The SDK validates the call (skill exists, function exists, required args present) before queuing the request. Invalid calls return an error string to the agent instead of emitting an event.

### Handling the event on the client

```python
from skill_agent.models import ClientFunctionRequestEvent

async for event in agent.run_stream(prompt):
    if event.type == "client_function_request":
        for req in event.requests:
            if req.name == "confirm_action":
                # req.args contains whatever the agent passed
                answer = input(f"Confirm: {req.args['action']}? [y/n] ")
                # If awaits_user=True, send the result back as the next user message
                if answer.lower() == "y":
                    await send_next_turn(agent, "Confirmed. Proceed.")
                else:
                    await send_next_turn(agent, "Cancelled. Do not proceed.")
```

If `awaits_user=True`, the agent has already stopped — your client sends a follow-up message to resume the run. If `awaits_user=False`, the agent continues on its own; the event is informational only.

Multiple functions can be requested in a single event (`event.requests` is a list).

### `client_function_request` event shape

```json
{
  "type": "client_function_request",
  "requests": [
    {
      "name": "confirm_action",
      "args": { "action": "delete all records", "details": "This cannot be undone." },
      "skill_name": "my_skill",
      "awaits_user": true
    }
  ]
}
```

### Pairing with `permissions.yaml`

For skills that gate write operations behind user approval, add a `permissions.yaml`
alongside `client_functions.json`. This file is loaded by your client application to
decide whether to prompt the user or approve silently:

```yaml
# permissions.yaml — client-controlled; agent can create but never overwrite
default_allow: false

rules:
  - domains: ["*"]
    actions: ["query", "read", "search", "list"]
    allow: true   # reads pre-approved, no prompt
  # writes fall through to default_allow: false → user is prompted
```

Load it in your client:

```python
from permissions import PermissionManifest

manifest = PermissionManifest.from_yaml(Path("skills/my_skill/permissions.yaml"))

def handle_client_function(name, args):
    if name == "request_permission":
        operation = args["operation"]
        domain = args["domain"]
        action = args["action"]
        if manifest.is_allowed(operation, domain, action):
            return f"Permission pre-approved for '{operation}'. Proceed."
        # Prompt the user, then:
        manifest.grant(operation)   # session-only grant
        return f"Permission granted for '{operation}'. Proceed."
```

**The agent cannot overwrite `permissions.yaml` once it exists.** This is enforced by the
SDK's `write_skill_file` tool and the learner's `write_skill_content.py` script. Users must
edit the file directly to change rules.

### Skill directory with a client function

```
my_skill/
├── SKILL.md
├── client_functions.json   ← declares functions the agent can request
├── permissions.yaml        ← client-controlled permission rules (optional)
├── scripts/
├── references/
└── assets/
```

---

## Creating a skill

A skill is a folder with a `SKILL.md` file. Drop it into your skills directory and the agent picks it up automatically on next init.

```
my_skill/
├── SKILL.md                 (required — YAML frontmatter + markdown instructions)
├── client_functions.json    (optional — client-side functions the agent can request)
├── scripts/                 (optional — Python scripts the LLM can run via run_script)
├── references/              (optional — docs the LLM can read via read_reference)
└── assets/                  (optional — templates, icons, etc.)
```

The recommended way to create a skill is to use the **learner** skill. For skills that wrap an API or service with write operations, scaffold with `api_writes=true` to automatically generate `client_functions.json` and `permissions.yaml`.

## Built-in tools

Every agent run has these tools available automatically:

| Tool | Purpose |
|---|---|
| `use_skill` | Load a skill's full instructions by name |
| `manage_todos` | Plan and track an internal task list |
| `read_reference` | Read a doc from a skill's `references/` directory |
| `run_script` | Run a Python script from a skill's `scripts/` directory |
| `call_client_function` | Request execution of a function declared in `client_functions.json` |
| `read_user_file` | *(Optional)* Read UTF-8 text from disk under `AgentConfig.user_file_roots` |

`read_user_file` is registered only when `user_file_roots` is non-empty.

## Configuration

```python
from skill_agent import Agent, AgentConfig

agent = Agent(
    model=model,
    skills_dir=Path("skills"),
    config=AgentConfig(
        max_tokens=4096,        # max tokens per LLM response (default: 4096)
        max_turns=64,           # optional: max model requests per run (default: None = no cap)
        system_prompt_extra="You are a helpful assistant.",  # appended to system prompt
        # Optional: attach large text/PDF via run(..., files=[...]) — truncation cap (None = no cap)
        max_attached_text_file_chars=400_000,
        # Optional: allow the model to read files on demand under these directories
        user_file_roots=[Path("data/workspace")],
        max_user_file_read_chars=15000,
    ),
)
```

## Project structure

```
skills/                  Your skills (not part of the SDK)
  wikipedia_lookup/
    SKILL.md
    scripts/lookup.py
skill_agent/             The SDK package
  __init__.py            Public API and exports
  models.py              All data models and event types
  agent.py               Agent loop, built-in tools, event stream
  registry.py            Skill discovery from disk
  user_prompt_files.py   Build user messages with optional file attachments
  system_prompt.md       Default system prompt template (loaded by agent.py)
Example.py               Runnable example (includes a simple CLI event consumer)
pyproject.toml
```
