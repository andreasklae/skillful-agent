"""Run endpoints: start streaming runs and subscribe to all runs."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from server.dependencies import get_agent
from server.models import RunRequest
from server.services.sse import format_run_envelope_sse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["runs"])


@router.post("/run")
async def run_stream(req: RunRequest, agent=Depends(get_agent)) -> StreamingResponse:
    """Queue a run and stream its events over SSE."""
    file_paths = [Path(f) for f in req.files] if req.files else None
    logger.info(
        "HTTP /run received prompt_len=%s files=%s",
        len(req.prompt),
        [str(p) for p in file_paths] if file_paths else [],
    )
    run_id = await agent.enqueue_run(
        req.prompt,
        files=file_paths,
        source="api",
        metadata={"origin": "http", "endpoint": "/run"},
    )
    logger.info("HTTP /run queued run_id=%s", run_id)

    async def generate():
        logger.info("HTTP /run stream opened run_id=%s", run_id)
        async for envelope in agent.subscribe_run(run_id):
            yield format_run_envelope_sse(envelope)
        logger.info("HTTP /run stream closed run_id=%s", run_id)

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/runs/subscribe")
async def subscribe_runs(agent=Depends(get_agent)) -> StreamingResponse:
    """SSE stream for every queued run, including background inbox runs."""

    async def generate():
        logger.info("HTTP /runs/subscribe stream opened")
        async for envelope in agent.subscribe_all_runs():
            yield format_run_envelope_sse(envelope, include_full_envelope=True)
        logger.info("HTTP /runs/subscribe stream closed")

    return StreamingResponse(generate(), media_type="text/event-stream")
