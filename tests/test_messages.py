import uuid
from datetime import datetime, timezone

from skill_agent.messages import (
    Message,
    MessageType,
    SourceContext,
    UIContext,
    EmailContext,
    SubAgentContext,
)


def test_message_auto_generates_id_and_timestamp():
    msg = Message(type=MessageType.user, content="hello")
    assert msg.id
    uuid.UUID(msg.id)
    assert isinstance(msg.timestamp, datetime)
    assert msg.summary is None
    assert msg.source_context is None


def test_message_types_are_string_enums():
    assert MessageType.user == "user"
    assert MessageType.tool_call == "tool_call"
    assert MessageType.system == "system"


def test_ui_context_defaults():
    ctx = UIContext()
    assert ctx.origin == "ui"
    assert ctx.sender is None
    uuid.UUID(ctx.interaction_id)


def test_email_context_fields():
    ctx = EmailContext(
        sender="alice@example.com",
        subject="Hello",
        thread_id="t-123",
        reply_to="msg-456",
    )
    assert ctx.origin == "email"
    assert ctx.subject == "Hello"
    assert ctx.thread_id == "t-123"
    assert ctx.reply_to == "msg-456"


def test_subagent_context_fields():
    ctx = SubAgentContext(
        subagent_id="sa-1",
        parent_interaction_id="int-2",
    )
    assert ctx.origin == "subagent"
    assert ctx.subagent_id == "sa-1"
    assert ctx.parent_interaction_id == "int-2"


def test_message_with_source_context():
    ctx = UIContext(sender="user-1")
    msg = Message(type=MessageType.user, content="hi", source_context=ctx)
    assert msg.source_context.origin == "ui"
    assert msg.source_context.sender == "user-1"


def test_message_serialization_roundtrip():
    msg = Message(type=MessageType.agent, content={"key": "value"})
    data = msg.model_dump()
    restored = Message(**data)
    assert restored.id == msg.id
    assert restored.content == {"key": "value"}
