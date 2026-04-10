"""Health check endpoint."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from server.dependencies import get_agent
from server.models import HealthResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(agent=Depends(get_agent)) -> HealthResponse:
    logger.debug(
        "HTTP /health skills=%s message_log=%s context_window=%s",
        len(agent._skills),
        len(agent.message_log),
        len(agent.context_window),
    )
    return HealthResponse(
        status="ok",
        skills=len(agent._skills),
        message_log_size=len(agent.message_log),
        context_window_size=len(agent.context_window),
    )
