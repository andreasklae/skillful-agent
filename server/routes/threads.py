"""Thread endpoints: list, read, post messages, and subscribe."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from server.dependencies import get_agent
from server.models import (
    ThreadItemResponse,
    ThreadMessageRequest,
    ThreadMessageResponse,
    ThreadResponse,
    ThreadSummaryResponse,
)
from skill_agent.messages import UIContext

logger = logging.getLogger(__name__)

router = APIRouter(tags=["threads"])


@router.get("/threads", response_model=list[ThreadSummaryResponse])
async def list_threads(agent=Depends(get_agent)) -> list[ThreadSummaryResponse]:
    """List all active threads."""
    threads = agent.thread_registry.active()
    logger.info("HTTP /threads returning count=%s", len(threads))
    return [
        ThreadSummaryResponse(
            name=t.name,
            status=t.status.value,
            archived=t.archived,
            participants=t.participants,
            message_count=len(t.messages),
            created_at=t.created_at.isoformat(),
        )
        for t in threads
    ]


@router.get("/threads/subscribe")
async def subscribe_threads(agent=Depends(get_agent)) -> StreamingResponse:
    """SSE stream of thread activity — yields events when any thread gets a new message."""

    async def generate():
        logger.info("HTTP /threads/subscribe stream opened")
        async for event in agent.thread_registry.subscribe():
            data = ThreadMessageResponse(
                id=event.message.id,
                timestamp=event.message.timestamp.isoformat(),
                role=event.message.role.value,
                content=event.message.content,
                thread_name=event.thread_name,
                events=event.message.events,
            )
            yield f"event: thread_message\ndata: {data.model_dump_json()}\n\n"
        logger.info("HTTP /threads/subscribe stream closed")

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/threads/{thread_name}", response_model=ThreadResponse)
async def get_thread(thread_name: str, agent=Depends(get_agent)) -> ThreadResponse:
    """Read full contents of a thread."""
    try:
        thread = agent.thread_registry.get(thread_name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Thread '{thread_name}' not found.")

    logger.info(
        "HTTP /threads/%s returning status=%s message_count=%s",
        thread_name,
        thread.status.value,
        len(thread.messages),
    )
    return ThreadResponse(
        name=thread.name,
        status=thread.status.value,
        archived=thread.archived,
        participants=thread.participants,
        created_at=thread.created_at.isoformat(),
        messages=[
            ThreadItemResponse(
                id=m.id,
                timestamp=m.timestamp.isoformat(),
                role=m.role.value,
                content=m.content,
                events=m.events,
            )
            for m in thread.messages
        ],
    )


@router.post("/threads/{thread_name}/messages", response_model=ThreadMessageResponse)
async def post_thread_message(
    thread_name: str,
    req: ThreadMessageRequest,
    agent=Depends(get_agent),
) -> ThreadMessageResponse:
    """Send a message to a thread. Creates the thread if it doesn't exist."""
    ctx = UIContext(sender=req.sender or "api")
    logger.info(
        "HTTP /threads/%s/messages received sender=%s content=%s",
        thread_name,
        req.sender or "api",
        req.content[:200],
    )
    msg = agent.receive_thread_message(
        thread_name,
        req.content,
        source_context=ctx,
        allow_create=True,
    )
    logger.info(
        "HTTP /threads/%s/messages stored msg_id=%s",
        thread_name,
        msg.id,
    )
    return ThreadMessageResponse(
        id=msg.id,
        timestamp=msg.timestamp.isoformat(),
        role=msg.role.value,
        content=msg.content,
        thread_name=thread_name,
    )
