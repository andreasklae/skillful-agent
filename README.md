# Skill Agent SDK

A pydantic-ai based SDK for building AI agents that discover and use skills via progressive disclosure.

## How it works

```
skills/                          src/ (the SDK)
  my_skill/                        agent.py    — agent loop + built-in tools
    SKILL.md                       models.py   — Pydantic models
    scripts/                       registry.py — SKILL.md discovery
    references/                    stream.py   — streaming output
    assets/
        ↓                              ↓
    Agent(model, skills_dir=Path("skills"))
        ↓
    agent.solve("your question") → AgentResult
```

**Progressive disclosure** means the LLM initially sees only skill *descriptions*. When it decides a skill is relevant, it calls `use_skill` to load the full instructions and see what resources are available. This scales to many skills without bloating context.

## Quick start

```bash
uv sync

# Set your API key in .env
echo 'API_KEY=your-key-here' > .env

uv run main.py
```

## Example usage

```python
from pathlib import Path

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from skill_agent import Agent

# Any pydantic-ai model works (OpenAI, Anthropic, Google, etc.)
model = OpenAIChatModel(
    "gpt-4o",
    provider=OpenAIProvider(api_key="your-api-key"),
)

# Create the agent — point it at your skills directory
agent = Agent(model=model, skills_dir=Path("skills"))

# Blocking
result = agent.solve("Your instructions")

# Streaming (shows tool calls, todo progress, and answer tokens live)
result = agent.solve_stream("Your instructions")

# Typed result
print(result.answer)              # str
print(result.activated_skills)    # list[str]
print(result.tool_log)            # list[ToolCallRecord]
print(result.todo_list)           # list[TodoItem]
print(result.usage.input_tokens)  # int
```

The `Agent` scans `skills_dir` on init, finds all `SKILL.md` files, and registers them. No manual wiring needed.

## Creating a skill

For a full guide on creating skills, see the [official skill documentation](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview). The recommended way to create a skill is to use the **skill-creator** skill, available for most AI providers — just ask it to create the skill you need.

A skill follows this structure:

```
my_skill/
├── SKILL.md         (required — YAML frontmatter + markdown instructions)
├── scripts/         (optional — executable code for deterministic tasks)
├── references/      (optional — docs loaded into context as needed)
└── assets/          (optional — templates, icons, fonts for output)
```

Drop it into your skills directory and the agent picks it up automatically on next init.

## Built-in tools

Every agent run has these tools available:

| Tool              | Purpose                                              |
|-------------------|------------------------------------------------------|
| `use_skill`       | Load a skill's full instructions by name             |
| `manage_todos`    | Plan and track an internal task list                 |
| `read_reference`  | Read a reference doc from a skill's `references/`    |
| `run_script`      | Run a Python script from a skill's `scripts/`        |

## Configuration

```python
from pathlib import Path
from skill_agent import Agent, AgentConfig

agent = Agent(
    model=model,
    skills_dir=Path("skills"),
    config=AgentConfig(
        max_tokens=4096,                    # per LLM response
        max_turns=12,                       # max agentic loop iterations
        system_prompt_extra="Custom text appended to the system prompt.",
    ),
)
```

## Project structure

```
skills/                    # Your skills (not part of the SDK)
  wikipedia_lookup/
    SKILL.md
    scripts/
      lookup.py
src/                       # The SDK (importable as skill_agent)
  __init__.py
  models.py
  agent.py
  stream.py
  registry.py
main.py                    # Example entrypoint
pyproject.toml
```
