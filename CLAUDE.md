# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A pydantic-ai based SDK for building AI agents with progressive skill disclosure. The agent discovers skills from a user-provided directory, loads them on demand, and uses bundled resources (scripts, references, assets) to complete tasks.

## Commands

```bash
uv sync                    # Install dependencies
uv sync --extra pdf        # Include PDF extraction support
uv sync --extra examples   # Include example skill dependencies (wikipedia-api)
uv run Example.py          # Run the example CLI agent
uv run main.py             # Run the main entrypoint (if it exists)
```

Tests: `uv run pytest tests/ -v`
No linter is configured in pyproject.toml.

## Architecture

### Core SDK (`skill_agent/`)

- **`agent.py`** — Agent class with `run()` (blocking) and `run_stream()` (async generator). Wraps pydantic-ai's `Agent` under the hood. Maintains conversation state across calls; `clear_conversation()` resets it. Per-run mutable state lives in the `_RunDeps` dataclass passed via `RunContext.deps`.
- **`models.py`** — All Pydantic models: `Skill`, `AgentConfig`, `AgentResult`, all event types (`AgentEvent` discriminated union), `TodoItem`, `ToolCallRecord`, `ClientFunction`/`ClientFunctionRequest`, `TokenUsage`.
- **`registry.py`** — `discover_skills()` recursively scans directories for `SKILL.md` files with YAML-like frontmatter. Custom frontmatter parser (no PyYAML dependency). Also loads `client_functions.json` if present.
- **`user_prompt_files.py`** — `build_user_message()` turns text + file paths into pydantic-ai message parts (text inlined, images as `BinaryContent`, PDFs extracted via pdfplumber).
- **`__init__.py`** — Public API re-exports. Everything consumers need is importable from `skill_agent`.

### Key Design Decisions

- **Progressive disclosure**: System prompt includes only skill names + descriptions. Full SKILL.md body is loaded only when the LLM calls `use_skill`. This keeps token usage low regardless of skill count.
- **Two skill sources**: Native skills ship with the SDK from `native-skills/` (adjacent to the package). User skills come from the `skills_dir` argument. User skills override native skills with the same name.
- **Event-driven streaming**: Both `run()` and `run_stream()` produce the same `AgentEvent` types. Events have a `type` literal discriminator for easy routing (SSE, CLI, React).
- **Client functions**: Skills can declare functions in `client_functions.json` that execute on the client side. The SDK validates calls and emits `ClientFunctionRequestEvent`; the client handles execution. `permissions.yaml` gates write operations.

### Agent Layer Infrastructure

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

### Built-in Tools (registered on the pydantic-ai agent)

| Tool | Purpose |
|---|---|
| `use_skill` | Load a skill's full instructions by name |
| `manage_todos` | Plan and track an internal task list |
| `read_reference` | Read a doc from a skill's `references/` directory |
| `run_script` | Run a Python script from a skill's `scripts/` directory |
| `call_client_function` | Request execution of a client-declared function |
| `read_user_file` | *(Conditional)* Read files under `AgentConfig.user_file_roots` |
| `compress_message` | Compress a context window message to a summary |
| `retrieve_message` | Restore a compressed message from the log |
| `compress_all` | Replace entire context window with a summary |
| `read_inbox` | Check for unread inbox messages |
| `read_thread` | Read full thread contents |
| `write_to_thread` | Write to a thread (auto-resolves target inbox) |
| `forward_thread_item` | Forward item to subagent without loading content |
| `dismiss_inbox_item` | Dismiss an inbox item |
| `delete_thread` | Delete thread and stop linked subagent |
| `spawn_subagent` | Spawn a background worker subagent |

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
- `Example.py` is the reference implementation showing CLI event consumption
