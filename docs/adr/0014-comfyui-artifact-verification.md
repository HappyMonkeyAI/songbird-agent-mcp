# ADR 0014: Controlled ComfyUI artifact verification

- Status: accepted
- Date: 2026-08-17

## Context

ComfyUI rendering returns a remote `/view` handle. The control plane must verify the bytes behind that handle before completion, but it must not trust a filename or mark unsupported formats valid by inference.

## Decision

Implement `ComfyArtifactQAAdapter` and `verify_artifact`. The adapter restricts reads to the configured ComfyUI `/view` endpoint, downloads bytes through an injectable transport, computes SHA-256, and probes duration for WAV payloads using the standard library. Missing or mismatched checks produce a failed `ArtifactQAReport`, which the existing service records as `blocked`.

MP3/FLAC duration support remains explicitly incomplete until a verified decoder/probe is added.

## Consequences

- Artifact completion is evidence-backed for supported WAV outputs.
- Unsupported formats cannot silently pass duration QA.
- No artifact is written to local disk by the verifier.