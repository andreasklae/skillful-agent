"""Build pydantic-ai user prompts from text plus local file paths.

Text-like and PDF (extracted) content is inlined as markdown sections. Images are
passed as ``BinaryContent`` parts alongside the text, matching vision model APIs.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from pydantic_ai.messages import BinaryContent

_IMAGE_SUFFIXES = frozenset({
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".bmp",
    ".heic",
    ".heif",
})
_TEXT_SUFFIXES = frozenset({
    ".csv",
    ".tsv",
    ".json",
    ".jsonl",
    ".xml",
    ".txt",
    ".md",
    ".py",
    ".yaml",
    ".yml",
    ".html",
    ".htm",
    ".css",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".sh",
    ".sql",
    ".log",
    ".toml",
    ".ini",
    ".cfg",
})


def _truncate(text: str, max_chars: int | None) -> str:
    if max_chars is None or len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...[truncated]"


def _read_text_file(path: Path, max_chars: int | None) -> str:
    text = path.read_text(encoding="utf-8")
    return _truncate(text, max_chars)


def _read_pdf_text(path: Path, max_chars: int | None) -> str:
    try:
        import pdfplumber
    except ImportError as e:
        raise RuntimeError(
            "Reading .pdf requires the optional dependency: pip install 'skill-agent[pdf]'"
        ) from e
    parts: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                parts.append(t)
    return _truncate("\n".join(parts), max_chars)


def build_user_message(
    prompt: str,
    files: Sequence[Path | str] | None,
    *,
    max_text_file_chars: int | None,
) -> str | list[str | BinaryContent]:
    """Return a string or a list of text + ``BinaryContent`` for ``run`` / ``run_stream``."""

    if not files:
        return prompt

    text_sections: list[str] = []
    image_parts: list[BinaryContent] = []

    for raw in files:
        path = Path(raw).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Not a file: {path}")
        suffix = path.suffix.lower()

        if suffix == ".pdf":
            body = _read_pdf_text(path, max_text_file_chars)
            text_sections.append(f"### File: {path.name} (extracted text)\n\n{body}")
        elif suffix in _IMAGE_SUFFIXES:
            image_parts.append(BinaryContent.from_path(path))
        elif suffix in _TEXT_SUFFIXES or suffix == "":
            body = _read_text_file(path, max_text_file_chars)
            text_sections.append(f"### File: {path.name}\n\n{body}")
        else:
            try:
                body = _read_text_file(path, max_text_file_chars)
            except UnicodeDecodeError as e:
                raise ValueError(
                    f"Unsupported or non-text file: {path.name}. "
                    "Use a known text extension, an image type, or .pdf."
                ) from e
            text_sections.append(f"### File: {path.name}\n\n{body}")

    text = prompt.strip()
    if text_sections:
        appendix = "\n\n".join(text_sections)
        text = f"{text}\n\n---\n\n{appendix}".strip() if text else appendix

    if image_parts and not text:
        text = "Refer to the attached image(s)."

    if image_parts:
        return [text, *image_parts]
    return text


def resolve_allowed_user_path(path_str: str, roots: tuple[Path, ...]) -> Path:
    """Resolve ``path_str`` to a file path under one of ``roots`` (path-traversal safe)."""

    if not roots:
        raise ValueError("No user file roots configured.")

    raw = Path(path_str.strip())
    candidates: list[Path] = []

    if raw.is_absolute():
        resolved = raw.resolve()
        for root in roots:
            rroot = root.resolve()
            if resolved.is_relative_to(rroot):
                candidates.append(resolved)
    else:
        for root in roots:
            rroot = root.resolve()
            cand = (rroot / raw).resolve()
            if cand.is_relative_to(rroot):
                candidates.append(cand)

    for cand in candidates:
        if cand.is_file():
            return cand

    raise FileNotFoundError(path_str)
