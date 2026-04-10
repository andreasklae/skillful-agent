"""Skill archive extraction with path traversal protection."""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path


def extract_skill_archive(
    filename: str, archive_bytes: bytes, extract_root: Path
) -> None:
    """Extract a skill archive (.zip, .tar.gz, .tgz) into extract_root."""
    lower_name = filename.lower()
    if lower_name.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            _safe_extract_zip(archive, extract_root)
        return

    if lower_name.endswith(".tar.gz") or lower_name.endswith(".tgz"):
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
            _safe_extract_tar(archive, extract_root)
        return

    raise ValueError("Unsupported archive type. Upload a .zip, .tar.gz, or .tgz file.")


def find_uploaded_skill_dir(extract_root: Path) -> Path:
    """Locate the single skill directory (containing SKILL.md) in an extracted archive."""
    skill_dirs = sorted({skill_md.parent for skill_md in extract_root.rglob("SKILL.md")})
    if not skill_dirs:
        raise ValueError("Archive does not contain a SKILL.md file.")
    if len(skill_dirs) > 1:
        raise ValueError("Archive must contain exactly one skill directory with one SKILL.md.")
    return skill_dirs[0]


def _safe_extract_zip(archive: zipfile.ZipFile, extract_root: Path) -> None:
    for member in archive.infolist():
        _ensure_within_root(extract_root / member.filename, extract_root)
    archive.extractall(extract_root)


def _safe_extract_tar(archive: tarfile.TarFile, extract_root: Path) -> None:
    for member in archive.getmembers():
        _ensure_within_root(extract_root / member.name, extract_root)
    archive.extractall(extract_root, filter="data")


def _ensure_within_root(path: Path, root: Path) -> None:
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    if resolved_root not in (resolved_path, *resolved_path.parents):
        raise ValueError("Archive contains unsafe paths.")
