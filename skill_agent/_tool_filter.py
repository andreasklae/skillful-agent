"""Tool-registration filter for AgentConfig.disabled_tools.

The pydantic-ai runner's ``@runner.tool(...)`` decorator registers each
decorated function as a callable tool. To honour ``AgentConfig.disabled_tools``,
each registrar wraps the runner with a small proxy that intercepts
``runner.tool(...)`` and silently skips registration for any function
whose ``__name__`` is in the disabled set. The function is still defined
locally (so closures over captured variables keep working), it just isn't
exposed to the model.
"""

from __future__ import annotations

from typing import Any


def filtered_runner(runner: Any, disabled: frozenset[str]) -> Any:
    """Return a proxy around ``runner`` whose ``.tool`` decorator skips
    functions named in ``disabled``. Pass-through otherwise."""
    def tool_decorator(*args: Any, **kwargs: Any):
        original = runner.tool(*args, **kwargs)

        def wrapper(fn):
            if fn.__name__ in disabled:
                return fn  # not registered with the runner
            return original(fn)

        return wrapper

    proxy = type("_RunnerProxy", (), {})()
    proxy.tool = tool_decorator
    return proxy
