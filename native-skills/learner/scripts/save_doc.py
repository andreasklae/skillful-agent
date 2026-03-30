#!/usr/bin/env python3
"""Fetch content from a source and save it directly to a skill's docs/ folder.

Usage (via run_script):
    run_script(skill_name="learner", filename="save_doc.py",
               args='{"skill_path": "/path/to/skill", "source": "wikipedia",
                      "query": "Oseberg ship", "language": "en"}')

The agent never handles the large content — this script fetches and writes
in one step, keeping articles out of the context window entirely.

Supported sources:
    wikipedia  — Fetch a full Wikipedia article by title
                 Args: query (title), language (default "en")
    url        — Fetch raw content from a URL
                 Args: query (the URL)
    text       — Write arbitrary text directly (for small content only)
                 Args: query (the text content), filename (required)

Returns a short summary: what was saved, where, and how large.
"""

import json
import sys
from pathlib import Path
from urllib.parse import quote


def _sanitize_filename(title: str) -> str:
    """Turn a title into a safe filename."""
    safe = title.lower().strip()
    for ch in r' /\:*?"<>|':
        safe = safe.replace(ch, "-")
    # Collapse multiple dashes
    while "--" in safe:
        safe = safe.replace("--", "-")
    return safe.strip("-")[:80] + ".md"


def _fetch_wikipedia(title: str, language: str = "en") -> tuple[str, str, str]:
    """Fetch a Wikipedia article. Returns (content, source_url, actual_title)."""
    import httpx

    url = f"https://{language}.wikipedia.org/w/api.php"
    params = {
        "action": "query",
        "prop": "extracts|info",
        "explaintext": "true",
        "titles": title,
        "format": "json",
        "inprop": "url",
    }
    headers = {"User-Agent": "SkillLearner/1.0 (research project)"}
    r = httpx.get(url, params=params, headers=headers, timeout=30)
    r.raise_for_status()
    pages = r.json().get("query", {}).get("pages", {})

    for page_id, page in pages.items():
        if page_id == "-1":
            raise ValueError(f"Wikipedia article not found: {title}")
        actual_title = page.get("title", title)
        extract = page.get("extract", "")
        source_url = page.get(
            "fullurl",
            f"https://{language}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}",
        )
        return extract, source_url, actual_title

    raise ValueError(f"No results for: {title}")


def _fetch_url(url: str) -> tuple[str, str, str]:
    """Fetch raw content from a URL. Returns (content, source_url, title)."""
    import httpx

    r = httpx.get(url, timeout=30, follow_redirects=True)
    r.raise_for_status()
    # Use the URL path as the title
    title = url.split("/")[-1] or "fetched-page"
    return r.text, url, title


def save_doc(
    skill_path: str,
    source: str,
    query: str,
    language: str = "en",
    filename: str | None = None,
) -> None:
    """Fetch from source and write to the skill's docs/ folder."""
    skill_dir = Path(skill_path).resolve()
    docs_dir = skill_dir / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)

    if source == "wikipedia":
        content, source_url, title = _fetch_wikipedia(query, language)
        reliability = "Official Wikipedia article"
        out_filename = filename or _sanitize_filename(title)
        # Prepend metadata header
        full_content = (
            f"# {title}\n\n"
            f"**Source:** {source_url}\n"
            f"**Fetched by:** save_doc.py\n\n"
            f"---\n\n{content}"
        )

    elif source == "url":
        content, source_url, title = _fetch_url(query)
        reliability = f"Fetched from URL: {source_url}"
        out_filename = filename or _sanitize_filename(title)
        full_content = (
            f"# {title}\n\n"
            f"**Source:** {source_url}\n"
            f"**Fetched by:** save_doc.py\n\n"
            f"---\n\n{content}"
        )

    elif source == "text":
        if not filename:
            print("Error: 'filename' is required for source 'text'.", file=sys.stderr)
            sys.exit(1)
        full_content = query
        source_url = "agent-generated"
        title = filename
        reliability = "Agent-generated content"
        out_filename = filename

    else:
        print(f"Error: unknown source '{source}'. Use: wikipedia, url, text", file=sys.stderr)
        sys.exit(1)

    # Write the file
    out_path = docs_dir / out_filename
    out_path.write_text(full_content, encoding="utf-8")

    # Summary output (this is what the agent sees — keep it small)
    char_count = len(full_content)
    print(f"Saved: {out_path}")
    print(f"  Title: {title}")
    print(f"  Size: {char_count:,} chars")
    print(f"  Source: {source_url}")
    print(f"  Reliability: {reliability}")
    print(f"\nUpdate docs/index.md with this entry.")


def main() -> None:
    args = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}

    skill_path = args.get("skill_path", "")
    source = args.get("source", "")
    query = args.get("query", "")

    if not skill_path or not source or not query:
        print(
            "Error: 'skill_path', 'source', and 'query' are required.",
            file=sys.stderr,
        )
        print(
            'Example: {"skill_path": "/path/to/skill", "source": "wikipedia", "query": "Oseberg ship"}',
            file=sys.stderr,
        )
        sys.exit(1)

    save_doc(
        skill_path=skill_path,
        source=source,
        query=query,
        language=args.get("language", "en"),
        filename=args.get("filename"),
    )


if __name__ == "__main__":
    main()
