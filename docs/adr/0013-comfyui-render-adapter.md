# ADR 0013: Opt-in ComfyUI render adapter

- Status: accepted
- Date: 2026-08-17

## Context

The control plane now maps Songbird inputs and enforces approval, but real queue submission must remain isolated from the MCP server and testable without a live ComfyUI instance.

## Decision

Implement `ComfyRenderAdapter` against the sibling client’s verified `POST /prompt`, `GET /history/{prompt_id}`, and `/view` artifact-handle flow. It loads a configured workflow template, applies the typed dry-run plan, submits only an approved matching `RenderRequest`, polls history, and returns a remote artifact URI. It never downloads or writes the artifact locally.

Register it only when both `COMFYUI_URL` and `SONGBIRD_WORKFLOW_ROOT` are configured. Use injectable HTTP functions for deterministic tests.

## Consequences

- Real rendering is opt-in and cannot occur at startup.
- Artifact checksum/duration QA remains mandatory after render.
- Workflow templates are an explicit deployment dependency, not imported from the sibling application.