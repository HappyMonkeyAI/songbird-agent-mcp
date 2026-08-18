# songbird-agent-mcp

A governed MCP control plane for AI agents that create music through revisable creative stages.

The intended interaction is: an agent creates a session, develops a brief/persona/musical direction/lyrics or instrumental prompt, calls QA, revises failed stages, and explicitly approves rendering. The server records snapshots and provenance so the final audio is inspectable rather than a black-box side effect.

## Status
Phase 1 control plane with working ComfyUI render integration. The server persists sessions, enforces creative-stage order, records QA, records explicit render approval, and renders approved sessions through configurable ComfyUI adapters (MiniMax Music 3, ACE-Step M2M style transfer, FFmpeg layer mixing), verifying artifact checksum and duration before completing. Optional LLM musical-direction adapters (Ollama, OpenAI-compatible) are opt-in via environment.

## Quick start

```bash
uv sync
uv run pytest -q
uv run fastmcp inspect src/songbird_agent_mcp/server.py:mcp
uv run fastmcp list --command 'uv run songbird-agent-mcp' --json
```

Set `SONGBIRD_AGENT_DB` to a SQLite path for persistence across server restarts. It defaults to `:memory:` for isolated local development.

Run the stdio server:

```bash
uv run songbird-agent-mcp
```

## Planned MCP surface

- `get_capabilities` — discover supported stages, use cases, and approval boundaries.
- `create_song_session` — create a governed draft session.
- `get_song_session` — inspect current stage snapshots and status.
- `get_workflow_plan` — return the recommended stage sequence for a use case.
- `submit_creative_stage` — submit the next typed creative-stage snapshot.
- `record_qa` — record pass/fail QA and required revisions.
- `approve_render` — record explicit approval after QA; does not render yet.
- `generate_musical_direction` — invoke an explicitly registered adapter and persist its typed result.
- `render_song` — invoke an explicitly registered render adapter only after approval and persist its artifact.
- `qa_artifact` — verify checksum and duration integrity; only a full pass reaches `completed`.
- `check_provider_health` — perform a read-only ComfyUI `/system_stats` check when `COMFYUI_URL` is configured.
- `dry_run_render` — map session stages to an ACE-Step or MiniMax Songbird render plan without side effects.
- `verify_artifact` — fetch a configured ComfyUI view handle, compute SHA-256, and probe WAV duration before terminal QA.
- MP3/FLAC duration uses fixed-argument `/usr/bin/ffprobe` over stdin when available; actual duration must match both requested and submitted duration.
- ComfyUI rendering is opt-in when both `COMFYUI_URL` and `SONGBIRD_WORKFLOW_ROOT` are configured; it submits approved prompts and returns `/view` artifact handles without local writes.
- `adapters.py` provides provider-neutral creative, QA, and render protocols plus deterministic mocks for local tests.
- `OllamaMusicDirectionAdapter` is available as an opt-in adapter for the verified Ollama `/api/generate` JSON contract; it is not auto-registered.
- Future: provider-backed `generate_persona`, `generate_lyrics`, `generate_instrumental_prompt`, and artifact validator implementations.

## Documents
- `CONTEXT.md` — operating manual and architecture boundary
- `AGENTS.md` / `HERMES.md` — agent workflow and verification rules
- `TASKS.md` — phased implementation plan
- `docs/adr/` — durable decisions
- `research/` — primary references and local implementation evidence

## License
MIT
