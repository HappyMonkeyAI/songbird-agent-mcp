# ADR 0001: Separate MCP control plane from Songbird

- Status: accepted
- Date: 2026-08-17

## Context
Songbird already contains working generation logic, but exposing its internals directly would couple external agents to a changing application and make approval, revision, provenance, and artifact QA difficult to govern.

## Decision
Create a sibling repository, `songbird-agent-mcp`, with stable agent-facing contracts and replaceable adapters. Songbird is the first backend, not the public API.

## Consequences
- MCP development can evolve without destabilizing Songbird.
- The adapter must translate contracts rather than import private Songbird modules by path.
- End-to-end claims require both adapter tests and a real render smoke.
