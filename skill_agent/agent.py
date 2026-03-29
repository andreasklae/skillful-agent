"""Core agent loop with progressive skill disclosure.

The agent works like this:

    1. On init, it scans skills_dir and builds a system prompt listing
       skill names and descriptions (not full instructions).

    2. When run() or run_stream() is called, the LLM starts the loop:
         a. Calls manage_todos to plan its approach.
         b. Calls use_skill to load the full instructions for a skill.
         c. Uses read_reference / run_script to access bundled resources.
         d. Repeats until it produces a final text answer.

    3. Every meaningful step emits a typed AgentEvent. These are the
       same events whether you use run() or run_stream() — the difference
       is only in how the caller receives them.

Public API
──────────
    agent.run(prompt)        → AgentResult   (blocking, collects all events)
    agent.run_stream(prompt) → AsyncGenerator[AgentEvent, ...]  (live stream)

Conversation state is kept on the agent between ``run`` / ``run_stream`` calls.
Call ``agent.clear_conversation()`` to start a new thread.
"""

import asyncio
import json
import logging
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, AsyncGenerator

from pydantic_ai import Agent as PydanticAgent, RunContext
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
)
from pydantic_ai.run import AgentRunResultEvent
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from .models import (
    AgentConfig,
    AgentEvent,
    AgentResult,
    RunCompleteEvent,
    Skill,
    TextDeltaEvent,
    TodoItem,
    TodoStatus,
    TodoUpdateEvent,
    TokenUsage,
    ToolCallEvent,
    ToolCallRecord,
    ToolResultEvent,
)

logger = logging.getLogger(__name__)


# ── Per-run state ──────────────────────────────────────────────────────
#
# This dataclass holds all mutable state for a single run. pydantic-ai
# passes it into every tool call via RunContext.deps, so tools can read
# skills and update logs without using global variables.

@dataclass
class _RunDeps:
    skills: dict[str, Skill]
    activated_skills: list[str] = field(default_factory=list)
    tool_log: list[ToolCallRecord] = field(default_factory=list)
    todo_list: list[TodoItem] = field(default_factory=list)
    _next_todo_id: int = 1


# ── Agent ─────────────────────────────────────────────────────────────

class Agent:
    """Skill-based AI agent. Initialize once, call run() or run_stream() many times.

    The agent discovers skills from a directory on init. No manual wiring needed —
    just drop a skill folder in and it gets picked up automatically.

    Basic usage:
        agent = Agent(model=model, skills_dir=Path("skills"))

        # Blocking — waits for the full answer, returns AgentResult
        result = agent.run("What is the speed of light?")
        print(result.answer)

        # Streaming — yields typed events as they happen (async)
        async for event in agent.run_stream("What is the speed of light?"):
            if isinstance(event, TextDeltaEvent):
                print(event.content, end="", flush=True)

        # Later prompts on the same agent see prior turns unless you call clear_conversation().
    """

    def __init__(
        self,
        *,
        model: Model,
        skills_dir: Path | list[Path],
        config: AgentConfig | None = None,
    ) -> None:
        from .registry import discover_skills

        # Discover all SKILL.md files under the given directory/directories
        skills = discover_skills(skills_dir)
        if not skills:
            raise RuntimeError(f"No skills found in {skills_dir}. Add at least one SKILL.md.")

        cfg = config or AgentConfig()

        self._skills = skills
        self._config = cfg

        # _deps holds mutable per-run state; reset between calls via _reset_run_state()
        self._deps = _RunDeps(skills=skills)

        # Build the system prompt and the underlying pydantic-ai runner
        system_prompt = _build_system_prompt(skills, cfg.system_prompt_extra)
        self._runner = _create_runner(model, system_prompt, skills)
        self._model_settings = ModelSettings(max_tokens=cfg.max_tokens)
        self._usage_limits = UsageLimits(
            request_limit=cfg.max_turns if cfg.max_turns is not None else None,
        )

        # pydantic-ai ModelMessage list; updated after each completed run
        self._conversation_messages: list[Any] = []

    # ── Public API ────────────────────────────────────────────────────

    def clear_conversation(self) -> None:
        """Drop all remembered turns. Next ``run`` / ``run_stream`` starts fresh."""
        self._conversation_messages.clear()

    def run(self, prompt: str) -> AgentResult:
        """Run the agent and wait for the full answer.

        Internally this drives the same _event_stream as run_stream, but
        collects all events before returning. The AgentResult includes
        the full event timeline, so you can inspect it after the fact:

            result = agent.run("question")
            todos   = [e for e in result.events if isinstance(e, TodoUpdateEvent)]
            tools   = [e for e in result.events if isinstance(e, ToolCallEvent)]
            answer  = result.answer  # or join TextDeltaEvents yourself
        """
        if not prompt.strip():
            raise ValueError("Prompt cannot be empty.")

        self._reset_run_state()
        return asyncio.run(self._collect_run(prompt))

    def run_stream(self, prompt: str) -> AsyncGenerator[AgentEvent, None]:
        """Run the agent and stream typed events as they happen.

        Returns an async generator — iterate it with `async for`. Each
        yielded object is a typed AgentEvent with a `type` literal field
        that makes it easy to route to the right part of your UI or output.

        CLI example:
            async for event in agent.run_stream("question"):
                if isinstance(event, TextDeltaEvent):
                    print(event.content, end="", flush=True)

        FastAPI SSE example:
            async def generate():
                async for event in agent.run_stream(prompt):
                    yield f"event: {event.type}\\ndata: {event.model_dump_json()}\\n\\n"

        JavaScript EventSource example:
            source.addEventListener("text_delta",  e => appendAnswer(e.data))
            source.addEventListener("todo_update", e => renderTodos(e.data))
            source.addEventListener("tool_call",   e => logTool(e.data))
        """
        if not prompt.strip():
            raise ValueError("Prompt cannot be empty.")

        self._reset_run_state()

        # Return the async generator directly. The caller is responsible
        # for iterating it inside an async context.
        return self._event_stream(prompt)

    # ── Internal helpers ──────────────────────────────────────────────

    def _reset_run_state(self) -> None:
        """Clear all mutable state so each run() / run_stream() starts fresh."""
        self._deps.activated_skills.clear()
        self._deps.tool_log.clear()
        self._deps.todo_list.clear()
        self._deps._next_todo_id = 1

    async def _collect_run(self, prompt: str) -> AgentResult:
        """Collect all events from _event_stream and build an AgentResult."""
        events: list[AgentEvent] = []
        async for event in self._event_stream(prompt):
            events.append(event)
        return self._build_result(events)

    def _build_result(self, events: list[AgentEvent]) -> AgentResult:
        """Assemble an AgentResult from the collected event list and run state."""
        # Reconstruct the answer from text delta events
        answer = "".join(e.content for e in events if isinstance(e, TextDeltaEvent))

        # Pull token usage from the RunCompleteEvent (last event in the list)
        usage = next(
            (e.usage for e in events if isinstance(e, RunCompleteEvent)),
            TokenUsage(),
        )

        return AgentResult(
            answer=answer,
            activated_skills=list(self._deps.activated_skills),
            tool_log=list(self._deps.tool_log),
            todo_list=list(self._deps.todo_list),
            usage=usage,
            events=events,
        )

    async def _event_stream(self, prompt: str) -> AsyncGenerator[AgentEvent, None]:
        """The core async generator that both run() and run_stream() rely on.

        Translates raw pydantic-ai stream events into typed AgentEvent objects:
          FunctionToolCallEvent   → ToolCallEvent
          FunctionToolResultEvent → ToolResultEvent  (+ TodoUpdateEvent for manage_todos)
          PartStartEvent (text)   → TextDeltaEvent (initial chunk in TextPart.content)
          PartDeltaEvent          → TextDeltaEvent
          AgentRunResultEvent     → RunCompleteEvent
        """
        hist = self._conversation_messages or None
        async for raw in self._runner.run_stream_events(
            prompt,
            deps=self._deps,
            model_settings=self._model_settings,
            usage_limits=self._usage_limits,
            message_history=hist,
        ):
            # The LLM is calling a tool
            if isinstance(raw, FunctionToolCallEvent):
                yield ToolCallEvent(
                    name=raw.part.tool_name,
                    args=raw.part.args_as_dict(),
                )

            # A tool call just finished
            elif isinstance(raw, FunctionToolResultEvent):
                yield ToolResultEvent(name=raw.result.tool_name)

                # manage_todos modifies the todo list in _deps — emit the new state
                if raw.result.tool_name == "manage_todos":
                    yield TodoUpdateEvent(items=list(self._deps.todo_list))

            # First chunk of streamed text often arrives on part start, not only in deltas
            elif isinstance(raw, PartStartEvent):
                if isinstance(raw.part, TextPart) and raw.part.content:
                    yield TextDeltaEvent(content=raw.part.content)

            # The model is streaming its answer token by token
            elif isinstance(raw, PartDeltaEvent):
                if isinstance(raw.delta, TextPartDelta):
                    yield TextDeltaEvent(content=raw.delta.content_delta)

            # The run is complete — emit final token usage
            elif isinstance(raw, AgentRunResultEvent):
                self._conversation_messages[:] = list(raw.result.all_messages())
                run_usage = raw.result.usage()
                yield RunCompleteEvent(
                    usage=TokenUsage(
                        input_tokens=run_usage.input_tokens or 0,
                        output_tokens=run_usage.output_tokens or 0,
                    ),
                )


# ── System prompt ──────────────────────────────────────────────────────
#
# The system prompt lists skill names and descriptions only — not their
# full bodies. The LLM must call use_skill to load the full instructions.
# This keeps the prompt lean regardless of how many skills are registered.

def _build_system_prompt(skills: dict[str, Skill], extra: str | None) -> str:
    today = date.today().isoformat()

    skill_lines = "\n".join(
        f"  - **{name}**: {skill.description}" for name, skill in skills.items()
    )

    prompt = f"""You are a general-purpose task-solving AI agent.

Today's date: {today}

## Available skills (call `use_skill` to load full instructions)
{skill_lines}

## Built-in tools
  - **use_skill**: Load a skill's instructions by name.
  - **manage_todos**: Plan and track your task list.
  - **read_reference**: Read a reference doc bundled with a skill.
  - **run_script**: Run a Python script bundled with a skill.

## Rules
1. Plan first: call `manage_todos` with action "set" to create a task list.
2. Pick the most relevant skill and call `use_skill` to load its instructions.
3. Work through your task list, updating item statuses as you go.
4. Use `read_reference` and `run_script` to access skill resources as needed.
5. Adapt: add, remove, or reorder tasks if you learn something new.
6. Return a concise final answer."""

    if extra:
        prompt += f"\n\n{extra}"
    return prompt


# ── Runner factory ─────────────────────────────────────────────────────
#
# Builds the underlying pydantic-ai Agent with all four built-in tools.
# This is called once on Agent.__init__ and reused for every run.

def _resolve_skill_dir(skill: Skill) -> Path:
    """Return the directory containing the skill's SKILL.md file."""
    if skill.path is None:
        raise ValueError(f"Skill '{skill.name}' has no path — cannot access resources.")
    return skill.path.parent


def _preview(text: str, limit: int = 300) -> str:
    """Truncate text to `limit` chars for log previews."""
    return text if len(text) <= limit else text[:limit] + "..."


def _normalize_task_line(entry: Any) -> str | None:
    """Turn one model-supplied task entry into a single-line string for TodoItem.content."""
    if entry is None:
        return None
    if isinstance(entry, str):
        s = entry.strip()
        return s or None
    if isinstance(entry, dict):
        for key in ("content", "text", "task", "title", "item", "description"):
            v = entry.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return None
    s = str(entry).strip()
    return s or None


def _coerce_todo_id(raw: Any) -> int | None:
    """Models often send id as a string; TodoItem.id is int."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.strip().lstrip("-").isdigit():
        return int(raw.strip())
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _parse_todo_status(raw: Any) -> TodoStatus | None:
    """Accept common synonyms (e.g. completed → done) for manage_todos updates."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, TodoStatus):
        return raw
    s = str(raw).lower().strip().replace(" ", "_").replace("-", "_")
    aliases: dict[str, TodoStatus] = {
        "complete": TodoStatus.done,
        "completed": TodoStatus.done,
        "finished": TodoStatus.done,
        "resolved": TodoStatus.done,
        "done": TodoStatus.done,
        "in_progress": TodoStatus.in_progress,
        "inprogress": TodoStatus.in_progress,
        "progress": TodoStatus.in_progress,
        "working": TodoStatus.in_progress,
        "active": TodoStatus.in_progress,
        "started": TodoStatus.in_progress,
        "pending": TodoStatus.pending,
        "todo": TodoStatus.pending,
        "open": TodoStatus.pending,
        "not_started": TodoStatus.pending,
        "notstarted": TodoStatus.pending,
    }
    if s in aliases:
        return aliases[s]
    try:
        return TodoStatus(s)
    except ValueError:
        return None


def _create_runner(
    model: Model,
    system_prompt: str,
    skills: dict[str, Skill],
) -> PydanticAgent[_RunDeps, str]:
    """Build the pydantic-ai runner and register all built-in tools."""

    runner: PydanticAgent[_RunDeps, str] = PydanticAgent(
        model=model,
        system_prompt=system_prompt,
        deps_type=_RunDeps,
        output_type=str,
    )

    # ── use_skill ─────────────────────────────────────────────────────
    # Loads the full body of a skill into the LLM's context, along with
    # a list of available bundled resources (scripts, references, assets).

    @runner.tool(description=(
        "Load a skill's full instructions by name. "
        "Call this BEFORE performing any domain-specific actions."
    ))
    def use_skill(ctx: RunContext[_RunDeps], skill_name: str) -> str:
        skill = ctx.deps.skills.get(skill_name)
        if not skill:
            available = ", ".join(ctx.deps.skills)
            return f"Skill '{skill_name}' not found. Available: {available}"

        ctx.deps.activated_skills.append(skill_name)

        # Start with the full skill body
        parts = [f"## Skill: {skill_name}\n\n{skill.body}"]

        # List any bundled resources so the LLM knows what it can access
        resources: list[str] = []
        if skill.scripts:
            resources.append(f"  Scripts: {', '.join(skill.scripts)}")
        if skill.references:
            resources.append(f"  References: {', '.join(skill.references)}")
        if skill.assets:
            resources.append(f"  Assets: {', '.join(skill.assets)}")

        if resources:
            parts.append("\n\n## Bundled Resources\n" + "\n".join(resources))
            parts.append(
                "\nUse `read_reference` to load docs and `run_script` to execute scripts."
            )

        parts.append("\n\nFollow these instructions.")
        return "".join(parts)

    # ── manage_todos ──────────────────────────────────────────────────
    # Lets the LLM maintain an internal task list. The list is stored in
    # _RunDeps.todo_list so it's visible to the event stream.

    @runner.tool(description=(
        "Manage your internal task list. "
        'Actions: "set" (replace list), "add" (append), "update" (change status), "remove" (delete). '
        "For action set, pass task strings in `items` (top-level array) or in `payload.items` or `payload.tasks`. "
        "For add, pass `content` top-level or in payload. "
        "For update/remove, pass numeric `id` in payload (string or int). "
        'Status for update: "pending", "in_progress", or "done" (also: completed, finished).'
    ))
    def manage_todos(
        ctx: RunContext[_RunDeps],
        action: str,
        payload: dict[str, Any] | None = None,
        items: list[Any] | None = None,
        content: str | None = None,
        item_id: int | str | None = None,
        status: str | None = None,
    ) -> str:
        todos = ctx.deps.todo_list
        pl = dict(payload or {})
        if content is not None and str(content).strip():
            pl.setdefault("content", str(content).strip())
        if item_id is not None:
            pl.setdefault("id", item_id)
        if status is not None and str(status).strip():
            pl.setdefault("status", str(status).strip())

        def _item_list_for_set() -> list[Any]:
            if items is not None:
                return list(items)
            if "items" in pl:
                return list(pl["items"])
            if "tasks" in pl:
                return list(pl["tasks"])
            return []

        if action == "set":
            todos.clear()
            ctx.deps._next_todo_id = 1
            for entry in _item_list_for_set():
                text = _normalize_task_line(entry)
                if text:
                    todos.append(TodoItem(id=ctx.deps._next_todo_id, content=text))
                    ctx.deps._next_todo_id += 1

        elif action == "add":
            raw = pl.get("content") or pl.get("text") or pl.get("task")
            if raw is None and items is not None and len(items) == 1:
                raw = _normalize_task_line(items[0])
            line = _normalize_task_line(raw) if raw is not None else None
            if line:
                todos.append(TodoItem(id=ctx.deps._next_todo_id, content=line))
                ctx.deps._next_todo_id += 1

        elif action == "update":
            tid = _coerce_todo_id(pl.get("id"))
            new_status = _parse_todo_status(pl.get("status"))
            if tid is not None and new_status is not None:
                for item in todos:
                    if item.id == tid:
                        item.status = new_status
                        break

        elif action == "remove":
            tid = _coerce_todo_id(pl.get("id"))
            if tid is not None:
                ctx.deps.todo_list = [t for t in todos if t.id != tid]

        else:
            return f"Unknown action '{action}'. Use: set, add, update, remove."

        return json.dumps([t.model_dump() for t in ctx.deps.todo_list], indent=2)

    # ── read_reference ────────────────────────────────────────────────
    # Reads a file from a skill's references/ directory into the LLM's context.

    @runner.tool(description=(
        "Read a reference document bundled with a skill. "
        "Provide the skill_name and filename (e.g. 'api_guide.md'). "
        "Only works for files listed in the skill's references."
    ))
    def read_reference(ctx: RunContext[_RunDeps], skill_name: str, filename: str) -> str:
        skill = ctx.deps.skills.get(skill_name)
        if not skill:
            return f"Skill '{skill_name}' not found."
        if filename not in skill.references:
            return f"'{filename}' not in {skill_name}. Available: {skill.references}"

        ref_path = _resolve_skill_dir(skill) / "references" / filename
        if not ref_path.exists():
            return f"File not found on disk: {ref_path}"

        content = ref_path.read_text(encoding="utf-8")

        ctx.deps.tool_log.append(ToolCallRecord(
            tool="read_reference",
            input={"skill_name": skill_name, "filename": filename},
            truncated=len(content) > 15000,
        ))

        return content[:15000]  # Cap to avoid overwhelming the context window

    # ── run_script ────────────────────────────────────────────────────
    # Executes a Python script from a skill's scripts/ directory.
    # An optional `args` string is passed as the first CLI argument.

    @runner.tool(description=(
        "Run a Python script bundled with a skill. "
        "Provide skill_name, filename, and an optional JSON-encoded args string. "
        "Returns JSON with keys: ok, stdout, stderr, exit_code."
    ))
    def run_script(ctx: RunContext[_RunDeps], skill_name: str, filename: str, args: str = "") -> str:
        # Validate skill and script exist
        skill = ctx.deps.skills.get(skill_name)
        if not skill:
            return json.dumps({"ok": False, "stdout": "", "stderr": f"Skill '{skill_name}' not found.", "exit_code": 2})
        if filename not in skill.scripts:
            return json.dumps({"ok": False, "stdout": "", "stderr": f"Script '{filename}' not in {skill_name}. Available: {skill.scripts}", "exit_code": 2})

        script_path = _resolve_skill_dir(skill) / "scripts" / filename
        if not script_path.exists():
            return json.dumps({"ok": False, "stdout": "", "stderr": f"File not found on disk: {script_path}", "exit_code": 2})

        # Run the script in a subprocess with a 30-second timeout
        cmd = [sys.executable, str(script_path)]
        if args:
            cmd.append(args)

        stdout, stderr, exit_code, ok, truncated = "", "", 1, False, False

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(script_path.parent),
            )
            stdout, stderr = proc.stdout or "", proc.stderr or ""
            exit_code = proc.returncode
            ok = proc.returncode == 0
        except subprocess.TimeoutExpired:
            stderr, exit_code = "Script timed out after 30 seconds.", 124
        except Exception as e:
            stderr, exit_code = f"Error running script: {e}", 1

        # Truncate large outputs so they don't overwhelm the context window
        max_chars = 7000
        if len(stdout) > max_chars:
            stdout, truncated = stdout[:max_chars] + "...[truncated]", True
        if len(stderr) > max_chars:
            stderr, truncated = stderr[:max_chars] + "...[truncated]", True

        response = json.dumps({"ok": ok, "stdout": stdout, "stderr": stderr, "exit_code": exit_code})

        ctx.deps.tool_log.append(ToolCallRecord(
            tool="run_script",
            input={"skill_name": skill_name, "filename": filename, "args": args},
            output_preview=_preview(response, limit=500),
            truncated=truncated,
        ))

        return response[:15000]

    return runner
