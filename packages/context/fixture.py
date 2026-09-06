"""The canonical fixture: one shared, deterministic conversation.

Both compaction strategies (and the T7 evaluation benchmark) must receive the
exact same input, per docs/architecture/evaluation.md. The fixture includes a
tool call, context growth (two long tool results) and enough messages for at
least one compaction under a small budget.
"""

from __future__ import annotations

import json

from packages.core.provider import ModelMessage

_LONG_FINDING_A = (
    "report lines: revenue=1200 churn=3.2 seats=84 backlog=17 "
    "nps=41 dso=52 esc=3 slo=99.2 deploy=38 mttr=22 "
    "top_accounts=acme,globex,initech"
)
_LONG_FINDING_B = (
    "risk scan: concentration=0.38 support_gap=14 oncall=2 "
    "regression=5 security_patch=2 vendor_lock=medium "
    "hiring_gap=3 roadmap_slip=2w budget_var=-4%"
)


def tool_observation(name: str, value: str, call_id: str) -> ModelMessage:
    return ModelMessage(
        role="tool",
        content=json.dumps({"ok": True, "value": value, "error": None}),
        name=name,
        tool_call_id=call_id,
    )


def canonical_messages() -> tuple[ModelMessage, ...]:
    """The fixed transcript both strategies are compared on."""
    return (
        ModelMessage(role="system", content="You are AgentFlow, a context debugger."),
        ModelMessage(role="user", content="Summarize the Q3 report and list risks."),
        ModelMessage(role="assistant", content="I will pull the report first."),
        tool_observation("fetch_report", _LONG_FINDING_A, "call-1"),
        ModelMessage(role="assistant", content="Revenue is 1200 with churn at 3.2%."),
        ModelMessage(role="user", content="Now scan for delivery risks."),
        tool_observation("scan_risks", _LONG_FINDING_B, "call-2"),
        ModelMessage(role="assistant", content="Top risks: concentration and support gap."),
        tool_observation("fetch_report", _LONG_FINDING_A, "call-3"),
        ModelMessage(role="user", content="Draft the mitigation plan next."),
        ModelMessage(role="assistant", content="Drafting the mitigation plan."),
    )
