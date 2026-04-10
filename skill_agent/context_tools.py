"""Context window management tools: compress, retrieve, and compress_all.

These tools allow the agent (or the runtime) to manage the context window
by compressing old messages to summaries and retrieving them when needed.

The implementation functions (*_impl) are pure logic operating on lists.
register_context_tools() wires them as pydantic-ai tools on the runner.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field
from pydantic_ai import RunContext

from .messages import Message, MessageType

ActivityDesc = Annotated[
    str,
    Field(description="Short plain-language phrase describing what you are doing."),
]


def compress_message_impl(
    context_window: list[Message],
    message_id: str,
    summary: str,
) -> str:
    """Replace a message's content with a summary in the context window."""
    for msg in context_window:
        if msg.id == message_id:
            msg.content = None
            msg.summary = summary
            return f"Compressed message {message_id}."
    return f"Message {message_id} not found in context window."


def retrieve_message_impl(
    message_log: list[Message],
    context_window: list[Message],
    message_id: str,
) -> str:
    """Restore a message from message_log into context_window."""
    original = None
    for msg in message_log:
        if msg.id == message_id:
            original = msg
            break
    if original is None:
        return f"Message {message_id} not found in message log."

    # Check if it's still in the window (compressed)
    for msg in context_window:
        if msg.id == message_id:
            msg.content = original.content
            msg.summary = None
            return f"Restored message {message_id}."

    # Re-insert at original position by timestamp
    insert_at = len(context_window)
    for i, msg in enumerate(context_window):
        if msg.timestamp > original.timestamp:
            insert_at = i
            break

    restored = Message(
        id=original.id,
        timestamp=original.timestamp,
        type=original.type,
        source_context=original.source_context,
        content=original.content,
        summary=None,
    )
    context_window.insert(insert_at, restored)
    return f"Re-inserted message {message_id} at position {insert_at}."


def compress_all_impl(
    message_log: list[Message],
    context_window: list[Message],
    summary: str,
    instruction: str,
) -> str:
    """Replace the entire context window with a single summary message."""
    if not context_window:
        return "Context window is already empty."

    first_id = context_window[0].id
    last_id = context_window[-1].id

    compressed = Message(
        type=MessageType.system,
        content=(
            f"[Context compressed: messages {first_id} through {last_id}]\n\n"
            f"Summary: {summary}\n\n"
            f"Resumption instruction: {instruction}"
        ),
    )

    notification = Message(
        type=MessageType.system,
        content="Context window was compressed to stay within token limits.",
    )

    context_window.clear()
    context_window.append(compressed)
    context_window.append(notification)

    message_log.append(compressed)
    message_log.append(notification)

    return f"Compressed {last_id} — context window now contains summary only."


def build_generic_summary(
    message_log: list[Message],
    todo_list: list[Any],
) -> tuple[str, str]:
    """Build a generic summary from message_log when the model fails to compress."""
    parts: list[str] = []

    if message_log:
        first = message_log[0]
        parts.append(f"First message: [{first.type.value}] {str(first.content)[:200]}")
        last = message_log[-1]
        parts.append(f"Last message: [{last.type.value}] {str(last.content)[:200]}")

    recent_tools = [
        m for m in message_log[-20:]
        if m.type == MessageType.tool_call
    ]
    if recent_tools:
        tool_names = [
            str(m.content.get("tool", "?")) if isinstance(m.content, dict) else "?"
            for m in recent_tools[-5:]
        ]
        parts.append(f"Recent tools: {', '.join(tool_names)}")

    if todo_list:
        todo_strs = [str(getattr(t, "content", t))[:80] for t in todo_list[:5]]
        parts.append(f"Active todos: {'; '.join(todo_strs)}")

    summary = "\n".join(parts) if parts else "Conversation history (details compressed)."
    instruction = (
        "Review the inbox and todo list to determine next steps. "
        "Ask the user for clarification if the task is unclear."
    )

    return summary, instruction


def register_context_tools(runner: Any) -> None:
    """Register compress_message, retrieve_message, and compress_all as pydantic-ai tools."""

    @runner.tool(description=(
        "Compress a message in the context window by replacing its content with a summary. "
        "The full content is preserved in the message log and can be retrieved later. "
        "Provide the message id and a concise summary of what the message contained."
    ))
    def compress_message(
        ctx: RunContext,
        message_id: str,
        summary: str,
        activity: ActivityDesc = "",
    ) -> str:
        return compress_message_impl(ctx.deps.context_window, message_id, summary)

    @runner.tool(description=(
        "Retrieve a previously compressed or removed message from the message log "
        "and restore it to the context window. Provide the message id."
    ))
    def retrieve_message(
        ctx: RunContext,
        message_id: str,
        activity: ActivityDesc = "",
    ) -> str:
        return retrieve_message_impl(
            ctx.deps.message_log, ctx.deps.context_window, message_id
        )

    @runner.tool(description=(
        "Compress the entire context window into a single summary message. "
        "Use this when the context is getting too large. Provide a comprehensive "
        "summary of the conversation so far, and an instruction with enough detail "
        "to resume work without reading the full history."
    ))
    def compress_all(
        ctx: RunContext,
        summary: str,
        instruction: str,
        activity: ActivityDesc = "",
    ) -> str:
        return compress_all_impl(
            ctx.deps.message_log, ctx.deps.context_window, summary, instruction
        )
