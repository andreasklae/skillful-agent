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
    agent.run(prompt, files=[...])        → AgentResult   (blocking, collects all events)
    agent.run_stream(prompt, files=[...]) → AsyncGenerator[AgentEvent, ...]  (live stream)

Optional ``files=`` attaches local paths (text inlined, images as vision parts, PDF as
extracted text if the ``[pdf]`` extra is installed). With ``AgentConfig.user_file_roots``,
the model can call ``read_user_file`` for on-demand reads under those directories.

Conversation state is kept on the agent between ``run`` / ``run_stream`` calls.
Call ``agent.clear_conversation()`` to start a new thread.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from collections.abc import Sequence
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
    ClientFunctionRequest,
    ClientFunctionRequestEvent,
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
from .inbox import Inbox
from .messages import Message, MessageType
from .user_prompt_files import build_user_message, resolve_allowed_user_path

logger = logging.getLogger(__name__)


# ── Per-run state ──────────────────────────────────────────────────────
#
# Mutable state shared across tool calls. ``todo_list`` persists across
# ``run`` / ``run_stream`` on the same agent; other fields reset each run.
# pydantic-ai passes deps via RunContext.deps.

@dataclass
class _RunDeps:
    skills: dict[str, Skill]
    # New infrastructure (references to Agent instance attributes)
    inbox: Inbox = field(default_factory=Inbox)
    message_log: list[Message] = field(default_factory=list)
    context_window: list[Message] = field(default_factory=list)
    active_subagents: dict = field(default_factory=dict)
    context_compression_threshold: int = 100_000
    _agent_ref: Any = field(default=None)
    _singleton_subagents: dict[str, str] = field(default_factory=dict)
    # Original fields
    activated_skills: list[str] = field(default_factory=list)
    tool_log: list[ToolCallRecord] = field(default_factory=list)
    todo_list: list[TodoItem] = field(default_factory=list)
    _next_todo_id: int = 1
    user_file_roots: tuple[Path, ...] = field(default_factory=tuple)
    max_user_file_read_chars: int = 15000
    user_skills_dirs: tuple[Path, ...] = field(default_factory=tuple)
    pending_client_requests: list[ClientFunctionRequest] = field(default_factory=list)


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
        skills_dir: Path,
        config: AgentConfig | None = None,
    ) -> None:
        from .registry import discover_skills

        # Discover native skills shipped with the SDK (native-skills/ next to this package)
        native_skills_dir = Path(__file__).resolve().parent.parent / "native-skills"
        native_skills = discover_skills(native_skills_dir) if native_skills_dir.is_dir() else {}

        # Discover user skills from the provided directory (recurses into subdirectories)
        skills = discover_skills(skills_dir)
        if not skills and not native_skills:
            raise RuntimeError(f"No skills found in {skills_dir}. Add at least one SKILL.md.")

        # Merge: user skills take precedence over native skills with the same name
        all_skills = {**native_skills, **skills}

        cfg = config or AgentConfig()

        self._skills = all_skills
        self._config = cfg

        # New infrastructure
        self.inbox = Inbox()
        self.message_log: list[Message] = []
        self.context_window: list[Message] = []
        self.subagent_logs: dict[str, list[Message]] = {}
        self._active_subagents: dict[str, Any] = {}
        self._subagent_tasks: dict[str, asyncio.Task] = {}
        self._singleton_subagents: dict[str, str] = {}
        self._running: bool = False
        self._pending_inbox_notification: bool = False

        roots = tuple(Path(p).expanduser().resolve() for p in cfg.user_file_roots)

        # _deps holds mutable per-run state; references to Agent instance attributes
        self._deps = _RunDeps(
            skills=all_skills,
            inbox=self.inbox,
            message_log=self.message_log,
            context_window=self.context_window,
            active_subagents=self._active_subagents,
            context_compression_threshold=cfg.context_compression_threshold,
            _agent_ref=self,
            _singleton_subagents=self._singleton_subagents,
            user_file_roots=roots,
            max_user_file_read_chars=cfg.max_user_file_read_chars,
            user_skills_dirs=(Path(skills_dir).resolve(),),
        )

        # Build the system prompt and the underlying pydantic-ai runner
        system_prompt = _build_system_prompt(all_skills, cfg.system_prompt_extra)

        if roots:
            listed = ", ".join(str(r) for r in roots)
            system_prompt += (
                "\n\n## User file access\n"
                f"Additional files may live under: {listed}. "
                "Call `read_user_file` with a path relative to one of these roots, "
                "or an absolute path that stays inside them."
            )
        self._runner = _create_runner(model, system_prompt, roots)
        self._model_settings = ModelSettings(max_tokens=cfg.max_tokens)
        self._usage_limits = UsageLimits(
            request_limit=cfg.max_turns if cfg.max_turns is not None else None,
        )

        # pydantic-ai ModelMessage list; updated after each completed run
        self._conversation_messages: list[Any] = []

    # ── Public API ────────────────────────────────────────────────────

    def clear_conversation(self) -> None:
        """Drop all remembered turns, todo list, message stores, and inbox."""
        self._conversation_messages.clear()
        self._deps.todo_list.clear()
        self._deps._next_todo_id = 1
        self.message_log.clear()
        self.context_window.clear()
        self.inbox = Inbox()
        self._deps.inbox = self.inbox

    def run(self, prompt: str, *, files: Sequence[Path | str] | None = None) -> AgentResult:
        """Run the agent and wait for the full answer.

        Internally this drives the same _event_stream as run_stream, but
        collects all events before returning. The AgentResult includes
        the full event timeline, so you can inspect it after the fact:

            result = agent.run("question")
            todos   = [e for e in result.events if isinstance(e, TodoUpdateEvent)]
            tools   = [e for e in result.events if isinstance(e, ToolCallEvent)]
            answer  = result.answer  # or join TextDeltaEvents yourself

        Pass ``files=`` with paths to attach text (read as UTF-8), images (vision), or
        PDFs (text extraction; requires ``pip install 'skill-agent[pdf]'``).
        """
        user_message = self._prepare_user_message(prompt, files)

        self._reset_run_state()
        return asyncio.run(self._collect_run(user_message))

    def run_stream(
        self, prompt: str, *, files: Sequence[Path | str] | None = None
    ) -> AsyncGenerator[AgentEvent, None]:
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
        user_message = self._prepare_user_message(prompt, files)

        self._reset_run_state()

        # Return the async generator directly. The caller is responsible
        # for iterating it inside an async context.
        return self._event_stream(user_message)

    # ── Internal helpers ──────────────────────────────────────────────

    def _prepare_user_message(
        self, prompt: str, files: Sequence[Path | str] | None
    ) -> str | list[Any]:
        msg = build_user_message(
            prompt,
            files,
            max_text_file_chars=self._config.max_attached_text_file_chars,
        )
        if isinstance(msg, str):
            if not msg.strip():
                raise ValueError("Prompt cannot be empty unless you attach files.")
        elif not msg or (isinstance(msg[0], str) and not str(msg[0]).strip() and len(msg) == 1):
            raise ValueError("Prompt cannot be empty unless you attach files.")
        return msg

    def _reset_run_state(self) -> None:
        """Clear per-run bookkeeping. Todo list and IDs persist across turns; clear via clear_conversation()."""
        self._deps.activated_skills.clear()
        self._deps.tool_log.clear()
        self._deps.pending_client_requests.clear()

    @property
    def current_todos(self) -> list[TodoItem]:
        """Copy of the live task list (persists across ``run`` / ``run_stream`` until cleared)."""
        return list(self._deps.todo_list)

    async def _collect_run(self, user_message: str | list[Any]) -> AgentResult:
        """Collect all events from _event_stream and build an AgentResult."""
        events: list[AgentEvent] = []
        async for event in self._event_stream(user_message):
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

    async def _event_stream(self, user_message: str | list[Any]) -> AsyncGenerator[AgentEvent, None]:
        """The core async generator that both run() and run_stream() rely on.

        Translates raw pydantic-ai stream events into typed AgentEvent objects:
          FunctionToolCallEvent   → ToolCallEvent
          FunctionToolResultEvent → ToolResultEvent  (+ TodoUpdateEvent for manage_todos)
          PartStartEvent (text)   → TextDeltaEvent (initial chunk in TextPart.content)
          PartDeltaEvent          → TextDeltaEvent
          AgentRunResultEvent     → RunCompleteEvent
        """
        # Log the user message to both stores
        user_content = user_message if isinstance(user_message, str) else str(user_message)
        user_msg = Message(type=MessageType.user, content=user_content)
        self.message_log.append(user_msg)
        self.context_window.append(user_msg)
        self._running = True
        answer_chunks: list[str] = []

        hist = self._conversation_messages or None
        async for raw in self._runner.run_stream_events(
            user_message,
            deps=self._deps,
            model_settings=self._model_settings,
            usage_limits=self._usage_limits,
            message_history=hist,
        ):
            # The LLM is calling a tool
            if isinstance(raw, FunctionToolCallEvent):
                args = dict(raw.part.args_as_dict())
                act = args.pop("activity", None)
                if isinstance(act, str):
                    act = act.strip() or None
                else:
                    act = None
                logger.debug(
                    "tool_call  %-20s  args=%s",
                    raw.part.tool_name,
                    json.dumps(args, ensure_ascii=False, default=str)[:400],
                )
                yield ToolCallEvent(
                    name=raw.part.tool_name,
                    args=args,
                    activity=act,
                )
                tool_msg = Message(
                    type=MessageType.tool_call,
                    content={"tool": raw.part.tool_name, "description": act or raw.part.tool_name},
                )
                self.message_log.append(tool_msg)
                self.context_window.append(tool_msg)

            # A tool call just finished
            elif isinstance(raw, FunctionToolResultEvent):
                logger.debug(
                    "tool_result %-20s  result=%s",
                    raw.result.tool_name,
                    str(raw.result.content)[:400],
                )
                yield ToolResultEvent(name=raw.result.tool_name)
                result_msg = Message(
                    type=MessageType.tool_result,
                    content={"tool": raw.result.tool_name},
                )
                self.message_log.append(result_msg)
                self.context_window.append(result_msg)

                # manage_todos modifies the todo list in _deps — emit the new state
                if raw.result.tool_name == "manage_todos":
                    yield TodoUpdateEvent(items=list(self._deps.todo_list))

                # call_client_function queues requests in _deps — emit and clear them
                if raw.result.tool_name == "call_client_function" and self._deps.pending_client_requests:
                    yield ClientFunctionRequestEvent(
                        requests=list(self._deps.pending_client_requests),
                    )
                    self._deps.pending_client_requests.clear()

            # First chunk of streamed text often arrives on part start, not only in deltas
            elif isinstance(raw, PartStartEvent):
                if isinstance(raw.part, TextPart) and raw.part.content:
                    answer_chunks.append(raw.part.content)
                    yield TextDeltaEvent(content=raw.part.content)

            # The model is streaming its answer token by token
            elif isinstance(raw, PartDeltaEvent):
                if isinstance(raw.delta, TextPartDelta):
                    answer_chunks.append(raw.delta.content_delta)
                    yield TextDeltaEvent(content=raw.delta.content_delta)

            # The run is complete — emit final token usage
            elif isinstance(raw, AgentRunResultEvent):
                self._conversation_messages[:] = list(raw.result.all_messages())

                # Log the full agent response as a single message
                full_answer = "".join(answer_chunks)
                if full_answer:
                    agent_msg = Message(type=MessageType.agent, content=full_answer)
                    self.message_log.append(agent_msg)
                    self.context_window.append(agent_msg)

                run_usage = raw.result.usage()

                # Auto-compression check
                input_tokens = run_usage.input_tokens or 0
                threshold = self._deps.context_compression_threshold
                if input_tokens > threshold and len(self.context_window) > 1:
                    from .context_tools import compress_all_impl, build_generic_summary
                    summary, instruction = build_generic_summary(
                        self.message_log, self._deps.todo_list
                    )
                    compress_all_impl(
                        self.message_log, self.context_window, summary, instruction
                    )

                yield RunCompleteEvent(
                    usage=TokenUsage(
                        input_tokens=input_tokens,
                        output_tokens=run_usage.output_tokens or 0,
                    ),
                )

        self._running = False

        # Check for pending inbox notifications
        if self.inbox.pending_notifications():
            self._pending_inbox_notification = True


# ── System prompt ──────────────────────────────────────────────────────
#
# The system prompt lists skill names and descriptions only — not their
# full bodies. The LLM must call use_skill to load the full instructions.
# This keeps the prompt lean regardless of how many skills are registered.

_SYSTEM_PROMPT_TEMPLATE_PATH = Path(__file__).with_name("system_prompt.md")


def _build_system_prompt(skills: dict[str, Skill], extra: str | None) -> str:
    template = _SYSTEM_PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8").strip()
    skill_lines = "\n".join(
        f"  - **{name}**: {skill.description}" for name, skill in skills.items()
    )
    skills_section = (
        "## Available skills (call `use_skill` to load full instructions)\n"
        f"{skill_lines}"
    )
    lines = template.splitlines()
    try:
        first_heading = next(i for i, line in enumerate(lines) if line.startswith("## "))
    except StopIteration:
        prompt = f"{template}\n\n{skills_section}"
    else:
        head = "\n".join(lines[:first_heading]).rstrip()
        tail = "\n".join(lines[first_heading:])
        prompt = f"{head}\n\n{skills_section}\n\n{tail}"
    if extra:
        prompt += f"\n\n{extra}"
    return prompt


# ── Runner factory ─────────────────────────────────────────────────────
#
# Builds the underlying pydantic-ai Agent with all built-in tools.
# Tool registration is delegated to focused modules.

def _create_runner(
    model: Model,
    system_prompt: str,
    user_file_roots: tuple[Path, ...],
) -> PydanticAgent[_RunDeps, str]:
    """Build the pydantic-ai runner and register all tools."""
    from .skill_tools import register_skill_tools
    from .context_tools import register_context_tools
    from .inbox_tools import register_inbox_tools, register_spawn_tools

    runner: PydanticAgent[_RunDeps, str] = PydanticAgent(
        model=model,
        system_prompt=system_prompt,
        deps_type=_RunDeps,
        output_type=str,
    )

    register_skill_tools(runner, user_file_roots)
    register_context_tools(runner)
    register_inbox_tools(runner)
    register_spawn_tools(runner)

    return runner
