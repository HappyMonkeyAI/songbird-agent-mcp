import json
import sqlite3

import pytest

from songbird_agent_mcp.contracts import SessionStatus, SongSession, StageName
from songbird_agent_mcp.session_store import (
    RevisionConflict,
    SessionAlreadyExists,
    SessionNotFound,
    SessionStore,
    SessionStoreError,
)


def make_session(revision=0, title="Neon Signal"):
    return SongSession(
        session_id="sess-1",
        use_case="song",
        genre="SYNTHWAVE",
        title=title,
        instrumental_only=False,
        status=SessionStatus.DRAFT,
        current_stage=StageName.BRIEF,
        revision=revision,
        created_at="2026-08-17T00:00:00+00:00",
        updated_at=f"2026-08-17T00:00:0{revision}+00:00",
    )


def test_create_and_get_round_trip_and_defensive_copy():
    store = SessionStore()
    created = store.create(make_session())
    created["title"] = "mutated"
    fetched = store.get("sess-1")
    assert fetched["title"] == "Neon Signal"
    assert fetched["revision"] == 0
    store.close()


def test_missing_get_returns_none():
    store = SessionStore()
    assert store.get("missing") is None
    store.close()


def test_duplicate_create_is_rejected():
    store = SessionStore()
    store.create(make_session())
    with pytest.raises(SessionAlreadyExists):
        store.create(make_session())
    store.close()


def test_save_updates_with_expected_revision():
    store = SessionStore()
    store.create(make_session())
    saved = store.save(make_session(revision=1, title="Updated"), expected_revision=0)
    assert saved["revision"] == 1
    assert store.get("sess-1")["title"] == "Updated"
    store.close()


def test_stale_revision_conflict_preserves_old_payload():
    store = SessionStore()
    store.create(make_session())
    with pytest.raises(RevisionConflict):
        store.save(make_session(revision=1, title="Stale"), expected_revision=2)
    assert store.get("sess-1")["title"] == "Neon Signal"
    store.close()


def test_missing_save_raises_not_found():
    store = SessionStore()
    with pytest.raises(SessionNotFound):
        store.save(make_session(), expected_revision=0)
    store.close()


@pytest.mark.parametrize("expected", [-1, True, 1.5])
def test_invalid_expected_revision_is_rejected(expected):
    store = SessionStore()
    store.create(make_session())
    with pytest.raises(ValueError, match="expected_revision"):
        store.save(make_session(revision=1), expected_revision=expected)
    store.close()


def test_non_session_input_is_rejected():
    store = SessionStore()
    with pytest.raises(TypeError, match="SongSession"):
        store.create({"session_id": "bad"})
    store.close()


def test_malformed_json_is_reported():
    store = SessionStore()
    store.create(make_session())
    store._connection.execute(
        "UPDATE sessions SET payload_json = ? WHERE session_id = ?",
        ("not-json", "sess-1"),
    )
    with pytest.raises(SessionStoreError, match="valid JSON"):
        store.get("sess-1")
    store.close()


def test_close_is_idempotence_not_required_but_closes_connection():
    store = SessionStore()
    store.close()
    with pytest.raises(sqlite3.ProgrammingError):
        store.get("sess-1")
