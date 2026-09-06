# ADR-004: Compare Structured Compaction Strategies

## Status

Accepted

## Decision

Ship two deterministic strategies: keep recent messages plus summary, and semantic structured state extraction. Record before/after budget, removed IDs, preserved state and trigger reason.

## Rationale

The portfolio needs an honest, measurable A/B experiment. A single opaque summary would not show what was preserved or enable meaningful comparison.

