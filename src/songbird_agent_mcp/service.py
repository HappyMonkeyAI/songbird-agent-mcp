"""Application service for governed creative-stage transitions."""

from __future__ import annotations

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
        session = SongSession(
            session_id=f"sess_{uuid4().hex}",
            use_case=use_case,
            genre=genre,
            title=title,
            instrumental_only=instrumental_only,
            generation_seed=generation_seed,
            status=SessionStatus.DRAFT,
            current_stage=StageName.BRIEF,
            revision=0,
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
        if stage.name in {StageName.QA.value, StageName.RENDER.value, StageName.ARTIFACT_QA.value}:
            raise StageTransitionError("use the dedicated QA or approval method for this stage")
        session.stages.append(stage)
        session.current_stage = stage.name
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

    def record_artifact(self, session: SongSession, artifact: ArtifactRecord) -> SongSession:
        if session.status != SessionStatus.RENDER_APPROVED.value:
            raise ArtifactPersistenceError("artifact recording requires render approval")
        if session.render_request is None:
            raise ArtifactPersistenceError("artifact recording requires a render request")
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

    def _next_stage(self, session: SongSession) -> StageName:
        sequence = [StageName.BRIEF, StageName.MUSICAL_DIRECTION]
        if not session.instrumental_only:
            sequence.insert(1, StageName.PERSONA)
            sequence.append(StageName.LYRICS_OR_PROMPT)
        else:
            sequence.append(StageName.INSTRUMENTAL_PROMPT)
        sequence.append(StageName.QA)
        if not session.stages:
            return StageName.BRIEF
        try:
            index = sequence.index(StageName(session.current_stage))
        except ValueError as exc:
            raise StageTransitionError("session current_stage is not in the workflow") from exc
        if index + 1 >= len(sequence):
            raise StageTransitionError("session has no next creative stage")
        return sequence[index + 1]

    def _persist(self, session: SongSession) -> None:
        expected_revision = session.revision
        session.revision += 1
        try:
            self._store.save(session, expected_revision=expected_revision)
        except Exception:
            session.revision = expected_revision
            raise
