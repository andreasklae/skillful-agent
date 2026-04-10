"""FastAPI server exposing the skill-agent SDK over HTTP.

Endpoints:
    POST /run              — start a streaming run (SSE)
    POST /inbox            — push an item into the agent's inbox
    POST /skills/upload    — upload a skill archive and register it live
    GET  /runs/subscribe   — SSE stream of all queued run events
    GET  /inbox            — read unread inbox items
    GET  /threads/{id}     — read a thread
    GET  /inbox/subscribe  — SSE stream of new inbox items
    GET  /health           — health check

Run:
    uv run uvicorn run_server:app --reload
"""

from server import create_app

app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
