#!/usr/bin/env python3
"""Fetch a URL and print its readable text as JSON.

stdlib only. No API key. Usage:
    python3 fetch.py "https://example.com" [max_chars]
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from html.parser import HTMLParser

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.0 Safari/605.1.15"
)

# Tags whose content we drop entirely (scripts, styles, true chrome).
# Note: we deliberately do NOT skip <head>, <header>, or <footer>:
#   - <head> contains <title>, which we want to capture.
#   - <header>/<footer> are often used semantically inside article content
#     (e.g. Wikipedia, MDN), so skipping them throws away the body.
# Only NON-VOID tags belong here. Void elements (<input>, <br>, <img>, etc.)
# never emit an end tag, so adding them to the skip set would leak skip_depth
# upward forever and silently swallow the rest of the document.
SKIP_TAGS = {
    "script", "style", "noscript", "svg", "iframe",
    "nav", "aside", "form",
    "button", "select", "textarea",
}
# Tags that should produce a line break around their content.
BLOCK_TAGS = {
    "p", "div", "li", "tr", "br",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "article", "blockquote", "pre",
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.title: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in SKIP_TAGS:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        if self._in_title:
            self.title.append(data)
        else:
            self.parts.append(data)


def _detect_charset(content_type: str, raw: bytes) -> str:
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    if m:
        return m.group(1)
    # Fallback: peek inside the first 2KB of bytes for a meta charset.
    head = raw[:2048].decode("ascii", errors="replace")
    m = re.search(r'<meta[^>]+charset=["\']?([\w-]+)', head, re.I)
    if m:
        return m.group(1)
    return "utf-8"


def fetch(url: str, max_chars: int = 10_000) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        ctype = resp.headers.get("Content-Type", "")
        raw = resp.read()
        final_url = resp.geturl()
    charset = _detect_charset(ctype, raw)
    html = raw.decode(charset, errors="replace")

    extractor = _TextExtractor()
    extractor.feed(html)
    extractor.close()

    title = "".join(extractor.title).strip()
    body = "".join(extractor.parts)
    body = re.sub(r"[ \t\r\f\v]+", " ", body)
    body = re.sub(r"\n[ \t]+", "\n", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()

    truncated = len(body) > max_chars
    if truncated:
        body = body[:max_chars]

    return {
        "url": final_url,
        "title": title,
        "text": body,
        "truncated": truncated,
        "char_count": len(body),
    }


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: fetch.py <url> [max_chars]", file=sys.stderr)
        return 2
    url = sys.argv[1]
    max_chars = int(sys.argv[2]) if len(sys.argv) > 2 else 10_000
    try:
        out = fetch(url, max_chars)
    except Exception as exc:  # noqa: BLE001 - surface failure as JSON
        print(json.dumps({"url": url, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
