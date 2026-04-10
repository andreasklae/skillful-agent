"""SSE (Server-Sent Events) formatting utilities."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def format_run_envelope_sse(
    envelope: dict[str, Any],
    *,
    include_full_envelope: bool = False,
) -> str:
    """Format a run envelope as an SSE event string."""
    logger.debug(
        "Formatting SSE envelope run_id=%s type=%s include_full_envelope=%s",
        envelope["run_id"],
        envelope["type"],
        include_full_envelope,
    )
    event_name = envelope["type"]
    if envelope["type"] == "agent_event":
        event_name = envelope["event"]["type"]
        payload = envelope if include_full_envelope else envelope["event"]
    else:
        payload = envelope
    body = json.dumps(payload, ensure_ascii=False, default=str)
    return f"id: {envelope['run_id']}\nevent: {event_name}\ndata: {body}\n\n"
