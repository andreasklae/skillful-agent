# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A pydantic-ai based SDK for building AI agents with progressive skill disclosure. The agent discovers skills from a user-provided directory, loads them on demand, and uses bundled resources (scripts, references, assets) to complete tasks. Includes structured message logging, context window management, an inbox/thread system for inter-agent communication, and subagent spawning.

## Commands

```bash
uv sync                    # Install dependencies
uv sync --extra pdf        # Include PDF extraction support
uv sync --extra examples   # Include example skill dependencies (wikipedia-api)
uv run Example.py          # Run the example CLI agent
uv run pytest tests/ -v    # Run the test suite (48 tests)
```

No linter is configured in pyproject.toml.

## Architecture

### Core SDK (`skill_agent/`)

- **`agent.py`** — Agent class with `run()` (blocking) and `run_stream()` (async generator). Wraps pydantic-ai's `Agent` under the hood. Maintains conversation state, dual message stores, inbox, and subagent tracking. Per-run mutable state lives in the `_RunDeps` dataclass passed via `RunContext.deps`. `_RunDeps` fields are **references** to Agent instance attributes, not copies.
- **`models.py`** — All Pydantic models: `Skill`, `AgentConfig`, `AgentResult`, all event types (`AgentEvent` discriminated union), `TodoItem`, `ToolCallRecord`, `ClientFunction`/`ClientFunctionRequest`, `TokenUsage`.
- **`messages.py`** — `Message` model (id, timestamp, type, content, summary) and `SourceContext` hierarchy (`UIContext`, `EmailContext`, `SubAgentContext`). Every conversation step is a Message.
- **`inbox.py`** — `Inbox`, `InboxItem`, `Thread`, `ThreadStatus`. General-purpose message routing for inter-agent and external communication. Exposed as `Agent.inbox`.
- **`skill_tools.py`** — Skill tools extracted from agent.py: `use_skill`, `register_skill`, `scaffold_skill`, `manage_todos`, `read_reference`, `run_script`, `write_skill_file`, `read_user_file`, `call_client_function`.
- **`context_tools.py`** — `compress_message`, `retrieve_message`, `compress_all`. Manage context window size by compressing messages to summaries. Auto-compression triggers when `input_tokens` exceeds `AgentConfig.context_compression_threshold` (default 100k).
- **`inbox_tools.py`** — `read_inbox`, `read_thread`, `write_to_thread`, `forward_thread_item`, `dismiss_inbox_item`, `delete_thread`, `spawn_subagent`. Registered as pydantic-ai tools.
- **`subagent.py`** — `SubAgent` class. Shares parent's model, communicates via inbox threads, runs as `asyncio.Task`.
- **`registry.py`** — `discover_skills()` recursively scans directories for `SKILL.md` files with YAML-like frontmatter. Custom frontmatter parser (no PyYAML dependency). Also loads `client_functions.json` if present.
- **`user_prompt_files.py`** — `build_user_message()` turns text + file paths into pydantic-ai message parts (text inlined, images as `BinaryContent`, PDFs extracted via pdfplumber).
- **`__init__.py`** — Public API re-exports. Everything consumers need is importable from `skill_agent`.

### Key Design Decisions

- **Progressive disclosure**: System prompt includes only skill names + descriptions. Full SKILL.md body is loaded only when the LLM calls `use_skill`. This keeps token usage low regardless of skill count.
- **Two skill sources**: Native skills ship with the SDK from `native-skills/` (adjacent to the package). User skills come from the `skills_dir` argument. User skills override native skills with the same name.
- **Event-driven streaming**: Both `run()` and `run_stream()` produce the same `AgentEvent` types. Events have a `type` literal discriminator for easy routing (SSE, CLI, React).
- **Client functions**: Skills can declare functions in `client_functions.json` that execute on the client side. The SDK validates calls and emits `ClientFunctionRequestEvent`; the client handles execution. `permissions.yaml` gates write operations.
- **Dual message stores**: `message_log` (append-only source of truth) and `context_window` (mutable working set for the model). Messages can be compressed to summaries while preserving the full history.
- **Inbox-based communication**: All inter-agent and external communication flows through inboxes. Each agent/subagent has its own inbox. `write_to_thread` auto-resolves the target inbox (subagent vs self) based on thread_id.
- **Tool registration refactor**: Tools are organized into focused files (`skill_tools.py`, `context_tools.py`, `inbox_tools.py`) with `register_*_tools(runner)` functions. `_create_runner` in `agent.py` is a slim coordinator that calls each.

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
| `read_inbox` | inbox_tools.py | Check for unread inbox messages |
| `read_thread` | inbox_tools.py | Read full thread contents |
| `write_to_thread` | inbox_tools.py | Write to a thread (auto-resolves target inbox) |
| `forward_thread_item` | inbox_tools.py | Forward item to subagent without loading content |
| `dismiss_inbox_item` | inbox_tools.py | Dismiss an inbox item |
| `delete_thread` | inbox_tools.py | Delete thread and stop linked subagent |
| `spawn_subagent` | inbox_tools.py | Spawn a background worker subagent |

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
