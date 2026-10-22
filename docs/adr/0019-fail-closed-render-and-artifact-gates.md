# ADR 0019: Fail-closed render and artifact gates

- Status: accepted
- Date: 2026-10-22

## Context

A code review found that several gates described in ADRs 0002, 0009, 0010 and 0014 could be bypassed by a model-controlled caller:

- `qa_artifact` accepted self-reported `checksum_verified`/`duration_verified` booleans, so a session could reach `completed` without any bytes being inspected.
- Artifact verifiers treated a missing render-time checksum as verified, and every ComfyUI adapter returned `checksum=None`.
- `render_song` let the caller pick any registered adapter regardless of the approved backend, and only checked state *after* the provider side effect, so a session could be re-rendered after completion.
- M2M and Demucs accepted any local filesystem path in render parameters, allowing arbitrary local files to be read and uploaded to ComfyUI.
- A failed QA left the session with no legal transition.

## Decision

1. **Completion is evidence-only.** `verify_artifact` is the only path to `completed`. `qa_artifact` now records a failing report with required findings and always blocks.
2. **Checksums are captured at render time.** ComfyUI, M2M and mix adapters hash the bytes they produced; verifiers fail closed when no render-time checksum exists. `verify_artifact` defaults to `provider="auto"` (local verifier for `file://`, ComfyUI otherwise); the local verifier is always registered.
3. **The approved backend binds the adapter.** `render_song` derives the adapter from the approved backend profile; an explicit `adapter_name` must match. `ComfyRenderAdapter` only accepts `ace_step` and `minimax_music`.
4. **Render claim before side effects.** `SessionService.begin_render` persists `render_claimed_at` under the optimistic revision lock before the provider is called; concurrent or repeated calls are rejected. A provider failure releases the claim so the render can be retried.
5. **Local source audio is confined.** Local `source_audio_uri` paths must resolve under `SONGBIRD_SOURCE_AUDIO_ROOT`; with no root configured, local sources are rejected. Remote sources must be `/view` handles on the configured ComfyUI instance.
6. **Revision loop.** `revise_creative_stage` appends a new revision of an already-submitted creative stage while the session is `in_progress`, `qa_failed` or `qa_passed`. It rewinds to that stage, clears QA, and requires later stages and QA to be redone. Downstream consumers use the latest revision of each stage.
7. Governance and transport failures are returned as structured JSON errors rather than raised tool exceptions.

## Consequences

- `qa_artifact`'s signature changed (`session_id`, `findings`); clients that self-reported a pass must call `verify_artifact`.
- ComfyUI renders now download the produced artifact once at render time to record its checksum.
- Demucs and local-file M2M deployments must set `SONGBIRD_SOURCE_AUDIO_ROOT`.
- A process crash between claim and artifact recording leaves `render_claimed_at` set; recovery currently requires operator intervention.
- `approve_render` remains model-callable and `approved_by` is self-declared. A real human approval token is still an open decision.
