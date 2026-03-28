"""Console formatting for streaming agent events.

StreamPrinter receives events from the pydantic-ai streaming loop and
renders them to stdout in real time: tool calls, todo progress, and
the final answer token-by-token.

Usage (inside Agent.solve_stream):
    printer = StreamPrinter()
    for event in stream:
        if tool_call:    printer.handle_tool_call(event)
        if tool_result:  printer.handle_tool_result(event, todo_list)
        if text_delta:   printer.handle_text_delta(delta)
    printer.finish()
"""

import sys

from pydantic_ai.messages import FunctionToolCallEvent, FunctionToolResultEvent

from .models import TodoItem, TodoStatus

# Status symbols for the todo table
_STATUS_SYMBOLS: dict[TodoStatus, str] = {
    TodoStatus.pending: " ",
    TodoStatus.in_progress: "~",
    TodoStatus.done: "x",
}


class StreamPrinter:
    """Formats streaming agent events for console output."""

    def __init__(self) -> None:
        self._in_answer = False  # True once we start printing the final answer

    def handle_tool_call(self, event: FunctionToolCallEvent) -> None:
        """Print a line when the agent calls a tool."""
        name = event.part.tool_name

        if name == "use_skill":
            args = event.part.args_as_dict()
            skill_name = args.get("skill_name", "?")
            self._print_event("skill", f"Loading: {skill_name}")

        elif name == "manage_todos":
            args = event.part.args_as_dict()
            action = args.get("action", "?")
            self._print_event("todo", action)

        elif name == "run_script":
            args = event.part.args_as_dict()
            filename = args.get("filename", "?")
            self._print_event("script", f"Running: {filename}")

        elif name == "read_reference":
            args = event.part.args_as_dict()
            filename = args.get("filename", "?")
            self._print_event("ref", f"Reading: {filename}")

        else:
            self._print_event("tool", name)

    def handle_tool_result(
        self, event: FunctionToolResultEvent, todo_list: list[TodoItem]
    ) -> None:
        """After a tool returns, print context-appropriate feedback."""
        name = event.result.tool_name

        if name == "manage_todos":
            self._print_todo_table(todo_list)
        elif name in ("use_skill", "run_script", "read_reference"):
            pass  # Already announced in handle_tool_call
        else:
            self._print_event("tool", f"{name} done")

    def handle_text_delta(self, content: str) -> None:
        """Print answer tokens as they arrive."""
        if not self._in_answer:
            self._in_answer = True
            sys.stdout.write("\n--- Answer ---\n")

        sys.stdout.write(content)
        sys.stdout.flush()

    def finish(self) -> None:
        """Print trailing newline after the answer."""
        if self._in_answer:
            sys.stdout.write("\n")
            sys.stdout.flush()

    def _print_event(self, tag: str, message: str) -> None:
        """Print a bracketed event line, e.g. [skill] Loading: wikipedia_lookup"""
        sys.stdout.write(f"[{tag}] {message}\n")
        sys.stdout.flush()

    def _print_todo_table(self, todos: list[TodoItem]) -> None:
        """Print a compact todo list with status symbols."""
        for item in todos:
            symbol = _STATUS_SYMBOLS.get(item.status, "?")
            sys.stdout.write(f"    [{symbol}] {item.content}\n")
        sys.stdout.flush()
