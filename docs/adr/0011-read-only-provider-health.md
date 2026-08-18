# ADR 0011: Read-only provider health checks

- Status: accepted
- Date: 2026-08-17

## Context

Provider execution should not begin until the control plane can distinguish an unavailable backend from a workflow failure. Health checks must not enqueue jobs, write files, or call a generation endpoint.

## Decision

Implement `ComfyHealthAdapter` against the sibling Songbird-verified `GET /system_stats` endpoint. Return a typed `ProviderHealthReport` for both reachable and transport-failure cases. Register it only when `COMFYUI_URL` is configured, and expose it through the explicit `check_provider_health` MCP tool.

## Consequences

- Startup performs no provider network calls.
- Health failures are data, not hidden exceptions, and include safe status/version/details fields.
- Queue/history probing and generation remain separate opt-in operations.