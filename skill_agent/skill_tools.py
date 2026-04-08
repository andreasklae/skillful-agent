"""Built-in skill tools: use_skill, register_skill, scaffold_skill, manage_todos,
read_reference, run_script, write_skill_file, read_user_file, call_client_function.

Extracted from agent.py to keep each file focused on one concern.
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field
from pydantic_ai import RunContext

from .models import (
    ClientFunctionRequest,
    Skill,
    TodoItem,
    TodoStatus,
    ToolCallRecord,
)
from .user_prompt_files import resolve_allowed_user_path


# ── Helpers ──────────────────────────────────────────────────────────


def _resolve_skill_dir(skill: Skill) -> Path:
    """Return the directory containing the skill's SKILL.md file."""
    if skill.path is None:
        raise ValueError(f"Skill '{skill.name}' has no path — cannot access resources.")
    return skill.path.parent


def _preview(text: str, limit: int = 300) -> str:
    """Truncate text to `limit` chars for log previews."""
    return text if len(text) <= limit else text[:limit] + "..."


def _normalize_task_line(entry: Any) -> str | None:
    """Turn one model-supplied task entry into a single-line string for TodoItem.content."""
    if entry is None:
        return None
    if isinstance(entry, str):
        s = entry.strip()
        return s or None
    if isinstance(entry, dict):
        for key in ("content", "text", "task", "title", "item", "description"):
            v = entry.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return None
    s = str(entry).strip()
    return s or None


def _coerce_todo_id(raw: Any) -> int | None:
    """Models often send id as a string; TodoItem.id is int."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.strip().lstrip("-").isdigit():
        return int(raw.strip())
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _parse_todo_status(raw: Any) -> TodoStatus | None:
    """Accept common synonyms (e.g. completed → done) for manage_todos updates."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, TodoStatus):
        return raw
    s = str(raw).lower().strip().replace(" ", "_").replace("-", "_")
    aliases: dict[str, TodoStatus] = {
        "complete": TodoStatus.done,
        "completed": TodoStatus.done,
        "finished": TodoStatus.done,
        "resolved": TodoStatus.done,
        "done": TodoStatus.done,
        "in_progress": TodoStatus.in_progress,
        "inprogress": TodoStatus.in_progress,
        "progress": TodoStatus.in_progress,
        "working": TodoStatus.in_progress,
        "active": TodoStatus.in_progress,
        "started": TodoStatus.in_progress,
        "pending": TodoStatus.pending,
        "todo": TodoStatus.pending,
        "open": TodoStatus.pending,
        "not_started": TodoStatus.pending,
        "notstarted": TodoStatus.pending,
    }
    if s in aliases:
        return aliases[s]
    try:
        return TodoStatus(s)
    except ValueError:
        return None


# ── Tool registration ────────────────────────────────────────────────


def register_skill_tools(runner: Any, user_file_roots: tuple[Path, ...]) -> None:
    """Register all skill-related tools on the pydantic-ai runner."""

    ActivityDesc = Annotated[
        str,
        Field(
            description=(
                "Very short plain-language phrase for the user interface describing what you are doing "
                "with this tool call."
            ),
        ),
    ]

    # ── use_skill ─────────────────────────────────────────────────────

    @runner.tool(description=(
        "Load a skill's full instructions by name. "
        "Call this BEFORE performing any domain-specific actions. "
        "Pass only the registered skill_name; do not pass search queries here—use the skill's tools after loading."
    ))
    def use_skill(
        ctx: RunContext,
        skill_name: str,
        activity: ActivityDesc = "",
        query: Annotated[
            str,
            Field(
                default="",
                description="Ignored. Some models wrongly send a search string here; only skill_name is used.",
            ),
        ] = "",
    ) -> str:
        skill = ctx.deps.skills.get(skill_name)
        if not skill:
            available = ", ".join(ctx.deps.skills)
            return f"Skill '{skill_name}' not found. Available: {available}"

        ctx.deps.activated_skills.append(skill_name)

        parts = [f"## Skill: {skill_name}\n\n{skill.body}"]

        resources: list[str] = []
        if skill.scripts:
            resources.append(f"  Scripts: {', '.join(skill.scripts)}")
        if skill.references:
            resources.append(f"  References: {', '.join(skill.references)}")
        if skill.assets:
            resources.append(f"  Assets: {', '.join(skill.assets)}")

        if resources:
            parts.append("\n\n## Bundled Resources\n" + "\n".join(resources))
            parts.append(
                "\nUse `read_reference` to load docs and `run_script` to execute scripts."
            )

        if skill.client_functions:
            cf_lines: list[str] = []
            for cf in skill.client_functions:
                param_names = ", ".join(p.name for p in cf.parameters)
                tag = " [awaits user]" if cf.awaits_user else ""
                cf_lines.append(f"  {cf.name}({param_names}){tag} — {cf.description}")
            parts.append("\n\n## Client Functions\n" + "\n".join(cf_lines))
            parts.append(
                "\nUse `call_client_function` to request these. "
                "They execute on the client, not in the agent."
            )

        parts.append("\n\nFollow these instructions.")
        return "".join(parts)

    # ── register_skill ───────────────────────────────────────────────

    @runner.tool(description=(
        "Register a newly created skill so it becomes usable in this session. "
        "Call this after creating a new skill directory with a SKILL.md file. "
        "Provide the absolute path to the skill directory (the folder containing SKILL.md). "
        "After registering, you can use use_skill, run_script, and read_reference with it."
    ))
    def register_skill(
        ctx: RunContext,
        skill_dir_path: str,
        activity: ActivityDesc = "",
    ) -> str:
        from .registry import _parse_skill

        skill_dir = Path(skill_dir_path).resolve()
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            return f"No SKILL.md found at {skill_dir}. Create the skill first."

        parsed = _parse_skill(skill_dir)
        if parsed is None:
            return f"Could not parse SKILL.md at {skill_dir}. Check that it has valid --- frontmatter."

        ctx.deps.skills[parsed.name] = parsed

        resources: list[str] = []
        if parsed.scripts:
            resources.append(f"Scripts: {', '.join(parsed.scripts)}")
        if parsed.references:
            resources.append(f"References: {', '.join(parsed.references)}")
        if parsed.assets:
            resources.append(f"Assets: {', '.join(parsed.assets)}")

        res_info = ("\n  ".join(resources)) if resources else "None"
        return (
            f"Registered skill '{parsed.name}' from {skill_dir}.\n"
            f"  Resources: {res_info}\n"
            f"You can now use use_skill('{parsed.name}'), run_script('{parsed.name}', ...), "
            f"and read_reference('{parsed.name}', ...)."
        )

    # ── scaffold_skill ───────────────────────────────────────────────

    @runner.tool(description=(
        "Create a new skill directory with the standard skeleton (SKILL.md, docs/, scripts/, tests/) "
        "and immediately register it for use in this session. "
        "Provide skill_name in kebab-case. "
        "Creates the skill in the configured skills directory. "
        "Returns the absolute path to the new skill directory."
    ))
    def scaffold_skill(
        ctx: RunContext,
        skill_name: str,
        activity: ActivityDesc = "",
    ) -> str:
        from .registry import _parse_skill

        name = skill_name.strip().lower().replace(" ", "-")
        if not name:
            return "Error: skill_name is required."

        if not ctx.deps.user_skills_dirs:
            return "Error: no skills directory configured."
        parent = ctx.deps.user_skills_dirs[0]

        skill_dir = parent / name

        if skill_dir.exists():
            return f"Directory already exists: {skill_dir}. Use register_skill to register it."

        try:
            for subdir in ("docs", "scripts", "tests"):
                (skill_dir / subdir).mkdir(parents=True, exist_ok=True)

            skill_md = skill_dir / "SKILL.md"
            skill_md.write_text(
                f"---\nname: {name}\ndescription: TODO — describe what this skill does and when to use it.\n---\n\n"
                f"# {name}\n\nTODO — write skill instructions here.\n",
                encoding="utf-8",
            )

            (skill_dir / "docs" / "index.md").write_text(
                f"# {name} — docs index\n\n| File | Description |\n|------|-------------|\n",
                encoding="utf-8",
            )
            (skill_dir / "tests" / "test_results.md").write_text(
                f"# {name} — test results\n\nNo tests run yet.\n",
                encoding="utf-8",
            )
        except Exception as e:
            return f"Error creating skill directory: {e}"

        parsed = _parse_skill(skill_dir)
        if parsed:
            ctx.deps.skills[parsed.name] = parsed

        ctx.deps.tool_log.append(ToolCallRecord(
            tool="scaffold_skill",
            input={"skill_name": name},
            output_preview=str(skill_dir),
        ))

        return (
            f"Created and registered skill '{name}' at:\n  {skill_dir}\n\n"
            f"Next: write the SKILL.md body and scripts using write_skill_file(skill_name='{name}', path=..., content=...).\n"
            f"Use run_script(skill_name='{name}', filename=...) to execute scripts once added."
        )

    # ── manage_todos ──────────────────────────────────────────────────

    @runner.tool(description=(
        "Manage your internal task list. "
        'Actions: "set" (replace list), "add" (append), "update" (change status), "remove" (delete). '
        "Each call returns JSON with every item's numeric `id`—use those ids with action update. "
        "While working: set the current item to in_progress before other tools, then done when that step is finished. "
        "For action set, pass task strings in `items` (top-level array) or in `payload.items` or `payload.tasks`. "
        "For add, pass `content` top-level or in payload. "
        "For update/remove, pass numeric `id` in payload (string or int). "
        'Status for update: "pending", "in_progress", or "done" (also: completed, finished).'
    ))
    def manage_todos(
        ctx: RunContext,
        action: str,
        payload: dict[str, Any] | None = None,
        items: list[Any] | None = None,
        content: str | None = None,
        item_id: int | str | None = None,
        status: str | None = None,
        activity: ActivityDesc = "",
    ) -> str:
        todos = ctx.deps.todo_list
        pl = dict(payload or {})
        if content is not None and str(content).strip():
            pl.setdefault("content", str(content).strip())
        if item_id is not None:
            pl.setdefault("id", item_id)
        if status is not None and str(status).strip():
            pl.setdefault("status", str(status).strip())

        def _item_list_for_set() -> list[Any]:
            if items is not None:
                return list(items)
            if "items" in pl:
                return list(pl["items"])
            if "tasks" in pl:
                return list(pl["tasks"])
            return []

        if action == "set":
            todos.clear()
            ctx.deps._next_todo_id = 1
            for entry in _item_list_for_set():
                text = _normalize_task_line(entry)
                if text:
                    todos.append(TodoItem(id=ctx.deps._next_todo_id, content=text))
                    ctx.deps._next_todo_id += 1

        elif action == "add":
            raw = pl.get("content") or pl.get("text") or pl.get("task")
            if raw is None and items is not None and len(items) == 1:
                raw = _normalize_task_line(items[0])
            line = _normalize_task_line(raw) if raw is not None else None
            if line:
                todos.append(TodoItem(id=ctx.deps._next_todo_id, content=line))
                ctx.deps._next_todo_id += 1

        elif action == "update":
            tid = _coerce_todo_id(pl.get("id"))
            new_status = _parse_todo_status(pl.get("status"))
            if tid is not None and new_status is not None:
                for item in todos:
                    if item.id == tid:
                        item.status = new_status
                        break

        elif action == "remove":
            tid = _coerce_todo_id(pl.get("id"))
            if tid is not None:
                ctx.deps.todo_list = [t for t in todos if t.id != tid]

        else:
            return f"Unknown action '{action}'. Use: set, add, update, remove."

        return json.dumps([t.model_dump() for t in ctx.deps.todo_list], indent=2)

    # ── read_reference ────────────────────────────────────────────────

    @runner.tool(description=(
        "Read a reference document bundled with a skill. "
        "Provide the skill_name and filename (e.g. 'api_guide.md'). "
        "Only works for files listed in the skill's references."
    ))
    def read_reference(
        ctx: RunContext,
        skill_name: str,
        filename: str,
        activity: ActivityDesc = "",
    ) -> str:
        skill = ctx.deps.skills.get(skill_name)
        if not skill:
            available = ", ".join(ctx.deps.skills) or "(none)"
            return (
                f"Skill '{skill_name}' not found. Available: {available}. "
                f"If you just created this skill with scaffold_skill.py, call "
                f"register_skill(skill_dir_path=<absolute path to the skill directory>) first."
            )
        if filename not in skill.references:
            return f"'{filename}' not in {skill_name}. Available: {skill.references}"

        ref_path = _resolve_skill_dir(skill) / "references" / filename
        if not ref_path.exists():
            return f"File not found on disk: {ref_path}"

        content = ref_path.read_text(encoding="utf-8")

        ctx.deps.tool_log.append(ToolCallRecord(
            tool="read_reference",
            input={"skill_name": skill_name, "filename": filename},
            truncated=len(content) > 15000,
        ))

        return content[:15000]

    # ── run_script ────────────────────────────────────────────────────

    @runner.tool(description=(
        "Run a Python script bundled with a skill. "
        "Provide skill_name, filename, and an optional JSON-encoded args string. "
        "Returns JSON with keys: ok, stdout, stderr, exit_code."
    ))
    def run_script(
        ctx: RunContext,
        skill_name: str,
        filename: str,
        args: str = "",
        activity: ActivityDesc = "",
    ) -> str:
        skill = ctx.deps.skills.get(skill_name)
        if not skill:
            available = ", ".join(ctx.deps.skills) or "(none)"
            return json.dumps({"ok": False, "stdout": "", "stderr": (
                f"Skill '{skill_name}' not found. Available: {available}. "
                f"If you just created this skill with scaffold_skill.py, you must call "
                f"register_skill(skill_dir_path=<absolute path to skill directory>) before "
                f"run_script or read_reference will work for it."
            ), "exit_code": 2})
        if filename not in skill.scripts:
            return json.dumps({"ok": False, "stdout": "", "stderr": (
                f"Script '{filename}' not found in skill '{skill_name}'. "
                f"Available scripts: {skill.scripts}. "
                f"If you just added this file, call register_skill again to refresh."
            ), "exit_code": 2})

        script_path = _resolve_skill_dir(skill) / "scripts" / filename
        if not script_path.exists():
            return json.dumps({"ok": False, "stdout": "", "stderr": f"File not found on disk: {script_path}", "exit_code": 2})

        cmd = [sys.executable, str(script_path)]
        if args:
            cmd.append(args)

        stdout, stderr, exit_code, ok, truncated = "", "", 1, False, False

        try:
            proc = subprocess.run(
                cmd,
                input=args or None,
                capture_output=True,
                text=True,
                timeout=90,
                cwd=None,
            )
            stdout, stderr = proc.stdout or "", proc.stderr or ""
            exit_code = proc.returncode
            ok = proc.returncode == 0
        except subprocess.TimeoutExpired:
            stderr, exit_code = "Script timed out after 90 seconds.", 124
        except Exception as e:
            stderr, exit_code = f"Error running script: {e}", 1

        max_chars = 7000
        if len(stdout) > max_chars:
            stdout, truncated = stdout[:max_chars] + "...[truncated]", True
        if len(stderr) > max_chars:
            stderr, truncated = stderr[:max_chars] + "...[truncated]", True

        response = json.dumps({"ok": ok, "stdout": stdout, "stderr": stderr, "exit_code": exit_code})

        ctx.deps.tool_log.append(ToolCallRecord(
            tool="run_script",
            input={"skill_name": skill_name, "filename": filename, "args": args},
            output_preview=_preview(response, limit=500),
            truncated=truncated,
        ))

        return response[:15000]

    # ── write_skill_file ─────────────────────────────────────────────

    @runner.tool(description=(
        "Write content to a file inside a skill directory. "
        "Preferred form: provide skill_name (registered name) and path (relative path within the skill, "
        "e.g. 'SKILL.md', 'scripts/get_weather.py', 'docs/index.md'). "
        "The tool resolves the absolute path automatically from the registered skill. "
        "Fallback: provide file_path as an absolute path if skill_name is unknown. "
        "Set append=True to append instead of overwrite. "
        "Creates parent directories as needed."
    ))
    def write_skill_file(
        ctx: RunContext,
        content: str,
        skill_name: str = "",
        path: str = "",
        file_path: str = "",
        append: bool = False,
        activity: ActivityDesc = "",
    ) -> str:
        if skill_name.strip() and path.strip():
            skill = ctx.deps.skills.get(skill_name.strip())
            if not skill:
                return (
                    f"Skill '{skill_name}' not found in registry. "
                    f"Call scaffold_skill first, or register_skill if the directory already exists."
                )
            try:
                base = _resolve_skill_dir(skill)
            except ValueError as e:
                return str(e)
            target = (base / path.strip()).resolve()
        elif file_path.strip():
            target = Path(file_path.strip()).resolve()
        else:
            return "Provide either (skill_name + path) or file_path."

        _WRITE_BLOCKLIST = {"permissions.yaml"}
        if target.name in _WRITE_BLOCKLIST and target.exists():
            return (
                f"'{target.name}' is client-controlled configuration and cannot be "
                f"overwritten by the agent. Ask the user to edit it directly."
            )

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if append:
                with open(target, "a", encoding="utf-8") as f:
                    f.write(content)
                action = "Appended to"
            else:
                target.write_text(content, encoding="utf-8")
                action = "Wrote"
        except Exception as e:
            return f"Error writing {target}: {e}"

        if target.name == "SKILL.md" and skill_name.strip():
            from .registry import _parse_skill
            refreshed = _parse_skill(target.parent)
            if refreshed:
                ctx.deps.skills[refreshed.name] = refreshed

        ctx.deps.tool_log.append(ToolCallRecord(
            tool="write_skill_file",
            input={"skill_name": skill_name, "path": path or file_path, "append": append},
            output_preview=f"{action} {len(content)} chars to {target.name}",
        ))

        return f"{action}: {target} ({len(content):,} chars)"

    # ── read_user_file (conditional) ─────────────────────────────────

    if user_file_roots:

        @runner.tool(description=(
            "Read a text file from the user workspace. "
            "Use a path relative to a configured root, or an absolute path inside those roots. "
            "Returns UTF-8 text (truncated if very long)."
        ))
        def read_user_file(
            ctx: RunContext,
            path: str,
            activity: ActivityDesc = "",
        ) -> str:
            try:
                file_path = resolve_allowed_user_path(path, ctx.deps.user_file_roots)
            except FileNotFoundError:
                return f"File not found or not under allowed roots: {path}"
            except ValueError as e:
                return str(e)
            try:
                content = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                return f"File is not valid UTF-8 text: {path}"
            limit = ctx.deps.max_user_file_read_chars
            truncated = len(content) > limit
            out = content[:limit] if truncated else content
            ctx.deps.tool_log.append(ToolCallRecord(
                tool="read_user_file",
                input={"path": path},
                truncated=truncated,
            ))
            return out

    # ── call_client_function ──────────────────────────────────────────

    @runner.tool(description=(
        "Request execution of a client-side function declared by a skill. "
        "The function runs on the client, not in the agent. "
        "Provide skill_name, function_name, and args matching the declared parameters. "
        "If the function has awaits_user=true, you MUST stop and wait for the user's next message "
        "before continuing."
    ))
    def call_client_function(
        ctx: RunContext,
        skill_name: str,
        function_name: str,
        args: dict[str, Any] | None = None,
        activity: ActivityDesc = "",
    ) -> str:
        skill = ctx.deps.skills.get(skill_name)
        if not skill:
            available = ", ".join(ctx.deps.skills) or "(none)"
            return f"Skill '{skill_name}' not found. Available: {available}"

        func = None
        for cf in skill.client_functions:
            if cf.name == function_name:
                func = cf
                break
        if func is None:
            available_fns = [cf.name for cf in skill.client_functions]
            return (
                f"Client function '{function_name}' not declared by skill '{skill_name}'. "
                f"Available: {available_fns}"
            )

        provided_args = args or {}
        for param in func.parameters:
            if param.required and param.name not in provided_args:
                return (
                    f"Missing required parameter '{param.name}' for "
                    f"client function '{function_name}'."
                )

        request = ClientFunctionRequest(
            name=function_name,
            args=provided_args,
            skill_name=skill_name,
            awaits_user=func.awaits_user,
        )
        ctx.deps.pending_client_requests.append(request)

        ctx.deps.tool_log.append(ToolCallRecord(
            tool="call_client_function",
            input={"skill_name": skill_name, "function_name": function_name, "args": provided_args},
            output_preview=f"Requested {function_name} (awaits_user={func.awaits_user})",
        ))

        if func.awaits_user:
            return (
                f"Client function '{function_name}' has been requested. "
                "This function requires user input. STOP here and wait for "
                "the user's next message before continuing."
            )
        return (
            f"Client function '{function_name}' has been requested. "
            "The client will handle it. Continue with your task."
        )
