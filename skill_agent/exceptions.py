"""Typed exceptions raised by the Agent runtime.

Currently a thin module — exists so callers can catch framework-specific
failures without coupling to provider SDK error types (e.g. openai.BadRequestError).
"""

from __future__ import annotations


class AgentError(Exception):
    """Base class for all skillful-agent runtime errors."""


class AgentContextOverflowError(AgentError):
    """Raised when a model rejects a request because the prompt exceeds the
    model's maximum context length.

    Auto-compression in `Agent._event_stream` fires *after* a run completes
    and so cannot prevent a pre-flight overflow. When the provider returns
    a context-length 400, the underlying SDK error is wrapped and re-raised
    as this type so callers can recover (drop history, compress, or surface
    as a graceful failure) without parsing provider-specific error strings.

    Attributes:
        requested_input_tokens: Estimated input-token count reported by the
            provider, or ``None`` if not parseable.
        model_context_limit: The model's declared context length, or ``None``.
        provider_message: The original provider error message, preserved for
            logging.
    """

    def __init__(
        self,
        message: str,
        *,
        requested_input_tokens: int | None = None,
        model_context_limit: int | None = None,
        provider_message: str | None = None,
    ) -> None:
        super().__init__(message)
        self.requested_input_tokens = requested_input_tokens
        self.model_context_limit = model_context_limit
        self.provider_message = provider_message
