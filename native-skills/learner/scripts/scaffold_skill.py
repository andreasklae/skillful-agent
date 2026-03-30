#!/usr/bin/env python3
"""Scaffold the directory structure for a new learned skill.

Usage (via run_script):
    run_script(skill_name="learner", filename="scaffold_skill.py",
               args='{"name": "my-skill"}')

    run_script(skill_name="learner", filename="scaffold_skill.py",
               args='{"name": "my-skill", "base_dir": "/path/to/category"}')

    # For skills that expose an API or service with write operations:
    run_script(skill_name="learner", filename="scaffold_skill.py",
               args='{"name": "my-api-skill", "base_dir": "/path/to/category", "api_writes": true}')

Creates the following structure under the target directory:
    <name>/SKILL.md               - Frontmatter template with metadata fields
    <name>/docs/                  - Knowledge base directory
    <name>/docs/index.md          - Table of contents for saved documents
    <name>/scripts/               - Executable scripts directory
    <name>/tests/                 - Test cases directory
    <name>/tests/test_results.md  - Test results log

When api_writes=true, also creates:
    <name>/client_functions.json  - Declares request_permission client function
    <name>/permissions.yaml       - Default permission rules (reads allowed, writes prompt)

Idempotent: will not overwrite existing files.
"""

import json
import sys
from datetime import date
from pathlib import Path

_CLIENT_FUNCTIONS_TEMPLATE = json.dumps(
    [
        {
            "name": "request_permission",
            "description": (
                "Request user permission to execute a write operation on this service. "
                "Call before any insert, update, delete, upsert, restore, or other destructive action."
            ),
            "awaits_user": True,
            "parameters": [
                {
                    "name": "operation",
                    "type": "string",
                    "description": "The exact operation or method name being called.",
                    "required": True,
                },
                {
                    "name": "domain",
                    "type": "string",
                    "description": "The domain or category of the operation (e.g. 'Users', 'Orders').",
                    "required": True,
                },
                {
                    "name": "action",
                    "type": "string",
                    "description": "The action type: insert, update, delete, upsert, restore, or similar.",
                    "required": True,
                },
            ],
        }
    ],
    indent=2,
)

_PERMISSIONS_YAML_TEMPLATE = """\
# Permission manifest for {skill_name}
#
# Controls which operations the agent may execute without user approval.
# Rules are evaluated in order — the last matching rule wins.
#
# IMPORTANT: This file is client-controlled. The agent may create it but
# cannot overwrite it once it exists. Edit it directly to change rules.
#
# Fields per rule:
#   domains: list of domains/categories to match, or ["*"] for all
#   actions:  list of action types to match, or ["*"] for all
#   allow:    true to allow without prompt, false to require user approval

default_allow: false

rules:
  # Read operations are pre-approved — no user prompt needed
  - domains: ["*"]
    actions: ["query", "read", "search", "get", "list", "count", "fetch"]
    allow: true

  # Write operations (insert, update, delete, etc.) require user approval.
  # To pre-approve specific write actions, add rules here, e.g.:
  # - domains: ["TestDomain"]
  #   actions: ["insert"]
  #   allow: true
"""


def scaffold(skill_name: str, base_dir: Path | None = None, api_writes: bool = False) -> None:
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

    # client_functions.json and permissions.yaml — only for API-write skills
    if api_writes:
        cf_path = skill_dir / "client_functions.json"
        if not cf_path.exists():
            cf_path.write_text(_CLIENT_FUNCTIONS_TEMPLATE + "\n", encoding="utf-8")
            print(f"  Created {cf_path.relative_to(base_dir)}")
        else:
            print(f"  Skipped {cf_path.relative_to(base_dir)} (already exists)")

        perm_path = skill_dir / "permissions.yaml"
        if not perm_path.exists():
            perm_path.write_text(
                _PERMISSIONS_YAML_TEMPLATE.format(skill_name=skill_name),
                encoding="utf-8",
            )
            print(f"  Created {perm_path.relative_to(base_dir)}")
        else:
            print(f"  Skipped {perm_path.relative_to(base_dir)} (already exists — client-controlled)")

    print(f"\nScaffolded learned skill at: {skill_dir}")
    if api_writes:
        print("  client_functions.json — agent will call request_permission before writes")
        print("  permissions.yaml      — edit directly to pre-approve or restrict operations")


def main() -> None:
    args = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}

    name = args.get("name", "").strip().lower().replace(" ", "-")
    if not name:
        print("Error: 'name' is required.", file=sys.stderr)
        sys.exit(1)

    base_dir = None
    if args.get("base_dir"):
        base_dir = Path(args["base_dir"])

    api_writes = bool(args.get("api_writes", False))

    scaffold(name, base_dir, api_writes=api_writes)


if __name__ == "__main__":
    main()
