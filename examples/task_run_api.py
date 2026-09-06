"""End-to-end task run through the API against a real provider (manual demo).

Reads AGENTFLOW_* values from `.env`, builds the app with the provider
factory and the example tools module, then POSTs one custom task and inspects
the replay. Requires real credentials (`AGENTFLOW_PROVIDER=openai-compat` +
base URL/key/model); the offline demos and tests never use this file.

    python examples/task_run_api.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from apps.api.main import create_app
from packages.runtime.provider_factory import create_provider
from packages.runtime.tool_loader import load_tools_from_module

env: dict[str, str] = {}
for line in Path(".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()

env["AGENTFLOW_PROVIDER"] = "openai-compat"  # explicit: task mode needs a real provider
provider = create_provider(env)
tools = load_tools_from_module("examples/agentflow_tools.py")
print(f"provider: {type(provider).__name__}; tools: {[tool.name for tool in tools]}")

with tempfile.TemporaryDirectory() as td:
    app = create_app(Path(td) / "task-run.db", provider=provider, tools=tools)
    with TestClient(app) as client:
        response = client.post(
            "/api/runs",
            json={
                "task": (
                    "Use the text_stats tool on the sentence "
                    "'AgentFlow runs real tasks now.' and report the numbers."
                ),
                "tools": ["text_stats"],
            },
        )
        print("HTTP:", response.status_code)
        body = response.json()
        print("scenario:", body.get("scenario"), "| status:", body.get("status"),
              "| steps:", body.get("steps"))
        print("answer:", str(body.get("answer"))[:300])
        session_id = body.get("session_id")
        replay = client.get(f"/api/sessions/{session_id}/replay").json()
        print("replay integrity:", replay.get("integrity", {}).get("valid"))
        for step in replay.get("steps", []):
            for call in step.get("tool_calls", []):
                print("tool:", call["name"], "| ok:", call["ok"], "| value:",
                      str(call["value"])[:140])
