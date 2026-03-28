"""Skill registry: discovers SKILL.md files and returns typed Skill models.

Each skill lives in its own directory under a user-provided skills folder:

    skills/
        my_skill/
            SKILL.md          <- instructions (required)
            scripts/          <- executable code (optional)
            references/       <- docs loaded into context as needed (optional)
            assets/           <- files used in output (optional)

The SKILL.md format uses YAML-like frontmatter between --- markers:

    ---
    name: my_skill
    description: When to use this skill.
    ---

    # Full instructions here (the "body")
    Only loaded when the agent calls use_skill.

Skills can bundle three types of resources:
    - scripts/    : Python files the agent can run for deterministic tasks
    - references/ : Markdown/text docs the agent can read into context
    - assets/     : Templates, icons, fonts, etc. used in output

Discovery scans the skills directory, parses each SKILL.md + its resources,
and returns a dict of {name: Skill} sorted alphabetically.
"""

from pathlib import Path

from .models import Skill


def _parse_frontmatter(raw: str) -> dict[str, str]:
    """Parse simple `key: value` frontmatter without requiring a YAML library.

    Handles:
      - Quoted values (single or double quotes are stripped)
      - Comment lines (starting with #) and blank lines are skipped
    """
    meta: dict[str, str] = {}
    for line in raw.splitlines():
        stripped = line.strip()

        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue

        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()

        # Strip surrounding quotes if present
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]

        meta[key] = value
    return meta


def _list_files(directory: Path) -> list[str]:
    """List filenames in a directory, excluding hidden files and __pycache__."""
    if not directory.is_dir():
        return []
    return sorted(
        f.name
        for f in directory.iterdir()
        if f.is_file() and not f.name.startswith(".")
    )


def _parse_skill(skill_dir: Path) -> Skill | None:
    """Parse a single skill directory into a typed Skill model.

    Loads SKILL.md (required) and discovers bundled resources (optional).
    Returns None if the directory has no valid SKILL.md.
    """
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.exists():
        return None

    content = skill_file.read_text(encoding="utf-8")

    # Frontmatter must start and end with ---
    if not content.startswith("---"):
        return None
    parts = content.split("---", 2)
    if len(parts) < 3:
        return None

    meta = _parse_frontmatter(parts[1])
    body = parts[2].strip()

    return Skill(
        name=str(meta.get("name", skill_dir.name)),
        description=str(meta.get("description", "")),
        body=body,
        path=skill_file,
        scripts=_list_files(skill_dir / "scripts"),
        references=_list_files(skill_dir / "references"),
        assets=_list_files(skill_dir / "assets"),
    )


def discover_skills(skills_dir: Path) -> dict[str, Skill]:
    """Discover all skills in the given directory.

    Scans for subdirectories containing a SKILL.md file, parses each one
    (including any bundled resources), and returns them in alphabetical order.

    Returns:
        Dict mapping skill name -> Skill model.
    """
    skills_dir = skills_dir.resolve()
    if not skills_dir.exists():
        return {}

    skills: list[Skill] = []
    for child in sorted(skills_dir.iterdir()):
        if child.is_dir() and (child / "SKILL.md").exists():
            parsed = _parse_skill(child)
            if parsed:
                skills.append(parsed)

    skills.sort(key=lambda s: s.name)
    return {s.name: s for s in skills}
