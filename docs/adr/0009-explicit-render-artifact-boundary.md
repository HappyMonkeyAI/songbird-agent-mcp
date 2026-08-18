# ADR 0009: Explicit render and artifact boundary

- Status: accepted
- Date: 2026-08-17

## Context

Render execution is a side effect and must not be reachable from a draft or failed-QA session. A returned audio path or URI also needs to be persisted before artifact QA can inspect it.

## Decision

Expose `render_song(session_id, adapter_name)` only through the adapter registry. The service requires an existing approved `RenderRequest`; the render adapter must receive that exact request and return an `ArtifactRecord`. The artifact is persisted and the session moves to `rendering`; completion is intentionally deferred until artifact QA.

## Consequences

- Repeated renders cannot silently use a different idempotency key or bypass approval.
- The MCP layer does not write files or call providers directly.
- Artifact QA is a required follow-up stage and is not inferred from adapter success.