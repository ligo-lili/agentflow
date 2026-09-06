"""Identifier and time helpers used across contracts."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime


def new_id() -> str:
    """Return a fresh opaque identifier (UUID4 hex)."""
    return uuid.uuid4().hex


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)
