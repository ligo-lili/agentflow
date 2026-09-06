"""Text-level redaction for free-form diagnostics.

``packages.core.redaction`` redacts keyed payload fields; exception messages
embed secrets inside plain strings (``api_key=sk-...``), which need a
pattern-based scrubber. Diagnostics stay useful (error type, context) while
secret values are replaced.
"""

from __future__ import annotations

import re

#: ``key=value`` or ``key: value`` where the key names a secret.
_SECRET_KV_RE = re.compile(
    r"(?i)\b(api[_-]?key|authorization|bearer|token|secret|password)\b\s*[:=]\s*\S+"
)

#: Common secret token shapes (``sk-...`` style API keys).
_SK_TOKEN_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{6,}\b")

_REDACTED = "[REDACTED]"


def redact_diagnostic(text: str) -> str:
    """Return ``text`` with secret-looking values replaced by ``[REDACTED]``."""
    scrubbed = _SECRET_KV_RE.sub(lambda m: f"{m.group(1)}={_REDACTED}", text)
    return _SK_TOKEN_RE.sub(_REDACTED, scrubbed)
