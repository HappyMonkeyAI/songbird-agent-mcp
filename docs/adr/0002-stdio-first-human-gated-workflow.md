# ADR 0002: Stdio-first, human-gated workflow

- Status: accepted
- Date: 2026-08-17

## Context
MCP tools are model-controlled, while prompts are user-controlled and resources are application-driven. Rendering audio has meaningful compute and filesystem side effects.

## Decision
Start with FastMCP stdio for local clients. Keep creative drafting/revision separate from the explicit render approval transition. Preserve stage snapshots, QA reports, provenance, and approval evidence.

## Consequences
- Local Hermes/Antigravity integration is simple and does not require another port.
- Agents can drive iteration, but cannot silently render after a failed QA stage.
- HTTP transport can be added later without changing domain contracts.
