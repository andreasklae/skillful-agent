"""Static AST extraction of JSON Schemas from skill scripts.

Tiered strategy (first match wins):
  1. Typed entrypoint: def run(...) / def main(...) with annotations
  2. argparse: walk source for ArgumentParser + add_argument calls
  3. (click / typer — future)
  4. Generic fallback: { args: { type: array, items: { type: string } } }

Never imports or executes any script. All analysis is pure AST.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

from .models import ArgSpec, ToolSpec

logger = logging.getLogger(__name__)

# argparse type= values → JSON Schema type strings
_TYPE_MAP: dict[str, str] = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
}

# Python annotation → JSON Schema type strings
_ANNOTATION_MAP: dict[str, str] = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "list": "array",
    "List": "array",
}


def extract_tool_spec(skill_name: str, script_path: Path) -> ToolSpec:
    """Extract a ToolSpec from a script file. Never fails — falls back to tier 4."""
    stem = script_path.stem
    tool_name = f"{skill_name}__{stem}"

    try:
        source = script_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(script_path))
    except Exception as exc:
        logger.debug("schema_extractor parse_failed script=%s err=%s", script_path, exc)
        return _generic_spec(skill_name, script_path.name, tool_name)

    module_doc = ast.get_docstring(tree) or ""

    # Tier 1: typed entrypoint
    spec = _try_typed_entrypoint(skill_name, script_path.name, tool_name, tree, module_doc)
    if spec is not None:
        return spec

    # Tier 2: argparse
    spec = _try_argparse(skill_name, script_path.name, tool_name, tree, module_doc)
    if spec is not None:
        return spec

    # Tier 4: generic fallback
    return _generic_spec(skill_name, script_path.name, tool_name, description=module_doc)


# ── Tier 1: typed entrypoint ──────────────────────────────────────────


def _try_typed_entrypoint(
    skill_name: str,
    filename: str,
    tool_name: str,
    tree: ast.Module,
    module_doc: str,
) -> ToolSpec | None:
    """Build schema from run()/main() signature if annotations are present."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name not in ("run", "main"):
            continue
        args = node.args
        # Require at least one annotated param (excluding self/cls)
        all_args = args.posonlyargs + args.args + args.kwonlyargs
        annotated = [a for a in all_args if a.annotation is not None]
        if not annotated:
            continue

        fn_doc = ast.get_docstring(node) or module_doc
        properties: dict[str, dict] = {}
        required: list[str] = []
        argv_map: list[ArgSpec] = []
        positional_index = 0

        defaults = args.defaults
        # defaults align to the *end* of args.args
        n_args = len(args.args)
        n_defaults = len(defaults)
        default_offset = n_args - n_defaults

        for i, arg in enumerate(args.args):
            name = arg.arg
            if name in ("self", "cls"):
                continue
            ann_str = _annotation_to_str(arg.annotation)
            json_type = _ANNOTATION_MAP.get(ann_str, "string")
            prop: dict = {"type": json_type}
            has_default = (i >= default_offset)

            properties[name] = prop
            if not has_default:
                required.append(name)

            argv_map.append(ArgSpec(
                prop_name=name,
                kind="positional",
                positional_index=positional_index,
            ))
            positional_index += 1

        if not properties:
            continue

        schema: dict = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required

        return ToolSpec(
            skill_name=skill_name,
            script_filename=filename,
            tool_name=tool_name,
            description=fn_doc,
            json_schema=schema,
            argv_map=argv_map,
            tier=1,
        )
    return None


def _annotation_to_str(node: ast.expr | None) -> str:
    if node is None:
        return ""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


# ── Tier 2: argparse ──────────────────────────────────────────────────


def _try_argparse(
    skill_name: str,
    filename: str,
    tool_name: str,
    tree: ast.Module,
    module_doc: str,
) -> ToolSpec | None:
    """Walk AST for ArgumentParser + add_argument calls."""
    # Find the local variable name bound to ArgumentParser()
    parser_names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Call):
            continue
        call = node.value
        func_name = _call_func_name(call)
        if func_name not in ("ArgumentParser", "argparse.ArgumentParser"):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                parser_names.add(target.id)

    if not parser_names:
        return None

    # Collect add_argument calls on those parser names
    properties: dict[str, dict] = {}
    required_props: list[str] = []
    argv_map: list[ArgSpec] = []
    positional_index = 0
    partial = False  # set True if we hit a non-literal call

    for node in ast.walk(tree):
        if not isinstance(node, ast.Expr):
            continue
        if not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if not isinstance(call.func, ast.Attribute):
            continue
        if call.func.attr != "add_argument":
            continue
        if not isinstance(call.func.value, ast.Name):
            continue
        if call.func.value.id not in parser_names:
            continue

        result = _parse_add_argument(call, positional_index)
        if result is None:
            partial = True
            continue

        prop_name, prop_schema, arg_spec, is_positional, is_required = result

        properties[prop_name] = prop_schema
        if is_required:
            required_props.append(prop_name)
        argv_map.append(arg_spec)
        if is_positional:
            positional_index += 1

    if not properties:
        if partial:
            logger.debug(
                "schema_extractor argparse_partial script=%s; falling back to tier 4", filename
            )
        return None

    if partial:
        logger.debug(
            "schema_extractor argparse_partial script=%s; some args unextracted", filename
        )

    schema: dict = {"type": "object", "properties": properties}
    if required_props:
        schema["required"] = required_props

    return ToolSpec(
        skill_name=skill_name,
        script_filename=filename,
        tool_name=tool_name,
        description=module_doc,
        json_schema=schema,
        argv_map=argv_map,
        tier=2,
    )


def _parse_add_argument(
    call: ast.Call,
    positional_index: int,
) -> tuple[str, dict, ArgSpec, bool, bool] | None:
    """Parse one add_argument call.

    Returns (prop_name, prop_schema, arg_spec, is_positional, is_required) or None
    if the call uses non-literal arguments that we can't statically analyse.
    """
    # Extract positional string args (the option strings / dest)
    option_strings: list[str] = []
    for arg in call.args:
        if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str):
            return None  # non-literal — can't safely analyse
        option_strings.append(arg.value)

    if not option_strings:
        return None

    # Keyword args
    kwargs: dict[str, ast.expr] = {}
    for kw in call.keywords:
        if kw.arg is None:
            return None  # **kwargs unpacking — can't safely analyse
        kwargs[kw.arg] = kw.value

    # Determine if this is a flag or positional
    is_flag = option_strings[0].startswith("-")
    is_positional = not is_flag

    # Determine property name
    if "dest" in kwargs and isinstance(kwargs["dest"], ast.Constant):
        prop_name = str(kwargs["dest"].value)
    elif is_flag:
        # Use the longest option string, strip leading dashes, replace - with _
        longest = max(option_strings, key=len)
        prop_name = longest.lstrip("-").replace("-", "_")
    else:
        prop_name = option_strings[0]

    prop_schema: dict = {}

    # action
    action = _literal_str(kwargs.get("action"))
    store_true = action == "store_true"
    if store_true:
        prop_schema["type"] = "boolean"

    # type=
    if not store_true and "type" in kwargs:
        type_node = kwargs["type"]
        type_name = _name_str(type_node)
        if type_name:
            prop_schema["type"] = _TYPE_MAP.get(type_name, "string")
        else:
            prop_schema["type"] = "string"
    elif not store_true and "type" not in prop_schema:
        prop_schema["type"] = "string"

    # nargs
    nargs = None
    if "nargs" in kwargs:
        nargs_node = kwargs["nargs"]
        if isinstance(nargs_node, ast.Constant):
            nargs = nargs_node.value
        elif isinstance(nargs_node, ast.Name):
            nargs = nargs_node.id
    if nargs in ("+", "*") or isinstance(nargs, int):
        prop_schema["type"] = "array"
        prop_schema["items"] = {"type": prop_schema.get("type", "string")}
        if "type" in prop_schema and prop_schema["type"] != "array":
            del prop_schema["type"]
        prop_schema["type"] = "array"

    # choices
    if "choices" in kwargs:
        choices_node = kwargs["choices"]
        if isinstance(choices_node, (ast.List, ast.Tuple)):
            vals = [
                elt.value
                for elt in choices_node.elts
                if isinstance(elt, ast.Constant)
            ]
            if vals:
                prop_schema["enum"] = vals

    # help
    help_text = _literal_str(kwargs.get("help"))
    if help_text:
        prop_schema["description"] = help_text

    # required
    is_required = False
    if is_positional:
        is_required = True  # positionals are required by default
    elif "required" in kwargs:
        req_node = kwargs["required"]
        if isinstance(req_node, ast.Constant):
            is_required = bool(req_node.value)
    # If a default is provided, it's optional
    if "default" in kwargs:
        is_required = False

    arg_spec = ArgSpec(
        prop_name=prop_name,
        kind="flag" if is_flag else "positional",
        option_string=option_strings[0] if is_flag else "",
        positional_index=positional_index if is_positional else 0,
        store_true=store_true,
        is_array=prop_schema.get("type") == "array",
    )

    return prop_name, prop_schema, arg_spec, is_positional, is_required


# ── Tier 4: generic fallback ──────────────────────────────────────────


def _generic_spec(
    skill_name: str,
    filename: str,
    tool_name: str,
    description: str = "",
) -> ToolSpec:
    schema: dict = {
        "type": "object",
        "properties": {
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Command-line arguments passed directly to the script.",
            }
        },
    }
    return ToolSpec(
        skill_name=skill_name,
        script_filename=filename,
        tool_name=tool_name,
        description=description,
        json_schema=schema,
        argv_map=[
            ArgSpec(prop_name="args", kind="positional", positional_index=0, is_array=True)
        ],
        tier=4,
    )


# ── Helpers ───────────────────────────────────────────────────────────


def _call_func_name(call: ast.Call) -> str:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        if isinstance(call.func.value, ast.Name):
            return f"{call.func.value.id}.{call.func.attr}"
    return ""


def _literal_str(node: ast.expr | None) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _name_str(node: ast.expr | None) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return node.id
    return None


# ── argv reconstruction ───────────────────────────────────────────────


def build_argv(tool_spec: ToolSpec, named_args: dict) -> list[str]:
    """Reconstruct sys.argv (excluding script name) from named_args using the ToolSpec.

    Handles flags, positionals, store_true booleans, and arrays.
    Tier-4 specs pass the 'args' list through directly.
    """
    if tool_spec.tier == 4:
        raw = named_args.get("args", [])
        if isinstance(raw, list):
            return [str(a) for a in raw]
        return [str(raw)]

    positionals: list[tuple[int, str]] = []  # (index, value)
    flags: list[str] = []

    for spec in tool_spec.argv_map:
        value = named_args.get(spec.prop_name)
        if value is None:
            continue

        if spec.kind == "positional":
            if spec.is_array and isinstance(value, list):
                for v in value:
                    positionals.append((spec.positional_index, str(v)))
            else:
                positionals.append((spec.positional_index, str(value)))
        else:  # flag
            if spec.store_true:
                if value:
                    flags.append(spec.option_string)
            elif spec.is_array and isinstance(value, list):
                for v in value:
                    flags.extend([spec.option_string, str(v)])
            else:
                flags.extend([spec.option_string, str(value)])

    positionals.sort(key=lambda t: t[0])
    return flags + [v for _, v in positionals]
