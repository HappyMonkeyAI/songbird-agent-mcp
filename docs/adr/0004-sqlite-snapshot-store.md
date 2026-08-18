# ADR 0004: SQLite snapshot store with optimistic revisions

- Status: accepted
- Date: 2026-08-17

## Context

Sessions need to survive MCP process restarts and must not allow an agent or concurrent worker to overwrite a newer creative-stage snapshot silently.

## Decision

Use a small stdlib `sqlite3` snapshot store with one row per session. Persist the JSON-safe `SongSession.to_dict()` payload and its revision. Updates require an expected database revision and execute inside an immediate transaction with a revision-qualified update.

## Consequences

- The persistence seam has no new runtime dependency.
- Stale updates fail with `RevisionConflict` and leave the stored payload unchanged.
- Hydration into typed objects remains a service-layer responsibility; the store is intentionally a snapshot boundary.
- Stage transition and render approval rules must be enforced above this store, not inferred from SQL.