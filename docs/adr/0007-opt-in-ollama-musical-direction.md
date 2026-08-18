# ADR 0007: Opt-in Ollama musical-direction adapter

- Status: accepted
- Date: 2026-08-17

## Context

The sibling Songbird project already uses Ollama or an OpenAI-compatible server for musical-direction generation. The MCP project needs one real, low-risk adapter without coupling its server startup to a reachable model endpoint.

## Decision

Implement `OllamaMusicDirectionAdapter` using the source-verified Ollama `POST /api/generate` contract. It sends a non-streaming JSON request and validates `tags`, positive `bpm`, and `keyscale` from the nested JSON response. It is not registered by default and is never called from MCP startup.

## Consequences

- The adapter can be tested with an injected stdlib transport and does not add a dependency.
- Endpoint, model, and timeout are explicit constructor inputs.
- Live Ollama integration remains opt-in until a controlled environment smoke test is available.
- OpenAI-compatible, Songbird workflow, research, ComfyUI, and audio adapters remain separate work.