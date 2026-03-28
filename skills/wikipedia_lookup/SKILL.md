---
name: wikipedia_lookup
description: Use this skill whenever the user asks about facts, people, places, events, dates, historical context, scientific concepts, definitions, or anything that could be answered with encyclopedic knowledge. Also trigger when the user asks "what is", "who was", "when did", "tell me about", or wants a summary of any real-world topic.
---

# Wikipedia Lookup

Answer knowledge questions using Wikipedia.

## Steps

1. Call `run_script` with skill_name `wikipedia_lookup`, filename `lookup.py`, and args as a JSON string containing the query:
   ```
   run_script(skill_name="wikipedia_lookup", filename="lookup.py", args='{"query": "Ada Lovelace"}')
   ```
   - Use the most specific term possible: "Ada Lovelace" not "first programmer"
   - For ambiguous topics, add context: "Mercury planet" vs "Mercury element"

2. Return a concise answer based on the summary.
   - If the result doesn't match what the user asked, try a more specific query.
   - If nothing relevant is found, say so honestly.
