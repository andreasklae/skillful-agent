"""Request and response models for the server API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RunRequest(BaseModel):
    prompt: str
    files: list[str] = Field(default_factory=list)


class ThreadMessageRequest(BaseModel):
    content: str
    sender: str | None = None


class ThreadMessageResponse(BaseModel):
    id: str
    timestamp: str
    role: str
    content: str
    thread_name: str
    events: list[dict] = []
    """Serialized AgentEvent list for the run that produced this message.
    Each entry has a 'type' field: tool_call, tool_result, todo_update,
    text_delta, run_complete, client_function_request.
    Empty for participant (inbound) messages."""


class ThreadItemResponse(BaseModel):
    id: str
    timestamp: str
    role: str
    content: str
    events: list[dict] = []
    """Serialized AgentEvent list for the run that produced this message."""


class ThreadSummaryResponse(BaseModel):
    name: str
    status: str
    archived: bool
    participants: list[str]
    message_count: int
    created_at: str


class ThreadResponse(BaseModel):
    name: str
    status: str
    archived: bool
    participants: list[str]
    created_at: str
    messages: list[ThreadItemResponse]


class SkillUploadResponse(BaseModel):
    skill_name: str
    skill_dir: str
    registered_skills: list[str]


class SkillSummaryResponse(BaseModel):
    name: str
    description: str
    path: str | None
    scripts: list[str]
    references: list[str]
    assets: list[str]


class HealthResponse(BaseModel):
    status: str
    skills: int
    message_log_size: int
    context_window_size: int
