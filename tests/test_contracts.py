import json

import pytest

from songbird_agent_mcp.contracts import (
    CreativeStage,
    QAReport,
    RenderRequest,
    ProvenanceRecord,
    SessionStatus,
    SongSession,
    StageName,
)


def provenance():
    return ProvenanceRecord(
        provider="test-provider",
        model="test-model",
        request_id="req-1",
        created_at="2026-08-17T00:00:00+00:00",
        source="unit-test",
    )


def test_contracts_serialize_to_json_safe_values():
    stage = CreativeStage(
        name=StageName.MUSICAL_DIRECTION,
        status="draft",
        revision=0,
        content={"bpm": 120, "instruments": ["synth"]},
        updated_at="2026-08-17T00:00:00+00:00",
        provenance=[provenance()],
    )
    session = SongSession(
        session_id="sess-1",
        use_case="song",
        genre="SYNTHWAVE",
        title="Neon Signal",
        instrumental_only=False,
        status=SessionStatus.DRAFT,
        current_stage=StageName.BRIEF,
        revision=0,
        stages=[stage],
        created_at="2026-08-17T00:00:00+00:00",
        updated_at="2026-08-17T00:00:00+00:00",
    )

    payload = session.to_dict()
    json.dumps(payload)
    assert payload["status"] == "draft"
    assert payload["stages"][0]["name"] == "musical_direction"
    assert payload["stages"][0]["provenance"][0]["provider"] == "test-provider"


def test_nan_qa_score_is_rejected():
    with pytest.raises(ValueError, match="score"):
        QAReport(True, float("nan"), [], [], "2026-08-17T00:00:00+00:00")


@pytest.mark.parametrize("score", [float("inf"), float("-inf"), -1, 101, True])
def test_invalid_qa_score_boundaries_are_rejected(score):
    with pytest.raises(ValueError, match="score"):
        QAReport(True, score, [], [], "2026-08-17T00:00:00+00:00")


def test_render_duration_above_minimax_ceiling_is_rejected():
    with pytest.raises(ValueError, match="at most 300"):
        RenderRequest("sess-1", "minimax_music", 301, "idem-1", "now", "user")


@pytest.mark.parametrize("duration", [float("inf"), float("nan"), True, 0, -1, 300.1])
def test_invalid_render_duration_boundaries_are_rejected(duration):
    with pytest.raises(ValueError, match="duration_seconds"):
        RenderRequest("sess-1", "minimax_music", duration, "idem-1", "now", "user")


def test_empty_provenance_provider_is_rejected():
    with pytest.raises(ValueError, match="provider"):
        ProvenanceRecord("", "model", "req", "now", "source")


def test_instrumental_session_cannot_contain_lyrics_stage():
    lyrics_stage = CreativeStage("lyrics_or_prompt", "draft", 0, {}, "now")
    with pytest.raises(ValueError, match="lyrics_or_prompt"):
        SongSession(
            "sess-1", "game_background", "SYNTHWAVE", None, True,
            "draft", "brief", 0, stages=[lyrics_stage], created_at="now", updated_at="now",
        )


def test_non_dict_stage_content_is_rejected():
    with pytest.raises(ValueError, match="content"):
        CreativeStage("brief", "draft", 0, ["not", "a", "dict"], "now")


def test_invalid_nested_provenance_is_rejected():
    with pytest.raises(ValueError, match="ProvenanceRecord"):
        CreativeStage("brief", "draft", 0, {}, "now", provenance=[{"provider": "bad"}])


def test_unknown_stage_name_is_rejected():
    with pytest.raises(ValueError, match="name"):
        CreativeStage("unknown", "draft", 0, {}, "now")


def test_mutable_content_is_defensively_copied():
    content = {"tags": ["one"]}
    stage = CreativeStage("brief", "draft", 0, content, "now")
    content["tags"].append("two")
    exported = stage.to_dict()
    exported["content"]["tags"].append("three")
    assert stage.content == {"tags": ["one"]}


def test_generation_seed_round_trips_through_to_dict():
    session = SongSession(
        session_id="sess-seed",
        use_case="song",
        genre="METAL",
        title="Persistent Band",
        instrumental_only=False,
        status=SessionStatus.DRAFT,
        current_stage=StageName.BRIEF,
        revision=0,
        generation_seed=4242,
        created_at="2026-08-18T00:00:00+00:00",
        updated_at="2026-08-18T00:00:00+00:00",
    )
    assert session.to_dict()["generation_seed"] == 4242


@pytest.mark.parametrize("seed", [-1, 1.5, True, "42", float("nan")])
def test_invalid_generation_seed_is_rejected(seed):
    with pytest.raises(ValueError, match="generation_seed"):
        SongSession(
            "sess-seed", "song", "METAL", None, False,
            "draft", "brief", 0, generation_seed=seed,
        )
