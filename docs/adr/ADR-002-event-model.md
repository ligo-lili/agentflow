# ADR-002: Events Are the Integration Boundary

## Status

Accepted

## Decision

Runtime lifecycle transitions emit versioned, ordered, JSON-serializable events. Persistence, timeline, replay, API and evaluation consume the event log.

## Rationale

This keeps observability out of the Agent Loop's internal state and makes replay deterministic and inspectable.

