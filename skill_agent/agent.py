"""Core agent loop with progressive skill disclosure.

This is the heart of the SDK. The flow:

    1. Caller provides discovered skills (each may bundle resources).
    2. Agent builds a system prompt listing skill descriptions only.
    3. LLM calls `manage_todos` to plan its approach.
    4. LLM calls `use_skill` to load full instructions for a skill.
    5. LLM uses `read_reference` / `run_script` to access bundled resources.
    6. Steps 3-5 repeat until the LLM gives a plain-text final answer.
    7. Agent returns a typed AgentResult.

Plug-and-play usage — drop a skill folder and it just works:

    skills = discover_skills()
    agent = Agent(model=model, skills=skills)
    result = agent.solve("your question")
"""

import asyncio
import json
import logging
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from pydantic_ai import Agent as PydanticAgent, RunContext
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    TextPartDelta,
)
from pydantic_ai.run import AgentRunResultEvent
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from .models import (
    AgentConfig,
    AgentResult,
    Skill,
    TodoItem,
    TodoStatus,
    ToolCallRecord,
    TokenUsage,
)

logger = logging.getLogger(__name__)

# Descriptions for built-in tools (always present in every run)
_USE_SKILL_DESCRIPTION = (
    "Load a skill's full instructions by name. "
    "Call this BEFORE performing any domain-specific actions."
)
_MANAGE_TODOS_DESCRIPTION = (
    "Manage your internal task list. Actions: "
    '"set" (replace list), "add" (append item), "update" (change status), "remove" (delete item). '
    "Always plan before acting."
)
_READ_REFERENCE_DESCRIPTION = (
    "Read a reference document bundled with a skill. "
    "Provide the skill_name and filename (e.g. 'api_guide.md'). "
    "Only works for files listed in the skill's references."
)
_RUN_SCRIPT_DESCRIPTION = (
    "Run a Python script bundled with a skill. "
    "Provide the skill_name, filename, and an optional JSON-encoded args string. "
    "The args string is passed as the first CLI argument to the script. "
    "Only works for files listed in the skill's scripts."
)


@dataclass
class _RunDeps:
    """Mutable state that travels through a single agent run.

    pydantic-ai passes this object into every tool call via RunContext.deps,
    so tools can read skills and accumulate logs without global state.
    """

    skills: dict[str, Skill]
    activated_skills: list[str] = field(default_factory=list)
    tool_log: list[ToolCallRecord] = field(default_factory=list)
    todo_list: list[TodoItem] = field(default_factory=list)
    _next_todo_id: int = 1


class Agent:
    """Skill-based agent. Initialize once, call solve(prompt) as many times as needed.

    Point it at one or more directories of skill folders and it discovers them automatically.
    When multiple directories are given, later entries override earlier ones on name conflict.

    Examples:
        agent = Agent(model=model, skills_dir=Path("skills"))
        agent = Agent(model=model, skills_dir=[Path("skills"), Path("extra_skills")])
        result = agent.solve("Who invented the telephone?")
        print(result.answer)
    """

    def __init__(
        self,
        *,
        model: Model,
        skills_dir: Path | list[Path],
        config: AgentConfig | None = None,
    ) -> None:
        from .registry import discover_skills

        skills = discover_skills(skills_dir)
        if not skills:
            dirs = skills_dir if isinstance(skills_dir, list) else [skills_dir]
            raise RuntimeError(f"No skills found in {dirs}. Add at least one SKILL.md.")

        cfg = config or AgentConfig()

        self._skills = skills
        self._deps = _RunDeps(skills=skills)

        system = _build_system_prompt(skills, cfg.system_prompt_extra)
        self._runner = _create_runner(model, system, skills)
        self._model_settings = ModelSettings(max_tokens=cfg.max_tokens)
        self._usage_limits = UsageLimits(request_limit=cfg.max_turns)

    def _reset_run_state(self) -> None:
        """Reset per-run state so each solve() call starts clean."""
        self._deps.activated_skills.clear()
        self._deps.tool_log.clear()
        self._deps.todo_list.clear()
        self._deps._next_todo_id = 1

    def _build_result(self, answer: str, input_tokens: int, output_tokens: int) -> AgentResult:
        """Build an AgentResult from the current run state."""
        return AgentResult(
            answer=answer,
            activated_skills=list(self._deps.activated_skills),
            tool_log=list(self._deps.tool_log),
            todo_list=list(self._deps.todo_list),
            usage=TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens),
        )

    def solve(self, prompt: str) -> AgentResult:
        """Run one prompt through the skill agent and return a typed result.

        Blocks until the agent finishes. For real-time output, use solve_stream().
        """
        if not prompt.strip():
            raise ValueError("Prompt cannot be empty.")

        self._reset_run_state()

        run_result = asyncio.run(
            self._runner.run(
                prompt,
                deps=self._deps,
                model_settings=self._model_settings,
                usage_limits=self._usage_limits,
            )
        )

        run_usage = run_result.usage()
        return self._build_result(
            answer=run_result.output.strip(),
            input_tokens=run_usage.input_tokens or 0,
            output_tokens=run_usage.output_tokens or 0,
        )

    def solve_stream(self, prompt: str) -> AgentResult:
        """Run one prompt with real-time streaming output to the console.

        Shows tool calls, todo progress, and the final answer token-by-token.
        Returns the same AgentResult as solve().
        """
        if not prompt.strip():
            raise ValueError("Prompt cannot be empty.")

        self._reset_run_state()
        return asyncio.run(self._solve_stream_async(prompt))

    async def _solve_stream_async(self, prompt: str) -> AgentResult:
        """Internal async implementation of solve_stream()."""
        from .stream import StreamPrinter

        printer = StreamPrinter()
        answer_parts: list[str] = []
        input_tokens = 0
        output_tokens = 0

        async for event in self._runner.run_stream_events(
            prompt,
            deps=self._deps,
            model_settings=self._model_settings,
            usage_limits=self._usage_limits,
        ):
            if isinstance(event, FunctionToolCallEvent):
                printer.handle_tool_call(event)
            elif isinstance(event, FunctionToolResultEvent):
                printer.handle_tool_result(event, self._deps.todo_list)
            elif isinstance(event, PartDeltaEvent):
                if isinstance(event.delta, TextPartDelta):
                    answer_parts.append(event.delta.content_delta)
                    printer.handle_text_delta(event.delta.content_delta)
            elif isinstance(event, AgentRunResultEvent):
                run_usage = event.result.usage()
                input_tokens = run_usage.input_tokens or 0
                output_tokens = run_usage.output_tokens or 0

        printer.finish()

        answer = "".join(answer_parts)
        return self._build_result(
            answer=answer,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )


# ── System Prompt ──────────────────────────────────────────────────────


def _build_system_prompt(
    skills: dict[str, Skill],
    extra: str | None,
) -> str:
    """Build the system prompt listing skill descriptions only.

    The LLM sees skill descriptions (not bodies) — it must call use_skill to
    get the full instructions. This keeps the prompt lean even with many skills.
    """
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


# ── Runner Factory ─────────────────────────────────────────────────────


def _resolve_skill_dir(skill: Skill):
    """Get the directory a skill lives in (parent of SKILL.md)."""
    if skill.path is None:
        raise ValueError(f"Skill '{skill.name}' has no path — cannot access resources.")
    return skill.path.parent


def _create_runner(
    model: Model,
    system_prompt: str,
    skills: dict[str, Skill],
) -> PydanticAgent[_RunDeps, str]:
    """Build the pydantic-ai runner with all built-in tools.

    Built-in tools:
      - use_skill: load skill instructions (lists available resources)
      - manage_todos: internal task planning
      - read_reference: read a reference doc from a skill's references/ dir
      - run_script: execute a Python script from a skill's scripts/ dir
    """

    runner: PydanticAgent[_RunDeps, str] = PydanticAgent(
        model=model,
        system_prompt=system_prompt,
        output_type=str,
    )

    # ── Built-in: use_skill ───────────────────────────────────────────
    # Injects the full skill body + lists available resources.

    @runner.tool(description=_USE_SKILL_DESCRIPTION)
    def use_skill(ctx: RunContext[_RunDeps], skill_name: str) -> str:
        skill = ctx.deps.skills.get(skill_name)
        if not skill:
            return f"Skill '{skill_name}' not found. Available: {', '.join(ctx.deps.skills)}"

        ctx.deps.activated_skills.append(skill_name)

        # Build the response: instructions + available resources
        parts = [f"## Skill: {skill_name}\n\n{skill.body}"]

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
                "\nUse `read_reference` to load reference docs and "
                "`run_script` to execute scripts."
            )

        parts.append("\n\nFollow these instructions.")
        return "".join(parts)

    # ── Built-in: manage_todos ────────────────────────────────────────

    @runner.tool(description=_MANAGE_TODOS_DESCRIPTION)
    def manage_todos(ctx: RunContext[_RunDeps], action: str, payload: dict[str, Any]) -> str:
        todos = ctx.deps.todo_list

        if action == "set":
            items = payload.get("items", [])
            todos.clear()
            ctx.deps._next_todo_id = 1
            for text in items:
                todos.append(TodoItem(id=ctx.deps._next_todo_id, content=text))
                ctx.deps._next_todo_id += 1

        elif action == "add":
            content = payload.get("content", "")
            if content:
                todos.append(TodoItem(id=ctx.deps._next_todo_id, content=content))
                ctx.deps._next_todo_id += 1

        elif action == "update":
            item_id = payload.get("id")
            new_status = payload.get("status", "")
            for item in todos:
                if item.id == item_id:
                    item.status = TodoStatus(new_status)
                    break

        elif action == "remove":
            item_id = payload.get("id")
            ctx.deps.todo_list = [t for t in todos if t.id != item_id]

        else:
            return f"Unknown action '{action}'. Use: set, add, update, remove."

        return json.dumps([t.model_dump() for t in ctx.deps.todo_list], indent=2)

    # ── Built-in: read_reference ──────────────────────────────────────
    # Reads a file from a skill's references/ directory into context.

    @runner.tool(description=_READ_REFERENCE_DESCRIPTION)
    def read_reference(ctx: RunContext[_RunDeps], skill_name: str, filename: str) -> str:
        skill = ctx.deps.skills.get(skill_name)
        if not skill:
            return f"Skill '{skill_name}' not found."
        if filename not in skill.references:
            return f"Reference '{filename}' not found in {skill_name}. Available: {skill.references}"

        ref_path = _resolve_skill_dir(skill) / "references" / filename
        if not ref_path.exists():
            return f"File not found on disk: {ref_path}"

        content = ref_path.read_text(encoding="utf-8")
        ctx.deps.tool_log.append(
            ToolCallRecord(tool="read_reference", input={"skill_name": skill_name, "filename": filename}, truncated=len(content) > 15000)
        )
        return content[:15000]

    # ── Built-in: run_script ──────────────────────────────────────────
    # Executes a Python script from a skill's scripts/ directory.
    # The optional args parameter is passed as the first CLI argument,
    # so scripts can receive structured input as a JSON string.

    @runner.tool(description=_RUN_SCRIPT_DESCRIPTION)
    def run_script(ctx: RunContext[_RunDeps], skill_name: str, filename: str, args: str = "") -> str:
        skill = ctx.deps.skills.get(skill_name)
        if not skill:
            return f"Skill '{skill_name}' not found."
        if filename not in skill.scripts:
            return f"Script '{filename}' not found in {skill_name}. Available: {skill.scripts}"

        script_path = _resolve_skill_dir(skill) / "scripts" / filename
        if not script_path.exists():
            return f"File not found on disk: {script_path}"

        cmd = [sys.executable, str(script_path)]
        if args:
            cmd.append(args)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(script_path.parent),
            )
            output = result.stdout
            if result.returncode != 0:
                output += f"\n[stderr]\n{result.stderr}" if result.stderr else ""
                output += f"\n[exit code: {result.returncode}]"
        except subprocess.TimeoutExpired:
            output = "Script timed out after 30 seconds."
        except Exception as e:
            output = f"Error running script: {e}"

        ctx.deps.tool_log.append(
            ToolCallRecord(tool="run_script", input={"skill_name": skill_name, "filename": filename, "args": args}, truncated=len(output) > 15000)
        )
        return output[:15000]

    return runner
