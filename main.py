"""Example: run the skill agent with streaming output.

Demonstrates the full flow:
  1. Point the agent at a skills directory
  2. Call solve_stream() to see live tool calls, todo progress, and answer tokens

Run:
    uv run main.py
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from skill_agent import Agent

load_dotenv()


def main() -> None:
    api_key = os.getenv("API_KEY")
    if not api_key:
        raise SystemExit("API_KEY not found. Set it in .env or as an environment variable.")

    model = OpenAIChatModel(
        "gpt-4o",
        provider=OpenAIProvider(api_key=api_key),
    )

    # Point at the skills directory — the agent discovers everything inside
    agent = Agent(model=model, skills_dir=Path("skills"))

    prompt = "What is fast api? be brief"
    print(f"Prompt: {prompt}\n")

    result = agent.solve_stream(prompt)

    print(f"\nActivated skills: {result.activated_skills}")
    print(f"Tool calls: {len(result.tool_log)}")
    print(f"Todo items: {len(result.todo_list)}")
    print(f"Tokens: {result.usage.input_tokens} in / {result.usage.output_tokens} out")


if __name__ == "__main__":
    main()
