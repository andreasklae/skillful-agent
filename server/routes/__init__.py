"""Route registration for the server application."""

from __future__ import annotations

from fastapi import FastAPI

from .health import router as health_router
from .runs import router as runs_router
from .skills import router as skills_router
from .threads import router as threads_router


def register_routes(app: FastAPI) -> None:
    """Include all API routers on the app."""
    app.include_router(health_router)
    app.include_router(runs_router)
    app.include_router(threads_router)
    app.include_router(skills_router)
