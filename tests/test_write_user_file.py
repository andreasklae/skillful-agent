"""Tests for the write_user_file skill tool.

Exercises:
  - Basic UTF-8 write
  - Binary write via base64 encoding
  - Path-escape rejection (../../etc/passwd etc.)
  - Refusal when user_file_roots is not configured
  - create_parents behavior
"""

from __future__ import annotations

import base64
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from skill_agent.user_prompt_files import resolve_allowed_user_path


# ── Minimal RunContext stub ────────────────────────────────────────────
#
# write_user_file is registered as a closure inside register_skill_tools().
# Rather than invoking the full tool registration machinery, we test the
# underlying path-resolution logic directly AND test the registered tool
# by calling register_skill_tools on a minimal stub runner.


@dataclass
class _StubDeps:
    user_file_roots: tuple[Path, ...] = field(default_factory=tuple)
    max_user_file_read_chars: int = 15000
    max_user_file_write_bytes: int = 2 * 1024 * 1024
    tool_log: list = field(default_factory=list)


@dataclass
class _StubCtx:
    deps: _StubDeps


def _make_write_tool(roots: tuple[Path, ...]):
    """Return a (runner, write_fn) pair by running the real registration code."""
    import types
    registered: dict[str, object] = {}

    class _StubRunner:
        """Minimal runner stub that captures @runner.tool registrations."""
        def tool(self, description=""):
            def decorator(fn):
                registered[fn.__name__] = fn
                return fn
            return decorator

    from skill_agent.skill_tools import register_skill_tools
    runner = _StubRunner()
    register_skill_tools(runner, roots)
    return registered.get("write_user_file")


# ── Basic write ────────────────────────────────────────────────────────


def test_write_user_file_basic(tmp_path: Path) -> None:
    write_fn = _make_write_tool(roots=(tmp_path,))
    assert write_fn is not None, "write_user_file not registered when roots provided"

    ctx = _StubCtx(deps=_StubDeps(user_file_roots=(tmp_path,)))
    result = write_fn(ctx, path="notes.txt", content="hello world")
    assert "bytes_written" in result
    assert (tmp_path / "notes.txt").read_text() == "hello world"


def test_write_user_file_creates_parents(tmp_path: Path) -> None:
    write_fn = _make_write_tool(roots=(tmp_path,))
    ctx = _StubCtx(deps=_StubDeps(user_file_roots=(tmp_path,)))

    result = write_fn(ctx, path="subdir/deep/file.md", content="# doc")
    assert "bytes_written" in result
    assert (tmp_path / "subdir" / "deep" / "file.md").exists()


def test_write_user_file_no_create_parents_fails_when_missing(tmp_path: Path) -> None:
    write_fn = _make_write_tool(roots=(tmp_path,))
    ctx = _StubCtx(deps=_StubDeps(user_file_roots=(tmp_path,)))

    result = write_fn(
        ctx, path="missing/subdir/file.txt", content="x", create_parents=False
    )
    assert "does not exist" in result or "Error" in result


# ── Binary write via base64 ────────────────────────────────────────────


def test_write_user_file_base64(tmp_path: Path) -> None:
    write_fn = _make_write_tool(roots=(tmp_path,))
    ctx = _StubCtx(deps=_StubDeps(user_file_roots=(tmp_path,)))

    raw_bytes = b"\x00\x01\x02\x03binary"
    b64 = base64.b64encode(raw_bytes).decode("ascii")
    result = write_fn(ctx, path="data.bin", content=b64, encoding="base64")
    assert "bytes_written" in result
    assert (tmp_path / "data.bin").read_bytes() == raw_bytes


# ── Path-escape rejection ──────────────────────────────────────────────


@pytest.mark.parametrize("escape_path", [
    "../../etc/passwd",
    "../outside.txt",
    "/etc/passwd",
    "/tmp/evil.txt",
])
def test_write_user_file_rejects_path_escape(tmp_path: Path, escape_path: str) -> None:
    write_fn = _make_write_tool(roots=(tmp_path,))
    ctx = _StubCtx(deps=_StubDeps(user_file_roots=(tmp_path,)))

    result = write_fn(ctx, path=escape_path, content="evil")
    # The tool must return an error string, not write anything
    assert any(
        keyword in result
        for keyword in ("not under any allowed root", "Allowed roots", "Error")
    ), f"Expected path rejection, got: {result!r}"
    # Verify nothing was actually written outside tmp_path
    evil_path = Path(escape_path)
    if evil_path.is_absolute():
        # Only check absolute paths that might have been accidentally created
        assert not (evil_path.exists() and evil_path.stat().st_size > 0 and evil_path.read_bytes() == b"evil")


def test_write_user_file_absolute_path_inside_root_allowed(tmp_path: Path) -> None:
    write_fn = _make_write_tool(roots=(tmp_path,))
    ctx = _StubCtx(deps=_StubDeps(user_file_roots=(tmp_path,)))

    abs_path = str(tmp_path / "allowed.txt")
    result = write_fn(ctx, path=abs_path, content="ok")
    assert "bytes_written" in result
    assert (tmp_path / "allowed.txt").read_text() == "ok"


# ── Refusal when no roots configured ──────────────────────────────────


def test_write_user_file_not_registered_without_roots() -> None:
    """write_user_file should not exist in the registry when roots is empty."""
    registered: dict[str, object] = {}

    class _StubRunner:
        def tool(self, description=""):
            def decorator(fn):
                registered[fn.__name__] = fn
                return fn
            return decorator

    from skill_agent.skill_tools import register_skill_tools
    register_skill_tools(_StubRunner(), ())
    assert "write_user_file" not in registered


def test_write_user_file_runtime_no_roots(tmp_path: Path) -> None:
    """If roots somehow gets cleared at runtime, the tool returns an error."""
    write_fn = _make_write_tool(roots=(tmp_path,))
    # Simulate roots cleared at runtime
    ctx = _StubCtx(deps=_StubDeps(user_file_roots=()))
    result = write_fn(ctx, path="file.txt", content="x")
    assert "No user file roots" in result or "not under" in result or "Error" in result


# ── Size limit ─────────────────────────────────────────────────────────


def test_write_user_file_enforces_size_limit(tmp_path: Path) -> None:
    write_fn = _make_write_tool(roots=(tmp_path,))
    ctx = _StubCtx(deps=_StubDeps(
        user_file_roots=(tmp_path,),
        max_user_file_write_bytes=10,  # tiny cap
    ))
    result = write_fn(ctx, path="big.txt", content="x" * 100)
    assert "too large" in result.lower() or "Error" in result
    assert not (tmp_path / "big.txt").exists()
