"""Offline tests for the operator tools-module loader and spec passthrough."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

import pytest

from packages.core.provider import ToolSpec
from packages.core.tools import ToolContext, ToolResult
from packages.runtime.tool_loader import (
    ENV_TOOLS_MODULE,
    ToolConfigError,
    load_tools,
    load_tools_from_module,
)
from packages.runtime.tools import ToolRuntime, ToolRuntimeError

VALID_MODULE = '''
from packages.core.tools import ToolContext, ToolResult

class EchoTool:
    name = "echo"
    description = "Return the text unchanged."
    parameters_schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    def run(self, arguments, context):
        return ToolResult(name=self.name, ok=True, value=dict(arguments))

TOOLS = [EchoTool()]
'''


def _write_module(tmp_path: Path, source: str, filename: str = "my_tools.py") -> str:
    path = tmp_path / filename
    path.write_text(source, encoding="utf-8")
    return str(path)


class _MinimalTool:
    name = "minimal"
    description = "No schema declared."

    def run(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        return ToolResult(name=self.name, ok=True)


def test_loads_tools_from_file_path_and_validates(tmp_path: Path) -> None:
    module_ref = _write_module(tmp_path, VALID_MODULE)
    tools = load_tools_from_module(module_ref)
    assert [tool.name for tool in tools] == ["echo"]
    runtime = ToolRuntime(tools)
    spec = runtime.specs()[0]
    assert spec.name == "echo"
    assert spec.parameters_schema["type"] == "object"
    assert spec.parameters_schema["required"] == ["text"]


def test_load_tools_env_unset_returns_empty() -> None:
    assert load_tools({}) == ()


def test_load_tools_env_file_path(tmp_path: Path) -> None:
    module_ref = _write_module(tmp_path, VALID_MODULE)
    assert [t.name for t in load_tools({ENV_TOOLS_MODULE: module_ref})] == ["echo"]


def test_missing_file_names_the_path(tmp_path: Path) -> None:
    with pytest.raises(ToolConfigError) as excinfo:
        load_tools({ENV_TOOLS_MODULE: str(tmp_path / "nope.py")})
    assert "nope.py" in str(excinfo.value)


def test_module_without_tools_sequence_rejected(tmp_path: Path) -> None:
    module_ref = _write_module(tmp_path, "X = 1\n")
    with pytest.raises(ToolConfigError) as excinfo:
        load_tools_from_module(module_ref)
    assert "TOOLS" in str(excinfo.value)


def test_tools_of_wrong_type_rejected(tmp_path: Path) -> None:
    module_ref = _write_module(tmp_path, "TOOLS = 'echo'\n")
    with pytest.raises(ToolConfigError):
        load_tools_from_module(module_ref)


def test_duplicate_names_rejected(tmp_path: Path) -> None:
    module_ref = _write_module(
        tmp_path,
        VALID_MODULE
        + '''
class Echo2Tool(EchoTool):
    name = "echo"

TOOLS = [EchoTool(), Echo2Tool()]
''',
    )
    with pytest.raises(ToolConfigError) as excinfo:
        load_tools_from_module(module_ref)
    assert "twice" in str(excinfo.value)


def test_non_mapping_schema_rejected(tmp_path: Path) -> None:
    module_ref = _write_module(
        tmp_path,
        VALID_MODULE.replace(
            '"required": ["text"],', '"required": ["text"], "bogus": [1],'
        ).replace("TOOLS = [EchoTool()]", "EchoTool.parameters_schema = 42\nTOOLS = [EchoTool()]"),
    )
    with pytest.raises(ToolConfigError):
        load_tools_from_module(module_ref)


def test_specs_passthrough_and_validation() -> None:
    runtime = ToolRuntime([_MinimalTool()])

    class _BadSchema:
        name = "bad"
        description = "Schema is not a mapping."
        parameters_schema: ClassVar[list[str]] = ["not", "a", "mapping"]

        def run(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
            return ToolResult(name=self.name, ok=True)

    with pytest.raises(ToolRuntimeError):
        ToolRuntime([_BadSchema()]).specs()

    specs: tuple[ToolSpec, ...] = runtime.specs()
    assert specs[0].parameters_schema == {}  # no attribute → empty default
