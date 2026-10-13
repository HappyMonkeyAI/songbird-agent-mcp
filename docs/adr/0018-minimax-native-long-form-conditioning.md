# ADR 0018: Prefer MiniMax native long-form generation and structured conditioning

- Status: accepted
- Date: 2026-08-19

## Context

Independent MiniMax Music 3 generations can produce noticeably different vocal identities and arrangements even when they reuse a seed. The model's documented long-range structure is inside one generation request; the current ComfyUI workflow starts each request with a fresh `EmptyMiniMaxMusic3LatentAudio` and exposes no continuation or prior-audio conditioning.

The previous dry-run mapped only a small JSON object containing tags, BPM, and key. That underuses MiniMax Music 3's documented structured-caption interface and does not tell an agent when a requested duration exceeds the native model cap.

## Decision

For MiniMax plans:

1. Requests at or below the 300-second cap are labelled `native_long_form`.
2. Requests above the cap are labelled `segmented_fallback_required`; the plan still reports the existing capped effective duration for compatibility, but the mode makes the limitation explicit.
3. The plan builds a structured caption with `Global Metadata`, `Vocal Details`, and `Arrangement` sections from the musical-direction stage.
4. The ComfyUI MiniMax workflow receives the structured caption as JSON. A legacy minimal caption remains the fallback only for non-MiniMax profiles or plans constructed outside the dry-run adapter.
5. Source research remains provenance and is compacted before it reaches the caption; raw Genius lyrics and LightRAG envelopes are not inserted.

## Consequences

- Native MiniMax renders now use the model's long-range conditioning path by default.
- Agents can distinguish a supported coherent long-form render from a duration request that still needs a real continuation/segmentation implementation.
- Existing duration capping remains backward-compatible, but it is no longer silent.
- Structured captions are persisted in dry-run output and can be locked across future bounded experiments.
- This does not claim that independent segments have shared vocalist identity; beat-aware assembly and continuity QA remain a separate follow-up.
- The FFmpeg fallback assembler now supports bounded chained `acrossfade` transitions through `mode=concat_crossfade`; this addresses hard cuts only and is deliberately not described as identity repair.

## Evidence

- MiniMax Music 3 model card: https://huggingface.co/MiniMaxAI/MiniMax-Music3
- Official ComfyUI guide: https://docs.comfy.org/tutorials/audio/minimax/minimax-music-3
- Local workflow: `/home/stephen/Documents/www/songbird/audio_minimax_music_3.json`
- Research note: `research/notes/2026-08-19-minimax-segment-continuity.md`