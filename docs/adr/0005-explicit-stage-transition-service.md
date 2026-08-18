# ADR 0005: Explicit stage-transition service

- Status: accepted
- Date: 2026-08-17

## Context

Persisting snapshots alone does not prevent an agent from skipping creative stages or requesting a render after failed QA. Those rules need a typed application boundary above SQLite.

## Decision

Use `SessionService` to enforce the workflow sequence for song and instrumental sessions. Each accepted stage or QA report increments the session revision and persists it. Render approval is a separate operation that requires matching session identity, the QA stage, a passing report, and no outstanding revisions.

## Consequences

- Invalid stage order and failed QA are rejected before any render adapter can be called.
- The service owns workflow rules; SQLite remains a snapshot/revision store.
- Provider calls and MCP tool presentation remain outside this slice.