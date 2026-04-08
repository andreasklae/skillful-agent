"""Skill-based agent SDK with progressive skill disclosure.

Public API:
    Agent             — Create with model + skills_dir, call run() or run_stream()
    AgentEvent        — Discriminated union of all event types
    TodoUpdateEvent   — Todo list state after each manage_todos call
    ToolCallEvent     — Tool invocation (name, args, optional activity)
    ToolResultEvent   — Tool completion
    TextDeltaEvent    — Answer token from the model
    RunCompleteEvent  — Final event (token usage); conversation memory lives on Agent
    ClientFunctionRequestEvent — Client function request from skill
    Skill             — Skill metadata model (name, description, body, resources)
    AgentConfig       — Optional configuration (max_tokens, max_turns, etc.)
    AgentResult       — Typed return value from agent.run()
    Message           — Structured message in the conversation log
    MessageType       — Enum of message roles
    SourceContext     — Base class for message origin
    UIContext         — UI-originated message
    EmailContext      — Email-originated message
    SubAgentContext   — Subagent-originated message
    Inbox             — In-memory inbox for inter-agent communication
    InboxItem         — One item in an inbox
    Thread            — Scoped view of inbox items sharing a thread_id
    ThreadStatus      — Lifecycle status enum for threads
    SubAgent          — Scoped worker agent communicating via inbox
"""

from .agent import Agent
from .user_prompt_files import build_user_message
from .models import (
    AgentConfig,
    AgentEvent,
    AgentResult,
    ClientFunction,
    ClientFunctionParam,
    ClientFunctionRequest,
    ClientFunctionRequestEvent,
    RunCompleteEvent,
    Skill,
    TextDeltaEvent,
    TodoItem,
    TodoStatus,
    TodoUpdateEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from .messages import (
    Message,
    MessageType,
    SourceContext,
    UIContext,
    EmailContext,
    SubAgentContext,
)
from .inbox import (
    Inbox,
    InboxItem,
    Thread,
    ThreadStatus,
)
from .subagent import SubAgent

__all__ = [
    "Agent",
    "build_user_message",
    "AgentConfig",
    "AgentEvent",
    "AgentResult",
    "ClientFunction",
    "ClientFunctionParam",
    "ClientFunctionRequest",
    "ClientFunctionRequestEvent",
    "RunCompleteEvent",
    "Skill",
    "TextDeltaEvent",
    "TodoItem",
    "TodoStatus",
    "TodoUpdateEvent",
    "ToolCallEvent",
    "ToolResultEvent",
    "Message",
    "MessageType",
    "SourceContext",
    "UIContext",
    "EmailContext",
    "SubAgentContext",
    "Inbox",
    "InboxItem",
    "Thread",
    "ThreadStatus",
    "SubAgent",
]
