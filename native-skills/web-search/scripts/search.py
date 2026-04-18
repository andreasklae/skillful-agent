#!/usr/bin/env python3
"""Search DuckDuckGo via the HTML endpoint and print JSON results.

stdlib only. No API key. Usage:
    python3 search.py "query" [limit]
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from html.parser import HTMLParser

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.0 Safari/605.1.15"
)
ENDPOINT = "https://html.duckduckgo.com/html/"


def _unwrap_redirect(href: str) -> str:
    """DDG wraps result links in /l/?uddg=<encoded-target>. Unwrap them."""
    if not href:
        return href
    if href.startswith("//"):
        href = "https:" + href
    parsed = urllib.parse.urlparse(href)
    if parsed.path.endswith("/l/") or "/l/?" in href:
        params = urllib.parse.parse_qs(parsed.query)
        target = params.get("uddg") or params.get("u")
        if target:
            return urllib.parse.unquote(target[0])
    return href


class _DDGParser(HTMLParser):
    """Pull (title, url, snippet) triples out of DDG's HTML result list."""

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict] = []
        self._cur: dict | None = None
        self._mode: str | None = None  # "title" | "snippet" | None
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        cls = a.get("class", "")
        if tag == "a" and "result__a" in cls:
            # New result starts here.
            if self._cur is not None:
                self._flush()
            self._cur = {"title": "", "url": _unwrap_redirect(a.get("href", "")), "snippet": ""}
            self._mode = "title"
            self._buf = []
        elif tag == "a" and "result__snippet" in cls and self._cur is not None:
            self._mode = "snippet"
            self._buf = []
        elif tag in ("div", "td") and "result__snippet" in cls and self._cur is not None:
            self._mode = "snippet"
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._mode == "title" and self._cur is not None:
            self._cur["title"] = "".join(self._buf).strip()
            self._mode = None
            self._buf = []
        elif tag in ("a", "div", "td") and self._mode == "snippet" and self._cur is not None:
            self._cur["snippet"] = "".join(self._buf).strip()
            self._mode = None
            self._buf = []

    def handle_data(self, data: str) -> None:
        if self._mode in ("title", "snippet"):
            self._buf.append(data)

    def close(self) -> None:  # noqa: D401 - HTMLParser hook
        self._flush()
        super().close()

    def _flush(self) -> None:
        if self._cur is None:
            return
        if self._cur.get("url") and self._cur.get("title"):
            self.results.append(self._cur)
        self._cur = None
        self._mode = None
        self._buf = []


def search(query: str, limit: int = 10) -> list[dict]:
    body = urllib.parse.urlencode({"q": query, "kl": "wt-wt"}).encode()
    req = urllib.request.Request(
        ENDPOINT,
        data=body,
        method="POST",
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    parser = _DDGParser()
    parser.feed(html)
    parser.close()
    return parser.results[:limit]


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: search.py <query> [limit]", file=sys.stderr)
        return 2
    query = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    try:
        results = search(query, limit)
    except Exception as exc:  # noqa: BLE001 - surface any failure cleanly
        print(json.dumps({"query": query, "error": str(exc), "results": []}, ensure_ascii=False))
        return 1
    print(json.dumps({"query": query, "results": results}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
