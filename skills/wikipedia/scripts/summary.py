"""Wikipedia full article text."""
import sys
import json
import httpx


def main():
    args = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    title = args.get("title", "")
    if not title:
        print("Error: 'title' is required")
        return
    language = args.get("language", "no")

    url = f"https://{language}.wikipedia.org/w/api.php"
    params = {
        "action": "query",
        "prop": "extracts|info",
        "explaintext": "true",
        "titles": title,
        "format": "json",
        "inprop": "url",
    }

    headers = {"User-Agent": "KulturarvAgent/1.0 (research project)"}
    try:
        r = httpx.get(url, params=params, headers=headers, timeout=30)
        r.raise_for_status()
        pages = r.json().get("query", {}).get("pages", {})

        for page_id, page in pages.items():
            if page_id == "-1":
                print(f"Article not found: {title}")
                return
            article_title = page.get("title", title)
            extract = page.get("extract", "No content available")
            source_url = page.get(
                "fullurl",
                f"https://{language}.wikipedia.org/wiki/{title.replace(' ', '_')}",
            )
            truncated = len(extract) > 8000
            if truncated:
                extract = extract[:8000] + "\n\n[... article continues — truncated at 8000 chars]"
            print(f"# {article_title}\n\n{extract}\n\n**Source:** {source_url}")
            return

        print(f"No results for: {title}")
    except Exception as e:
        print(f"Error fetching Wikipedia article: {e}")


if __name__ == "__main__":
    main()
