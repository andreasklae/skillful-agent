#!/usr/bin/env python3
"""Scaffold the directory structure for a new learned skill.

Usage (via run_script):
    run_script(skill_name="learner", filename="scaffold_skill.py",
               args='{"name": "my-skill"}')

    run_script(skill_name="learner", filename="scaffold_skill.py",
               args='{"name": "my-skill", "base_dir": "/path/to/category"}')

Creates the following structure under the target directory:
    <name>/SKILL.md          - Frontmatter template with metadata fields
    <name>/docs/             - Knowledge base directory
    <name>/docs/index.md     - Table of contents for saved documents
    <name>/scripts/          - Executable scripts directory
    <name>/tests/            - Test cases directory
    <name>/tests/test_results.md - Test results log

Idempotent: will not overwrite existing files.
"""

import json
import sys
from datetime import date
from pathlib import Path


def scaffold(skill_name: str, base_dir: Path | None = None) -> None:
    """Create the directory structure and template files for a learned skill."""
    if base_dir is None:
        print("Error: base_dir is required. Pass one of the user skill directories", file=sys.stderr)
        print("listed in the system prompt.", file=sys.stderr)
        sys.exit(1)

    skill_dir = base_dir / skill_name

    # Create directories
    for subdir in ["docs", "scripts", "tests"]:
        (skill_dir / subdir).mkdir(parents=True, exist_ok=True)

    # SKILL.md with frontmatter template
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        skill_md.write_text(
            f"""---
name: {skill_name}
description: >
  TODO — Write a specific description of what this skill does and when to
  trigger it. Include trigger phrases and contexts.
sources: []
status: draft
learned_date: "{date.today().isoformat()}"
confidence: medium
source_reliability: >
  TODO — Summarize the reliability of the sources used to create this skill.
---

# {skill_name.replace("-", " ").replace("_", " ").title()}

TODO — Write the skill instructions here.
""",
            encoding="utf-8",
        )
        print(f"  Created {skill_md.relative_to(base_dir)}")
    else:
        print(f"  Skipped {skill_md.relative_to(base_dir)} (already exists)")

    # docs/index.md — table of contents for saved documents
    index_md = skill_dir / "docs" / "index.md"
    if not index_md.exists():
        index_md.write_text(
            f"""# {skill_name.replace("-", " ").replace("_", " ").title()} — Document Index

Saved documents for this skill's knowledge base. Each entry describes the
content and reliability of a saved source so the agent can find and load the
right file on demand without reading everything.

| File | Description | Source | Reliability | Confidence |
|------|-------------|--------|-------------|------------|
| | | | | |
""",
            encoding="utf-8",
        )
        print(f"  Created {index_md.relative_to(base_dir)}")
    else:
        print(f"  Skipped {index_md.relative_to(base_dir)} (already exists)")

    # tests/test_results.md
    test_results = skill_dir / "tests" / "test_results.md"
    if not test_results.exists():
        test_results.write_text(
            f"""# Test Results — {skill_name.replace("-", " ").replace("_", " ").title()}

## Status: Not yet tested

Record what was tested, what passed, what failed, and what needs user action.

| Test | Result | Notes |
|------|--------|-------|
| | | |
""",
            encoding="utf-8",
        )
        print(f"  Created {test_results.relative_to(base_dir)}")
    else:
        print(f"  Skipped {test_results.relative_to(base_dir)} (already exists)")

    print(f"\nScaffolded learned skill at: {skill_dir}")


def main() -> None:
    args = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}

    name = args.get("name", "").strip().lower().replace(" ", "-")
    if not name:
        print("Error: 'name' is required.", file=sys.stderr)
        sys.exit(1)

    base_dir = None
    if args.get("base_dir"):
        base_dir = Path(args["base_dir"])

    scaffold(name, base_dir)


if __name__ == "__main__":
    main()
