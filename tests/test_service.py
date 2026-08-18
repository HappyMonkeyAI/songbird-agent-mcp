import pytest

from songbird_agent_mcp.contracts import CreativeStage, QAReport, RenderRequest, SessionStatus
from songbird_agent_mcp.service import QANotPassed, RenderApprovalError, SessionService, StageTransitionError
from songbird_agent_mcp.session_store import SessionStore


def stage(name, revision):
    return CreativeStage(name, "draft", revision, {"text": name}, f"t-{revision}")


def service_and_session(instrumental_only=False):
    store = SessionStore()
    service = SessionService(store)
    session = service.create_session(
        use_case="game_background" if instrumental_only else "song",
        genre="SYNTHWAVE",
        title="Neon Signal",
        instrumental_only=instrumental_only,
    )
    return service, store, session


def advance_song_to_qa(service, session):
    for name in ("brief", "persona", "musical_direction", "lyrics_or_prompt"):
        service.apply_stage(session, stage(name, session.revision + 1))
    return session


def test_song_stages_are_ordered_and_revisioned():
    service, store, session = service_and_session()
    service.apply_stage(session, stage("brief", 1))
    service.apply_stage(session, stage("persona", 2))
    assert session.revision == 2
    assert store.get(session.session_id)["current_stage"] == "persona"
    with pytest.raises(StageTransitionError, match="expected stage musical_direction"):
        service.apply_stage(session, stage("lyrics_or_prompt", 3))
    store.close()


def test_generation_seed_persists_through_store_round_trip():
    store = SessionStore()
    service = SessionService(store)
    session = service.create_session(
        use_case="song", genre="METAL", title="Persistent Band", generation_seed=99,
    )
    loaded = service.load_session(session.session_id)
    assert loaded.generation_seed == 99
    assert store.get(session.session_id)["generation_seed"] == 99
    store.close()


def test_instrumental_workflow_skips_persona_and_lyrics():
    service, store, session = service_and_session(instrumental_only=True)
    service.apply_stage(session, stage("brief", 1))
    service.apply_stage(session, stage("musical_direction", 2))
    service.apply_stage(session, stage("instrumental_prompt", 3))
    assert session.current_stage == "instrumental_prompt"
    with pytest.raises(StageTransitionError, match="expected stage qa"):
        service.apply_stage(session, stage("lyrics_or_prompt", 4))
    store.close()


def test_failed_qa_blocks_render_approval():
    service, store, session = service_and_session()
    advance_song_to_qa(service, session)
    service.record_qa(session, QAReport(False, 55, ["repetition"], ["rewrite chorus"], "now"))
    request = RenderRequest(session.session_id, "minimax_music", 180, "idem-1", "now", "user")
    with pytest.raises(QANotPassed):
        service.approve_render(session, request)
    assert session.status == SessionStatus.QA_FAILED.value
    store.close()


def test_passing_qa_allows_explicit_render_approval():
    service, store, session = service_and_session()
    advance_song_to_qa(service, session)
    service.record_qa(session, QAReport(True, 95, [], [], "now"))
    request = RenderRequest(session.session_id, "minimax_music", 180, "idem-1", "now", "user")
    service.approve_render(session, request)
    assert session.status == SessionStatus.RENDER_APPROVED.value
    saved = store.get(session.session_id)
    assert saved["render_request"]["idempotency_key"] == "idem-1"
    store.close()


def test_render_approval_requires_matching_session_id_and_qa_stage():
    service, store, session = service_and_session()
    request = RenderRequest("other-session", "ace_step", 120, "idem-1", "now", "user")
    with pytest.raises(RenderApprovalError, match="session_id"):
        service.approve_render(session, request)
    store.close()


def test_persistence_failure_rolls_back_in_memory_revision():
    service, store, session = service_and_session()
    original_save = store.save
    store.save = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("disk failure"))
    with pytest.raises(RuntimeError, match="disk failure"):
        service.apply_stage(session, stage("brief", 1))
    assert session.revision == 0
    store.save = original_save
    store.close()


def test_session_can_be_hydrated_from_persisted_snapshot():
    service, store, session = service_and_session()
    service.apply_stage(session, stage("brief", 1))
    loaded = service.load_session(session.session_id)
    assert loaded is not None
    assert loaded.session_id == session.session_id
    assert loaded.current_stage == "brief"
    assert loaded.revision == 1
    store.close()
