# ADR 0015: Fixed ffprobe media duration path

- Status: accepted
- Date: 2026-08-17

## Context

WAV duration can be read with the Python standard library, but MP3 and FLAC duration requires a decoder/probe. Adding a Python media dependency would expand the deployment surface for the control plane.

## Decision

Use the verified host binary `/usr/bin/ffprobe` with fixed arguments and artifact bytes supplied through stdin. No shell is invoked and no temporary artifact file is created. The verifier compares actual probed duration against both approved requested duration and persisted submitted duration. A mismatch fails artifact QA.

The probe remains injectable for deterministic tests and alternate deployments.

MP3 duration falls back to a stdlib Xing/Info header parse when ffprobe cannot report a duration on a non-seekable stdin pipe (ffprobe returns `N/A` for MP3 over stdin even for valid streams). The fallback reads the total frame count from the Xing/Info tag and derives duration from the MPEG-1/2 frame rate without creating a file or adding a Python media dependency.

## Consequences

- MP3/FLAC QA is available where `/usr/bin/ffprobe` exists.
- Missing probe binaries, invalid media, timeouts, and mismatches fail closed.
- The project still has no Python media dependency.
- MP3 artifacts without a usable Xing/Info header and without a probing ffprobe fail closed rather than guessing.