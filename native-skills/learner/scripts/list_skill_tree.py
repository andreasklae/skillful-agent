#!/usr/bin/env python3
"""Show the skill directory tree so the agent can reason about placement.

Usage:
    python list_skill_tree.py                    # scan from default root
    python list_skill_tree.py '{"root": "/path"}'  # scan a specific root

Walks the skill directory tree and prints a structured overview of:
  - Categories (directories without SKILL.md, used for organization)
  - Skills (directories with SKILL.md, showing name and description)
  - The full path to each, so the agent can target a --base-dir

Default root: two levels above the learner skill (i.e., skill-directory/).
"""

import json
import sys
from pathlib import Path


def _read_frontmatter(skill_md: Path) -> dict[str, str]:
    """Extract name and description from a SKILL.md frontmatter."""
    content = skill_md.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return {}
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}

    meta: dict[str, str] = {}
    current_key: str | None = None
    current_lines: list[str] = []

    def save():
        nonlocal current_key, current_lines
        if current_key is not None:
            meta[current_key] = " ".join(current_lines).strip()
        current_key = None
        current_lines = []

    for line in parts[1].splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line[0:1] in (" ", "\t") and current_key is not None:
            current_lines.append(stripped)
            continue
        if ":" in stripped:
            save()
            key, value = stripped.split(":", 1)
            current_key = key.strip()
            value = value.strip()
            if value in (">", "|", ">-", "|-", ">+", "|+"):
                pass
            elif value:
                current_lines.append(value)
    save()
    return meta


def scan_tree(root: Path, prefix: str = "") -> list[str]:
    """Recursively scan a directory tree and return display lines."""
    lines: list[str] = []
    if not root.is_dir():
        return lines

    children = sorted(
        [c for c in root.iterdir() if c.is_dir() and not c.name.startswith(".")],
        key=lambda p: p.name,
    )

    for i, child in enumerate(children):
        is_last = i == len(children) - 1
        connector = "\u2514\u2500\u2500 " if is_last else "\u251c\u2500\u2500 "
        extension = "    " if is_last else "\u2502   "

        skill_md = child / "SKILL.md"
        if skill_md.exists():
            meta = _read_frontmatter(skill_md)
            name = meta.get("name", child.name)
            desc = meta.get("description", "")
            if len(desc) > 80:
                desc = desc[:77] + "..."
            lines.append(f"{prefix}{connector}[skill] {name}: {desc}")
            lines.append(f"{prefix}{extension}  path: {child}")
        else:
            lines.append(f"{prefix}{connector}[category] {child.name}/")
            lines.append(f"{prefix}{extension}  path: {child}")
            sub = scan_tree(child, prefix + extension)
            lines.extend(sub)

    return lines


def main() -> None:
    # Parse optional JSON args — accepts a single root or a list of roots
    roots: list[Path] = []
    if len(sys.argv) > 1:
        try:
            args = json.loads(sys.argv[1])
            if "roots" in args:
                roots = [Path(r).resolve() for r in args["roots"]]
            elif "root" in args:
                roots = [Path(args["root"]).resolve()]
        except (json.JSONDecodeError, KeyError):
            roots = [Path(sys.argv[1]).resolve()]

    if not roots:
        print("Error: pass a root directory or list of roots.")
        print('  Example: \'{"roots": ["/path/to/Research", "/path/to/Learning"]}\'')
        print("  The user skill directories are listed in the system prompt.")
        sys.exit(1)

    for root in roots:
        print(f"Skill tree from: {root}\n")
        lines = scan_tree(root)
        if lines:
            print("\n".join(lines))
        else:
            print("(empty — no skills or categories found)")
        print()

    print("To create a skill in a category, use scaffold_skill.py with base_dir=<category-path>")


if __name__ == "__main__":
    main()
