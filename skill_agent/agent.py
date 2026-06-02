"""Core agent loop with progressive skill disclosure.

The agent works like this:

    1. On init, it scans skills_dir and builds a system prompt listing
       skill names and descriptions (not full instructions).

    2. When run() or run_stream() is called, the LLM starts the loop:
         a. Calls manage_todos to plan its approach.
         b. Calls use_skill to load the full instructions for a skill.
         c. Uses the skill's typed script tools (<skill>__<script>) and
            read_reference to access bundled resources.
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
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncGenerator

from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
)
from pydantic_ai.run import AgentRunResultEvent
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from .exceptions import AgentContextOverflowError
from .models import (
    AgentConfig,
    AgentEvent,
    AgentResult,
    ClientFunctionRequest,
    ClientFunctionRequestEvent,
    RunCompleteEvent,
    Skill,
    SkillLoadedEvent,
    TextDeltaEvent,
    TodoItem,
    TodoStatus,
    TodoUpdateEvent,
    TokenUsage,
    ToolCallEvent,
    ToolCallRecord,
    ToolResultEvent,
)
from .messages import Message, MessageType
from .threads import ThreadRegistry
from .user_prompt_files import build_user_message, resolve_allowed_user_path
from . import run_queue as _rq
from .run_queue import _QueuedRun, _preview_text

logger = logging.getLogger(__name__)


# ── Per-run state ──────────────────────────────────────────────────────
#
# Mutable state shared across tool calls. ``todo_list`` persists across
# ``run`` / ``run_stream`` on the same agent; other fields reset each run.
# pydantic-ai passes deps via RunContext.deps.

@dataclass
class _RunDeps:
    skills: dict[str, Skill]
    # Thread-based infrastructure (references to Agent instance attributes)
    thread_registry: ThreadRegistry = field(default_factory=ThreadRegistry)
    message_log: list[Message] = field(default_factory=list)
    context_window: list[Message] = field(default_factory=list)
    context_compression_threshold: int = 100_000
    _agent_ref: Any = field(default=None)
    _singleton_agents: dict[str, str] = field(default_factory=dict)
    # Original fields
    activated_skills: list[str] = field(default_factory=list)
    tool_log: list[ToolCallRecord] = field(default_factory=list)
    todo_list: list[TodoItem] = field(default_factory=list)
    _next_todo_id: int = 1
    user_file_roots: tuple[Path, ...] = field(default_factory=tuple)
    max_user_file_read_chars: int = 15000
    max_user_file_write_bytes: int = 2 * 1024 * 1024
    user_skills_dirs: tuple[Path, ...] = field(default_factory=tuple)
    pending_client_requests: list[ClientFunctionRequest] = field(default_factory=list)
    pending_skill_loaded: list[SkillLoadedEvent] = field(default_factory=list)


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
        cfg = config or AgentConfig()
        self._model = model
        self._skills_dir = Path(skills_dir).expanduser().resolve()
        self._native_skills_dir = Path(__file__).resolve().parent.parent / "native-skills"

        self._config = cfg

        # Thread-based infrastructure
        self.thread_registry = ThreadRegistry()
        self.thread_registry.create(name="main", participants=["user"])
        self.message_log: list[Message] = []
        self.context_window: list[Message] = []
        self.subagent_logs: dict[str, list[Message]] = {}
        self._singleton_agents: dict[str, str] = {}
        self._running: bool = False
        self._run_queue: asyncio.Queue[_QueuedRun] | None = None
        self._run_worker_task: asyncio.Task | None = None
        self._queued_runs: dict[str, _QueuedRun] = {}
        self._global_run_subscribers: list[asyncio.Queue[dict[str, Any] | None]] = []
        self._queued_run_keys: set[str] = set()
        self._auto_thread_run_counts: dict[str, int] = {}

        roots = tuple(Path(p).expanduser().resolve() for p in cfg.user_file_roots)

        # _deps holds mutable per-run state; references to Agent instance attributes
        self._deps = _RunDeps(
            skills={},
            thread_registry=self.thread_registry,
            message_log=self.message_log,
            context_window=self.context_window,
            context_compression_threshold=cfg.context_compression_threshold,
            _agent_ref=self,
            _singleton_agents=self._singleton_agents,
            user_file_roots=roots,
            max_user_file_read_chars=cfg.max_user_file_read_chars,
            max_user_file_write_bytes=cfg.max_user_file_write_bytes,
            user_skills_dirs=(self._skills_dir,),
        )

        self._reload_skills(rebuild_runner=False)
        self._runner = _create_runner(
            model,
            self._system_prompt,
            roots,
            skills=self._skills,
            disabled_tools=tuple(cfg.disabled_tools),
            history_processors=list(cfg.history_processors),
        )
        self._model_settings = ModelSettings(max_tokens=cfg.max_tokens)
        self._usage_limits = UsageLimits(
            request_limit=cfg.max_turns if cfg.max_turns is not None else None,
        )

        # pydantic-ai ModelMessage list; updated after each completed run
        self._conversation_messages: list[Any] = []

    # ── Public API ────────────────────────────────────────────────────

    def clear_conversation(self) -> None:
        """Drop all remembered turns, todo list, message stores, threads, and activated skills."""
        self._conversation_messages.clear()
        self._deps.todo_list.clear()
        self._deps._next_todo_id = 1
        self._deps.activated_skills.clear()
        self.message_log.clear()
        self.context_window.clear()
        self.thread_registry = ThreadRegistry()
        self.thread_registry.create(name="main", participants=["user"])
        self._deps.thread_registry = self.thread_registry
        self._singleton_agents.clear()
        self._auto_thread_run_counts.clear()
        logger.info("conversation_cleared")

    @property
    def skills_dir(self) -> Path:
        return self._skills_dir

    def receive_thread_message(
        self,
        thread_name: str,
        content: str,
        source_context: Any = None,
        *,
        allow_create: bool = False,
    ):
        """Send a participant message into a named thread.

        If the thread does not exist and allow_create is True, creates it first.
        Otherwise raises KeyError. Inbound listeners handle notification.
        """
        logger.info(
            "thread_message_received thread=%s sender=%s content=%s allow_create=%s",
            thread_name,
            getattr(source_context, "sender", None),
            content[:200],
            allow_create,
        )
        try:
            thread = self.thread_registry.get(thread_name)
        except KeyError:
            if not allow_create:
                raise
            thread = self.thread_registry.create(name=thread_name)
            self._register_thread_notification(thread)

        msg = thread.send(content, source_context)
        return msg

    def set_user_file_roots(self, roots: list[Path | str]) -> None:
        """Update the user file roots and rebuild the runner with the new paths.

        Affects both read_user_file and write_user_file — they resolve paths
        relative to these roots and reject any path that escapes them.
        """
        resolved = tuple(Path(p).expanduser().resolve() for p in roots)
        self._config = self._config.model_copy(update={"user_file_roots": list(resolved)})
        self._deps.user_file_roots = resolved
        self._deps.max_user_file_write_bytes = self._config.max_user_file_write_bytes
        self._system_prompt = self._build_runtime_system_prompt(self._skills)
        self._runner = _create_runner(
            self._model,
            self._system_prompt,
            resolved,
            skills=self._skills,
            disabled_tools=tuple(getattr(self._config, "disabled_tools", ()) or ()),
            history_processors=list(getattr(self._config, "history_processors", None) or []),
        )
        logger.info("user_file_roots_updated roots=%s", [str(r) for r in resolved])

    def set_skills_dir(self, skills_dir: Path | str) -> list[str]:
        """Update the skills directory, reload all skills, and rebuild the runner."""
        self._skills_dir = Path(skills_dir).expanduser().resolve()
        self._deps.user_skills_dirs = (self._skills_dir,)
        return self._reload_skills(rebuild_runner=True)

    def refresh_skills(self) -> list[str]:
        """Reload native and user skills from disk and rebuild the runner."""
        return self._reload_skills(rebuild_runner=True)

    def add_skill_dir(self, skill_dir: Path | str) -> str:
        """Register a skill directory already present on disk and rebuild runtime state."""
        from .registry import _parse_skill

        resolved = Path(skill_dir).expanduser().resolve()
        skill_md = resolved / "SKILL.md"
        if not skill_md.exists():
            raise ValueError(f"No SKILL.md found at {resolved}")

        if not self._is_relative_to(resolved, self._skills_dir):
            raise ValueError(f"Skill directory must stay inside {self._skills_dir}")

        parsed = _parse_skill(resolved)
        if parsed is None:
            raise ValueError(f"Could not parse SKILL.md at {resolved}")

        self.refresh_skills()
        return parsed.name

    async def enqueue_run(
        self,
        prompt: str,
        *,
        files: Sequence[Path | str] | None = None,
        source: str = "api",
        metadata: dict[str, Any] | None = None,
        coalesce_key: str | None = None,
    ) -> str:
        """Queue a run and return its run_id."""
        return await _rq.enqueue_run(
            self,
            prompt,
            files=files,
            source=source,
            metadata=metadata,
            coalesce_key=coalesce_key,
        )

    async def enqueue_run_message(
        self,
        user_message: str | list[Any],
        *,
        source: str,
        metadata: dict[str, Any] | None = None,
        coalesce_key: str | None = None,
    ) -> str:
        """Queue an already-prepared prompt payload and return its run_id."""
        return await _rq.enqueue_run_message(
            self,
            user_message,
            source=source,
            metadata=metadata,
            coalesce_key=coalesce_key,
        )

    def subscribe_run(self, run_id: str) -> AsyncGenerator[dict[str, Any], None]:
        """Yield lifecycle and agent events for a single queued run."""
        return _rq.subscribe_run(self, run_id)

    def subscribe_all_runs(self) -> AsyncGenerator[dict[str, Any], None]:
        """Yield envelopes for every queued run, including background ones."""
        return _rq.subscribe_all_runs(self)

    def _register_thread_notification(self, thread: Any) -> None:
        """Register an inbound listener on a non-main thread to trigger agent runs."""
        _rq.register_thread_notification(self, thread)

    def _queue_thread_follow_up(self, thread_name: str) -> None:
        """Queue an agent run in response to a new inbound message on a thread."""
        _rq.queue_thread_follow_up(self, thread_name)

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
        logger.info("sync_run_start prompt_preview=%s", _preview_text(user_message))
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
        logger.info("direct_stream_start prompt_preview=%s", _preview_text(user_message))

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
        """Clear per-run bookkeeping. Todo list, activated_skills, and IDs persist across turns; clear via clear_conversation()."""
        self._deps.tool_log.clear()
        self._deps.pending_client_requests.clear()
        self._deps.pending_skill_loaded.clear()

    @property
    def current_todos(self) -> list[TodoItem]:
        """Copy of the live task list (persists across ``run`` / ``run_stream`` until cleared)."""
        return list(self._deps.todo_list)

    async def _collect_run(self, user_message: str | list[Any]) -> AgentResult:
        """Collect all events from _event_stream and build an AgentResult."""
        events: list[AgentEvent] = []
        logger.info("collect_run_start prompt_preview=%s", _preview_text(user_message))
        async for event in self._event_stream(user_message):
            events.append(event)
        logger.info("collect_run_end prompt_preview=%s events=%s", _preview_text(user_message), len(events))
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
        # Inject active thread summary into user message if there are non-main threads
        thread_summary = self.thread_registry.summary()
        if thread_summary:
            prefix = f"[{thread_summary}]\n\n"
            if isinstance(user_message, str):
                user_message = prefix + user_message
            elif isinstance(user_message, list) and user_message and isinstance(user_message[0], str):
                user_message = [prefix + user_message[0]] + user_message[1:]

        # Log the user message to both stores
        user_content = user_message if isinstance(user_message, str) else str(user_message)
        user_msg = Message(type=MessageType.user, content=user_content)
        self.message_log.append(user_msg)
        self.context_window.append(user_msg)
        self._running = True
        answer_chunks: list[str] = []
        run_events: list[Any] = []  # collect yielded AgentEvents to attach to thread message
        logger.info(
            "event_stream_start prompt_preview=%s message_history=%s",
            _preview_text(user_message),
            len(self._conversation_messages),
        )

        # Sanitize history: pydantic-ai sets content=None on assistant messages that
        # contain only tool calls and no text. Some models (e.g. gpt-5.4-mini) reject
        # null content with a 400 error. Ensure every ModelResponse has at least one
        # TextPart so the serialized content field is never null.
        hist: list | None = None
        if self._conversation_messages:
            import dataclasses
            from pydantic_ai.messages import ToolCallPart
            hist = []
            for msg in self._conversation_messages:
                if isinstance(msg, ModelResponse):
                    has_text = any(isinstance(p, TextPart) for p in msg.parts)
                    has_tool_calls = any(isinstance(p, ToolCallPart) for p in msg.parts)
                    if not has_text and has_tool_calls:
                        # Add an empty text part so OpenAI gets content="" not null
                        patched_parts = list(msg.parts) + [TextPart(content="")]
                        msg = dataclasses.replace(msg, parts=patched_parts)
                hist.append(msg)

        try:
            stream = self._runner.run_stream_events(
                user_message,
                deps=self._deps,
                model_settings=self._model_settings,
                usage_limits=self._usage_limits,
                message_history=hist,
            )
        except Exception as exc:
            _maybe_raise_context_overflow(exc)
            raise

        raw_iter = stream.__aiter__()
        while True:
            try:
                raw = await raw_iter.__anext__()
            except StopAsyncIteration:
                break
            except Exception as exc:
                # Provider/SDK errors surface during iteration (e.g. an
                # OpenAI BadRequestError on context overflow). Translate
                # known patterns into typed exceptions; re-raise others
                # unchanged so existing handlers still see them.
                # TODO(post-baseline): on AgentContextOverflowError, run
                # compress_all_impl on the current context_window and
                # retry the request once before giving up. This is the
                # natural pairing of post-run auto-compression below
                # (~line 643), which only fires AFTER a successful run.
                _maybe_raise_context_overflow(exc)
                raise

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
                _ev = ToolCallEvent(
                    name=raw.part.tool_name,
                    args=args,
                    activity=act,
                )
                run_events.append(_ev)
                yield _ev
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
                _result_str = raw.result.content if isinstance(raw.result.content, str) else None
                _ev = ToolResultEvent(name=raw.result.tool_name, result=_result_str)
                run_events.append(_ev)
                yield _ev
                result_msg = Message(
                    type=MessageType.tool_result,
                    content={"tool": raw.result.tool_name},
                )
                self.message_log.append(result_msg)
                self.context_window.append(result_msg)

                # manage_todos modifies the todo list in _deps — emit the new state
                if raw.result.tool_name == "manage_todos":
                    _todo_ev = TodoUpdateEvent(items=list(self._deps.todo_list))
                    run_events.append(_todo_ev)
                    yield _todo_ev

                # use_skill queues a SkillLoadedEvent in _deps — emit and clear it
                if raw.result.tool_name == "use_skill" and self._deps.pending_skill_loaded:
                    for _skill_ev in self._deps.pending_skill_loaded:
                        run_events.append(_skill_ev)
                        yield _skill_ev
                    self._deps.pending_skill_loaded.clear()

                # call_client_function queues requests in _deps — emit and clear them
                if raw.result.tool_name == "call_client_function" and self._deps.pending_client_requests:
                    _cf_ev = ClientFunctionRequestEvent(
                        requests=list(self._deps.pending_client_requests),
                    )
                    run_events.append(_cf_ev)
                    yield _cf_ev
                    self._deps.pending_client_requests.clear()

            # First chunk of streamed text often arrives on part start, not only in deltas
            elif isinstance(raw, PartStartEvent):
                if isinstance(raw.part, TextPart) and raw.part.content:
                    answer_chunks.append(raw.part.content)
                    _ev = TextDeltaEvent(content=raw.part.content)
                    run_events.append(_ev)
                    yield _ev

            # The model is streaming its answer token by token
            elif isinstance(raw, PartDeltaEvent):
                if isinstance(raw.delta, TextPartDelta):
                    answer_chunks.append(raw.delta.content_delta)
                    _ev = TextDeltaEvent(content=raw.delta.content_delta)
                    run_events.append(_ev)
                    yield _ev

            # The run is complete — emit final token usage
            elif isinstance(raw, AgentRunResultEvent):
                self._conversation_messages[:] = list(raw.result.all_messages())

                # Log the full agent response as a single message
                full_answer = "".join(answer_chunks)
                run_usage = raw.result.usage()
                input_tokens = run_usage.input_tokens or 0

                _complete_ev = RunCompleteEvent(
                    usage=TokenUsage(
                        input_tokens=input_tokens,
                        output_tokens=run_usage.output_tokens or 0,
                    ),
                )
                run_events.append(_complete_ev)

                # Serialize all events collected this run for thread attachment
                serialized_run_events = [e.model_dump(mode="json") for e in run_events]

                if full_answer:
                    agent_msg = Message(type=MessageType.agent, content=full_answer)
                    self.message_log.append(agent_msg)
                    self.context_window.append(agent_msg)
                    # Append to main thread with full event log attached
                    try:
                        self.thread_registry.get("main").reply(
                            full_answer,
                            events=serialized_run_events,
                        )
                    except KeyError:
                        pass

                # Auto-compression check. NOTE: this fires AFTER a successful
                # run; it cannot prevent a pre-flight overflow. See the TODO
                # in the iteration error handler above for the planned retry
                # pairing.
                threshold = self._deps.context_compression_threshold
                if input_tokens > threshold and len(self.context_window) > 1:
                    from .context_tools import compress_all_impl, build_generic_summary
                    summary, instruction = build_generic_summary(
                        self.message_log, self._deps.todo_list
                    )
                    compress_all_impl(
                        self.message_log, self.context_window, summary, instruction,
                        agent_ref=self,
                    )

                yield _complete_ev
                logger.info(
                    "event_stream_complete prompt_preview=%s input_tokens=%s output_tokens=%s answer_chars=%s",
                    _preview_text(user_message),
                    input_tokens,
                    run_usage.output_tokens or 0,
                    len(full_answer),
                )

        self._running = False
        logger.info("event_stream_end prompt_preview=%s", _preview_text(user_message))

    def _reload_skills(self, *, rebuild_runner: bool) -> list[str]:
        from .registry import discover_skills

        disable_native = getattr(self._config, "disable_native_skills", False)
        native_skills = (
            discover_skills(self._native_skills_dir)
            if self._native_skills_dir.is_dir() and not disable_native
            else {}
        )
        user_skills = discover_skills(self._skills_dir)
        if not user_skills and not native_skills:
            raise RuntimeError(f"No skills found in {self._skills_dir}. Add at least one SKILL.md.")

        all_skills = {**native_skills, **user_skills}
        self._skills = all_skills
        self._deps.skills = all_skills
        self._system_prompt = self._build_runtime_system_prompt(all_skills)
        logger.info(
            "skills_reloaded total=%s user=%s native=%s rebuild_runner=%s",
            len(all_skills),
            len(user_skills),
            len(native_skills),
            rebuild_runner,
        )

        roots = tuple(Path(p).expanduser().resolve() for p in self._config.user_file_roots)
        if rebuild_runner:
            self._runner = _create_runner(
                self._model,
                self._system_prompt,
                roots,
                skills=all_skills,
                disabled_tools=tuple(getattr(self._config, "disabled_tools", ()) or ()),
                history_processors=list(getattr(self._config, "history_processors", None) or []),
            )

        return sorted(all_skills)

    @staticmethod
    def _is_relative_to(path: Path, parent: Path) -> bool:
        try:
            path.relative_to(parent)
            return True
        except ValueError:
            return False

    def _build_runtime_system_prompt(self, skills: dict[str, Skill]) -> str:
        disabled = frozenset(getattr(self._config, "disabled_tools", None) or ())
        prompt = _build_system_prompt(skills, self._config.system_prompt_extra, disabled_tools=disabled)
        roots = tuple(Path(p).expanduser().resolve() for p in self._config.user_file_roots)
        if roots:
            listed = ", ".join(str(r) for r in roots)
            prompt += (
                "\n\n## User file access\n"
                f"Additional files may live under: {listed}. "
                "Call `read_user_file` with a path relative to one of these roots, "
                "or an absolute path that stays inside them."
            )
        return prompt

    def _ensure_run_worker(self) -> None:
        _rq.ensure_run_worker(self)

    async def _run_queue_worker(self) -> None:
        await _rq.run_queue_worker(self)

    async def _publish_run_envelope(self, job: _QueuedRun, envelope: dict[str, Any]) -> None:
        await _rq.publish_run_envelope(self, job, envelope)


# ── System prompt ──────────────────────────────────────────────────────
#
# The system prompt lists skill names and descriptions only — not their
# full bodies. The LLM must call use_skill to load the full instructions.
# This keeps the prompt lean regardless of how many skills are registered.

_SYSTEM_PROMPT_TEMPLATE_PATH = Path(__file__).with_name("system_prompt.md")


def _build_system_prompt(
    skills: dict[str, Skill],
    extra: str | None,
    disabled_tools: frozenset[str] = frozenset(),
) -> str:
    template = _SYSTEM_PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8").strip()

    if disabled_tools:
        template = _strip_disabled_tools_from_template(template, disabled_tools)

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


_ALL_KNOWN_TOOLS: frozenset[str] = frozenset({
    "use_skill", "read_reference",
    "manage_todos",
    "read_thread", "reply_to_thread", "archive_thread", "spawn_agent",
    "compress_message", "retrieve_message", "compress_all",
    "read_user_file", "write_user_file",
    "call_client_function", "register_skill", "scaffold_skill",
    "write_skill_file", "list_skill_files",
})


def _strip_disabled_tools_from_template(template: str, disabled_tools: frozenset[str]) -> str:
    """Remove disabled tool entries from the system prompt template.

    Strips bullet lines that start with ``  - **<tool_name>**`` for every
    disabled tool name, and removes numbered rules whose only tool references
    are to disabled tools (so rules that mix enabled + disabled tools survive).
    """
    import re as _re

    lines = template.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]

        # Strip bullet-list tool entries:  "  - **tool_name**: ..."
        stripped = line.strip()
        if stripped.startswith("- **"):
            end = stripped.find("**", 4)
            if end != -1:
                tool_name = stripped[4:end]
                if tool_name in disabled_tools:
                    i += 1
                    continue

        # Strip numbered rules whose ONLY tool references are disabled tools.
        # Rules look like "N. text" with possible indented continuation lines.
        if _re.match(r"^\d+\.\s", line):
            rule_lines = [line]
            j = i + 1
            while j < len(lines) and lines[j] and lines[j][0] in (" ", "\t"):
                rule_lines.append(lines[j])
                j += 1
            rule_text = " ".join(rule_lines)
            mentioned_tools = set(_re.findall(r"`(\w+)`", rule_text)) & _ALL_KNOWN_TOOLS
            # Drop if ALL tool references are to disabled tools (and at least one exists)
            if mentioned_tools and mentioned_tools <= disabled_tools:
                i = j
                continue

        out.append(line)
        i += 1

    # Remove section headings that now have no bullet entries under them.
    # A heading is empty if it is followed immediately by another heading,
    # an empty line then a heading, or end-of-content.
    result_lines = out
    cleaned: list[str] = []
    for idx, l in enumerate(result_lines):
        if l.startswith("## "):
            # Look ahead past blank lines to see if the next non-blank line
            # is another heading or end-of-content.
            peek = idx + 1
            while peek < len(result_lines) and result_lines[peek].strip() == "":
                peek += 1
            next_non_blank = result_lines[peek] if peek < len(result_lines) else ""
            if next_non_blank.startswith("## ") or next_non_blank == "":
                # Empty section — skip the heading AND trailing blanks
                continue
        cleaned.append(l)

    return "\n".join(cleaned)


# ── Runner factory ─────────────────────────────────────────────────────
#
# Builds the underlying pydantic-ai Agent with all built-in tools.
# Tool registration is delegated to focused modules.

def _create_runner(
    model: Model,
    system_prompt: str,
    user_file_roots: tuple[Path, ...],
    skills: dict[str, "Skill"] | None = None,
    disabled_tools: tuple[str, ...] = (),
    history_processors: list[Any] | None = None,
) -> PydanticAgent[_RunDeps, str]:
    """Build the pydantic-ai runner and register all tools.

    ``skills`` is the full skill registry. Each skill's ToolSpecs are registered
    upfront with a ``prepare`` closure that hides them until the skill is active
    (i.e. appears in ctx.deps.activated_skills). This implements progressive
    disclosure of typed tool schemas without runner rebuilds.

    ``disabled_tools`` is an iterable of tool names that should not be
    registered with the runner. Each ``register_*_tools`` registrar is
    given the full set; matching tools are silently skipped so the
    model never sees them as callable. Unknown names are no-ops — this
    keeps the API permissive for downstream callers without forcing
    them to track which tools currently exist.

    ``history_processors`` is an optional list of pydantic-ai
    HistoryProcessor callables that intercept and rewrite
    ``_conversation_messages`` before every model request.
    """
    from .skill_tools import register_skill_tools
    from .context_tools import register_context_tools
    from .thread_tools import register_thread_tools

    # Wrap each history processor in the pydantic-ai capability class.
    # The name changed between pydantic-ai 1.x (HistoryProcessor) and 2.x
    # (ProcessHistory); try the new name first, fall back to the old one.
    try:
        from pydantic_ai.capabilities import ProcessHistory as _HistCls
    except ImportError:
        from pydantic_ai.capabilities import HistoryProcessor as _HistCls  # type: ignore[no-redef]

    capabilities = [_HistCls(p) for p in (history_processors or [])]

    runner: PydanticAgent[_RunDeps, str] = PydanticAgent(
        model=model,
        system_prompt=system_prompt,
        deps_type=_RunDeps,
        output_type=str,
        capabilities=capabilities or None,
    )

    disabled = frozenset(disabled_tools)
    register_skill_tools(runner, user_file_roots, disabled_tools=disabled)
    register_context_tools(runner, disabled_tools=disabled)
    register_thread_tools(runner, disabled_tools=disabled)

    if skills:
        _register_script_tools(runner, skills, disabled_tools=disabled)

    return runner


def _register_script_tools(
    runner: "PydanticAgent[_RunDeps, str]",
    skills: dict[str, "Skill"],
    disabled_tools: frozenset[str] = frozenset(),
) -> None:
    """Register each skill script as a typed tool with a prepare gate.

    Tools are registered at build time but hidden from the model until
    their owning skill appears in ctx.deps.activated_skills.
    """
    import json
    import os
    import subprocess
    import sys

    from pydantic_ai.tools import Tool as PydanticTool
    from ._schema_extractor import build_argv

    for skill in skills.values():
        skill_dir = skill.path.parent if skill.path else None
        for spec in skill.tool_specs:
            if spec.tool_name in disabled_tools:
                continue

            def _make_handler(spec=spec, sk=skill, sd=skill_dir):
                def handler(**kwargs: Any) -> str:
                    if sd is None:
                        return json.dumps({"ok": False, "stdout": "", "stderr": "Skill has no path.", "exit_code": 2})

                    argv = build_argv(spec, kwargs)

                    if sk.exec_style == "module":
                        stem = spec.script_filename[:-3] if spec.script_filename.endswith(".py") else spec.script_filename
                        cmd = [sys.executable, "-m", f"scripts.{stem}"]
                    else:
                        script_path = sd / "scripts" / spec.script_filename
                        cmd = [sys.executable, str(script_path)]
                    cmd.extend(argv)

                    env = os.environ.copy()
                    existing_pp = env.get("PYTHONPATH", "")
                    paths = [str(sd), str(sd / "scripts")]
                    if existing_pp:
                        paths.append(existing_pp)
                    env["PYTHONPATH"] = os.pathsep.join(paths)

                    try:
                        proc = subprocess.run(
                            cmd,
                            capture_output=True,
                            text=True,
                            timeout=90,
                            cwd=str(sd),
                            env=env,
                        )
                        stdout = (proc.stdout or "")[:7000]
                        stderr = (proc.stderr or "")[:7000]
                        return json.dumps({"ok": proc.returncode == 0, "stdout": stdout, "stderr": stderr, "exit_code": proc.returncode})
                    except subprocess.TimeoutExpired:
                        return json.dumps({"ok": False, "stdout": "", "stderr": "Script timed out after 90 seconds.", "exit_code": 124})
                    except Exception as exc:
                        return json.dumps({"ok": False, "stdout": "", "stderr": f"Error running script: {exc}", "exit_code": 1})

                return handler

            def _make_prepare(sn: str = spec.skill_name):
                def prepare(ctx: Any, tool_def: Any) -> Any:
                    if sn in ctx.deps.activated_skills:
                        return tool_def
                    return None
                return prepare

            try:
                tool = PydanticTool.from_schema(
                    function=_make_handler(),
                    name=spec.tool_name,
                    description=spec.description or f"Run {spec.script_filename} from skill {spec.skill_name}.",
                    json_schema=spec.json_schema,
                    takes_ctx=False,
                )
                tool.prepare = _make_prepare()
                runner._function_toolset.add_tool(tool)  # type: ignore[attr-defined]
            except Exception as exc:
                logger.warning(
                    "script_tool_registration_failed tool=%s err=%s",
                    spec.tool_name, exc,
                )


# ── Provider-error translation ─────────────────────────────────────────
#
# Provider SDKs (currently OpenAI's) raise their own typed errors that
# bubble up through pydantic-ai unchanged. We translate one specific case
# — context-length overflow — into a typed AgentContextOverflowError so
# callers can recover without parsing provider-specific error strings.

# Matches OpenAI/vLLM's standard overflow message:
#   "This model's maximum context length is 65536 tokens. However, you
#    requested 4096 output tokens and your prompt contains at least
#    61441 input tokens, for a total of at least 65537 tokens."
_OVERFLOW_PATTERN = re.compile(
    r"maximum context length is\s+(?P<limit>\d+)\s+tokens.*?"
    r"prompt contains at least\s+(?P<input>\d+)\s+input tokens",
    re.IGNORECASE | re.DOTALL,
)


def _maybe_raise_context_overflow(exc: BaseException) -> None:
    """Translate a context-overflow provider error into AgentContextOverflowError.

    Returns silently if `exc` does not look like a context-overflow error.
    The caller must `raise` (or re-raise) afterwards if this returns.
    """
    msg = str(exc)
    if "maximum context length" not in msg.lower():
        return
    match = _OVERFLOW_PATTERN.search(msg)
    limit = int(match.group("limit")) if match else None
    requested = int(match.group("input")) if match else None
    raise AgentContextOverflowError(
        f"Model rejected prompt: {requested or '?'} input tokens exceeds "
        f"context limit of {limit or '?'}.",
        requested_input_tokens=requested,
        model_context_limit=limit,
        provider_message=msg,
    ) from exc
