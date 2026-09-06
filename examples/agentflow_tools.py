"""Example server-declared tools for AgentFlow task runs (Phase 6).

Point ``AGENTFLOW_TOOLS_MODULE`` at this file (or your own copy) to make these
tools available to ``POST /api/runs`` task runs:

    AGENTFLOW_TOOLS_MODULE=examples/agentflow_tools.py

``parameters_schema`` is a JSON-schema object — real providers need it to
issue meaningful tool calls. ``word_count`` and ``text_stats`` are fully
offline; ``http_get`` performs a network request when the model calls it.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Mapping
from typing import Any, ClassVar

from packages.core.tools import ToolContext, ToolResult


def _text_argument(arguments: Mapping[str, Any]) -> str:
    value = arguments.get("text", "")
    return value if isinstance(value, str) else str(value)


class WordCountTool:
    name = "word_count"
    description = "Count the whitespace-separated words in the provided text."
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The text to count words in."}
        },
        "required": ["text"],
    }

    def run(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        return ToolResult(name=self.name, ok=True, value=len(_text_argument(arguments).split()))


class TextStatsTool:
    name = "text_stats"
    description = "Report characters, words and lines of the provided text."
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The text to measure."}
        },
        "required": ["text"],
    }

    def run(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        text = _text_argument(arguments)
        return ToolResult(
            name=self.name,
            ok=True,
            value={"characters": len(text), "words": len(text.split()), "lines": text.count("\n") + 1 if text else 0},
        )


class HttpGetTool:
    name = "http_get"
    description = (
        "Fetch a URL with GET and return the response body truncated to 2000 "
        "characters. Network tool: only register it where egress is intended."
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Absolute http(s) URL to fetch."}
        },
        "required": ["url"],
    }

    def run(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        url = arguments.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            return ToolResult(name=self.name, ok=False, error="url must be an absolute http(s) URL")
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                body = response.read(4000).decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 - tool boundary reports, never crashes
            return ToolResult(name=self.name, ok=False, error=f"{type(exc).__name__}: {exc}")
        return ToolResult(name=self.name, ok=True, value=json.dumps({"url": url, "body": body[:2000]}))


TOOLS = [WordCountTool(), TextStatsTool(), HttpGetTool()]
