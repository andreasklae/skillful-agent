"""Wikipedia geosearch — find articles near GPS coordinates."""
import sys
import json
import httpx


def main():
    args = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    lat = args.get("latitude")
    lon = args.get("longitude")
    if lat is None or lon is None:
        print("Error: 'latitude' and 'longitude' are required")
        return
    language = args.get("language", "no")
    radius = int(args.get("radius", 1000))
    limit = int(args.get("limit", 10))

    url = f"https://{language}.wikipedia.org/w/api.php"
    params = {
        "action": "query",
        "list": "geosearch",
        "gscoord": f"{lat}|{lon}",
        "gsradius": min(radius, 10000),
        "gslimit": min(limit, 500),
        "format": "json",
    }

    headers = {"User-Agent": "KulturarvAgent/1.0 (research project)"}
    try:
        r = httpx.get(url, params=params, headers=headers, timeout=30)
        r.raise_for_status()
        results = r.json().get("query", {}).get("geosearch", [])

        if not results:
            print(f"No Wikipedia articles found within {radius}m of ({lat}, {lon})")
            return

        lines = [f"Found {len(results)} Wikipedia articles near ({lat}, {lon}):\n"]
        for i, result in enumerate(results, 1):
            title = result.get("title", "Unknown")
            dist = result.get("dist", 0)
            page_id = result.get("pageid", "")
            article_url = f"https://{language}.wikipedia.org/?curid={page_id}"
            lines.append(f"{i}. **{title}** ({dist:.0f}m away)")
            lines.append(f"   {article_url}")
        print("\n".join(lines))
    except Exception as e:
        print(f"Error in Wikipedia geosearch: {e}")


if __name__ == "__main__":
    main()
