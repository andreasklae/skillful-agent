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
    """Parse YAML-like frontmatter without requiring a YAML library.

    Handles:
      - Simple ``key: value`` on a single line
      - Quoted values (single or double quotes are stripped)
      - Multi-line block scalars: ``key: >`` or ``key: |`` followed by
        indented continuation lines (joined with spaces)
      - Implicit continuation: ``key:`` with the value on indented lines
      - Comment lines (starting with #) and blank lines are skipped
    """
    meta: dict[str, str] = {}
    current_key: str | None = None
    current_lines: list[str] = []

    def _save():
        nonlocal current_key, current_lines
        if current_key is not None:
            value = " ".join(current_lines).strip()
            if value:
                meta[current_key] = value
        current_key = None
        current_lines = []

    for line in raw.splitlines():
        stripped = line.strip()

        if not stripped or stripped.startswith("#"):
            continue

        # Indented line while we have an active key → continuation
        if line[0:1] in (" ", "\t") and current_key is not None:
            current_lines.append(stripped)
            continue

        # New key: value line
        if ":" in stripped:
            _save()
            key, value = stripped.split(":", 1)
            current_key = key.strip()
            value = value.strip()

            # Strip surrounding quotes
            if len(value) >= 2 and (
                (value[0] == '"' and value[-1] == '"')
                or (value[0] == "'" and value[-1] == "'")
            ):
                value = value[1:-1]

            # Block scalar indicators → value comes from continuation lines
            if value in (">", "|", ">-", "|-", ">+", "|+"):
                pass  # current_lines stays empty; continuations will fill it
            elif value:
                current_lines.append(value)

    _save()
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


def discover_skills(skills_dir: Path | list[Path]) -> dict[str, Skill]:
    """Discover all skills in one or more directory trees.

    Accepts either a single Path or a list of Paths. Recursively scans each
    directory tree for any subdirectory containing a SKILL.md file, parses
    them (including any bundled resources), and returns all skills merged and
    sorted alphabetically.

    This means you can pass a top-level directory containing nested
    categories of skills and every SKILL.md at any depth will be found.

    If the same skill name appears in multiple directories, the last one wins.

    Returns:
        Dict mapping skill name -> Skill model.
    """
    dirs = [skills_dir] if isinstance(skills_dir, Path) else skills_dir

    skills: list[Skill] = []
    for d in dirs:
        resolved = d.resolve()
        if not resolved.exists():
            continue
        for skill_md in sorted(resolved.rglob("SKILL.md")):
            parsed = _parse_skill(skill_md.parent)
            if parsed:
                skills.append(parsed)

    skills.sort(key=lambda s: s.name)
    return {s.name: s for s in skills}
