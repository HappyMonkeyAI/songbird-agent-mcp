# ADR 0012: Songbird dry-run mapping

- Status: accepted
- Date: 2026-08-17

## Context

The MCP control plane needs to prove that its typed stages map to the existing Songbird audio profiles before enabling real workflow submission. Importing the sibling application would couple its runtime, secrets, and side effects to the control plane.

## Decision

Implement `SongbirdDryRunAdapter` with locally declared, source-verified profile metadata for `ace_step` and `minimax_music`. It maps musical-direction and lyrics/instrumental-prompt stages into a typed `RenderPlan`, applies the MiniMax 300-second cap, and reports explicit zero-side-effect guarantees.

## Consequences

- `dry_run_render` can validate profile selection and input mapping without approval, provider calls, queue submission, file writes, or artifact creation.
- Real workflow submission remains a separate adapter and approval-gated operation.
- Profile metadata must be deliberately updated if the sibling Songbird configuration changes.