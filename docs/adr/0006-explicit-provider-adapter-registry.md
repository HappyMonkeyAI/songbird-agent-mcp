# ADR 0006: Explicit provider adapter registry

- Status: accepted
- Date: 2026-08-17

## Context

The MCP control plane must not silently fall back from a governed workflow into an external provider call. Provider failures and side effects need a typed boundary that can be tested without network access.

## Decision

Define `CreativeAdapter`, `QAAdapter`, and `RenderAdapter` protocols. Require adapters to be explicitly registered by name in `AdapterRegistry`; missing names raise `AdapterNotConfigured`. Ship deterministic static adapters for contract and smoke tests only. The MCP server does not register live providers in this slice.

## Consequences

- Adapter contracts can be tested independently of Songbird, LLM, ComfyUI, or network availability.
- Render mocks require `render_approved` and the exact approved idempotency key.
- Live adapters must be added and registered deliberately, with their own focused and opt-in integration tests.