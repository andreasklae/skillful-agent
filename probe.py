#!/usr/bin/env python3
"""
probe.py — two-turn interaction with the skillful agent against a local vLLM server.

Turn 1: generic question (no skill needed)
Turn 2: use the greeter skill

Logs the raw pydantic-ai ModelMessages (request + response) to probe_log.json.
Run: uv run probe.py
"""

import json
from pathlib import Path

from pydantic_ai.models.openai import OpenAIChatModel, OpenAIModelProfile
from pydantic_ai.providers.openai import OpenAIProvider

from pydantic_ai.messages import ModelMessagesTypeAdapter
from skill_agent import Agent, AgentConfig

VLLM_BASE_URL = "http://localhost:11500/v1"
MODEL_ID      = "google/gemma-4-31B-it"
SKILLS_DIR    = Path(__file__).parent / "skills"
LOG_FILE      = Path(__file__).parent / "probe_log.json"

PROMPTS = [
    # Turn 1: generic question — no skill, verify basic connectivity
    "What is the capital of Norway? Answer in one sentence.",
    # Turn 2: use_skill fires (ModelRetry → new LLM step), greeter__greet typed tool
    # appears in tool list, model calls it directly instead of run_script
    (
        "Use the greeter skill to greet me — my name is Andreas, use enthusiastic style. "
        "Tell me exactly what greeting was produced."
    ),
]


def main() -> None:
    provider = OpenAIProvider(base_url=VLLM_BASE_URL, api_key="dummy")
    profile  = OpenAIModelProfile(
        openai_supports_strict_tool_definition=False,
        openai_supports_tool_choice_required=False,
    )
    model  = OpenAIChatModel(MODEL_ID, provider=provider, profile=profile)
    config = AgentConfig(max_turns=12)
    agent  = Agent(model=model, skills_dir=SKILLS_DIR, config=config)

    snapshots = []

    for i, prompt in enumerate(PROMPTS, start=1):
        print(f"\nTurn {i}: {prompt[:80]}")
        result = agent.run(prompt)
        print(result.answer)

        # Snapshot all raw messages accumulated so far (pydantic-ai dataclasses)
        messages = ModelMessagesTypeAdapter.dump_python(
            agent._conversation_messages, mode="json"
        )
        snapshots.append({"turn": i, "prompt": prompt, "messages": messages})

    LOG_FILE.write_text(
        json.dumps(snapshots, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nLog written → {LOG_FILE}")


if __name__ == "__main__":
    main()
