# Role-loop and stem-assembly research from first live MiniMax session

Date: 2026-09-08
Question: can we generate usable "stem" or "role" loops (drums, bass, synth, chords, vocals, atmos) for assembly into tracks, and would ACE-Step be better than MiniMax Music 3 for this?

## Executive finding

MiniMax Music 3 is a **complete-song generative model**, not an instrument-stem or source-separation model. Prompting it for "drums only", "bass only", or "chords only" does not isolate those elements: independent requests return full musical mixes that include vocals, crowd/ambient sounds, and other content. Only one of four role attempts (a synth lead) happened to come back usable, and even that was close to a full mix.

Independent role-loops are also **not beat-phase aligned**: they share BPM/key metadata in the caption but are generated with fresh latent trajectories, so layering them with `amix` starts every loop at time 0 with no beat grid. This differs from real DAW stems, which lock to a shared musical grid.

ACE-Step is the higher-probability candidate for clean, short role loops because it is **tag-driven and supports negative text** (MiniMax has no negative prompt). See the comparison below.

## What the user reported (empirical, this session)

- Bass, chords, and drums role attempts each contained "people vocalising and other sounds".
- The synth attempt was "basically a whole track with drums etc", not an isolated lead.
- The assembled version (drums + bass + synth + chords via `comfyui_mix`, FFmpeg `amix`) "sounded alright after the intro".
- A clean full-length synth track or longer loop is usable in a sequencer.
- Same for other elements (vocals, atmos). "Sometimes they hand you a set of samples all the full length of a track, and there will be silence in those samples where it is not meant to play, etc."

That last point is the key workflow insight: for seeded-music use, treat each output as a **full-length sample with sparseness** (silence where that role is not active), not an isolated stem. A mixer/sequencer that drops a role during parts of the bar (like a gated pattern) turns a noisy full-mix sample into a usable bed.

## Concrete result

- 4 role loops generated via MiniMax Music 3 (128 BPM, G minor, instrumental, empty lyrics after the lyrics bug fix):
  - drums 21.2s, bass 16.4s, synth 91.9s, chords 18.8s
- Assembled with `comfyui_mix` (`amix duration=longest`, gains drums 1.0 / bass 1.0 / synth 0.8 / chords 0.7) -> 91.9s
- Artifact QA passed (checksum + duration), session `completed`.
- Local file: `output/Derezzed Loop Assembly v1_ce73a57d.mp3`

## Bug fixed during this session (relevant to all future seeded work)

For `instrumental_only` sessions the render adapter was writing the verbal instrumental prompt into the model's `lyrics` field; both MiniMax and ACE-Step vocalize that field, so the model "sang the instructions." Fix: `RenderPlan.instrumental_only` flag; adapter passes empty `lyrics` for instrumental and keeps the description in the caption/tags only. Covered by tests.

## MiniMax Music 3 vs ACE-Step for role loops

MiniMax Music 3 (`audio_minimax_music_3.json`):
- Inputs: `caption` (JSON, Global Metadata / Vocal Details / Arrangement), `lyrics`, `max_duration`, seed, cfg/top_k.
- No negative text.
- Generates complete songs up to ~300s; typically returns a musically complete piece 60-115s even when the cap is larger.
- Tendency: produced full mixes with vocals/other content even for "X only" prompts.

ACE-Step 1.5 (`audio_ace_step_1_5_checkpoint.json`, node 94 `TextEncodeAceStepAudio1.5`):
- Inputs: `tags` (compact comma list), `lyrics`, `bpm`, `timesignature`, `keyscale`, `language`, `duration`/`seconds`, plus explicit generation params and **negative prompt via a separate `CLIPTextEncode` node** (`default_negative_prompt`).
- `supports_negative_text: True` in the profile.
- Tag-driven and typically returns shorter, more literal clips -> better match for loop/stem use.

Verdict: ACE-Step is the higher-probability first choice for isolated role/loop generation, mainly for two reasons: (1) it is tag-literal (a "drums, 128bpm, house, no vocals" tag list is more likely to be honored than MiniMax interpreting prose), and (2) the negative prompt can actively suppress vocals, instruments, and other sounds at generation time, which directly addresses the observed failure mode. Expected tradeoff: shorter, less fully-produced clips, which is acceptable/desirable for loops.

Not expected to completely solve: true source separation. ACE-Step is also a text-to-music generator, not a demixer. If the goal is "take one full track and split into drums/bass/vocals/etc.", a dedicated source-separation adapter (HTDemucs/Demucs or similar) is the correct tool, not either music model.

## Next experiments to run

1. ACE-Step role-loop test: generate "drums only" and "bass only" with a tight tag list and a strong negative prompt (e.g. `vocals, singing, voice, crowd, noise`) and A/B against the MiniMax attempts.
2. Beat-phase alignment before mixing: estimate BPM and bar grid per sample, and either (a) loop-align to a shared grid, or (b) rely on pattern-gating as in the "samples with silence" workflow.
3. Pattern-gated assembly: use FFmpeg sidechain/gate or a sequencer pattern so a full-length sample only plays in its active role windows.
4. Full-length sparse samples: request longer outputs, then gate/route them so silence carries the inactive parts (matching the user's description of sample packs).
5. True stem extraction: add an HTDemucs/Demucs-based adapter to demix a single track into separate stems for resequencing.

## Evidence / references

- Local workflows: `/home/stephen/Documents/www/songbird/audio_minimax_music_3.json`, `/home/stephen/Documents/www/songbird/audio_ace_step_1_5_checkpoint.json`
- Local MCP profile data: `adapters.py` `_PROFILES` (ace_step vs minimax_music)
- Previous note: `research/notes/2026-08-19-minimax-segment-continuity.md`
- Artifacts: `output/Derezzed Loop Assembly v1_ce73a57d.mp3` and the four role-loop files in `output/`
