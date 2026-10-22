from urllib.error import URLError

from songbird_agent_mcp.adapters import AdapterError, StaticCreativeAdapter, StaticRenderAdapter
from songbird_agent_mcp.contracts import ArtifactQAReport
from songbird_agent_mcp.server import (
    _adapters,
    _artifact_qa_adapters,
    revise_creative_stage,
    create_song_session,
    get_capabilities,
    get_song_session,
    get_workflow_plan,
    generate_musical_direction,
    approve_render,
    check_provider_health,
    check_supporting_services,
    dry_run_render,
    get_genius_song,
    search_genius_songs,
    search_lyric_context,
    record_qa,
    render_song,
    qa_artifact,
    verify_artifact,
    submit_creative_stage,
)


def test_capabilities_report_registered_adapters():
    result = get_capabilities()
    assert result["execution_backend"] == ("connected" if result["registered_adapters"]["render"] else "not_connected")
    assert "local" in result["artifact_verifiers"]
    assert "render" in result["approval_required_for"]


def test_song_plan_includes_lyrics_and_qa():
    result = get_workflow_plan("song", "SYNTHWAVE")
    assert result["stages"] == [
        "brief", "persona", "musical_direction", "lyrics_or_prompt", "qa", "render", "artifact_qa"
    ]


def test_instrumental_plan_skips_persona_and_lyrics():
    result = get_workflow_plan("game_background", "SYNTHWAVE", instrumental_only=True)
    assert "lyrics_or_prompt" not in result["stages"]
    assert "instrumental_prompt" in result["stages"]


def test_session_create_and_read():
    session = create_song_session("game_background", "SYNTHWAVE", "Neon Menu", True)
    assert session["status"] == "draft"
    assert session["current_stage"] == "brief"
    assert session["stages"] == []
    assert get_song_session(session["session_id"]) == session


def test_session_create_with_generation_seed():
    session = create_song_session("song", "METAL", "Persistent Band", False, 777)
    assert session["generation_seed"] == 777
    assert get_song_session(session["session_id"])["generation_seed"] == 777


def test_missing_session_is_structured():
    assert get_song_session("sess_missing")["error"] == "session_not_found"


def test_unconfigured_provider_returns_structured_error():
    session = create_song_session("instrumental", "SYNTHWAVE", "No Adapter", True)
    result = generate_musical_direction(session["session_id"])
    assert result["error"] == "adapter_not_configured"


def test_unconfigured_health_provider_returns_structured_error():
    result = check_provider_health()
    assert result == {"error": "provider_not_configured", "provider": "comfyui"}


def test_supporting_services_reports_unconfigured_dependencies_without_network_calls():
    result = check_supporting_services()
    assert result["status"] == "degraded"
    assert {item["provider"] for item in result["services"]} == {"comfyui", "lightrag", "perplexica", "ollama"}
    assert all(item["status"] == "not_configured" for item in result["services"])


def test_research_tools_report_unconfigured_backends():
    assert search_genius_songs("neon") == {"error": "provider_not_configured", "provider": "genius"}
    assert get_genius_song(123)["error"] == "provider_not_configured"
    assert search_lyric_context("neon") == {"error": "provider_not_configured", "provider": "lightrag"}


def test_unconfigured_artifact_verifier_returns_structured_error():
    result = verify_artifact("missing-session")
    assert result == {"error": "session_not_found", "session_id": "missing-session"}


def test_dry_run_render_maps_minimax_without_persisting_or_calling_provider():
    session = create_song_session("instrumental", "SYNTHWAVE", "Dry Run", True)
    session_id = session["session_id"]
    submit_creative_stage(session_id, "brief", {"text": "night drive"})
    submit_creative_stage(session_id, "musical_direction", {"tags": "analog synth, cinematic", "bpm": 118, "keyscale": "F minor"})
    submit_creative_stage(session_id, "instrumental_prompt", {"text": "wide stereo arpeggios"})
    before = get_song_session(session_id)
    plan = dry_run_render(session_id, "minimax", 420, "preview", 7)
    after = get_song_session(session_id)
    assert plan["profile_id"] == "minimax_music"
    assert plan["duration_requested"] == 420
    assert plan["duration_effective"] == 300
    assert plan["parameters"]["generation_seed"] == 7
    assert plan["parameters"]["generation_mode"] == "segmented_fallback_required"
    assert plan["parameters"]["structured_caption"]["Global Metadata"] == {
        "genre": "SYNTHWAVE",
        "bpm": 118,
        "key": "F minor",
        "scale": None,
    }
    assert plan["side_effects"]
    assert after["revision"] == before["revision"]


def test_dry_run_minimax_prefers_native_long_form_and_locks_caption_sections():
    session = create_song_session("song", "NU_DISCO_ELECTROPOP", "Caption Lock", False)
    session_id = session["session_id"]
    submit_creative_stage(session_id, "brief", {"text": "night drive release"})
    submit_creative_stage(session_id, "persona", {"artist_name": "Mara Vale", "identity": "fictional original artist"})
    submit_creative_stage(session_id, "musical_direction", {
        "tags": "nu-disco, electropop",
        "bpm": 122,
        "keyscale": "F# minor",
        "vocal_details": {"artist_name": "Mara Vale", "timbre": "warm, clear female lead"},
        "arrangement": {"core": "funk bass, rhythm guitar, analog synths", "progression": "restrained verse to wide final chorus"},
    })
    submit_creative_stage(session_id, "lyrics_or_prompt", {"text": "[Verse]\nNeon on the glass\n[Chorus]\nTurn the signal on"})

    plan = dry_run_render(session_id, "minimax", 180, "caption-lock", 11)

    assert plan["parameters"]["generation_mode"] == "native_long_form"
    caption = plan["parameters"]["structured_caption"]
    assert caption["Global Metadata"] == {
        "genre": "NU_DISCO_ELECTROPOP",
        "bpm": 122,
        "key": "F# minor",
        "scale": None,
    }
    assert caption["Vocal Details"] == {"artist_name": "Mara Vale", "timbre": "warm, clear female lead"}
    assert caption["Arrangement"] == {"core": "funk bass, rhythm guitar, analog synths", "progression": "restrained verse to wide final chorus"}


def test_registered_mock_provider_generates_and_persists_stage():
    _adapters.register_creative("test-mock", StaticCreativeAdapter("musical_direction", {"tags": "synths"}))
    session = create_song_session("instrumental", "SYNTHWAVE", "Mock Adapter", True)
    submit_creative_stage(session["session_id"], "brief", {"text": "night drive"})
    result = generate_musical_direction(session["session_id"], "test-mock")
    assert result["current_stage"] == "musical_direction"
    assert result["stages"][-1]["content"]["tags"] == "synths"


def test_render_requires_approval_and_persists_artifact():
    _adapters.register_render("test-render", StaticRenderAdapter())
    session = create_song_session("instrumental", "SYNTHWAVE", "Render Mock", True)
    session_id = session["session_id"]
    for name in ("brief", "musical_direction", "instrumental_prompt"):
        submit_creative_stage(session_id, name, {"text": name})
    record_qa(session_id, True, 98, [], [])
    approved = approve_render(session_id, "mock", 180, "render-test-1", "operator")
    result = render_song(session_id, "test-render")
    assert result["status"] == "rendering"
    assert result["artifact"]["kind"] == "audio"
    assert result["artifact"]["duration_seconds"] == 180
    assert approved["status"] == "render_approved"
    _artifact_qa_adapters["test-pass"] = PassingVerifier()
    completed = verify_artifact(session_id, "test-pass")
    assert completed["status"] == "completed"
    assert completed["current_stage"] == "artifact_qa"


def test_approve_render_persists_m2m_parameters():
    session = create_song_session("song", "POP", "M2M Mock", False)
    session_id = session["session_id"]
    for name in ("brief", "persona", "musical_direction", "lyrics_or_prompt"):
        submit_creative_stage(session_id, name, {"text": name})
    record_qa(session_id, True, 98, [], [])
    approved = approve_render(
        session_id, "ace_step_m2m", 161, "m2m-render-1", "operator",
        parameters={"source_audio_uri": "http://comfy:8188/view?filename=src.mp3&subfolder=audio&type=output", "denoise": 0.3},
    )
    assert approved["status"] == "render_approved"
    assert approved["render_request"]["backend"] == "ace_step_m2m"
    assert approved["render_request"]["parameters"]["denoise"] == 0.3
    assert approved["render_request"]["parameters"]["source_audio_uri"].startswith("http://comfy:8188/view?")


def test_approve_render_round_trips_empty_parameters():
    session = create_song_session("instrumental", "SYNTHWAVE", "Empty Params", True)
    session_id = session["session_id"]
    for name in ("brief", "musical_direction", "instrumental_prompt"):
        submit_creative_stage(session_id, name, {"text": name})
    record_qa(session_id, True, 98, [], [])
    approved = approve_render(session_id, "mock", 120, "render-empty-params", "operator")
    assert approved["status"] == "render_approved"
    assert approved["render_request"]["parameters"] == {}


def test_artifact_qa_blocks_when_integrity_check_fails():
    _adapters.register_render("test-render-failed-qa", StaticRenderAdapter())
    session = create_song_session("instrumental", "SYNTHWAVE", "Blocked Artifact", True)
    session_id = session["session_id"]
    for name in ("brief", "musical_direction", "instrumental_prompt"):
        submit_creative_stage(session_id, name, {"text": name})
    record_qa(session_id, True, 98, [], [])
    approve_render(session_id, "mock", 180, "render-test-2", "operator")
    render_song(session_id, "test-render-failed-qa")
    blocked = qa_artifact(session_id, ["audible clipping in chorus"])
    assert blocked["status"] == "blocked"
    assert blocked["artifact_qa"]["passed"] is False
    assert blocked["artifact_qa"]["checksum_verified"] is False


class PassingVerifier:
    def evaluate(self, session):
        return ArtifactQAReport(True, True, True, ["verified by test double"], "now")


class CountingRenderAdapter(StaticRenderAdapter):
    def __init__(self, error=None):
        self.calls = 0
        self.error = error

    def render(self, session, request):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return super().render(session, request)


def approved_instrumental_session(title, backend="mock", idempotency_key=None):
    session_id = create_song_session("instrumental", "SYNTHWAVE", title, True)["session_id"]
    for name in ("brief", "musical_direction", "instrumental_prompt"):
        submit_creative_stage(session_id, name, {"text": name})
    record_qa(session_id, True, 98, [], [])
    approve_render(session_id, backend, 180, idempotency_key or f"idem-{session_id}", "operator")
    return session_id


def test_qa_artifact_cannot_self_report_completion():
    _adapters.register_render("test-render-self-report", StaticRenderAdapter())
    session_id = approved_instrumental_session("Self Report")
    render_song(session_id, "test-render-self-report")
    assert qa_artifact(session_id, [])["error"] == "findings_required"
    assert get_song_session(session_id)["status"] == "rendering"


def test_render_song_runs_provider_at_most_once():
    adapter = CountingRenderAdapter()
    _adapters.register_render("test-render-once", adapter)
    session_id = approved_instrumental_session("Render Once")
    assert render_song(session_id, "test-render-once")["status"] == "rendering"
    again = render_song(session_id, "test-render-once")
    assert again["error"] == "stage_transition_rejected"
    assert adapter.calls == 1


def test_render_song_refuses_completed_session():
    adapter = CountingRenderAdapter()
    _adapters.register_render("test-render-completed", adapter)
    _artifact_qa_adapters["test-pass"] = PassingVerifier()
    session_id = approved_instrumental_session("Render Completed")
    render_song(session_id, "test-render-completed")
    assert verify_artifact(session_id, "test-pass")["status"] == "completed"
    assert render_song(session_id, "test-render-completed")["error"] == "stage_transition_rejected"
    assert adapter.calls == 1


def test_render_song_rejects_adapter_that_does_not_match_approved_backend():
    adapter = CountingRenderAdapter()
    _adapters.register_render("test-render-mismatch", adapter)
    session_id = approved_instrumental_session("Mismatch", backend="minimax_music")
    result = render_song(session_id, "test-render-mismatch")
    assert result["error"] == "adapter_backend_mismatch"
    assert result["expected_adapter"] == "comfyui"
    assert adapter.calls == 0


def test_render_song_requires_adapter_for_unbound_backend():
    session_id = approved_instrumental_session("Unbound Backend")
    assert render_song(session_id)["error"] == "adapter_not_resolved"


def test_failed_render_releases_claim_and_returns_structured_transport_error():
    _adapters.register_render("test-render-urlerror", CountingRenderAdapter(error=URLError("connection refused")))
    _adapters.register_render("test-render-retry", CountingRenderAdapter())
    session_id = approved_instrumental_session("Transport Failure")
    failed = render_song(session_id, "test-render-urlerror")
    assert failed["error"] == "render_failed"
    assert get_song_session(session_id)["render_claimed_at"] is None
    assert render_song(session_id, "test-render-retry")["status"] == "rendering"


def test_failed_qa_can_be_revised_and_requalified():
    session_id = create_song_session("song", "POP", "Revise Loop", False)["session_id"]
    for name in ("brief", "persona", "musical_direction", "lyrics_or_prompt"):
        submit_creative_stage(session_id, name, {"text": name})
    failed = record_qa(session_id, False, 40, ["weak chorus"], ["rewrite chorus"])
    assert failed["status"] == "qa_failed"
    revised = revise_creative_stage(session_id, "lyrics_or_prompt", {"text": "better chorus"})
    assert revised["status"] == "in_progress"
    assert revised["latest_qa"] is None
    assert record_qa(session_id, True, 92, [], [])["status"] == "qa_passed"
    approved = approve_render(session_id, "minimax_music", 120, "revise-loop-1", "operator")
    assert approved["status"] == "render_approved"
    assert dry_run_render(session_id, "minimax", 120, "revise")["lyrics_or_prompt"] == "better chorus"


def test_revising_upstream_stage_requires_downstream_resubmission():
    session_id = create_song_session("song", "POP", "Rewind", False)["session_id"]
    for name in ("brief", "persona", "musical_direction", "lyrics_or_prompt"):
        submit_creative_stage(session_id, name, {"text": name})
    revise_creative_stage(session_id, "musical_direction", {"tags": "darker"})
    assert record_qa(session_id, True, 90, [], [])["error"] == "stage_transition_rejected"
    submit_creative_stage(session_id, "lyrics_or_prompt", {"text": "new lyrics"})
    assert record_qa(session_id, True, 90, [], [])["status"] == "qa_passed"


def test_cannot_revise_after_render_approval_or_unsubmitted_stage():
    session_id = approved_instrumental_session("Locked")
    assert revise_creative_stage(session_id, "brief", {"text": "late"})["error"] == "stage_transition_rejected"
    fresh = create_song_session("song", "POP", "Fresh", False)["session_id"]
    submit_creative_stage(fresh, "brief", {"text": "brief"})
    assert revise_creative_stage(fresh, "persona", {"text": "p"})["error"] == "stage_transition_rejected"


def test_governance_violations_return_structured_errors():
    session_id = create_song_session("song", "POP", "Out Of Order", False)["session_id"]
    result = submit_creative_stage(session_id, "lyrics_or_prompt", {"text": "too early"})
    assert result["error"] == "stage_transition_rejected"
    assert "expected stage brief" in result["message"]
    assert approve_render(session_id, "minimax_music", 120, "idem", "op")["error"] == "stage_transition_rejected"


def test_session_timestamps_are_recorded():
    session = create_song_session("song", "POP", "Timestamps", False)
    assert session["created_at"] and session["updated_at"] == session["created_at"]
    updated = submit_creative_stage(session["session_id"], "brief", {"text": "brief"})
    assert updated["created_at"] == session["created_at"]
    assert updated["updated_at"] >= session["updated_at"]


def test_verify_artifact_auto_selects_local_verifier_for_file_artifacts(tmp_path, monkeypatch):
    import songbird_agent_mcp.server as server
    from songbird_agent_mcp.contracts import ArtifactRecord

    class FileRenderAdapter:
        def render(self, session, request):
            path = tmp_path / "stem.mp3"
            path.write_bytes(b"STEM")
            return ArtifactRecord("a1", "audio", f"file://{path}", "audio/mpeg", None, 180, "now")

    seen = []

    class RecordingVerifier:
        def evaluate(self, session):
            seen.append(session.artifact.uri)
            return ArtifactQAReport(False, False, True, ["no render-time checksum recorded"], "now")

    _adapters.register_render("test-render-file", FileRenderAdapter())
    monkeypatch.setitem(server._artifact_qa_adapters, "local", RecordingVerifier())
    session_id = approved_instrumental_session("File Artifact")
    render_song(session_id, "test-render-file")
    result = verify_artifact(session_id)
    assert seen and seen[0].startswith("file://")
    assert result["status"] == "blocked"
