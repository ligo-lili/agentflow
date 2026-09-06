# ADR-001: Own a Small Agent Runtime

## Status

Accepted

## Decision

Implement a small framework-neutral Agent Loop with typed Provider and Tool protocols. Do not hard-depend on LangChain, LangGraph or another runtime framework.

## Rationale

The portfolio must demonstrate runtime and context engineering rather than framework configuration. Adapters can be added later without changing core lifecycle events.

