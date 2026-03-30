"""Data models for the skill-based agent SDK.

Everything that flows through the agent is defined here:
  - Skill        — a skill loaded from a SKILL.md file
  - Tool         — a callable capability the LLM can invoke
  - AgentEvent   — typed events emitted during a run (todos, tool calls, text, etc.)
  - AgentResult  — the final output of agent.run(), including the full event timeline
  - AgentConfig  — optional settings (max tokens, max turns, etc.)
"""

from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Callable, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


# ── Skill ─────────────────────────────────────────────────────────────
#
# A skill is a folder on disk containing a SKILL.md file plus optional
# resources (scripts, references, assets). The agent loads skill
# descriptions into the system prompt, but only loads the full body
# when the LLM explicitly calls use_skill("skill_name").
#
# This "progressive disclosure" pattern keeps the context window lean
# even when many skills are registered.


class Skill(BaseModel):
    """One skill discovered from a SKILL.md file."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = Field(description="Unique identifier used to call this skill.")
    description: str = Field(description="One-line summary shown to the LLM in the system prompt.")
    body: str = Field(description="Full markdown instructions, loaded on demand via use_skill.")
    path: Path | None = Field(default=None, description="Path to the SKILL.md file on disk.")

    # Bundled resources the LLM can access after loading the skill
    scripts: list[str] = Field(default_factory=list, description="Python scripts in scripts/.")
    references: list[str] = Field(default_factory=list, description="Docs in references/.")
    assets: list[str] = Field(default_factory=list, description="Other files in assets/.")

    # Client-side functions declared by this skill (loaded from client_functions.json)
    client_functions: list["ClientFunction"] = Field(
        default_factory=list,
        description="Functions that execute on the client, not the agent.",
    )


# ── Client-side functions ─────────────────────────────────────────────
#
# Skills can declare functions that execute on the client, not the agent.
# The agent requests them via call_client_function; the SDK validates the
# call and emits a ClientFunctionRequestEvent on the stream. The client
# picks it up and handles execution (e.g. prompting the user for permission).


class ClientFunctionParam(BaseModel):
    """One parameter in a client function's declared schema."""

    name: str = Field(description="Parameter name.")
    type: str = Field(default="string", description="JSON Schema type (string, number, boolean, etc.).")
    description: str = Field(default="", description="What this parameter is for.")
    required: bool = Field(default=True, description="Whether the parameter must be provided.")


class ClientFunction(BaseModel):
    """A client-side function declared by a skill via client_functions.json."""

    name: str = Field(description="Unique function name within the skill.")
    description: str = Field(description="What this function does — shown to the agent.")
    awaits_user: bool = Field(
        default=True,
        description="If true, the agent must wait for the user's next message after calling this.",
    )
    parameters: list[ClientFunctionParam] = Field(
        default_factory=list,
        description="Declared parameters the agent must provide when calling this function.",
    )


class ClientFunctionRequest(BaseModel):
    """One client function request, queued for emission on the event stream."""

    name: str = Field(description="Name of the client function to call.")
    args: dict[str, Any] = Field(description="Arguments provided by the agent.")
    skill_name: str = Field(description="Skill that declared this function.")
    awaits_user: bool = Field(description="Whether the agent should wait for user input.")


# ── Tool ──────────────────────────────────────────────────────────────
#
# Tools are callable capabilities the LLM can invoke during a run.
# Each tool has a name, a description for the LLM, a JSON Schema for
# its parameters, and a Python handler that does the actual work.


class Tool(BaseModel):
    """A callable tool the LLM can invoke during a run."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = Field(description="Unique tool name.")
    description: str = Field(description="What this tool does — shown to the LLM.")
    input_schema: dict[str, Any] = Field(description="JSON Schema for the tool's parameters.")
    handler: Callable[[dict[str, Any]], str] = Field(
        exclude=True,
        description="Python function: receives input dict, returns string result.",
    )


# ── Todo list ─────────────────────────────────────────────────────────
#
# The agent maintains an internal task list to plan its work. These
# models track the state of that list across the run.


class TodoStatus(str, Enum):
    """Lifecycle state of a single todo item."""

    pending = "pending"
    in_progress = "in_progress"
    done = "done"


class TodoItem(BaseModel):
    """One item in the agent's task list."""

    id: int = Field(description="Unique ID within this run.")
    content: str = Field(description="What needs to be done.")
    status: TodoStatus = Field(default=TodoStatus.pending)


# ── Tool call log ─────────────────────────────────────────────────────
#
# A lightweight record of each tool invocation, used to populate
# AgentResult.tool_log for inspection after a run.


class ToolCallRecord(BaseModel):
    """A record of one tool invocation."""

    tool: str = Field(description="Name of the tool that was called.")
    input: dict[str, Any] = Field(description="The arguments the LLM provided.")
    output_preview: str = Field(default="", description="Short preview of the output.")
    truncated: bool = Field(default=False, description="True if the output was truncated.")


# ── Token usage ───────────────────────────────────────────────────────


class TokenUsage(BaseModel):
    """Total tokens used across all turns of a run."""

    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)


# ── Stream events ─────────────────────────────────────────────────────
#
# These are the typed events emitted by _event_stream() in agent.py.
# Every event has a `type` string literal, which makes them easy to
# route on the consumer side — whether that's a CLI printer, a FastAPI
# SSE endpoint, or a React frontend listening on EventSource.
#
# Consumer pattern (Python):
#   async for event in agent.run_stream(prompt):
#       if isinstance(event, TextDeltaEvent):
#           print(event.content, end="")
#
# Consumer pattern (FastAPI SSE):
#   yield f"event: {event.type}\ndata: {event.model_dump_json()}\n\n"
#
# Consumer pattern (JavaScript EventSource):
#   source.addEventListener("text_delta", e => appendAnswer(e.data))
#   source.addEventListener("todo_update", e => renderTodos(e.data))


class TodoUpdateEvent(BaseModel):
    """Emitted after every manage_todos call with the full current list.

    The items list always reflects the complete current state, not just
    the change — so consumers can replace their local state directly.
    """

    type: Literal["todo_update"] = "todo_update"
    items: list[TodoItem]


class ToolCallEvent(BaseModel):
    """Emitted when the agent invokes a tool, before the result arrives."""

    type: Literal["tool_call"] = "tool_call"
    name: str
    args: dict[str, Any]
    activity: str | None = Field(
        default=None,
        description="Short user-facing description of what the model is doing (from the tool call).",
    )


class ToolResultEvent(BaseModel):
    """Emitted when a tool call completes."""

    type: Literal["tool_result"] = "tool_result"
    name: str


class TextDeltaEvent(BaseModel):
    """Emitted for each answer token as the model streams its response."""

    type: Literal["text_delta"] = "text_delta"
    content: str


class RunCompleteEvent(BaseModel):
    """Emitted once when the run finishes. Carries final token usage."""

    type: Literal["run_complete"] = "run_complete"
    usage: TokenUsage


class ClientFunctionRequestEvent(BaseModel):
    """Emitted when the agent requests client-side function execution.

    The client picks up this event from the stream and handles it
    (e.g. prompting the user for permission). If any request has
    awaits_user=True, the agent will stop and wait for the user's
    next message before continuing.
    """

    type: Literal["client_function_request"] = "client_function_request"
    requests: list[ClientFunctionRequest] = Field(
        description="One or more client function requests in this batch.",
    )


# Discriminated union of all event types.
# The `type` field is the discriminator — Pydantic uses it to deserialize
# the correct subtype automatically.
AgentEvent = Annotated[
    Union[
        TodoUpdateEvent,
        ToolCallEvent,
        ToolResultEvent,
        TextDeltaEvent,
        RunCompleteEvent,
        ClientFunctionRequestEvent,
    ],
    Field(discriminator="type"),
]


# ── Agent result ──────────────────────────────────────────────────────
#
# Returned by agent.run(). Contains the answer plus several views of
# what happened during the run — handy summaries AND the full event
# timeline for consumers who want fine-grained control.


class AgentResult(BaseModel):
    """The full output of a completed agent run.

    Convenience fields (pre-filtered summaries):
        result.answer           — the final text response
        result.activated_skills — skills that were loaded via use_skill
        result.tool_log         — chronological list of tool invocations
        result.todo_list        — final state of the task list
        result.usage            — total token usage

    Full timeline:
        result.events           — every event in order, filter as needed:
            tool_calls = [e for e in result.events if isinstance(e, ToolCallEvent)]
    """

    answer: str
    activated_skills: list[str] = Field(default_factory=list)
    tool_log: list[ToolCallRecord] = Field(default_factory=list)
    todo_list: list[TodoItem] = Field(default_factory=list)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    events: list[AgentEvent] = Field(
        default_factory=list,
        description="Ordered timeline of all events emitted during the run.",
    )


# ── Agent configuration ───────────────────────────────────────────────
#
# All fields are optional — the defaults are sensible for most use cases.
# Pass an AgentConfig to Agent() to override them.


class AgentConfig(BaseModel):
    """Optional settings for the agent. All fields have sensible defaults."""

    model: str = Field(
        default="claude-sonnet-4-20250514",
        description="Anthropic model ID.",
    )
    max_tokens: int = Field(
        default=4096,
        description="Maximum tokens per LLM response.",
    )
    max_turns: int | None = Field(
        default=None,
        description="Maximum model requests per run (each tool round counts). None = no cap (default).",
    )
    system_prompt_extra: str | None = Field(
        default=None,
        description="Extra text appended to the system prompt.",
    )
    user_file_roots: list[Path] = Field(
        default_factory=list,
        description="If non-empty, registers read_user_file; paths must stay under these roots.",
    )
    max_user_file_read_chars: int = Field(
        default=15000,
        description="Maximum characters read_user_file returns per call.",
    )
    max_attached_text_file_chars: int | None = Field(
        default=400_000,
        description="Truncate inlined text/PDF content from run(..., files=...). None = no limit.",
    )
