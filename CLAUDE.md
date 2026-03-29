# Skill Agent SDK

## What This Is

A pydantic-ai based SDK for building AI agents with progressive skill disclosure. The agent discovers skills from a user-provided directory, loads them on demand, and uses bundled resources (scripts, references, assets) to complete tasks.

## Architecture

- **Core:** `skill_agent/agent.py` — agent loop + built-in tools (use_skill, manage_todos, run_script, read_reference)
- **Models:** `skill_agent/models.py` — all Pydantic models and typed event types (Skill, AgentConfig, AgentResult, AgentEvent, …)
- **Registry:** `skill_agent/registry.py` — discovers SKILL.md files + bundled resources from a given directory

## Key Pattern

1. `Agent(model, skills_dir=Path("skills"))` — discovers skills on init
2. System prompt contains skill **descriptions only** (lightweight)
3. LLM calls `use_skill` to load the full skill body + see available resources
4. LLM uses `run_script` / `read_reference` to access bundled resources
5. Returns typed `AgentResult` with answer, activated skills, tool log, todo list, usage

## Skill Structure

```
skill-name/
├── SKILL.md         # Instructions (required)
├── scripts/         # Executable code (optional)
├── references/      # Docs loaded into context (optional)
└── assets/          # Templates, icons, etc. (optional)
```

## Running

```bash
uv sync
echo 'API_KEY=your-key' > .env
uv run main.py
```

## Project Structure

```
skills/                  # User-land skills (not part of the SDK)
  wikipedia_lookup/
    SKILL.md
    scripts/lookup.py
skill_agent/             # The SDK package
  __init__.py
  models.py
  agent.py
  registry.py
Example.py               # Example entrypoint (includes a simple CLI event consumer)
pyproject.toml
```
