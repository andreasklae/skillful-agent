"""Pydantic models for the skill-based agent SDK.

This module defines every data structure that flows through the agent.

The models form a simple pipeline:
  Skill (from SKILL.md) + Tool (user-defined) → solve() → AgentResult
"""

from enum import Enum
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field


# ── Skill Model ───────────────────────────────────────────────────────
# Skills are parsed from SKILL.md files on disk. The agent sees only
# the name + description in its system prompt (progressive disclosure).
# The full body is loaded on demand when the agent calls `use_skill`.


class Skill(BaseModel):
    """A skill parsed from a SKILL.md file.

    Progressive disclosure pattern:
      - The LLM initially sees only `name` and `description`
      - When it calls `use_skill`, it receives the full `body`
      - This keeps the system prompt small while allowing rich instructions

    Skills can bundle resources alongside SKILL.md:
      - scripts/    — executable code for deterministic/repetitive tasks
      - references/ — docs loaded into context as needed via `read_reference`
      - assets/     — files used in output (templates, icons, fonts)
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = Field(description="Unique identifier for the skill.")
    description: str = Field(description="Short summary shown to the LLM in the system prompt.")
    body: str = Field(description="Full markdown instructions, loaded on demand via use_skill.")
    path: Path | None = Field(default=None, description="Filesystem path to the SKILL.md file.")
    scripts: list[str] = Field(
        default_factory=list,
        description="Filenames in the scripts/ directory (e.g. ['lookup.py']).",
    )
    references: list[str] = Field(
        default_factory=list,
        description="Filenames in the references/ directory (e.g. ['api_guide.md']).",
    )
    assets: list[str] = Field(
        default_factory=list,
        description="Filenames in the assets/ directory (e.g. ['template.html']).",
    )


# ── Tool Model ────────────────────────────────────────────────────────
# Tools are callable capabilities that the LLM can invoke. Each tool
# bundles together: a name/description for the LLM, a JSON Schema for
# its parameters, and a Python handler function that does the actual work.
#
# The handler signature is deliberately simple:
#   handler(input: dict[str, Any]) -> str
#
# It receives the raw input dict from the LLM and returns a string
# result. Returning str (not dict) keeps things simple — the string
# goes directly into the tool_result message content.


class Tool(BaseModel):
    """A callable tool that the LLM can invoke during execution.

    Example — defining a weather tool:

        def get_weather(input: dict) -> str:
            city = input["city"]
            return json.dumps({"temp": 22, "city": city})

        weather_tool = Tool(
            name="weather",
            description="Get current weather for a city.",
            input_schema={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
            handler=get_weather,
        )
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = Field(description="Tool name — must be unique across all registered tools.")
    description: str = Field(description="What this tool does — shown to the LLM.")
    input_schema: dict[str, Any] = Field(description="JSON Schema defining the tool's parameters.")
    handler: Callable[[dict[str, Any]], str] = Field(
        exclude=True,  # Don't include the function when serializing
        description="Python function: receives input dict, returns string result.",
    )



# ── Result Models ─────────────────────────────────────────────────────
# These capture the output of an agent run: what happened, what was
# called, and how many tokens were used.


class TodoStatus(str, Enum):
    """Status of a todo item in the agent's internal task list."""

    pending = "pending"
    in_progress = "in_progress"
    done = "done"


class TodoItem(BaseModel):
    """A single item in the agent's internal task list."""

    id: int = Field(description="Unique identifier for this todo item.")
    content: str = Field(description="What needs to be done.")
    status: TodoStatus = Field(default=TodoStatus.pending, description="Current status.")


class ToolCallRecord(BaseModel):
    """A log entry for a single tool invocation during the agent loop."""

    tool: str = Field(description="Name of the tool that was called.")
    input: dict[str, Any] = Field(description="The input dict the LLM provided.")
    truncated: bool = Field(default=False, description="Whether the result was truncated to fit.")


class TokenUsage(BaseModel):
    """Token usage counters accumulated across all turns of the agent loop."""

    input_tokens: int = Field(default=0, description="Total input tokens across all turns.")
    output_tokens: int = Field(default=0, description="Total output tokens across all turns.")


class AgentResult(BaseModel):
    """The structured output of a single agent run.

    Access fields directly as typed attributes:
        result.answer           # str
        result.activated_skills # list[str]
        result.tool_log         # list[ToolCallRecord]
        result.usage            # TokenUsage
    """

    answer: str = Field(description="The agent's final text response.")
    activated_skills: list[str] = Field(
        default_factory=list,
        description="Names of skills that were loaded via use_skill.",
    )
    tool_log: list[ToolCallRecord] = Field(
        default_factory=list,
        description="Chronological log of tool invocations (excludes use_skill).",
    )
    todo_list: list[TodoItem] = Field(
        default_factory=list,
        description="Final state of the agent's internal task list.",
    )
    usage: TokenUsage = Field(
        default_factory=TokenUsage,
        description="Total token usage across all turns.",
    )


# ── Configuration Model ──────────────────────────────────────────────
# Optional settings for the agent run. All fields have sensible defaults,
# so you can call solve() without providing a config at all.


class AgentConfig(BaseModel):
    """Configuration for an agent run. All fields are optional with defaults."""

    model: str = Field(
        default="claude-sonnet-4-20250514",
        description="Anthropic model ID to use.",
    )
    max_tokens: int = Field(
        default=4096,
        description="Maximum tokens per LLM response.",
    )
    max_turns: int = Field(
        default=12,
        description="Maximum agentic loop iterations before stopping.",
    )
    system_prompt_extra: str | None = Field(
        default=None,
        description="Optional extra text appended to the system prompt.",
    )
