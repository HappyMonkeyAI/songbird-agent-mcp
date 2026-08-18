from songbird_agent_mcp.adapters import StaticCreativeAdapter, StaticRenderAdapter
from songbird_agent_mcp.server import (
    _adapters,
    create_song_session,
    get_capabilities,
    get_song_session,
    get_workflow_plan,
    generate_musical_direction,
    approve_render,
    check_provider_health,
    dry_run_render,
    record_qa,
    render_song,
    qa_artifact,
    verify_artifact,
    submit_creative_stage,
)


def test_capabilities_are_explicitly_planning_only():
    result = get_capabilities()
    assert result["status"] == "scaffold"
    assert result["execution_backend"] == "not_connected"
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
    assert plan["side_effects"]
    assert after["revision"] == before["revision"]


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
    completed = qa_artifact(session_id, True, True, True, [])
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
    blocked = qa_artifact(session_id, True, False, True, ["checksum mismatch"])
    assert blocked["status"] == "blocked"
    assert blocked["artifact_qa"]["checksum_verified"] is False
