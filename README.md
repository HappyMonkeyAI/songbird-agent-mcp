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

To verify your environment setup before running the server, use the verification script:

```bash
./verify-setup.sh
```

Run the stdio server:

```bash
uv run songbird-agent-mcp
```

## ComfyUI Integration Prerequisites

ComfyUI rendering is opt-in when both `COMFYUI_URL` and `SONGBIRD_WORKFLOW_ROOT` are configured. To use ComfyUI render adapters (MiniMax Music 3, ACE-Step M2M style transfer, FFmpeg layer mixing), you must:

1. Install and run ComfyUI, accessible via the `COMFYUI_URL` environment variable (e.g., `http://localhost:8188`).
2. Install the required custom nodes for the workflows (MiniMax Music 3 nodes, ACE-Step nodes, etc.).
3. Place the workflow JSON files in the directory specified by `SONGBIRD_WORKFLOW_ROOT`:
   - `audio_ace_step_1_5_checkpoint.json` (for ACE-Step 1.5 renders)
   - `audio_ace_step_m2m_edit.json` (for ACE-Step music-to-music style transfer)
   - `audio_minimax_music_3.json` (for MiniMax Music 3 renders)
4. Download and place the required models in the correct ComfyUI directories:
   - For ACE-Step: `ace_step_1.5_turbo_aio.safetensors` (checkpoint), along with required text encoders and VAE
   - For MiniMax Music 3: `minimax_music3_dit_fp16.safetensors`, `minimax_music3_text_encoder_pruned_int8_convrot.safetensors`, and `minimax_music3_dav.safetensors`

## Demucs Stem Separation Prerequisites

The `demucs_stem` render adapter enables HTDemucs source separation (drums/bass/other/vocals) over a local source audio file. To use this feature:

1. Ensure the `demucs` binary is installed and accessible.
2. Set the `SONGBIRD_DEMUCS_BIN` environment variable to the path of the `demucs` binary.
3. Ensure the `htdemucs` model is available to the demucs installation.
4. Ensure `ffprobe` is installed for duration probing (typically available via the `ffmpeg` package).

## Planned MCP surface

- `get_capabilities` — discover supported stages, use cases, and approval boundaries.
- `create_song_session` — create a governed draft session.
- `get_song_session` — inspect current stage snapshots and status.
- `get_workflow_plan` — return the recommended stage sequence for a use case.
- `submit_creative_stage` — submit the next typed creative-stage snapshot.
- `record_qa` — record pass/fail QA and required revisions.
- `approve_render` — record explicit approval after QA; does not render yet.
- `generate_musical_direction` — invoke an explicitly registered adapter and persist its typed result.
- `render_song` — invoke an explicitly registered render adapter only after approval and persist its artifact. Supports ComfyUI workflows (MiniMax Music 3, ACE-Step M2M, FFmpeg mix) and `demucs_stem` for HTDemucs source separation (drums/bass/other/vocals) when `SONGBIRD_DEMUCS_BIN` is configured.
- FFmpeg fallback assembly supports `parameters.mode=concat_crossfade` with bounded `crossfade_seconds` (0.01–5.0); this smooths boundaries but is not a vocalist-identity guarantee.
- `qa_artifact` — verify checksum and duration integrity; only a full pass reaches `completed`.
- `check_provider_health` — perform a read-only ComfyUI `/system_stats` check when `COMFYUI_URL` is configured.
- `check_supporting_services` — perform read-only contract probes for configured ComfyUI, LightRAG, Perplexica, and Ollama services; reports each dependency independently.
- `search_genius_songs` / `get_genius_song` — use configured Genius OAuth credentials or `GENIUS_ACCESS_TOKEN` for read-only song metadata and lyric-availability lookup; lyric text is not returned.
- `search_lyric_context` — query the configured LightRAG lyric corpus in context-only mode and return references for provenance.
- `dry_run_render` — map session stages to an ACE-Step, MiniMax, or demucs_stem render plan without side effects.
- MiniMax dry-run plans expose `parameters.generation_mode` (`native_long_form` within the 300-second model cap, otherwise `segmented_fallback_required`) and a structured caption with `Global Metadata`, `Vocal Details`, and `Arrangement`; the latter is passed to the official MiniMax workflow as locked conditioning.
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
