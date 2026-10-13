# ADR 0016: Supporting-service health probes

- Status: accepted
- Date: 2026-08-19

## Context

The existing provider health tool only checked ComfyUI. Songbird also depends on
LightRAG for lyric-corpus retrieval, Perplexica for local research, and Ollama
for the models behind those services. A live port or successful MCP tool listing
does not prove those downstream paths are ready.

## Decision

Expose `check_supporting_services` as a read-only aggregate MCP tool. It probes
the real/query contracts: ComfyUI `/system_stats`, LightRAG `POST /query`
with `only_need_context`, Perplexica `POST /api/search`, and Ollama `/api/tags`
or OpenAI-compatible `/v1/models` when the configured base URL ends in `/v1`.
Each service is reported independently as `ready`, `unavailable`, or
`not_configured`; startup remains network-free.

## Consequences

- Health checks can identify the first failing supporting service.
- LightRAG and Perplexica probes exercise more of the actual dependency path than
  a TCP or HTTP reachability check, but may consume backend resources.
- Credentials are never returned in the health response.
- OpenAI-compatible probes accept the existing `SONGBIRD_LLM_API_KEY`,
  `LLM_API_KEY`, or legacy `OLLAMA_KEY` environment names without exposing the
  key in results.