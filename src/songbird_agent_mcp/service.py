"""Application service for governed creative-stage transitions."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from .contracts import (
    ArtifactRecord,
    ArtifactQAReport,
    CreativeStage,
    QAReport,
    ProvenanceRecord,
    RenderRequest,
    SessionStatus,
    SongSession,
    StageName,
)
from .session_store import SessionStore, SessionStoreError


class StageTransitionError(ValueError):
    """Raised when a session attempts an invalid stage transition."""


class QANotPassed(StageTransitionError):
    """Raised when rendering is requested without a passing QA report."""


class RenderApprovalError(StageTransitionError):
    """Raised when a render approval is invalid for the current session."""


class ArtifactPersistenceError(StageTransitionError):
    """Raised when a rendered artifact cannot be attached to the approved session."""


class RenderClaimError(StageTransitionError):
    """Raised when a render cannot be started because the session is not claimable."""


_DEDICATED_STAGES = {StageName.QA.value, StageName.RENDER.value, StageName.ARTIFACT_QA.value}
_REVISABLE_STATUSES = {
    SessionStatus.IN_PROGRESS.value,
    SessionStatus.QA_FAILED.value,
    SessionStatus.QA_PASSED.value,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionService:
    """Coordinate typed session mutations and persist each revisioned snapshot."""

    def __init__(self, store: SessionStore) -> None:
        self._store = store

    def create_session(
        self,
        *,
        use_case: str,
        genre: str,
        title: str | None = None,
        instrumental_only: bool = False,
        generation_seed: int | None = None,
    ) -> SongSession:
        created_at = _now()
        session = SongSession(
            session_id=f"sess_{uuid4().hex}",
            use_case=use_case,
            genre=genre,
            title=title,
            # The workflow plan treats the instrumental use case as lyric-free; keep
            # the enforced stage sequence consistent with that advertised plan.
            instrumental_only=instrumental_only or use_case == "instrumental",
            generation_seed=generation_seed,
            status=SessionStatus.DRAFT,
            current_stage=StageName.BRIEF,
            revision=0,
            created_at=created_at,
            updated_at=created_at,
        )
        self._store.create(session)
        return session

    def load_session(self, session_id: str) -> SongSession | None:
        payload = self._store.get(session_id)
        if payload is None:
            return None
        try:
            stages = [
                CreativeStage(
                    name=item["name"],
                    status=item["status"],
                    revision=item["revision"],
                    content=item["content"],
                    updated_at=item["updated_at"],
                    provenance=[ProvenanceRecord(**record) for record in item.get("provenance", [])],
                )
                for item in payload.get("stages", [])
            ]
            latest_qa = payload.get("latest_qa")
            render_request = payload.get("render_request")
            artifact = payload.get("artifact")
            artifact_qa = payload.get("artifact_qa")
            artifact_model = None
            if artifact:
                artifact_model = ArtifactRecord(
                    artifact_id=artifact["artifact_id"],
                    kind=artifact["kind"],
                    uri=artifact["uri"],
                    mime_type=artifact["mime_type"],
                    checksum=artifact.get("checksum"),
                    duration_seconds=artifact["duration_seconds"],
                    created_at=artifact["created_at"],
                    provenance=[ProvenanceRecord(**record) for record in artifact.get("provenance", [])],
                )
            return SongSession(
                session_id=payload["session_id"],
                use_case=payload["use_case"],
                genre=payload["genre"],
                title=payload.get("title"),
                instrumental_only=payload["instrumental_only"],
                status=payload["status"],
                current_stage=payload["current_stage"],
                revision=payload["revision"],
                generation_seed=payload.get("generation_seed"),
                stages=stages,
                latest_qa=QAReport(**latest_qa) if latest_qa else None,
                render_request=RenderRequest(**render_request) if render_request else None,
                artifact=artifact_model,
                artifact_qa=ArtifactQAReport(**artifact_qa) if artifact_qa else None,
                render_claimed_at=payload.get("render_claimed_at"),
                created_at=payload.get("created_at", ""),
                updated_at=payload.get("updated_at", ""),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SessionStoreError(f"stored session {session_id} failed contract hydration") from exc

    def apply_stage(self, session: SongSession, stage: CreativeStage) -> SongSession:
        expected = self._next_stage(session)
        if stage.name != expected.value:
            raise StageTransitionError(
                f"expected stage {expected.value}, received {stage.name}"
            )
        if stage.name in _DEDICATED_STAGES:
            raise StageTransitionError("use the dedicated QA or approval method for this stage")
        session.stages.append(stage)
        session.current_stage = stage.name
        session.status = SessionStatus.IN_PROGRESS
        self._persist(session)
        return session

    def revise_stage(self, session: SongSession, stage: CreativeStage) -> SongSession:
        """Append a new revision of an already-submitted creative stage.

        The session rewinds to the revised stage, so every later creative stage
        must be resubmitted and QA must be recorded again before render approval.
        """
        if session.status not in _REVISABLE_STATUSES:
            raise StageTransitionError(f"stage revision is not allowed in status {session.status}")
        if stage.name in _DEDICATED_STAGES or stage.name not in {s.value for s in self._creative_sequence(session)}:
            raise StageTransitionError(f"{stage.name} is not a creative stage in this workflow")
        if not any(item.name == stage.name for item in session.stages):
            raise StageTransitionError(f"stage {stage.name} has not been submitted yet")
        session.stages.append(stage)
        session.current_stage = stage.name
        session.latest_qa = None
        session.status = SessionStatus.IN_PROGRESS
        self._persist(session)
        return session

    def record_qa(self, session: SongSession, report: QAReport) -> SongSession:
        expected = self._next_stage(session)
        if expected is not StageName.QA:
            raise StageTransitionError(f"expected stage {expected.value}, received qa")
        session.latest_qa = report
        session.current_stage = StageName.QA.value
        session.status = SessionStatus.QA_PASSED if report.passed else SessionStatus.QA_FAILED
        self._persist(session)
        return session

    def approve_render(self, session: SongSession, request: RenderRequest) -> SongSession:
        if request.session_id != session.session_id:
            raise RenderApprovalError("render request session_id does not match session")
        if session.current_stage != StageName.QA.value:
            raise RenderApprovalError("render approval requires the qa stage")
        if session.latest_qa is None or not session.latest_qa.passed:
            raise QANotPassed("render approval requires a passing QA report")
        if session.latest_qa.required_revisions:
            raise QANotPassed("render approval requires no outstanding revisions")
        session.render_request = request
        session.current_stage = StageName.RENDER.value
        session.status = SessionStatus.RENDER_APPROVED
        self._persist(session)
        return session

    def begin_render(self, session: SongSession) -> SongSession:
        """Atomically claim an approved session before any provider side effect.

        The optimistic revision check in the store guarantees that only one of
        several concurrent callers holding the same snapshot wins the claim.
        """
        if session.status != SessionStatus.RENDER_APPROVED.value or session.render_request is None:
            raise RenderClaimError(f"render requires status render_approved, found {session.status}")
        if session.render_claimed_at is not None:
            raise RenderClaimError(f"render already in progress since {session.render_claimed_at}")
        session.render_claimed_at = _now()
        self._persist(session)
        return session

    def release_render(self, session: SongSession) -> SongSession:
        """Release a render claim after a provider failure so the render may be retried."""
        if session.status != SessionStatus.RENDER_APPROVED.value or session.render_claimed_at is None:
            return session
        session.render_claimed_at = None
        self._persist(session)
        return session

    def record_artifact(self, session: SongSession, artifact: ArtifactRecord) -> SongSession:
        if session.status != SessionStatus.RENDER_APPROVED.value:
            raise ArtifactPersistenceError("artifact recording requires render approval")
        if session.render_request is None:
            raise ArtifactPersistenceError("artifact recording requires a render request")
        if session.render_claimed_at is None:
            raise ArtifactPersistenceError("artifact recording requires a render claim")
        if artifact.duration_seconds > session.render_request.duration_seconds:
            raise ArtifactPersistenceError("artifact duration exceeds approved render duration")
        session.artifact = artifact
        session.status = SessionStatus.RENDERING.value
        self._persist(session)
        return session

    def record_artifact_qa(self, session: SongSession, report: ArtifactQAReport) -> SongSession:
        if session.status != SessionStatus.RENDERING.value:
            raise StageTransitionError("artifact QA requires a rendered session")
        if session.artifact is None:
            raise StageTransitionError("artifact QA requires a persisted artifact")
        session.artifact_qa = report
        session.current_stage = StageName.ARTIFACT_QA.value
        session.status = (
            SessionStatus.COMPLETED.value
            if report.passed and report.checksum_verified and report.duration_verified
            else SessionStatus.BLOCKED.value
        )
        self._persist(session)
        return session

    @staticmethod
    def _creative_sequence(session: SongSession) -> list[StageName]:
        sequence = [StageName.BRIEF, StageName.MUSICAL_DIRECTION]
        if not session.instrumental_only:
            sequence.insert(1, StageName.PERSONA)
            sequence.append(StageName.LYRICS_OR_PROMPT)
        else:
            sequence.append(StageName.INSTRUMENTAL_PROMPT)
        return sequence

    def _next_stage(self, session: SongSession) -> StageName:
        sequence = self._creative_sequence(session) + [StageName.QA]
        if not session.stages:
            return StageName.BRIEF
        try:
            index = sequence.index(StageName(session.current_stage))
        except ValueError as exc:
            raise StageTransitionError("session current_stage is not in the workflow") from exc
        if index + 1 >= len(sequence):
            raise StageTransitionError("session has no next creative stage; use revise_stage to change a draft")
        return sequence[index + 1]

    def _persist(self, session: SongSession) -> None:
        expected_revision = session.revision
        previous_updated_at = session.updated_at
        session.revision += 1
        session.updated_at = _now()
        try:
            self._store.save(session, expected_revision=expected_revision)
        except Exception:
            session.revision = expected_revision
            session.updated_at = previous_updated_at
            raise
