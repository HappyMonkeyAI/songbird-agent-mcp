# ADR 0017: Genius metadata and local lyric-context tools

- Status: accepted
- Date: 2026-08-19

## Context

Song-creation agents benefit from external song discovery and local corpus
grounding. The sibling Songbird application already uses Genius OAuth client
credentials and LightRAG's context-only query contract.

## Decision

Expose read-only `search_genius_songs`, `get_genius_song`, and
`search_lyric_context` MCP tools. Genius responses are reduced to song/artist
metadata, URLs, release information, and lyric availability; lyric text is not
returned by the MCP surface. LightRAG is queried with `only_need_context=true`
and returns context plus references. Both integrations are opt-in and remain
network-free when their environment variables are absent.

## Consequences

- Agents can triangulate external metadata with the local lyric corpus before
  drafting creative stages.
- Genius requires `GENIUS_ACCESS_TOKEN`, or `GENIUS_CLIENTID` and `GENIUS_CLIENTSECRET`.
- LightRAG requires `LIGHTRAG_URL` and optionally `LIGHTRAG_API_KEY`.
- Provider failures return structured tool results rather than being hidden.