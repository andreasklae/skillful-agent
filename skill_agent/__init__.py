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
    SkillLoadedEvent  — Emitted when use_skill loads a skill's full instructions
    Skill             — Skill metadata model (name, description, body, resources)
    AgentConfig       — Optional configuration (max_tokens, max_turns, etc.)
    AgentResult       — Typed return value from agent.run()
    Message           — Structured message in the conversation log
    MessageType       — Enum of message roles
    SourceContext     — Base class for message origin
    UIContext         — UI-originated message
    EmailContext      — Email-originated message
    SubAgentContext   — Subagent-originated message
    Thread            — Bidirectional message channel
    ThreadMessage     — One message in a thread
    ThreadRole        — Who authored a thread message
    ThreadStatus      — Lifecycle status of a thread
    ThreadRegistry    — Manages all threads for an agent
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
    SkillLoadedEvent,
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
from .threads import (
    Thread,
    ThreadMessage,
    ThreadRole,
    ThreadStatus,
    ThreadRegistry,
)

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
    "SkillLoadedEvent",
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
    "Thread",
    "ThreadMessage",
    "ThreadRole",
    "ThreadStatus",
    "ThreadRegistry",
]
