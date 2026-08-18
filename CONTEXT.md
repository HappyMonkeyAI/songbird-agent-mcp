# CONTEXT — songbird-agent-mcp

## Purpose
A new sibling project exposing Songbird's music-production capabilities to external AI agents through MCP. An agent should be able to plan a song, inspect and revise creative stages, request QA, and only then approve rendering.

This repository is intentionally separate from `/home/stephen/Documents/www/songbird`. Songbird remains the first execution backend; its internals are not the public MCP contract.

## Source-of-truth hierarchy
Code/tests > this file > accepted ADRs > `TASKS.md` > `research/`. Research records evidence and leads; it does not override the implementation.

## Protocol facts used here
- MCP tools are model-controlled and should retain a human ability to deny invocations.
- MCP resources are application-driven context surfaces.
- MCP prompts are user-controlled reusable workflows.
- The initial transport is stdio for local agent clients; HTTP is a later deployment option.
- FastMCP is the Python implementation layer and must be inspected/listed/called before integration claims.

## Proposed architecture
1. `contracts/` — stable session, stage, QA, provenance, and artifact schemas.
2. `orchestrator/` — state machine for creative stages and approval transitions.
3. `adapters/` — Songbird, LLM, research, ComfyUI/audio backends; all replaceable and tested behind interfaces.
4. `server.py` — thin MCP presentation layer; tools call application services, not provider code.
5. `tests/` — deterministic contract tests first, live adapter/e2e tests opt-in.

## Stage model
`brief -> persona -> musical_direction -> lyrics_or_prompt -> qa -> render -> artifact_qa`

Instrumental game/background music may skip lyrics but must still produce a musical direction, generation prompt, render request, and artifact QA result.

## Approval boundaries
- Creative drafts may be generated and revised in a session.
- Rendering is an explicit transition from `qa_passed` to `render_requested`.
- The server must record who/what approved the transition and the exact input snapshot.
- Remote/cloud calls, local ComfyUI jobs, and writes outside the project artifact root are adapter policy decisions, not hidden side effects.

## Initial executable slice
The current server persists session snapshots, enforces ordered creative stages, records QA, and records explicit render approval. It does not yet call Songbird, ComfyUI, or an LLM. This is deliberate: provider adapters must be tested behind the control-plane boundary before wiring side effects.

An opt-in `OllamaMusicDirectionAdapter` now exists behind the adapter boundary. It is source-verified and transport-tested, but intentionally not registered by the MCP server or invoked automatically.

Render execution is also explicit: `render_song` requires the persisted approval request, enforces the adapter-returned artifact boundary, and leaves the session at `rendering` until artifact QA is implemented.

Artifact QA is now the terminal gate: `qa_artifact` persists checksum/duration findings and transitions to `completed` only when every integrity check passes; otherwise the session is `blocked`.

Provider hardening now includes a read-only `ComfyHealthAdapter` for the source-verified `/system_stats` endpoint. It is registered only from `COMFYUI_URL`; startup remains network-free.

Songbird mapping is available through `dry_run_render`: it normalizes ACE-Step/MiniMax profile aliases, maps stage fields, applies the verified MiniMax 300-second cap, and never submits work or persists a revision.

The ComfyUI render adapter is now opt-in from `COMFYUI_URL` plus `SONGBIRD_WORKFLOW_ROOT`. It submits approved prompts, polls history, and returns a remote `/view` artifact handle; it does not download files or bypass artifact QA.

`verify_artifact` verifies ComfyUI bytes and SHA-256 and probes WAV duration through the configured `/view` endpoint. MP3/FLAC duration uses the fixed ffprobe path documented below and fails closed when unavailable or invalid.

MP3/FLAC duration probing is now supported through fixed-argument `/usr/bin/ffprobe` stdin input, with a stdlib Xing/Info header fallback for MP3 when ffprobe cannot report a duration on a non-seekable pipe. The verifier reconciles actual, approved-requested, and persisted-submitted durations and fails closed on any mismatch or probe failure.

`approve_render` now carries an optional provider-neutral `parameters` map on the render request, and a second render adapter `comfyui_m2m` is registered from the same `COMFYUI_URL` + `SONGBIRD_WORKFLOW_ROOT` pair. It performs an ACE-Step music-to-music second pass: the approved `parameters.source_audio_uri` (a `/view` handle on the same instance) is downloaded, uploaded into the ComfyUI input folder, and the M2M edit workflow (`audio_ace_step_m2m_edit.json`) is submitted with the session's tags/lyrics as the new style caption. Duration and checksum are verified by `verify_artifact` as usual; the M2M pass preserves the source duration.

Title-derived ComfyUI save prefixes are sanitized for Windows-host filesystems (`<>:"/\|?*` replaced, empty results fall back to `songbird`), because ComfyUI runs on Windows and illegal characters abort the save node.

A third render adapter `comfyui_mix` (backend `ffmpeg_mix`) layers two or more `/view` handles with local ffmpeg: `parameters.source_audio_uris` are downloaded from the configured instance, mixed with optional per-track `parameters.gains`, re-encoded to MP3, and uploaded into the ComfyUI output folder (the upload `type` must be a multipart form field, not a query param) so the returned artifact is a normal verified `/view` handle. `verify_artifact` confirms checksum and duration as usual.

## Runtime
- Python >= 3.10
- `uv` for environments and commands
- FastMCP / MCP Python SDK
- stdio first; no required network port
- `SONGBIRD_AGENT_DB` selects the SQLite snapshot path; default `:memory:` is isolated per process.

## Non-goals for the first implementation phase
- Reimplementing Songbird agents inside the MCP server
- Exposing arbitrary shell execution
- Automatically publishing to YouTube/Spotify/game stores
- Unreviewed voice or artist imitation
- Hiding provider failures behind successful tool responses
