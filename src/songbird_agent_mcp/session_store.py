"""SQLite snapshot persistence for governed song sessions."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any

from .contracts import SongSession


class SessionStoreError(Exception):
    """Base error for session-store failures."""


class SessionAlreadyExists(SessionStoreError):
    """Raised when creating a duplicate session."""


class SessionNotFound(SessionStoreError):
    """Raised when updating a missing session."""


class RevisionConflict(SessionStoreError):
    """Raised when an optimistic update has a stale expected revision."""


class SessionStore:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(path), isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                revision INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

    def create(self, session: SongSession) -> dict[str, Any]:
        self._validate_session(session)
        payload = session.to_dict()
        try:
            self._connection.execute(
                """
                INSERT INTO sessions
                    (session_id, revision, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    session.session_id,
                    session.revision,
                    json.dumps(payload, separators=(",", ":"), sort_keys=True),
                    session.created_at,
                    session.updated_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise SessionAlreadyExists(session.session_id) from exc
        return self._copy_payload(payload)

    def get(self, session_id: str) -> dict[str, Any] | None:
        session_id = self._validate_session_id(session_id)
        row = self._connection.execute(
            "SELECT payload_json FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise SessionStoreError(f"stored session {session_id} is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise SessionStoreError(f"stored session {session_id} is not a JSON object")
        return self._copy_payload(payload)

    def save(self, session: SongSession, expected_revision: int) -> dict[str, Any]:
        self._validate_session(session)
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or expected_revision < 0:
            raise ValueError("expected_revision must be a non-negative integer")
        payload = session.to_dict()
        encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            row = self._connection.execute(
                "SELECT revision FROM sessions WHERE session_id = ?",
                (session.session_id,),
            ).fetchone()
            if row is None:
                raise SessionNotFound(session.session_id)
            if row["revision"] != expected_revision:
                raise RevisionConflict(
                    f"session {session.session_id} expected revision {expected_revision}, "
                    f"found {row['revision']}"
                )
            updated = self._connection.execute(
                """
                UPDATE sessions
                SET revision = ?, payload_json = ?, created_at = ?, updated_at = ?
                WHERE session_id = ? AND revision = ?
                """,
                (
                    session.revision,
                    encoded,
                    session.created_at,
                    session.updated_at,
                    session.session_id,
                    expected_revision,
                ),
            )
            if updated.rowcount != 1:
                raise RevisionConflict(f"session {session.session_id} changed during update")
            self._connection.execute("COMMIT")
        except Exception:
            self._connection.execute("ROLLBACK")
            raise
        return self._copy_payload(payload)

    def close(self) -> None:
        self._connection.close()

    @staticmethod
    def _validate_session(session: SongSession) -> None:
        if not isinstance(session, SongSession):
            raise TypeError("session must be a SongSession")

    @staticmethod
    def _validate_session_id(session_id: str) -> str:
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must be a non-empty string")
        return session_id.strip()

    @staticmethod
    def _copy_payload(payload: dict[str, Any]) -> dict[str, Any]:
        return json.loads(json.dumps(payload))
