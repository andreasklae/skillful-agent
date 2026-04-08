"""Inbox tools for agent-to-agent and external communication.

Provides read_inbox, read_thread, write_to_thread, forward_thread_item,
dismiss_inbox_item, delete_thread, and spawn_subagent.

Implementation functions (*_impl) are pure logic. register_*_tools()
wires them as pydantic-ai tools on the runner.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Annotated, Any

from pydantic import Field
from pydantic_ai import RunContext

from .inbox import Inbox, InboxItem, ThreadStatus
from .messages import Message, MessageType, SourceContext, UIContext


# ── Implementation functions ─────────────────────────────────────────


def read_inbox_impl(
    inbox: Inbox,
    message_log: list[Message],
) -> str:
    """Return unread items with subjects and thread status. Marks them read."""
    unread = inbox.read_inbox()
    if not unread:
        return "Inbox is empty — no unread items."

    lines: list[str] = []
    seen_threads: set[str] = set()
    for item in unread:
        thread = inbox.get_thread(item.thread_id)
        status_tag = f"[{thread.status.value}]" if item.thread_id not in seen_threads else ""
        seen_threads.add(item.thread_id)
        lines.append(
            f"- [{item.thread_id}] {status_tag} {item.subject}"
            f" (notify={'yes' if item.notify else 'no'})"
        )

    result_text = f"{len(unread)} unread item(s):\n" + "\n".join(lines)

    message_log.append(Message(
        type=MessageType.tool_result,
        content={"tool": "read_inbox", "item_count": len(unread)},
    ))

    return result_text


def read_thread_impl(
    inbox: Inbox,
    message_log: list[Message],
    context_window: list[Message],
    thread_id: str,
) -> str:
    """Return full thread contents. Auto-compresses previous reads of same thread."""
    items = inbox.read_thread(thread_id)
    if not items:
        return f"Thread '{thread_id}' not found or empty."

    thread = inbox.get_thread(thread_id)

    # Auto-compress previous reads of this thread in context_window
    for msg in context_window:
        if (
            msg.type == MessageType.tool_result
            and isinstance(msg.content, dict)
            and msg.content.get("tool") == "read_thread"
            and msg.content.get("thread_id") == thread_id
            and msg.summary is None
        ):
            msg.content = None
            msg.summary = f"[previous read of thread {thread_id}: {thread.subject}]"

    lines: list[str] = []
    for item in items:
        lines.append(
            f"[{item.timestamp.isoformat()}] {item.source_context.origin}/"
            f"{item.source_context.sender or '?'}: {item.content}"
        )

    result_text = f"Thread {thread_id} ({thread.status.value}):\n" + "\n".join(lines)

    log_msg = Message(
        type=MessageType.tool_result,
        content={"tool": "read_thread", "thread_id": thread_id},
    )
    message_log.append(log_msg)
    context_window.append(log_msg)

    return result_text


def write_to_thread_impl(
    own_inbox: Inbox,
    active_subagents: dict,
    message_log: list[Message],
    thread_id: str,
    content: str,
    notify: bool,
    source_context: SourceContext | None = None,
    subject: str | None = None,
    status: ThreadStatus | None = None,
) -> str:
    """Write to a thread, resolving target inbox automatically."""
    ctx = source_context or UIContext(sender="agent")

    # Resolve target inbox: subagent inbox if thread matches, else own
    target_inbox = own_inbox
    if thread_id in active_subagents:
        subagent = active_subagents[thread_id]
        target_inbox = subagent.inbox

    try:
        target_inbox.write_to_thread(
            thread_id=thread_id,
            content=content,
            source_context=ctx,
            notify=notify,
            subject=subject,
            status=status,
        )
    except KeyError:
        if target_inbox is not own_inbox:
            try:
                own_inbox.write_to_thread(
                    thread_id=thread_id,
                    content=content,
                    source_context=ctx,
                    notify=notify,
                    subject=subject,
                    status=status,
                )
            except KeyError:
                return f"Thread '{thread_id}' not found in any inbox."
        else:
            return f"Thread '{thread_id}' not found."

    message_log.append(Message(
        type=MessageType.tool_call,
        content={"tool": "write_to_thread", "description": f"Wrote to thread {thread_id}"},
    ))

    return f"Wrote to thread {thread_id} (notify={notify})."


def forward_thread_item_impl(
    own_inbox: Inbox,
    active_subagents: dict,
    item_id: str,
    to_subagent_id: str,
) -> str:
    """Forward an inbox item to a subagent's inbox without loading content."""
    source_item = None
    for item in own_inbox.items:
        if item.id == item_id:
            source_item = item
            break
    if source_item is None:
        return f"Item '{item_id}' not found in inbox."

    if to_subagent_id not in active_subagents:
        return f"No active subagent with id '{to_subagent_id}'."

    subagent = active_subagents[to_subagent_id]
    target_inbox: Inbox = subagent.inbox

    target_inbox.create_item(
        content=f"[Forwarded from parent] {source_item.content}",
        subject=f"[FWD] {source_item.subject}",
        source_context=source_item.source_context,
        notify=True,
    )

    return f"Forwarded item '{source_item.subject}' to subagent {to_subagent_id}."


def dismiss_inbox_item_impl(inbox: Inbox, item_id: str) -> str:
    """Mark an inbox item as dismissed."""
    try:
        inbox.dismiss_item(item_id)
        return f"Dismissed item {item_id}."
    except KeyError:
        return f"Item '{item_id}' not found."


def delete_thread_impl(
    inbox: Inbox,
    thread_id: str,
    active_subagents: dict,
    singleton_subagents: dict,
) -> tuple[list[InboxItem], str]:
    """Delete a thread and clean up subagent references."""
    deleted = inbox.delete_thread(thread_id)

    to_remove = [k for k, v in singleton_subagents.items() if v == thread_id]
    for k in to_remove:
        del singleton_subagents[k]

    return deleted, f"Deleted thread {thread_id} ({len(deleted)} items archived)."


# ── Tool registration ────────────────────────────────────────────────


def register_inbox_tools(runner: Any) -> None:
    """Register inbox tools on the pydantic-ai runner."""

    ActivityDesc = Annotated[
        str,
        Field(description="Short plain-language phrase describing what you are doing."),
    ]

    @runner.tool(description=(
        "Check your inbox for unread messages. Returns subjects and thread status "
        "for each unread item. Use read_thread to see full contents of a thread."
    ))
    def read_inbox(ctx: RunContext, activity: ActivityDesc = "") -> str:
        return read_inbox_impl(ctx.deps.inbox, ctx.deps.message_log)

    @runner.tool(description=(
        "Read the full contents of a thread by thread_id. "
        "Previous reads of the same thread in context are automatically compressed."
    ))
    def read_thread(ctx: RunContext, thread_id: str, activity: ActivityDesc = "") -> str:
        return read_thread_impl(
            ctx.deps.inbox, ctx.deps.message_log, ctx.deps.context_window, thread_id
        )

    @runner.tool(description=(
        "Write a message to a thread. Target inbox is resolved automatically: "
        "if thread_id matches an active subagent, the message goes to that subagent's inbox. "
        "Otherwise it goes to your own inbox. Set notify=true only for final results or blocking errors."
    ))
    def write_to_thread(
        ctx: RunContext,
        thread_id: str,
        content: str,
        notify: bool = False,
        status: str | None = None,
        activity: ActivityDesc = "",
    ) -> str:
        thread_status = None
        if status:
            try:
                thread_status = ThreadStatus(status)
            except ValueError:
                return f"Invalid status '{status}'. Use: in_progress, waiting_for_response, done."
        return write_to_thread_impl(
            own_inbox=ctx.deps.inbox,
            active_subagents=ctx.deps.active_subagents,
            message_log=ctx.deps.message_log,
            thread_id=thread_id,
            content=content,
            notify=notify,
            status=thread_status,
        )

    @runner.tool(description=(
        "Forward an inbox item to a subagent without loading its content into your context. "
        "You only see the subject line. Provide the item_id and the target subagent's thread_id."
    ))
    def forward_thread_item(
        ctx: RunContext,
        item_id: str,
        to_subagent_id: str,
        activity: ActivityDesc = "",
    ) -> str:
        return forward_thread_item_impl(
            ctx.deps.inbox, ctx.deps.active_subagents, item_id, to_subagent_id
        )

    @runner.tool(description="Mark an inbox item as dismissed without processing.")
    def dismiss_inbox_item(ctx: RunContext, item_id: str, activity: ActivityDesc = "") -> str:
        return dismiss_inbox_item_impl(ctx.deps.inbox, item_id)

    @runner.tool(description=(
        "Delete a thread and archive its contents. If the thread is linked to a subagent, "
        "the subagent will wind down gracefully. This is how you stop a subagent."
    ))
    def delete_thread(ctx: RunContext, thread_id: str, activity: ActivityDesc = "") -> str:
        deleted, result = delete_thread_impl(
            ctx.deps.inbox, thread_id,
            ctx.deps.active_subagents,
            ctx.deps._singleton_subagents,
        )
        return result


def register_spawn_tools(runner: Any) -> None:
    """Register the spawn_subagent tool on the pydantic-ai runner."""

    ActivityDesc = Annotated[
        str,
        Field(description="Short plain-language phrase describing what you are doing."),
    ]

    @runner.tool(description=(
        "Spawn a subagent to handle a scoped task. Returns a thread_id for communication. "
        "The subagent runs autonomously and posts updates to the thread. "
        "Use delete_thread to stop a subagent. "
        "Set blocking=true to wait for the result. "
        "Set singleton=true with a singleton_id to ensure only one instance runs."
    ))
    async def spawn_subagent(
        ctx: RunContext,
        instructions: str,
        system_prompt: str,
        tools: list[str] | None = None,
        skills: list[str] | None = None,
        blocking: bool = False,
        singleton: bool = False,
        singleton_id: str | None = None,
        activity: ActivityDesc = "",
    ) -> str:
        from .subagent import SubAgent

        tools = tools or []
        skills = skills or []

        # Singleton check
        if singleton:
            if not singleton_id:
                return "singleton=true requires singleton_id."
            singleton_map = ctx.deps._singleton_subagents
            if singleton_id in singleton_map:
                existing_tid = singleton_map[singleton_id]
                if existing_tid in ctx.deps.active_subagents:
                    return f"Singleton '{singleton_id}' already running. Thread: {existing_tid}"

        # Create thread in parent's inbox
        thread_id = f"sa-{uuid.uuid4().hex[:12]}"
        subject = instructions[:80]
        ctx.deps.inbox.create_item(
            content=f"Spawning subagent: {instructions}",
            subject=subject,
            source_context=UIContext(sender="spawn_tool"),
            notify=False,
            thread_id=thread_id,
            status=ThreadStatus.in_progress,
        )

        parent_agent = ctx.deps._agent_ref
        if parent_agent is None:
            return f"Thread {thread_id} created but subagent could not be instantiated (no agent ref)."

        subagent = SubAgent(
            parent=parent_agent,
            instructions=instructions,
            system_prompt=system_prompt,
            tools=tools,
            skills=skills,
            thread_id=thread_id,
        )

        ctx.deps.active_subagents[thread_id] = subagent

        if singleton and singleton_id:
            ctx.deps._singleton_subagents[singleton_id] = thread_id

        if blocking:
            await subagent.run_loop()
            return f"Subagent completed. Thread: {thread_id}"
        else:
            task = asyncio.create_task(subagent.run_loop())
            if hasattr(parent_agent, '_subagent_tasks'):
                parent_agent._subagent_tasks[thread_id] = task
            return f"Subagent spawned. Thread: {thread_id}"
