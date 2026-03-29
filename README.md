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
  └─ run(prompt)        → AgentResult
  └─ run_stream(prompt) → AsyncGenerator[AgentEvent, ...]
  └─ clear_conversation() — forget prior turns (optional)
```

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

## Event types

Every event has a `type` string literal field, making them easy to route in any consumer — a CLI printer, a FastAPI SSE endpoint, or a JavaScript `EventSource`.

| Event | `type` | Contents |
|---|---|---|
| `TodoUpdateEvent` | `"todo_update"` | Full current todo list |
| `ToolCallEvent` | `"tool_call"` | Tool name + args |
| `ToolResultEvent` | `"tool_result"` | Tool name |
| `TextDeltaEvent` | `"text_delta"` | Incremental answer text (streamed chunk; not necessarily a single model token) |
| `RunCompleteEvent` | `"run_complete"` | Final token usage (`usage`) |

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
  "args": { "skill_name": "wikipedia_lookup" }
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
  }
}
```

**`tool_call`** — `read_reference`

```json
{
  "type": "tool_call",
  "name": "read_reference",
  "args": { "skill_name": "wikipedia_lookup", "filename": "api_notes.md" }
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
  }
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

## Creating a skill

A skill is a folder with a `SKILL.md` file. Drop it into your skills directory and the agent picks it up automatically on next init.

```
my_skill/
├── SKILL.md         (required — YAML frontmatter + markdown instructions)
├── scripts/         (optional — Python scripts the LLM can run via run_script)
├── references/      (optional — docs the LLM can read via read_reference)
└── assets/          (optional — templates, icons, etc.)
```

The recommended way to create a skill is to use the **skill-creator** skill — just ask it to create the skill you need.

## Built-in tools

Every agent run has these tools available automatically:

| Tool | Purpose |
|---|---|
| `use_skill` | Load a skill's full instructions by name |
| `manage_todos` | Plan and track an internal task list |
| `read_reference` | Read a doc from a skill's `references/` directory |
| `run_script` | Run a Python script from a skill's `scripts/` directory |

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
Example.py               Runnable example (includes a simple CLI event consumer)
pyproject.toml
```
