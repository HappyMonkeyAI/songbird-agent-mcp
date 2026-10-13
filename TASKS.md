# TASKS — initial implementation plan

## Phase 0 — contract and governance
- [x] Create separate repository and documentation spine.
- [x] Define stage/session/approval boundaries.
- [x] Add executable MCP discovery/session slice.
- [x] Add deterministic tests and FastMCP inspection commands.

## Phase 1 — domain contracts
- [x] Add typed `SongSession`, `CreativeStage`, `QAReport`, `RenderRequest`, `ArtifactRecord`, and `ProvenanceRecord` models.
- [x] Define JSON-safe contract serialization and store error taxonomy.
- [x] Add durable SQLite session store with optimistic snapshot revision checks.
- [x] Add service-layer stage transitions and approval enforcement.

## Phase 2 — creative adapters
- [x] Add provider-neutral interfaces for creative stages, QA, and render artifacts.
- [x] Add deterministic mock adapters and an explicit adapter registry.
- [x] Add opt-in Ollama musical-direction adapter against the verified `/api/generate` contract.
- [x] Add explicit MCP musical-direction execution tool with structured unconfigured-provider errors.
- [x] Add explicit render execution tool with approval, idempotency, and artifact persistence gates.
- [x] Add artifact QA report, integrity checks, and terminal completed/blocked transitions.
- [ ] Add Songbird/LLM/research/ComfyUI implementations behind the interfaces.
- [ ] Add mocked adapters and revision loop.
- [x] Support instrumental-only use cases without lyric stages.

## Phase 3 — Songbird integration
- [x] Implement a read-only ComfyUI capability/health adapter.
- [x] Add explicit `check_provider_health` MCP tool with no-startup-network behavior.
- [x] Add aggregate read-only supporting-service health tool for ComfyUI, LightRAG, Perplexica, and Ollama.
- [x] Add read-only Genius metadata lookup and LightRAG lyric-context MCP tools.
- [x] Map Songbird profile and workflow inputs into a side-effect-free render plan.
- [x] Add a Songbird workflow adapter with dry-run mode.
- [ ] Add opt-in LLM transport configuration without copying Songbird secrets.

## Phase 4 — render and artifact QA
- [x] Add explicit approval transition for ComfyUI submission.
- [x] Add opt-in ComfyUI prompt/history render adapter with mocked HTTP coverage.
- [ ] Add ACE-Step and MiniMax adapters behind one render contract.
- [x] Add ComfyUI artifact byte/checksum verification and WAV duration probing.
- [x] Add MP3/FLAC duration probing and requested/submitted/actual duration reconciliation.
- [ ] Verify artifact metadata and lyric/prompt QA.
- [ ] Block invalid artifacts and expose actionable revision suggestions.

## Phase 5 — client integration
- [ ] Register local stdio server with Hermes/Dynamic MCP Proxy after smoke gates pass.
- [ ] Add HTTP transport only if a remote client requires it.
- [ ] Add example agent workflows for pop, synthwave, game background, and instrumental loops.
