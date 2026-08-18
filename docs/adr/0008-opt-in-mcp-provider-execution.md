# ADR 0008: Opt-in MCP provider execution

- Status: accepted
- Date: 2026-08-17

## Context

The MCP server needs a controlled path from a persisted session to a provider adapter, but startup must not create hidden network side effects and missing configuration must not look like successful generation.

## Decision

Expose `generate_musical_direction(session_id, adapter_name)` as the first provider execution tool. It resolves only explicitly registered adapters, persists the returned typed stage through `SessionService`, and returns structured `adapter_not_configured` or `adapter_failed` errors. The Ollama adapter is registered only when both `SONGBIRD_OLLAMA_MODEL` and `OLLAMA_BASE_URL` are present; registration itself performs no network call.

## Consequences

- MCP clients can distinguish configuration, provider, and session errors.
- Every successful provider result passes the same ordered-stage and revision gates as manual submission.
- Live generation remains an explicit tool invocation and never occurs during server import.