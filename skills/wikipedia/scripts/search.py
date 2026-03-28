"""Wikipedia article search."""
import sys
import json
import httpx


def main():
    args = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    query = args.get("query", "")
    if not query:
        print("Error: 'query' is required")
        return
    language = args.get("language", "no")
    limit = int(args.get("limit", 10))

    url = f"https://{language}.wikipedia.org/w/api.php"
    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srlimit": min(limit, 500),
        "format": "json",
    }

    headers = {"User-Agent": "KulturarvAgent/1.0 (research project)"}
    try:
        r = httpx.get(url, params=params, headers=headers, timeout=30)
        r.raise_for_status()
        results = r.json().get("query", {}).get("search", [])

        if not results:
            print(f"No Wikipedia articles found for: {query}")
            return

        lines = [f"Found {len(results)} Wikipedia articles for '{query}':\n"]
        for i, result in enumerate(results, 1):
            title = result.get("title", "Unknown")
            snippet = (
                result.get("snippet", "")
                .replace('<span class="searchmatch">', "")
                .replace("</span>", "")
            )
            lines.append(f"{i}. **{title}**")
            if snippet:
                lines.append(f"   {snippet}...")
        print("\n".join(lines))
    except Exception as e:
        print(f"Error searching Wikipedia: {e}")


if __name__ == "__main__":
    main()
