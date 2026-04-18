---
name: web-search-free
description: Search the web and read pages with no API key and no billing — uses DuckDuckGo's HTML endpoint and Python stdlib only. Use this skill whenever you need current information, recent events, version numbers, documentation lookups, or any fact your training data may not cover, even if the user does not literally say "search the web." Trigger on phrases like "look up", "what's the latest", "find me", "recent X", "current version of Y", "is there news about Z", or any question where you suspect your knowledge is stale. Also use it whenever the user pastes a URL and asks you to read or summarize it.
---

# Free web search

Search the web and read pages without an API key. Two scripts in `scripts/` do the work; this file tells you when and how to use them.

## When to reach for this

Use the skill whenever a live web lookup would meaningfully improve your answer:

- The user asks about recent events, releases, prices, sports scores, or "latest" anything.
- You'd otherwise hedge with "as of my knowledge cutoff…"
- The user pastes a URL or asks you to summarize / extract from a page.
- A library, product, or person was likely created or updated after early 2025.
- The question is small but verifiable, and getting it wrong has a real cost (e.g. citing a wrong API, recommending a deprecated library).

If the question is about timeless facts you already know cold, skip the skill — extra round trips waste tokens and add latency.

## The two scripts

Both live in `scripts/` next to this file. Run them via the `run_script` tool with `skill_name="web-search-free"`.

### Search

```
run_script(skill_name="web-search-free", filename="search.py", args="<query>")
```

- `filename` is `"search.py"` — **not** `"scripts/search.py"`. The `scripts/` prefix is handled automatically.
- `args` is the query string. For multi-word queries wrap in shell quotes so `shlex` keeps them as one argument: `args='"fun facts Germany"'` → `sys.argv[1] = "fun facts Germany"`. A bare unquoted string like `args="fun facts Germany"` would be split into three separate argv items and only the first word would be used as the query.
- Default result limit is 10. To override: `args='"fun facts Germany" 5'`

Output JSON:
```json
{"query": "...", "results": [{"title": "...", "url": "https://...", "snippet": "..."}, ...]}
```

Pick the 1–3 most promising hits and fetch those — don't fetch every result.

### Fetch

```
run_script(skill_name="web-search-free", filename="fetch.py", args="https://example.com")
run_script(skill_name="web-search-free", filename="fetch.py", args="https://example.com 30000")
```

- `filename` is `"fetch.py"` — **not** `"scripts/fetch.py"`.
- Pass URL and optional `max_chars` as a space-separated string in `args`. They are split by `shlex` into separate argv items, so no quoting is needed for URLs (they contain no spaces).
- Default cap is 10,000 characters. Bump to 30000 when a single page is your whole answer.
- Do **not** wrap the URL in quotes inside `args` — that adds literal `"` characters and causes `unknown url type`.

Output JSON:
```json
{"url": "...", "title": "...", "text": "...", "truncated": true|false}
```

### Query crafting

DuckDuckGo's HTML endpoint is picky. Follow these rules to avoid empty results:

- **Keep it short**: 3–6 words outperform long conversational phrases. "VG nyheter" beats "get the latest headline from the Norwegian newspaper VG."
- **Use the language of the target**: Norwegian sites return more results with Norwegian query words. Use `norske nyheter VG` not `Norwegian news VG`.
- **Be specific, not descriptive**: Use proper nouns, brand names, version numbers. Avoid filler words like "the", "a", "latest", "information about".
- **Retry with a shorter variant on 0 results**: If the first query returns an empty `results` list, trim it by one or two words and try again before giving up.
- **Add a site hint as a keyword** (not a filter): `site:vg.no` syntax is not supported, but including `vg.no` as a keyword (e.g. `vg.no nyheter`) biases results toward that domain.

### Typical flow

1. Craft a short, specific query following the rules above.
2. Run `search.py` once. If results is empty, retry once with a shorter variant.
3. Read titles + snippets, pick 1–3 URLs that look authoritative.
4. Run `fetch.py` on each — these are independent, so issue them as parallel Bash calls in the same turn.
5. Synthesize the answer, citing the sources you actually used.

## Citation is mandatory

Every answer that draws on these results ends with a `Sources:` section listing the URLs you actually used as markdown links:

```
Sources:
- [Page title](https://example.com/page)
- [Another page](https://example.com/other)
```

This isn't decoration. It lets the user verify the claim, makes it obvious when you're grounded vs. guessing, and protects you from quietly hallucinating a fact that "felt right." If you fetched a page and ignored it, don't cite it. If you fetched nothing and answered from memory, say so explicitly instead of fabricating a source.

## What can go wrong, and what to do about it

- **DuckDuckGo rate-limits or returns 0 results.** The script will exit cleanly with an empty `results` list. This is DDG throttling, not a bug. Wait a minute, try once more, or tell the user the search failed — don't invent results to fill the gap.
- **Markup changes break the parser.** If `search.py` returns 0 results but the request itself succeeded, DDG probably changed their HTML. Inspect the raw page (`curl -A '<UA>' -d 'q=test' https://html.duckduckgo.com/html/`) and patch the parser in `scripts/search.py` rather than papering over it.
- **JS-only pages look empty.** `fetch.py` reads raw HTML; SPAs that render in the browser return a near-empty `text` field. If you see this on a major site, it's the limitation, not a bug. Pick a different result or note the gap to the user.
- **Datacenter / cloud IPs get blocked harder than residential.** If you're running in CI and seeing constant zero-result responses, that's why. Don't burn many retries.

## Why this skill exists

Some Claude environments don't ship a built-in web search. Others have one but the model under-uses it. Bundling the search and fetch logic as standalone scripts means any agent with `python3` and outbound HTTPS can answer time-sensitive questions and ground its claims in real sources — no API key, no provider lock-in, no surprise bill.
