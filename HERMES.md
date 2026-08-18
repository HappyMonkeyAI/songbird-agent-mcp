# HERMES.md — songbird-agent-mcp

## Before coding
1. Read `CONTEXT.md`, `TASKS.md`, and relevant ADRs.
2. Run `uv sync` and `uv run pytest -q`.
3. Validate MCP with `uv run fastmcp inspect src/songbird_agent_mcp/server.py:mcp` and list stdio tools with `uv run fastmcp list --command 'uv run songbird-agent-mcp' --json`.

## Development loop
- Keep the MCP server thin and typed.
- Add or update a contract test before adding a new stage/tool.
- Preserve stage snapshots and provenance at every mutation.
- Keep stdout clean for stdio transport; log diagnostics to stderr.
- Do not configure Hermes or the launcher registry until the local tool surface is stable.

## Integration order
1. Contract/session service
2. Mocked adapters and QA policy
3. Read-only Songbird inspection adapter
4. Draft generation adapter
5. Explicit render adapter
6. Artifact QA and optional publication adapters
