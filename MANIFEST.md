# Project manifest

## Product
Agent-facing MCP control plane for governed, revisable music generation.

## Initial components
- FastMCP stdio server
- In-memory planning/session discovery slice
- Documentation and ADR spine
- Deterministic tests

## Planned components
- Durable session store
- Creative-stage adapters
- Songbird execution adapter
- QA/revision loop
- ComfyUI/MiniMax/ACE render adapter
- Artifact and provenance registry

## Explicit non-claims
No Songbird, LLM, ComfyUI, remote API, or publication integration is wired in the initial scaffold.
