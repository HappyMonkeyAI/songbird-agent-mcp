# ADR 0010: Artifact QA terminal gate

- Status: accepted
- Date: 2026-08-17

## Context

An adapter returning an artifact does not prove that the artifact is usable. The control plane needs a final integrity decision before marking a session complete.

## Decision

Add `ArtifactQAReport` with explicit `passed`, `checksum_verified`, `duration_verified`, findings, and timestamp fields. `qa_artifact` is valid only for a persisted rendered artifact. The session becomes `completed` only when all three boolean checks pass; otherwise it becomes `blocked` with the report retained.

## Consequences

- Completion is auditable and cannot be inferred from render adapter success.
- Artifact provenance and QA reports survive process restarts through the existing snapshot store.
- Future artifact validators can populate the same report without changing the MCP state machine.