# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A pydantic-ai based SDK for building AI agents with progressive skill disclosure. The agent discovers skills from a user-provided directory, loads them on demand, and uses bundled resources (scripts, references, assets) to complete tasks. Includes structured message logging, context window management, a thread-based communication system for inter-agent communication, and subagent spawning via plain Agent instances.

## Commands

```bash
uv sync                    # Install dependencies
uv sync --extra pdf        # Include PDF extraction support
uv sync --extra examples   # Include example skill dependencies (wikipedia-api)
uv sync --extra server     # Include FastAPI server dependencies
uv run Example.py          # Run the example CLI agent
uv run run_server.py       # Run the HTTP server
uv run pytest tests/ -v    # Run the test suite (70 tests)
```

No linter is configured in pyproject.toml.

## Architecture

### Core SDK (`skill_agent/`)

- **`agent.py`** — `Agent` class with `run()` (blocking), `run_stream()` (async generator), `enqueue_run()` / `enqueue_run_message()` (async queue), `subscribe_run()` / `subscribe_all_runs()` (SSE subscriptions). Maintains conversation state, dual message stores, thread registry, and run queue. Per-run mutable state lives in the `_RunDeps` dataclass injected via `RunContext.deps`. `_RunDeps` fields are **references** to Agent instance attributes, not copies.
- **`models.py`** — All Pydantic models: `Skill`, `AgentConfig`, `AgentResult`, all event types (`AgentEvent` discriminated union), `TodoItem`, `ToolCallRecord`, `ClientFunction`/`ClientFunctionRequest`, `TokenUsage`.
- **`messages.py`** — `Message` model (id, timestamp, type, content, summary) and `SourceContext` hierarchy (`UIContext`, `EmailContext`, `SubAgentContext`). Every conversation step is logged as a `Message`.
- **`threads.py`** — `Thread`, `ThreadMessage`, `ThreadRole`, `ThreadStatus`, `ThreadRegistry`, `ThreadEvent`. Bidirectional message channels. `send()` = participant-authored (inbound, fires inbound listeners). `reply()` = agent-authored (outbound, fires outbound listeners). `ThreadMessage.events` carries the serialized `AgentEvent` list for the run that produced the message.
- **`thread_tools.py`** — `read_thread`, `reply_to_thread`, `archive_thread`, `spawn_agent`. Registered as pydantic-ai tools. `spawn_agent` creates a plain `Agent` instance and wires bidirectional listeners: outbound (parent→subagent via `thread.reply()`) and inbound notification (subagent→parent via `thread.send()` → `_register_thread_notification`). The event loop is captured at spawn time and stored in the closure.
- **`skill_tools.py`** — `use_skill`, `manage_todos`, `read_reference`, `run_script`, `write_skill_file`, `read_user_file`, `call_client_function`.
- **`context_tools.py`** — `compress_message`, `retrieve_message`, `compress_all`. Auto-compression triggers when `input_tokens` exceeds `AgentConfig.context_compression_threshold` (default 100k).
- **`registry.py`** — `discover_skills()` recursively scans directories for `SKILL.md` files with YAML-like frontmatter. Custom frontmatter parser (no PyYAML dependency). Also loads `client_functions.json` if present.
- **`user_prompt_files.py`** — `build_user_message()` turns text + file paths into pydantic-ai message parts (text inlined, images as `BinaryContent`, PDFs extracted via pdfplumber).
- **`__init__.py`** — Public API re-exports.

### Server (`server/`)

FastAPI app with `create_app(agent=None)` factory pattern for dependency injection and testability.

- **`app.py`** — `create_app()` wires the agent as a dependency, registers all routers, configures CORS.
- **`dependencies.py`** — `get_agent()` FastAPI dependency.
- **`config.py`** — Server configuration loaded from environment / Azure Key Vault.
- **`routes/runs.py`** — `POST /run` (queue + SSE stream), `GET /runs/subscribe` (global SSE).
- **`routes/threads.py`** — `GET /threads`, `GET /threads/subscribe` (SSE), `GET /threads/{name}`, `POST /threads/{name}/messages`.
- **`routes/skills.py`** — `GET /skills`, `POST /skills/upload`.
- **`routes/health.py`** — `GET /health`.
- **`services/sse.py`** — `format_run_envelope_sse()` formats run envelopes as SSE strings.
- **`models.py`** — Request/response Pydantic models. `ThreadItemResponse` and `ThreadMessageResponse` include an `events: list[dict]` field carrying the per-message event log.

### Key Design Decisions

- **Progressive disclosure**: System prompt includes only skill names + descriptions. Full SKILL.md body is loaded only when the LLM calls `use_skill`.
- **Two skill sources**: Native skills ship with the SDK from `native-skills/`. User skills come from `skills_dir`. User skills override native skills with the same name.
- **Thread-based communication**: All inter-agent and external communication flows through named threads. `send()` = participant-authored (inbound). `reply()` = agent-authored (outbound). The asymmetry is intentional — the parent agent is the "agent" side of subagent threads, the subagent is the "participant".
- **Main thread mirrors context_window**: The `"main"` thread is not a separate store. `thread_registry.get("main").reply(answer)` is called at run completion to attach the answer and its event log to the thread, but the actual storage is `context_window`.
- **Event loop captured at spawn time**: The outbound listener in `spawn_agent_impl` may fire from inside a sync pydantic-ai tool context. `asyncio.get_running_loop()` may fail there. The loop is captured when `spawn_agent_impl` is called (always async) and stored in the closure.
- **Coalesce key released at run start**: Thread notification coalesce keys are discarded when a run is picked up from the queue (not when it finishes). This ensures a second notification arriving during an active run queues a new run rather than being dropped.
- **Null content sanitization**: pydantic-ai sets `content=null` on assistant messages with only tool calls and no text. Some models reject this. A `TextPart(content="")` is injected into any such `ModelResponse` before passing history to the model.
- **Per-message event logs**: Every `ThreadMessage.events` carries the serialized `AgentEvent` list for the run that produced it. Both `GET /threads/{name}` and `GET /threads/subscribe` expose this to the frontend.
- **Run queue anti-flood**: `_auto_thread_run_counts` tracks auto-triggered runs per thread (max 10). Counts clear when a user-initiated run completes.

### Built-in Tools

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
| `reply_to_thread` | thread_tools.py | Send ONE message to a thread, then end turn (triggers subagent run) |
| `archive_thread` | thread_tools.py | Archive a thread |
| `spawn_agent` | thread_tools.py | Spawn a subagent wired to a named thread |

### Thread Communication Flow

```
Parent calls reply_to_thread("researcher", "what did you find?")
  → thread.reply(content)
  → outbound listener fires (captured spawn_loop.create_task)
  → _run_subagent_and_post(subagent, thread, content, source_ctx)
  → subagent._collect_run(prompt)  [runs subagent's _event_stream]
  → thread.send(answer, source_ctx, events=serialized_events)
  → inbound listener fires (_register_thread_notification)
  → _queue_thread_follow_up("researcher")
  → enqueue_run_message("new message in 'researcher'", source="thread")
  → run worker picks it up → parent's _event_stream runs
  → parent calls read_thread("researcher"), sees answer, calls reply_to_thread
```

### Skill Structure

```
skill-name/
├── SKILL.md                # YAML frontmatter (name, description) + markdown body
├── client_functions.json   # Client-side function declarations (optional)
├── permissions.yaml        # Client-controlled permission rules (optional, agent cannot overwrite)
├── scripts/                # Python scripts runnable via run_script
├── references/             # Docs readable via read_reference
└── assets/                 # Templates, icons, etc.
```

## Conventions

- Python 3.12+ required
- Dependencies managed with `uv`
- Environment: `API_KEY` in `.env` file (loaded via python-dotenv)
- The SDK package is `skill_agent/`; user-land skills go in `skills/`; SDK-bundled skills go in `native-skills/`
- Server entry point is `run_server.py` (not `server.py` — avoids name collision with the `server/` package)
- `Example.py` is the reference implementation showing CLI event consumption
- Tests use `pytest` + `pytest-anyio` for async tests; fake agents/registries in `test_server_cors.py`
