"""Initial MCP contract slice for songbird-agent-mcp."""

from __future__ import annotations

from datetime import datetime, timezone
from functools import wraps
import os
from typing import Any, Callable, TypeVar
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP

from songbird_agent_mcp.adapters import (
    AdapterError,
    AdapterNotConfigured,
    AdapterRegistry,
    ComfyArtifactQAAdapter,
    ComfyHealthAdapter,
    ComfyM2MRenderAdapter,
    ComfyMixRenderAdapter,
    ComfyRenderAdapter,
    GeniusAPIAdapter,
    JsonHTTPHealthAdapter,
    LightRAGQueryAdapter,
    LocalArtifactQAAdapter,
    LocalDemucsStemAdapter,
    OllamaMusicDirectionAdapter,
    OpenAIMusicDirectionAdapter,
    SongbirdDryRunAdapter,
)
from songbird_agent_mcp.contracts import ArtifactQAReport, CreativeStage, QAReport, RenderRequest
from songbird_agent_mcp.service import SessionService, StageTransitionError
from songbird_agent_mcp.session_store import SessionStore, SessionStoreError

mcp = FastMCP(
    "songbird-agent-mcp",
    instructions=(
        "Use this server to plan governed music-production sessions. "
        "Creative stages, QA, and render approval are enforced in order; rendering runs only through "
        "explicitly configured provider adapters, and a session completes only after verify_artifact passes. "
        "Call get_capabilities to see which providers are configured."
    ),
)

_store = SessionStore(os.environ.get("SONGBIRD_AGENT_DB", ":memory:"))
_service = SessionService(_store)
_adapters = AdapterRegistry()
_health_adapters: dict[str, ComfyHealthAdapter] = {}
_supporting_health_adapters: dict[str, JsonHTTPHealthAdapter] = {}
_genius_adapter: GeniusAPIAdapter | None = None
_lightrag_query_adapter: LightRAGQueryAdapter | None = None
_artifact_qa_adapters: dict[str, Any] = {
    "local": LocalArtifactQAAdapter(os.environ.get("SONGBIRD_FFPROBE", "/usr/bin/ffprobe")),
}
_dry_run_adapter = SongbirdDryRunAdapter()
_source_audio_root = os.environ.get("SONGBIRD_SOURCE_AUDIO_ROOT")
if os.environ.get("SONGBIRD_OLLAMA_MODEL") and os.environ.get("OLLAMA_BASE_URL"):
    _adapters.register_creative(
        "ollama",
        OllamaMusicDirectionAdapter(
            model=os.environ["SONGBIRD_OLLAMA_MODEL"],
            base_url=os.environ["OLLAMA_BASE_URL"],
        ),
    )
if os.environ.get("SONGBIRD_LLM_MODEL") and os.environ.get("SONGBIRD_LLM_BASE_URL"):
    _adapters.register_creative(
        "openai",
        OpenAIMusicDirectionAdapter(
            model=os.environ["SONGBIRD_LLM_MODEL"],
            base_url=os.environ["SONGBIRD_LLM_BASE_URL"],
            api_key=os.environ.get("SONGBIRD_LLM_API_KEY"),
        ),
    )
if os.environ.get("COMFYUI_URL"):
    _health_adapters["comfyui"] = ComfyHealthAdapter(os.environ["COMFYUI_URL"])
    _artifact_qa_adapters["comfyui"] = ComfyArtifactQAAdapter(os.environ["COMFYUI_URL"])
if os.environ.get("LIGHTRAG_URL"):
    _lightrag_query_adapter = LightRAGQueryAdapter(
        os.environ["LIGHTRAG_URL"], api_key=os.environ.get("LIGHTRAG_API_KEY")
    )
    _supporting_health_adapters["lightrag"] = JsonHTTPHealthAdapter(
        "lightrag", os.environ["LIGHTRAG_URL"], "/query", method="POST",
        payload={"query": "__songbird_healthcheck__", "mode": "local", "only_need_context": True, "response_type": "string"},
        headers={"X-API-Key": os.environ["LIGHTRAG_API_KEY"]} if os.environ.get("LIGHTRAG_API_KEY") else {},
    )
if os.environ.get("GENIUS_ACCESS_TOKEN") or (os.environ.get("GENIUS_CLIENTID") and os.environ.get("GENIUS_CLIENTSECRET")):
    _genius_adapter = GeniusAPIAdapter(
        os.environ.get("GENIUS_CLIENTID", ""),
        os.environ.get("GENIUS_CLIENTSECRET", ""),
        access_token=os.environ.get("GENIUS_ACCESS_TOKEN"),
    )
if os.environ.get("PERPLEXICA_URL"):
    _supporting_health_adapters["perplexica"] = JsonHTTPHealthAdapter(
        "perplexica", os.environ["PERPLEXICA_URL"], "/api/search", method="POST",
        payload={
            "chatModel": {"provider": "ollama", "model": os.environ.get("PERPLEXICA_CHAT_MODEL", "")},
            "embeddingModel": {"provider": "ollama", "model": os.environ.get("PERPLEXICA_EMBEDDING_MODEL", "nomic-embed-text:latest")},
            "optimizationMode": os.environ.get("PERPLEXICA_OPTIMIZATION_MODE", "speed"),
            "focusMode": "webSearch", "query": "__songbird_healthcheck__", "history": [],
        },
    )
if os.environ.get("OLLAMA_BASE_URL"):
    _ollama_health_url = os.environ["OLLAMA_BASE_URL"].rstrip("/")
    if _ollama_health_url.endswith("/v1"):
        # Only reuse the general LLM key when it is configured for this same host;
        # never send a credential issued for one service to a different one.
        _ollama_api_key = os.environ.get("OLLAMA_KEY", "")
        _llm_base_url = os.environ.get("SONGBIRD_LLM_BASE_URL", "")
        if not _ollama_api_key and _llm_base_url and urlparse(_llm_base_url).netloc == urlparse(_ollama_health_url).netloc:
            _ollama_api_key = os.environ.get("SONGBIRD_LLM_API_KEY", "")
        _supporting_health_adapters["ollama"] = JsonHTTPHealthAdapter(
            "ollama", _ollama_health_url, "/models",
            headers={"Authorization": f"Bearer {_ollama_api_key}"} if _ollama_api_key else {},
        )
    else:
        _supporting_health_adapters["ollama"] = JsonHTTPHealthAdapter("ollama", _ollama_health_url, "/api/tags")
if os.environ.get("COMFYUI_URL") and os.environ.get("SONGBIRD_WORKFLOW_ROOT"):
    _render_timeout = float(os.environ.get("SONGBIRD_RENDER_TIMEOUT", "120"))
    _adapters.register_render(
        "comfyui",
        ComfyRenderAdapter(
            os.environ["COMFYUI_URL"],
            os.environ["SONGBIRD_WORKFLOW_ROOT"],
            output_dir=os.environ.get("SONGBIRD_OUTPUT_DIR"),
            timeout=_render_timeout,
        ),
    )
    _adapters.register_render(
        "comfyui_m2m",
        ComfyM2MRenderAdapter(
            os.environ["COMFYUI_URL"],
            os.environ["SONGBIRD_WORKFLOW_ROOT"],
            source_root=_source_audio_root,
            timeout=_render_timeout,
        ),
    )
    _adapters.register_render(
        "comfyui_mix",
        ComfyMixRenderAdapter(
            os.environ["COMFYUI_URL"],
            os.environ["SONGBIRD_WORKFLOW_ROOT"],
            timeout=max(_render_timeout, 300.0),
        ),
    )
if os.environ.get("SONGBIRD_DEMUCS_BIN"):
    _demucs_timeout = float(os.environ.get("SONGBIRD_DEMUCS_TIMEOUT", "1800.0"))
    _adapters.register_render(
        "demucs_stem",
        LocalDemucsStemAdapter(
            demucs_bin=os.environ["SONGBIRD_DEMUCS_BIN"],
            ffprobe=os.environ.get("SONGBIRD_FFPROBE", "/usr/bin/ffprobe"),
            export_dir=os.environ.get("SONGBIRD_DEMUCS_EXPORT_DIR"),
            source_root=_source_audio_root,
            timeout=_demucs_timeout,
        ),
    )

# Approved backend profile -> the only render adapter allowed to execute it.
_PROFILE_ADAPTERS = {
    "ace_step": "comfyui",
    "minimax_music": "comfyui",
    "ace_step_m2m": "comfyui_m2m",
    "ffmpeg_mix": "comfyui_mix",
    "demucs_stem": "demucs_stem",
}

_Tool = TypeVar("_Tool", bound=Callable[..., dict[str, Any]])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _structured_errors(fn: _Tool) -> _Tool:
    """Return governance and persistence failures as JSON-safe results instead of raising."""

    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return fn(*args, **kwargs)
        except StageTransitionError as exc:
            return {"error": "stage_transition_rejected", "error_type": type(exc).__name__, "message": str(exc)}
        except SessionStoreError as exc:
            return {"error": "session_store_error", "error_type": type(exc).__name__, "message": str(exc)}
        except ValueError as exc:
            return {"error": "invalid_input", "message": str(exc)}

    return wrapper  # type: ignore[return-value]


def _approved_adapter_name(backend: str) -> str | None:
    profile = SongbirdDryRunAdapter._ALIASES.get(backend.strip().lower().replace(" ", "_"), backend.strip().lower())
    return _PROFILE_ADAPTERS.get(profile)


@mcp.tool()
def get_capabilities() -> dict[str, Any]:
    """Return supported workflow stages, use cases, approval boundaries, and configured providers."""
    registered = _adapters.registered()
    return {
        "status": "governed",
        "execution_backend": "connected" if registered["render"] else "not_connected",
        "use_cases": ["song", "instrumental", "game_background", "loop"],
        "stages": [
            "brief",
            "persona",
            "musical_direction",
            "lyrics_or_prompt",
            "qa",
            "render",
            "artifact_qa",
        ],
        "approval_required_for": ["render", "external_publication", "writes_outside_artifact_root"],
        "registered_adapters": registered,
        "artifact_verifiers": sorted(_artifact_qa_adapters),
    }


@mcp.tool()
def get_workflow_plan(
    use_case: str = "song",
    genre: str = "POP",
    instrumental_only: bool = False,
) -> dict[str, Any]:
    """Return the recommended stage sequence for a music use case."""
    stages = ["brief", "musical_direction"]
    if use_case != "instrumental" and not instrumental_only:
        stages[1:1] = ["persona"]
        stages.append("lyrics_or_prompt")
    else:
        stages.append("instrumental_prompt")
    stages.extend(["qa", "render", "artifact_qa"])
    return {
        "use_case": use_case,
        "genre": genre,
        "instrumental_only": instrumental_only,
        "stages": stages,
        "render_policy": "explicit approval after qa_passed",
        "status": "planning_only",
    }


@mcp.tool()
@_structured_errors
def create_song_session(
    use_case: str = "song",
    genre: str = "POP",
    title: str | None = None,
    instrumental_only: bool = False,
    generation_seed: int | None = None,
) -> dict[str, Any]:
    """Create a persisted governed draft session; no provider or render call is made."""
    return _service.create_session(
        use_case=use_case,
        genre=genre,
        title=title,
        instrumental_only=instrumental_only,
        generation_seed=generation_seed,
    ).to_dict()


@mcp.tool()
@_structured_errors
def get_song_session(session_id: str) -> dict[str, Any]:
    """Return a session snapshot or a structured not-found error."""
    session = _service.load_session(session_id)
    if session is None:
        return {"error": "session_not_found", "session_id": session_id}
    return session.to_dict()


@mcp.tool()
@_structured_errors
def submit_creative_stage(
    session_id: str,
    stage_name: str,
    content: dict[str, Any],
    status: str = "draft",
) -> dict[str, Any]:
    """Submit the next creative stage; ordering and revision persistence are enforced."""
    session = _service.load_session(session_id)
    if session is None:
        return {"error": "session_not_found", "session_id": session_id}
    stage = CreativeStage(
        name=stage_name,
        status=status,
        revision=session.revision + 1,
        content=content,
        updated_at=_now(),
    )
    return _service.apply_stage(session, stage).to_dict()


@mcp.tool()
@_structured_errors
def revise_creative_stage(
    session_id: str,
    stage_name: str,
    content: dict[str, Any],
    status: str = "revised",
) -> dict[str, Any]:
    """Revise an already-submitted creative stage before render approval.

    The session rewinds to the revised stage: later creative stages must be
    resubmitted and QA recorded again. This is the recovery path after failed QA.
    """
    session = _service.load_session(session_id)
    if session is None:
        return {"error": "session_not_found", "session_id": session_id}
    stage = CreativeStage(
        name=stage_name,
        status=status,
        revision=session.revision + 1,
        content=content,
        updated_at=_now(),
    )
    return _service.revise_stage(session, stage).to_dict()


@mcp.tool()
@_structured_errors
def generate_musical_direction(
    session_id: str,
    adapter_name: str = "ollama",
) -> dict[str, Any]:
    """Generate and persist musical direction through an explicitly registered adapter."""
    session = _service.load_session(session_id)
    if session is None:
        return {"error": "session_not_found", "session_id": session_id}
    try:
        adapter = _adapters.creative(adapter_name)
        stage = adapter.generate(session)
        return _service.apply_stage(session, stage).to_dict()
    except AdapterNotConfigured as exc:
        return {"error": "adapter_not_configured", "adapter": adapter_name, "message": str(exc)}
    except StageTransitionError:
        raise
    except (AdapterError, ValueError, OSError) as exc:
        return {"error": "adapter_failed", "adapter": adapter_name, "message": str(exc)}


@mcp.tool()
@_structured_errors
def record_qa(
    session_id: str,
    passed: bool,
    score: float | None = None,
    findings: list[str] | None = None,
    required_revisions: list[str] | None = None,
) -> dict[str, Any]:
    """Record QA for the current creative draft; failed QA blocks render approval."""
    session = _service.load_session(session_id)
    if session is None:
        return {"error": "session_not_found", "session_id": session_id}
    report = QAReport(
        passed=passed,
        score=score,
        findings=findings or [],
        required_revisions=required_revisions or [],
        created_at=_now(),
    )
    return _service.record_qa(session, report).to_dict()


@mcp.tool()
@_structured_errors
def approve_render(
    session_id: str,
    backend: str,
    duration_seconds: float,
    idempotency_key: str,
    approved_by: str,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record explicit render approval; no audio is submitted until render_song is called."""
    session = _service.load_session(session_id)
    if session is None:
        return {"error": "session_not_found", "session_id": session_id}
    request = RenderRequest(
        session_id=session_id,
        backend=backend,
        duration_seconds=duration_seconds,
        idempotency_key=idempotency_key,
        approved_at=_now(),
        approved_by=approved_by,
        parameters=parameters or {},
    )
    return _service.approve_render(session, request).to_dict()


@mcp.tool()
@_structured_errors
def render_song(
    session_id: str,
    adapter_name: str | None = None,
) -> dict[str, Any]:
    """Render an explicitly approved session through the adapter bound to its approved backend.

    The adapter is derived from the approved backend; an explicit adapter_name must match it.
    The session is claimed before any provider call, so a session renders at most once.
    """
    session = _service.load_session(session_id)
    if session is None:
        return {"error": "session_not_found", "session_id": session_id}
    if session.render_request is None:
        return {"error": "render_not_approved", "session_id": session_id}
    approved_adapter = _approved_adapter_name(session.render_request.backend)
    if adapter_name is None:
        adapter_name = approved_adapter
    if adapter_name is None:
        return {
            "error": "adapter_not_resolved",
            "session_id": session_id,
            "backend": session.render_request.backend,
            "message": "approved backend has no bound render adapter; pass adapter_name",
        }
    if approved_adapter is not None and adapter_name != approved_adapter:
        return {
            "error": "adapter_backend_mismatch",
            "session_id": session_id,
            "adapter": adapter_name,
            "approved_backend": session.render_request.backend,
            "expected_adapter": approved_adapter,
        }
    try:
        adapter = _adapters.render(adapter_name)
    except AdapterNotConfigured as exc:
        return {"error": "adapter_not_configured", "adapter": adapter_name, "message": str(exc)}
    session = _service.begin_render(session)
    try:
        artifact = adapter.render(session, session.render_request)
        return _service.record_artifact(session, artifact).to_dict()
    except (AdapterError, ValueError, OSError) as exc:
        _service.release_render(session)
        return {"error": "render_failed", "adapter": adapter_name, "message": str(exc)}


@mcp.tool()
@_structured_errors
def qa_artifact(
    session_id: str,
    findings: list[str],
) -> dict[str, Any]:
    """Record a failing artifact QA (e.g. a listening defect) and block the session.

    Completion cannot be self-reported: only verify_artifact, which inspects the
    artifact bytes, can mark a session completed.
    """
    session = _service.load_session(session_id)
    if session is None:
        return {"error": "session_not_found", "session_id": session_id}
    if not findings:
        return {"error": "findings_required", "message": "describe why the artifact fails QA"}
    report = ArtifactQAReport(
        passed=False,
        checksum_verified=False,
        duration_verified=False,
        findings=findings,
        created_at=_now(),
    )
    return _service.record_artifact_qa(session, report).to_dict()


@mcp.tool()
def check_provider_health(provider: str = "comfyui") -> dict[str, Any]:
    """Run a read-only health check against an explicitly configured provider."""
    adapter = _health_adapters.get(provider)
    if adapter is None:
        return {"error": "provider_not_configured", "provider": provider}
    return adapter.check().to_dict()


@mcp.tool()
def check_supporting_services() -> dict[str, Any]:
    """Run read-only contract probes for configured Songbird dependencies."""
    configured = {"comfyui": _health_adapters.get("comfyui"), **_supporting_health_adapters}
    services: list[dict[str, Any]] = []
    for name in ("comfyui", "lightrag", "perplexica", "ollama"):
        adapter = configured.get(name)
        if adapter is None:
            services.append({"provider": name, "configured": False, "status": "not_configured"})
            continue
        report = adapter.check().to_dict()
        report["configured"] = True
        report["status"] = "ready" if report["reachable"] else "unavailable"
        services.append(report)
    configured_services = [item for item in services if item["configured"]]
    return {
        "status": "ready" if configured_services and all(item["status"] == "ready" for item in configured_services) else "degraded",
        "services": services,
        "checked_at": _now(),
    }


@mcp.tool()
def search_genius_songs(query: str, limit: int = 5) -> dict[str, Any]:
    """Search Genius for song metadata and lyric availability, without lyric text."""
    if _genius_adapter is None:
        return {"error": "provider_not_configured", "provider": "genius"}
    try:
        return {"provider": "genius", "query": query, "results": _genius_adapter.search(query, limit=limit)}
    except (AdapterError, ValueError) as exc:
        return {"error": "genius_search_failed", "provider": "genius", "message": str(exc)}


@mcp.tool()
def get_genius_song(song_id: int) -> dict[str, Any]:
    """Fetch Genius metadata for one song, without lyric text."""
    if _genius_adapter is None:
        return {"error": "provider_not_configured", "provider": "genius"}
    try:
        return {"provider": "genius", "song": _genius_adapter.get_song(song_id)}
    except (AdapterError, ValueError) as exc:
        return {"error": "genius_song_failed", "provider": "genius", "message": str(exc)}


@mcp.tool()
def search_lyric_context(query: str, mode: str = "mix") -> dict[str, Any]:
    """Retrieve context and references from the configured LightRAG lyric corpus."""
    if _lightrag_query_adapter is None:
        return {"error": "provider_not_configured", "provider": "lightrag"}
    try:
        return {"provider": "lightrag", "query": query, **_lightrag_query_adapter.query(query, mode=mode)}
    except (AdapterError, ValueError) as exc:
        return {"error": "lightrag_query_failed", "provider": "lightrag", "message": str(exc)}


@mcp.tool()
@_structured_errors
def dry_run_render(
    session_id: str,
    model: str = "minimax_music",
    duration: int = 180,
    filename_prefix: str = "songbird-preview",
    generation_seed: int | None = None,
) -> dict[str, Any]:
    """Map a session to a Songbird render plan without provider side effects."""
    session = _service.load_session(session_id)
    if session is None:
        return {"error": "session_not_found", "session_id": session_id}
    try:
        return _dry_run_adapter.plan(session, model, duration, filename_prefix, generation_seed).to_dict()
    except ValueError as exc:
        return {"error": "dry_run_failed", "message": str(exc)}


@mcp.tool()
@_structured_errors
def verify_artifact(session_id: str, provider: str = "auto") -> dict[str, Any]:
    """Fetch and verify a persisted artifact; the only path to a completed session.

    provider "auto" selects the local verifier for file:// artifacts and ComfyUI otherwise.
    """
    session = _service.load_session(session_id)
    if session is None:
        return {"error": "session_not_found", "session_id": session_id}
    if provider == "auto":
        provider = "local" if session.artifact is not None and session.artifact.uri.startswith("file://") else "comfyui"
    adapter = _artifact_qa_adapters.get(provider)
    if adapter is None:
        return {"error": "provider_not_configured", "provider": provider}
    try:
        report = adapter.evaluate(session)
    except (AdapterError, ValueError, OSError) as exc:
        return {"error": "artifact_verification_failed", "message": str(exc)}
    return _service.record_artifact_qa(session, report).to_dict()


@mcp.resource("songbird-agent://capabilities")
def capabilities_resource() -> str:
    """Expose a stable, read-only capability summary for MCP clients."""
    registered = _adapters.registered()["render"]
    rendering = f"render adapters: {', '.join(registered)}" if registered else "no render adapters configured"
    return (
        "songbird-agent-mcp: persisted planning, stage QA, explicit render approval, "
        f"and byte-level artifact verification; {rendering}."
    )


@mcp.prompt()
def create_music_session_prompt(use_case: str = "song", genre: str = "POP") -> str:
    """Provide a user-controlled prompt for beginning a governed music session."""
    return (
        f"Create a governed {genre} {use_case} session. First inspect capabilities and workflow plan, "
        "then draft and QA each creative stage. Do not render until the user explicitly approves."
    )


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
