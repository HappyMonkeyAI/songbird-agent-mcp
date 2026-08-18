# ADR 0003: Provider adapters behind domain contracts

- Status: proposed
- Date: 2026-08-17

## Context
The system may use local LLMs, Perplexica/LightRAG, Songbird, ACE-Step, MiniMax Music 3, and future game-audio workflows. Their schemas and availability differ.

## Decision
Expose stable domain operations such as `generate_musical_direction`, `qa_stage`, and `render_song`; keep provider-specific payloads in adapters. Return provider, model, request ID, timings, and errors as provenance rather than hiding them.

## Consequences
- Pop, synthwave, and instrumental/game use cases can share the orchestration layer.
- Provider failures remain visible and recoverable.
- Each adapter needs mocked contract tests plus opt-in live smoke tests.
