"""Search Wikipedia and return the intro summary of a topic.

Usage:
    python lookup.py '{"query": "Ada Lovelace"}'

Receives a JSON string as the first CLI argument with a "query" field.
Prints the page title and summary to stdout.

Requirements:
    pip install wikipedia-api
"""

import json
import sys

import wikipediaapi

_wiki = wikipediaapi.Wikipedia(user_agent="SkillAgentSDK/1.0", language="en")


def lookup(query: str) -> str:
    """Search Wikipedia and return the best-matching page summary."""
    results = _wiki.search(query, limit=3)
    if not results.pages:
        return f"No Wikipedia results found for: {query}"

    page = next(iter(results.pages.values()))
    if not page.exists():
        return f"Page '{page.title}' does not exist."

    return f"**{page.title}**\n\n{page.summary}"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Error: pass a JSON string with a 'query' field.", file=sys.stderr)
        sys.exit(1)

    try:
        args = json.loads(sys.argv[1])
    except json.JSONDecodeError as e:
        print(f"Error: invalid JSON — {e}", file=sys.stderr)
        sys.exit(1)

    query = args.get("query", "").strip()
    if not query:
        print("Error: 'query' is required.", file=sys.stderr)
        sys.exit(1)

    print(lookup(query))
