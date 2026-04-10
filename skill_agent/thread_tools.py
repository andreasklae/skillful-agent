"""Thread tools for agent-to-agent and external communication.

Provides read_thread, reply_to_thread, archive_thread, and spawn_agent.

Implementation functions (*_impl) are pure logic. register_thread_tools()
wires them as pydantic-ai tools on the runner.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field
from pydantic_ai import RunContext

from .messages import Message, MessageType, SourceContext, SubAgentContext, UIContext
from .threads import Thread, ThreadMessage, ThreadRegistry, ThreadRole, ThreadStatus

logger = logging.getLogger(__name__)

ActivityDesc = Annotated[
    str,
    Field(description="Short plain-language phrase describing what you are doing."),
]


# ── Implementation functions ─────────────────────────────────────────


def read_thread_impl(
    thread_registry: ThreadRegistry,
    message_log: list[Message],
    context_window: list[Message],
    thread_name: str,
) -> str:
    """Return full thread contents. Auto-compresses previous reads of same thread."""
    try:
        thread = thread_registry.get(thread_name)
    except KeyError:
        return f"Thread '{thread_name}' not found."

    messages = thread.messages
    if not messages:
        return f"Thread '{thread_name}' exists but has no messages."

    # Auto-compress previous reads of this thread in context_window
    for msg in context_window:
        if (
            msg.type == MessageType.tool_result
            and isinstance(msg.content, dict)
            and msg.content.get("tool") == "read_thread"
            and msg.content.get("thread_name") == thread_name
            and msg.summary is None
        ):
            msg.content = None
            msg.summary = f"[previous read of thread '{thread_name}']"

    lines: list[str] = []
    for m in messages:
        lines.append(
            f"[{m.timestamp.isoformat()}] {m.role.value}: {m.content}"
        )

    result_text = (
        f"Thread '{thread_name}' ({thread.status.value}, "
        f"{len(messages)} messages):\n" + "\n".join(lines)
    )

    log_msg = Message(
        type=MessageType.tool_result,
        content={"tool": "read_thread", "thread_name": thread_name},
    )
    message_log.append(log_msg)
    context_window.append(log_msg)

    return result_text


def reply_to_thread_impl(
    thread_registry: ThreadRegistry,
    message_log: list[Message],
    thread_name: str,
    message: str,
) -> str:
    """Reply to a thread (agent → participant direction)."""
    try:
        thread = thread_registry.get(thread_name)
    except KeyError:
        return f"Thread '{thread_name}' not found."

    if thread_name == "main":
        return "Cannot reply to the main thread via tool. Your text output is the reply."

    thread.reply(message)

    message_log.append(Message(
        type=MessageType.tool_call,
        content={"tool": "reply_to_thread", "description": f"Replied to '{thread_name}'"},
    ))

    return f"Replied to thread '{thread_name}'."


def archive_thread_impl(
    thread_registry: ThreadRegistry,
    thread_name: str,
) -> str:
    """Archive a thread. It stays queryable but leaves the active list."""
    if thread_name == "main":
        return "Cannot archive the main thread."

    try:
        thread_registry.archive(thread_name)
    except KeyError:
        return f"Thread '{thread_name}' not found."

    return f"Archived thread '{thread_name}'."


# ── Tool registration ────────────────────────────────────────────────


def register_thread_tools(runner: Any) -> None:
    """Register thread and spawn tools on the pydantic-ai runner."""

    @runner.tool(description=(
        "Read all messages in a named thread. Returns the full conversation history. "
        "Previous reads of the same thread are auto-compressed in context."
    ))
    def read_thread(ctx: RunContext, thread_name: str, activity: ActivityDesc = "") -> str:
        return read_thread_impl(
            ctx.deps.thread_registry,
            ctx.deps.message_log,
            ctx.deps.context_window,
            thread_name,
        )

    @runner.tool(description=(
        "Send ONE message to a named thread. The message goes to the subagent or participant "
        "on the other side. After calling this tool, end your turn immediately — the other side "
        "will respond and you will be notified via a follow-up run. "
        "Do NOT call this tool multiple times in one turn for the same thread. "
        "Do NOT use this for the main thread; your text output is the reply to the user."
    ))
    def reply_to_thread(
        ctx: RunContext,
        thread_name: str,
        message: str,
        activity: ActivityDesc = "",
    ) -> str:
        return reply_to_thread_impl(
            ctx.deps.thread_registry,
            ctx.deps.message_log,
            thread_name,
            message,
        )

    @runner.tool(description=(
        "Archive a thread. It stays readable but disappears from the active thread list. "
        "Use this when a conversation is concluded."
    ))
    def archive_thread(ctx: RunContext, thread_name: str, activity: ActivityDesc = "") -> str:
        return archive_thread_impl(ctx.deps.thread_registry, thread_name)

    @runner.tool(description=(
        "Spawn a subagent to handle a scoped task. Creates a named communication thread. "
        "The subagent runs autonomously and posts results back to the thread. "
        "Use reply_to_thread to send messages to the subagent. "
        "Use archive_thread to stop it. "
        "Set blocking=true to wait for the subagent's first response. "
        "Set singleton_id to ensure only one instance runs for that id."
    ))
    async def spawn_agent(
        ctx: RunContext,
        thread_name: str,
        instructions: str,
        system_prompt: str,
        tools: list[str] | None = None,
        skills: list[str] | None = None,
        blocking: bool = False,
        singleton_id: str | None = None,
        activity: ActivityDesc = "",
    ) -> str:
        return await spawn_agent_impl(
            ctx=ctx,
            thread_name=thread_name,
            instructions=instructions,
            system_prompt=system_prompt,
            tools=tools or [],
            skills=skills or [],
            blocking=blocking,
            singleton_id=singleton_id,
        )


async def spawn_agent_impl(
    *,
    ctx: RunContext,
    thread_name: str,
    instructions: str,
    system_prompt: str,
    tools: list[str],
    skills: list[str],
    blocking: bool,
    singleton_id: str | None,
) -> str:
    """Create a thread and wire a subagent (plain Agent) to it."""
    from .agent import Agent

    thread_registry: ThreadRegistry = ctx.deps.thread_registry
    parent_agent = ctx.deps._agent_ref
    if parent_agent is None:
        return "Cannot spawn subagent: no agent reference available."

    # Singleton check
    singleton_agents: dict[str, str] = ctx.deps._singleton_agents
    if singleton_id:
        if singleton_id in singleton_agents:
            existing_name = singleton_agents[singleton_id]
            try:
                existing = thread_registry.get(existing_name)
                if not existing.archived:
                    return f"Singleton '{singleton_id}' already running. Thread: {existing_name}"
            except KeyError:
                pass

    # Create the thread
    try:
        thread = thread_registry.create(
            name=thread_name,
            participants=[f"subagent:{thread_name}"],
            source_context=UIContext(sender="spawn_agent"),
        )
    except ValueError:
        return f"Thread '{thread_name}' already exists."

    # Register the inbound notification listener so the parent agent is woken
    # up when the subagent posts back to this thread via thread.send().
    print(f"[SPAWN] registering inbound notification listener  thread={thread_name!r}", flush=True)
    parent_agent._register_thread_notification(thread)

    # Create the subagent as a plain Agent
    sub_skills = {
        name: parent_agent._skills[name]
        for name in skills
        if name in parent_agent._skills
    }
    sub_config_dict = {
        "max_tokens": parent_agent._config.max_tokens,
        "max_turns": parent_agent._config.max_turns,
        "context_compression_threshold": parent_agent._config.context_compression_threshold,
        "system_prompt_extra": system_prompt,
    }
    from .models import AgentConfig
    sub_config = AgentConfig(**sub_config_dict)

    subagent = Agent(
        model=parent_agent._model,
        skills_dir=parent_agent._skills_dir,
        config=sub_config,
    )

    # Register subagent's message_log in parent's subagent_logs
    parent_agent.subagent_logs[thread_name] = subagent.message_log

    # Track singleton
    if singleton_id:
        singleton_agents[singleton_id] = thread_name

    source_ctx = SubAgentContext(
        subagent_id=thread_name,
        parent_interaction_id=thread_name,
        sender=f"subagent:{thread_name}",
    )

    # Capture the event loop now, while we're definitely inside an async context.
    # The outbound listener is a sync callback that may fire from inside a sync
    # tool function where asyncio.get_running_loop() may not be reachable.
    try:
        spawn_loop = asyncio.get_running_loop()
    except RuntimeError:
        return f"Cannot wire subagent: no running event loop at spawn time."

    # Wire outbound listener: parent replies → subagent runs
    def on_parent_reply(msg: ThreadMessage) -> None:
        """Parent called thread.reply() — route to subagent."""
        logger.info(
            "spawn_wire_outbound thread=%s content=%s",
            thread_name,
            msg.content[:200],
        )
        print(
            f"[SPAWN] outbound_listener_fired  thread={thread_name!r}"
            f"  content={msg.content[:120]!r}",
            flush=True,
        )
        print(f"[SPAWN] creating subagent task  thread={thread_name!r}", flush=True)
        spawn_loop.create_task(_run_subagent_and_post(subagent, thread, msg.content, source_ctx))

    thread.subscribe_outbound(on_parent_reply)

    # Wire inbound listener: subagent posts back → parent gets notified
    # (Inbound listeners on non-main threads trigger notification runs on the parent.)
    # This is handled by the agent's own notification mechanism registered
    # when the thread is created — no extra wiring needed here, as the
    # agent registers a global inbound listener for non-main threads.

    # Kick off the subagent with the initial instructions
    logger.info(
        "spawn_agent thread=%s instructions=%s singleton_id=%s blocking=%s",
        thread_name,
        instructions[:200],
        singleton_id,
        blocking,
    )

    if blocking:
        await _run_subagent_and_post(subagent, thread, instructions, source_ctx)
        return f"Subagent completed. Thread: {thread_name}"
    else:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return f"Thread '{thread_name}' created but no event loop for async dispatch."
        loop.create_task(_run_subagent_and_post(subagent, thread, instructions, source_ctx))
        return f"Subagent spawned. Thread: {thread_name}"


async def _run_subagent_and_post(
    subagent: Any,
    thread: Thread,
    prompt: str,
    source_context: SourceContext,
) -> None:
    """Run the subagent and post its output back to the thread."""
    logger.info(
        "subagent_run_start thread=%s prompt=%s",
        thread.name,
        prompt[:200],
    )
    print(f"[SUBAGENT] run_start  thread={thread.name!r}  prompt={prompt[:120]!r}", flush=True)
    try:
        result = await subagent._collect_run(
            subagent._prepare_user_message(prompt, None)
        )
        answer = result.answer.strip()
        serialized_events = [e.model_dump(mode="json") for e in result.events]
        print(
            f"[SUBAGENT] run_end  thread={thread.name!r}  answer_chars={len(answer)}"
            f"  events={len(serialized_events)}  answer={answer[:120]!r}",
            flush=True,
        )
        if answer:
            thread.send(answer, source_context, events=serialized_events)
        else:
            thread.send("Subagent finished without a text response.", source_context, events=serialized_events)
        logger.info(
            "subagent_run_end thread=%s answer_chars=%s events=%s",
            thread.name,
            len(answer),
            len(serialized_events),
        )
    except Exception as exc:
        logger.error("subagent_run_error thread=%s error=%s", thread.name, exc)
        print(f"[SUBAGENT] run_ERROR  thread={thread.name!r}  error={exc}", flush=True)
        thread.send(f"Subagent error: {exc}", source_context)
