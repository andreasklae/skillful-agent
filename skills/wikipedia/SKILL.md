---
name: wikipedia
description: Use this skill whenever the user asks about facts, people, places, historical events, science, culture, geography, landmarks, or any encyclopedic topic. Also trigger when the user needs to look up a specific article, find information near GPS coordinates, or search for background knowledge about a location or subject. Use for Norwegian topics (default language is 'no') and international topics alike. Three operations available — search (find articles by keyword), summary (get full article text), geosearch (find articles near coordinates).
---

# Wikipedia Lookup

Answer knowledge questions using Wikipedia. Three operations are available.

## Operations

### 1. Search — find articles by keyword

```
run_script(skill_name="wikipedia", filename="search.py", args='{"query": "Akershus festning", "language": "no", "limit": 5}')
```

- `query` (required): search term
- `language`: Wikipedia language code, default `"no"` (Norwegian). Use `"en"` for English.
- `limit`: max results, default 10

Returns titles and snippets. Pick the most relevant title, then use `summary.py` to get the full article.

### 2. Read article — get full article text

```
run_script(skill_name="wikipedia", filename="summary.py", args='{"title": "Akershus festning", "language": "no"}')
```

- `title` (required): exact article title from a search result
- `language`: default `"no"`

Returns the full article text (plain text, all sections) up to 8000 characters, with source URL. Use this after `search.py` to read the content of a relevant article.

### 3. Geosearch — find articles near coordinates

```
run_script(skill_name="wikipedia", filename="geosearch.py", args='{"latitude": 59.907, "longitude": 10.737, "radius": 1000, "language": "no"}')
```

- `latitude`, `longitude` (required): GPS coordinates in decimal degrees
- `radius`: search radius in meters, default 1000 (max 10000)
- `language`: default `"no"`
- `limit`: max results, default 10

Returns nearby Wikipedia articles with distances. Ideal for identifying landmarks when you have GPS coordinates.

## Tips

- For ambiguous terms, add context: `"Mercury planet"` not `"Mercury"`
- If summary returns the wrong article, search again with a more specific query
- Geosearch is the right tool when you have coordinates and want to know what's near them
