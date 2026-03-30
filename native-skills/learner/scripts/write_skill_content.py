#!/usr/bin/env python3
"""Write content to a file, creating parent directories as needed.

Usage (via run_script):
    run_script(skill_name="learner", filename="write_skill_content.py",
               args='{"path": "/abs/path/to/file.md", "content": "file content"}')

Reads args from stdin (piped by run_script) for reliable handling of large
content and special characters. Falls back to sys.argv[1] for small inputs.

Accepts JSON with:
  - path (required): absolute path to the file to write
  - content (required): the content to write
  - append (optional, default false): if true, append instead of overwrite
"""

import json
import sys
from pathlib import Path


def main() -> None:
    # Read args from stdin (preferred — handles large content reliably)
    # Falls back to sys.argv[1] for backwards compatibility
    raw = sys.stdin.read()
    if not raw.strip() and len(sys.argv) > 1:
        raw = sys.argv[1]

    if not raw.strip():
        print("Error: pass JSON with 'path' and 'content' keys.", file=sys.stderr)
        sys.exit(1)

    try:
        args = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Error: invalid JSON: {e}", file=sys.stderr)
        sys.exit(1)

    file_path = args.get("path")
    content = args.get("content")
    append = args.get("append", False)

    if not file_path or content is None:
        print("Error: 'path' and 'content' are required.", file=sys.stderr)
        sys.exit(1)

    target = Path(file_path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    if append:
        with open(target, "a", encoding="utf-8") as f:
            f.write(content)
        print(f"Appended to: {target} ({len(content)} chars)")
    else:
        target.write_text(content, encoding="utf-8")
        print(f"Wrote: {target} ({len(content)} chars)")


if __name__ == "__main__":
    main()
