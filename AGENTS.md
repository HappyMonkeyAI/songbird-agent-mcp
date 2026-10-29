# AGENTS.md — songbird-agent-mcp

## Prime directive
Build a safe, inspectable MCP control plane for agent-driven music creation. Deliver verified artifacts, not orchestration promises.

## Source of truth
1. Working code and tests
2. `CONTEXT.md`
3. Accepted ADRs in `docs/adr/`
4. `TASKS.md` for active work
5. `research/` as reference-only evidence

## Before changing code
- Read `CONTEXT.md` and the relevant ADRs.
- Inspect the current tree and Git status.
- Keep Songbird integration behind explicit adapter interfaces; do not import Songbird internals by filesystem path.
- Never copy credentials, live `.env` files, generated audio, or private research into this repository.

## Tool safety
- Planning and inspection tools may be model-controlled.
- Rendering, file writes outside the project workspace, remote calls, and destructive operations require an explicit approval boundary in the session state.
- A QA failure must block rendering; do not return a successful-looking audio artifact with invalid lyrics or missing provenance.
- Use structured JSON-safe results with session IDs, stage status, provenance, and artifact paths/handles.

## Verification
```bash
uv sync
uv run pytest -q
uv run fastmcp inspect src/songbird_agent_mcp/server.py:mcp
uv run fastmcp list --command 'uv run songbird-agent-mcp' --json
uv run python -m compileall -q src tests
```

Do not claim Songbird/ComfyUI integration until an adapter test and a real end-to-end smoke have both passed.

## Session start and end (Spine)
If the `spine` MCP is available, call `session_brief(project="songbird-agent-mcp")` (or the absolute path of this repo) first and use `get_section` / `recall` instead of reading PROGRESS.md or CONTEXT.md whole. Without the MCP, run `spine brief` from the repo root. If neither works, ground normally.
Before you stop, call `checkpoint` with your agent name (e.g. `cursor`), what was done, what's next and any blockers. Save durable why/gotcha/decision facts with `record_memory`.
