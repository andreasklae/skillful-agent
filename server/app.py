"""FastAPI application factory.

Usage:
    # Production (settings from environment):
    app = create_app()

    # Testing (inject a fake agent directly):
    app = create_app(agent=fake_agent)
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import ServerSettings, configure_logging, resolve_openai_api_key
from .dependencies import init_agent
from .routes import register_routes

if TYPE_CHECKING:
    from skill_agent import Agent


def create_app(
    *,
    agent: Agent | None = None,
    settings: ServerSettings | None = None,
) -> FastAPI:
    """Build and return the FastAPI application.

    Args:
        agent: Pre-built Agent instance (used in tests to skip model/Key Vault setup).
        settings: Server configuration. Loaded from environment if not provided.
    """
    if settings is None:
        settings = ServerSettings.from_env()

    configure_logging(settings)

    if agent is None:
        agent = _build_agent(settings)

    init_agent(agent)

    app = FastAPI(title="Skill Agent Server", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.parsed_cors_origins(),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_routes(app)
    return app


def _build_agent(settings: ServerSettings) -> Agent:
    """Create an Agent from settings, fetching the API key as needed."""
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    from skill_agent import Agent, AgentConfig

    api_key = resolve_openai_api_key(settings)
    model = OpenAIChatModel(
        settings.openai_model,
        provider=OpenAIProvider(api_key=api_key),
    )
    return Agent(
        model=model,
        skills_dir=Path(settings.skills_dir),
        config=AgentConfig(context_compression_threshold=100_000),
    )
