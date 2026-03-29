import asyncio
from pathlib import Path

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from skill_agent import Agent, TextDeltaEvent, ToolCallEvent, TodoUpdateEvent

# Any pydantic-ai compatible model works (OpenAI, Anthropic, Azure, Gemini, etc.)
model = OpenAIChatModel("gpt-4o", provider=OpenAIProvider(api_key="your-key"))

agent = Agent(model=model, skills_dir=Path("skills"))


# ── Blocking ──────────────────────────────────────────────────────────
# Waits for the full answer. Returns AgentResult with the complete event timeline.

result = agent.run("What is the speed of light?")

print(result.answer)
print(result.activated_skills)   # which skills were loaded
print(result.usage.input_tokens) # token usage

# Filter the event timeline by type
tool_calls = [e for e in result.events if isinstance(e, ToolCallEvent)]
todo_states = [e for e in result.events if isinstance(e, TodoUpdateEvent)]


# ── Streaming ─────────────────────────────────────────────────────────
# Yields typed events in real time. The caller decides what to do with them.

async def stream_to_cli():
    async for event in agent.run_stream("What is the speed of light?"):
        if isinstance(event, TextDeltaEvent):
            print(event.content, end="", flush=True)
        elif isinstance(event, ToolCallEvent):
            print(f"[tool] {event.name}")
        elif isinstance(event, TodoUpdateEvent):
            for item in event.items:
                print(f"  - {item.content} ({item.status})")
    print()

asyncio.run(stream_to_cli())

# Same agent instance remembers context for a follow-up:
# async for event in agent.run_stream("And in miles per hour?"):
#     ...
# agent.clear_conversation()  # start over when needed