"""Typed, JSON-safe contracts for governed music-production sessions."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any


class StageName(str, Enum):
    BRIEF = "brief"
    PERSONA = "persona"
    MUSICAL_DIRECTION = "musical_direction"
    LYRICS_OR_PROMPT = "lyrics_or_prompt"
    INSTRUMENTAL_PROMPT = "instrumental_prompt"
    QA = "qa"
    RENDER = "render"
    ARTIFACT_QA = "artifact_qa"


class SessionStatus(str, Enum):
    DRAFT = "draft"
    IN_PROGRESS = "in_progress"
    QA_FAILED = "qa_failed"
    QA_PASSED = "qa_passed"
    RENDER_APPROVED = "render_approved"
    RENDERING = "rendering"
    COMPLETED = "completed"
    BLOCKED = "blocked"


def _required(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _finite_duration(value: float, field_name: str, *, maximum: float | None = None) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f"{field_name} must be finite")
    if value <= 0:
        raise ValueError(f"{field_name} must be greater than zero")
    if maximum is not None and value > maximum:
        raise ValueError(f"{field_name} must be at most {maximum:g}")
    return float(value)


def _non_negative_revision(value: int, field_name: str = "revision") -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def _string_list(values: list[str], field_name: str) -> list[str]:
    if not isinstance(values, list) or any(not isinstance(item, str) or not item.strip() for item in values):
        raise ValueError(f"{field_name} must be a list of non-empty strings")
    return [item.strip() for item in values]


def _enum_value(value: Enum | str, enum_type: type[Enum], field_name: str) -> str:
    if isinstance(value, enum_type):
        return value.value
    try:
        return enum_type(value).value
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} is invalid") from exc


@dataclass
class ProvenanceRecord:
    provider: str
    model: str | None
    request_id: str | None
    created_at: str
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.provider = _required(self.provider, "provider")
        self.source = _required(self.source, "source")
        if not isinstance(self.metadata, dict):
            raise ValueError("metadata must be a dict")
        self.metadata = deepcopy(self.metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "request_id": self.request_id,
            "created_at": self.created_at,
            "source": self.source,
            "metadata": deepcopy(self.metadata),
        }


@dataclass
class CreativeStage:
    name: StageName | str
    status: str
    revision: int
    content: dict[str, Any]
    updated_at: str
    provenance: list[ProvenanceRecord] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.name = _enum_value(self.name, StageName, "name")
        self.status = _required(self.status, "status")
        self.revision = _non_negative_revision(self.revision)
        if not isinstance(self.content, dict):
            raise ValueError("content must be a dict")
        if not isinstance(self.provenance, list) or any(not isinstance(item, ProvenanceRecord) for item in self.provenance):
            raise ValueError("provenance must be a list of ProvenanceRecord")
        self.content = deepcopy(self.content)
        self.provenance = list(self.provenance)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "revision": self.revision,
            "content": deepcopy(self.content),
            "updated_at": self.updated_at,
            "provenance": [item.to_dict() for item in self.provenance],
        }


@dataclass
class QAReport:
    passed: bool
    score: float | None
    findings: list[str]
    required_revisions: list[str]
    created_at: str

    def __post_init__(self) -> None:
        if not isinstance(self.passed, bool):
            raise ValueError("passed must be a bool")
        if self.score is not None and (
            not isinstance(self.score, (int, float))
            or isinstance(self.score, bool)
            or not math.isfinite(self.score)
            or not 0 <= self.score <= 100
        ):
            raise ValueError("score must be None or a finite number from 0 to 100")
        self.findings = _string_list(self.findings, "findings")
        self.required_revisions = _string_list(self.required_revisions, "required_revisions")

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "score": self.score,
            "findings": list(self.findings),
            "required_revisions": list(self.required_revisions),
            "created_at": self.created_at,
        }


@dataclass
class RenderRequest:
    session_id: str
    backend: str
    duration_seconds: float
    idempotency_key: str
    approved_at: str
    approved_by: str
    parameters: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.session_id = _required(self.session_id, "session_id")
        self.backend = _required(self.backend, "backend")
        self.duration_seconds = _finite_duration(self.duration_seconds, "duration_seconds", maximum=300)
        self.idempotency_key = _required(self.idempotency_key, "idempotency_key")
        self.approved_at = _required(self.approved_at, "approved_at")
        self.approved_by = _required(self.approved_by, "approved_by")
        if not isinstance(self.parameters, dict):
            raise ValueError("parameters must be a dict")
        self.parameters = deepcopy(self.parameters)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "backend": self.backend,
            "duration_seconds": self.duration_seconds,
            "idempotency_key": self.idempotency_key,
            "approved_at": self.approved_at,
            "approved_by": self.approved_by,
            "parameters": deepcopy(self.parameters),
        }


@dataclass
class ArtifactRecord:
    artifact_id: str
    kind: str
    uri: str
    mime_type: str
    checksum: str | None
    duration_seconds: float
    created_at: str
    provenance: list[ProvenanceRecord] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.artifact_id = _required(self.artifact_id, "artifact_id")
        self.kind = _required(self.kind, "kind")
        self.uri = _required(self.uri, "uri")
        self.mime_type = _required(self.mime_type, "mime_type")
        self.duration_seconds = _finite_duration(self.duration_seconds, "duration_seconds")
        if not isinstance(self.provenance, list) or any(not isinstance(item, ProvenanceRecord) for item in self.provenance):
            raise ValueError("provenance must be a list of ProvenanceRecord")
        self.provenance = list(self.provenance)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "kind": self.kind,
            "uri": self.uri,
            "mime_type": self.mime_type,
            "checksum": self.checksum,
            "duration_seconds": self.duration_seconds,
            "created_at": self.created_at,
            "provenance": [item.to_dict() for item in self.provenance],
        }


@dataclass
class ArtifactQAReport:
    passed: bool
    checksum_verified: bool
    duration_verified: bool
    findings: list[str]
    created_at: str

    def __post_init__(self) -> None:
        if not isinstance(self.passed, bool):
            raise ValueError("passed must be a bool")
        if not isinstance(self.checksum_verified, bool):
            raise ValueError("checksum_verified must be a bool")
        if not isinstance(self.duration_verified, bool):
            raise ValueError("duration_verified must be a bool")
        self.findings = _string_list(self.findings, "findings")

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checksum_verified": self.checksum_verified,
            "duration_verified": self.duration_verified,
            "findings": list(self.findings),
            "created_at": self.created_at,
        }


@dataclass
class ProviderHealthReport:
    provider: str
    base_url: str
    reachable: bool
    status_code: int | None
    version: str | None
    details: dict[str, Any]
    checked_at: str

    def __post_init__(self) -> None:
        self.provider = _required(self.provider, "provider")
        self.base_url = _required(self.base_url, "base_url")
        if not isinstance(self.reachable, bool):
            raise ValueError("reachable must be a bool")
        if self.status_code is not None and (
            not isinstance(self.status_code, int) or isinstance(self.status_code, bool) or self.status_code < 100
        ):
            raise ValueError("status_code must be None or an integer >= 100")
        if not isinstance(self.details, dict):
            raise ValueError("details must be a dict")
        self.details = deepcopy(self.details)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "base_url": self.base_url,
            "reachable": self.reachable,
            "status_code": self.status_code,
            "version": self.version,
            "details": deepcopy(self.details),
            "checked_at": self.checked_at,
        }


@dataclass
class RenderPlan:
    profile_id: str
    profile_label: str
    workflow: str
    kind: str
    duration_requested: int
    duration_effective: int
    filename_prefix: str
    tags: str
    lyrics_or_prompt: str
    bpm: int | None
    keyscale: str | None
    parameters: dict[str, Any]
    side_effects: list[str]
    instrumental_only: bool = False

    def __post_init__(self) -> None:
        self.profile_id = _required(self.profile_id, "profile_id")
        self.profile_label = _required(self.profile_label, "profile_label")
        self.workflow = _required(self.workflow, "workflow")
        self.kind = _required(self.kind, "kind")
        self.filename_prefix = _required(self.filename_prefix, "filename_prefix")
        self.tags = self.tags or ""
        self.lyrics_or_prompt = self.lyrics_or_prompt or ""
        if not isinstance(self.duration_requested, int) or isinstance(self.duration_requested, bool) or self.duration_requested <= 0:
            raise ValueError("duration_requested must be a positive integer")
        if not isinstance(self.duration_effective, int) or isinstance(self.duration_effective, bool) or self.duration_effective <= 0:
            raise ValueError("duration_effective must be a positive integer")
        if self.bpm is not None and (not isinstance(self.bpm, int) or isinstance(self.bpm, bool) or self.bpm <= 0):
            raise ValueError("bpm must be None or a positive integer")
        if not isinstance(self.parameters, dict):
            raise ValueError("parameters must be a dict")
        if not isinstance(self.side_effects, list) or any(not isinstance(item, str) for item in self.side_effects):
            raise ValueError("side_effects must be a list of strings")

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "profile_label": self.profile_label,
            "workflow": self.workflow,
            "kind": self.kind,
            "duration_requested": self.duration_requested,
            "duration_effective": self.duration_effective,
            "filename_prefix": self.filename_prefix,
            "tags": self.tags,
            "lyrics_or_prompt": self.lyrics_or_prompt,
            "bpm": self.bpm,
            "keyscale": self.keyscale,
            "instrumental_only": self.instrumental_only,
            "parameters": deepcopy(self.parameters),
            "side_effects": list(self.side_effects),
        }


@dataclass
class SongSession:
    session_id: str
    use_case: str
    genre: str
    title: str | None
    instrumental_only: bool
    status: SessionStatus | str
    current_stage: StageName | str
    revision: int
    generation_seed: int | None = None
    stages: list[CreativeStage] = field(default_factory=list)
    latest_qa: QAReport | None = None
    render_request: RenderRequest | None = None
    artifact: ArtifactRecord | None = None
    artifact_qa: ArtifactQAReport | None = None
    render_claimed_at: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        self.session_id = _required(self.session_id, "session_id")
        self.use_case = _required(self.use_case, "use_case")
        self.genre = _required(self.genre, "genre")
        if self.title is not None and not isinstance(self.title, str):
            raise ValueError("title must be a string or None")
        if not isinstance(self.instrumental_only, bool):
            raise ValueError("instrumental_only must be a bool")
        if self.generation_seed is not None and (
            not isinstance(self.generation_seed, int)
            or isinstance(self.generation_seed, bool)
            or self.generation_seed < 0
        ):
            raise ValueError("generation_seed must be None or a non-negative integer")
        self.status = _enum_value(self.status, SessionStatus, "status")
        self.current_stage = _enum_value(self.current_stage, StageName, "current_stage")
        self.revision = _non_negative_revision(self.revision)
        if not isinstance(self.stages, list) or any(not isinstance(item, CreativeStage) for item in self.stages):
            raise ValueError("stages must be a list of CreativeStage")
        if self.instrumental_only and any(item.name == StageName.LYRICS_OR_PROMPT.value for item in self.stages):
            raise ValueError("instrumental sessions cannot contain a lyrics_or_prompt stage")
        if self.latest_qa is not None and not isinstance(self.latest_qa, QAReport):
            raise ValueError("latest_qa must be a QAReport or None")
        if self.render_request is not None and not isinstance(self.render_request, RenderRequest):
            raise ValueError("render_request must be a RenderRequest or None")
        if self.artifact is not None and not isinstance(self.artifact, ArtifactRecord):
            raise ValueError("artifact must be an ArtifactRecord or None")
        if self.artifact_qa is not None and not isinstance(self.artifact_qa, ArtifactQAReport):
            raise ValueError("artifact_qa must be an ArtifactQAReport or None")
        if self.render_claimed_at is not None and not isinstance(self.render_claimed_at, str):
            raise ValueError("render_claimed_at must be a string or None")
        self.stages = list(self.stages)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "use_case": self.use_case,
            "genre": self.genre,
            "title": self.title,
            "instrumental_only": self.instrumental_only,
            "status": self.status,
            "current_stage": self.current_stage,
            "revision": self.revision,
            "generation_seed": self.generation_seed,
            "stages": [item.to_dict() for item in self.stages],
            "latest_qa": self.latest_qa.to_dict() if self.latest_qa else None,
            "render_request": self.render_request.to_dict() if self.render_request else None,
            "artifact": self.artifact.to_dict() if self.artifact else None,
            "artifact_qa": self.artifact_qa.to_dict() if self.artifact_qa else None,
            "render_claimed_at": self.render_claimed_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
