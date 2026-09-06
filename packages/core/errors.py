"""Typed error hierarchy so failures are diagnosable, never silent."""

from __future__ import annotations


class AgentFlowError(Exception):
    """Base class for all AgentFlow errors."""


class EventOrderError(AgentFlowError):
    """Raised when an event breaks the per-session monotonic sequence."""


class StoreError(AgentFlowError):
    """Raised when a store fails to persist or load data."""
