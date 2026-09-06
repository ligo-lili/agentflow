"""ToolRuntime: registers tools and executes them with explicit failures.

Tool exceptions are never silently swallowed: they become a failed
``ToolResult`` that the loop reports as a ``ToolCallFinished`` event.

Tools may optionally declare ``parameters_schema`` (a JSON-schema mapping
describing their arguments). Real providers need this to issue meaningful
tool calls; tools without the attribute keep the empty default schema.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from packages.core.errors import AgentFlowError
from packages.core.provider import ToolSpec
from packages.core.tools import Tool, ToolContext, ToolResult
from packages.runtime.diagnostics import redact_diagnostic


class ToolRuntimeError(AgentFlowError):
    """Raised for tool registration mistakes (duplicate names, bad schemas)."""


def _parameters_schema_of(tool: Tool) -> dict[str, Any]:
    """Duck-typed optional ``parameters_schema``; validated, never trusted."""
    schema: Any = getattr(tool, "parameters_schema", None)
    if schema is None:
        return {}
    if not isinstance(schema, Mapping):
        raise ToolRuntimeError(
            f"tool {tool.name!r} parameters_schema must be a mapping, "
            f"got {type(schema).__name__}"
        )
    return dict(schema)


class ToolRuntime:
    """Name-indexed tool registry and executor."""

    def __init__(self, tools: Sequence[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ToolRuntimeError(f"tool {tool.name!r} is already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(
            ToolSpec(
                name=tool.name,
                description=tool.description,
                parameters_schema=_parameters_schema_of(tool),
            )
            for tool in self._tools.values()
        )

    def execute(self, name: str, arguments: Mapping[str, object], context: ToolContext) -> ToolResult:
        """Run a tool by name; unknown tools and tool exceptions fail explicitly."""
        tool = self._tools.get(name)
        if tool is None:
            known = ", ".join(sorted(self._tools)) or "<none>"
            return ToolResult(
                name=name, ok=False, error=f"unknown tool {name!r}; known tools: {known}"
            )
        try:
            return tool.run(arguments, context)
        except Exception as exc:  # noqa: BLE001 - tool boundary must not crash the loop
            return ToolResult(
                name=name,
                ok=False,
                error=redact_diagnostic(f"{type(exc).__name__}: {exc}"),
            )
