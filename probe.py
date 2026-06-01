#!/usr/bin/env python3
"""
probe.py — two-turn interaction with the skillful agent against a local vLLM server.

Turn 1: generic question (no skill needed)
Turn 2: use the greeter skill

Logs raw pydantic-ai ModelMessages plus the tool list sent on each model request
to probe_log.json.
Run: uv run probe.py
"""

import dataclasses
import json
from pathlib import Path

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIModelProfile
from pydantic_ai.providers.openai import OpenAIProvider

from skill_agent import Agent, AgentConfig

VLLM_BASE_URL = "http://localhost:11500/v1"
MODEL_ID      = "google/gemma-4-31B-it"
SKILLS_DIR    = Path(__file__).parent / "skills"
LOG_FILE      = Path(__file__).parent / "probe_log.json"

PROMPTS = [
    "What is the capital of Norway? Answer in one sentence.",
    (
        "Use the greeter skill to greet me — my name is Andreas, use enthusiastic style. "
        "Tell me exactly what greeting was produced."
    ),
]


class ToolListCapture(AbstractCapability):
    """Capability that records the tool list sent on every model request."""

    def __init__(self):
        self.requests: list[dict] = []

    async def before_model_request(self, ctx, request_context: ModelRequestContext):
        tools = [
            dataclasses.asdict(td)
            for td in request_context.model_request_parameters.function_tools
        ]
        self.requests.append({"tool_names": [t["name"] for t in tools], "tools": tools})
        return request_context


def main() -> None:
    provider = OpenAIProvider(base_url=VLLM_BASE_URL, api_key="dummy")
    profile  = OpenAIModelProfile(
        openai_supports_strict_tool_definition=False,
        openai_supports_tool_choice_required=False,
    )
    model  = OpenAIChatModel(MODEL_ID, provider=provider, profile=profile)

    capture = ToolListCapture()
    config  = AgentConfig(max_turns=12)
    agent   = Agent(model=model, skills_dir=SKILLS_DIR, config=config)

    # Append our capture capability to the runner's root combined capability
    agent._runner._root_capability.capabilities = list(
        agent._runner._root_capability.capabilities
    ) + [capture]

    snapshots = []

    for i, prompt in enumerate(PROMPTS, start=1):
        print(f"\nTurn {i}: {prompt[:80]}")
        n_requests_before = len(capture.requests)
        result = agent.run(prompt)
        print(result.answer)

        messages = ModelMessagesTypeAdapter.dump_python(
            agent._conversation_messages, mode="json"
        )
        requests_this_turn = capture.requests[n_requests_before:]
        snapshots.append({
            "turn":     i,
            "prompt":   prompt,
            "requests": requests_this_turn,   # tool list per LLM request this turn
            "messages": messages,
        })

    LOG_FILE.write_text(
        json.dumps(snapshots, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nLog written → {LOG_FILE}")


if __name__ == "__main__":
    main()
