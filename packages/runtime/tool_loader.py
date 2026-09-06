"""Load operator-declared tools from an explicitly configured Python module.

``AGENTFLOW_TOOLS_MODULE`` names a Python module (dotted path, or a path to a
``.py`` file) that exposes ``TOOLS: Sequence[Tool]``. Loading executes that
module: this is **operator-configured, trusted code by design** — the same
trust boundary as the server process itself (documented in
docs/architecture/deployment.md), never client-supplied.

Validation is strict and eager so misconfiguration fails at startup with a
typed error instead of mid-run: unique non-empty names, callable ``run``,
non-empty string ``description``, and (when present) a mapping
``parameters_schema``.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

from packages.core.errors import AgentFlowError
from packages.core.tools import Tool

ENV_TOOLS_MODULE = "AGENTFLOW_TOOLS_MODULE"


class ToolConfigError(AgentFlowError):
    """Raised when the configured tools module is missing or invalid."""


def load_tools_from_module(module_ref: str) -> tuple[Tool, ...]:
    """Import ``module_ref`` (dotted path or ``.py`` file) and read ``TOOLS``."""
    module = _import_module(module_ref)
    tools_attr: Any = getattr(module, "TOOLS", None)
    if tools_attr is None:
        raise ToolConfigError(
            f"tools module {module_ref!r} does not define a module-level TOOLS sequence"
        )
    if isinstance(tools_attr, (str, bytes)) or not isinstance(tools_attr, Sequence):
        raise ToolConfigError(
            f"TOOLS in {module_ref!r} must be a sequence of Tool instances, "
            f"got {type(tools_attr).__name__}"
        )
    tools = tuple(tools_attr)
    _validate(tools, module_ref)
    return tools


def load_tools(env: Mapping[str, str] | None = None) -> tuple[Tool, ...]:
    """Load tools named by ``AGENTFLOW_TOOLS_MODULE``; empty when unset."""
    environ = os.environ if env is None else env
    module_ref = (environ.get(ENV_TOOLS_MODULE) or "").strip()
    if not module_ref:
        return ()
    return load_tools_from_module(module_ref)


def _import_module(module_ref: str) -> ModuleType:
    if module_ref.endswith(".py") or any(sep in module_ref for sep in ("/", "\\")):
        path = Path(module_ref)
        if not path.is_file():
            raise ToolConfigError(f"tools module file not found: {module_ref!r}")
        spec = importlib.util.spec_from_file_location("agentflow_tools_config", path)
        if spec is None or spec.loader is None:
            raise ToolConfigError(f"cannot load tools module from {module_ref!r}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    try:
        return importlib.import_module(module_ref)
    except ImportError as exc:
        raise ToolConfigError(
            f"cannot import tools module {module_ref!r}: {type(exc).__name__}: {exc}"
        ) from exc


def _validate(tools: tuple[Tool, ...], module_ref: str) -> None:
    seen: set[str] = set()
    for tool in tools:
        name = getattr(tool, "name", None)
        if not isinstance(name, str) or not name.strip():
            raise ToolConfigError(
                f"TOOLS in {module_ref!r} contains an item without a valid name"
            )
        if name in seen:
            raise ToolConfigError(f"TOOLS in {module_ref!r} declares {name!r} twice")
        seen.add(name)
        description = getattr(tool, "description", None)
        if not isinstance(description, str) or not description.strip():
            raise ToolConfigError(f"tool {name!r} needs a non-empty description")
        if not callable(getattr(tool, "run", None)):
            raise ToolConfigError(f"tool {name!r} needs a callable run(arguments, context)")
        schema: Any = getattr(tool, "parameters_schema", None)
        if schema is not None and not isinstance(schema, Mapping):
            raise ToolConfigError(
                f"tool {name!r} parameters_schema must be a mapping, "
                f"got {type(schema).__name__}"
            )
