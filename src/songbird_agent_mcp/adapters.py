"""Provider-neutral adapter interfaces and deterministic test doubles."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
import math
import re
import subprocess
import tempfile
import time
from typing import Any, Protocol, runtime_checkable
from pathlib import Path
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4
import wave

from .contracts import (
    ArtifactRecord,
    ArtifactQAReport,
    CreativeStage,
    ProvenanceRecord,
    ProviderHealthReport,
    QAReport,
    RenderPlan,
    RenderRequest,
    SessionStatus,
    SongSession,
    StageName,
)


class AdapterError(RuntimeError):
    """Base error for adapter failures."""


class AdapterNotConfigured(AdapterError):
    """Raised when a requested provider adapter is not registered."""


class OllamaAdapterError(AdapterError):
    """Raised when the Ollama musical-direction contract fails."""


class OpenAICompatibleAdapterError(AdapterError):
    """Raised when an OpenAI-compatible musical-direction contract fails."""


class GeniusAPIAdapter:
    """Read-only Genius API client returning metadata rather than lyric text."""

    def __init__(self, client_id: str = "", client_secret: str = "", *, access_token: str | None = None, timeout: float = 30.0, opener=urlopen) -> None:
        if not access_token and (not client_id.strip() or not client_secret.strip()):
            raise ValueError("Genius access token or client credentials must be provided")
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self.timeout = timeout
        self._opener = opener
        self._access_token: str | None = access_token.strip() if access_token else None
        self.base_url = "https://api.genius.com"

    def _request_json(self, request: Request) -> dict[str, Any]:
        try:
            with self._opener(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Genius response must be a JSON object")
            return payload
        except (HTTPError, URLError, TimeoutError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AdapterError(f"Genius API request failed: {exc}") from exc

    def _token(self) -> str:
        if self._access_token:
            return self._access_token
        body = urlencode({
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }).encode("utf-8")
        payload = self._request_json(Request(
            f"{self.base_url}/oauth/token",
            data=body,
            headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        ))
        token = payload.get("access_token")
        if not isinstance(token, str) or not token.strip():
            raise AdapterError("Genius token response did not include access_token")
        self._access_token = token
        return token

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query = f"?{urlencode(params)}" if params else ""
        return self._request_json(Request(
            f"{self.base_url}{path}{query}",
            headers={"Accept": "application/json", "Authorization": f"Bearer {self._token()}"},
            method="GET",
        ))

    @staticmethod
    def _song_metadata(song: dict[str, Any]) -> dict[str, Any]:
        artist = song.get("primary_artist") or {}
        return {
            "id": song.get("id"),
            "title": song.get("title"),
            "url": song.get("url"),
            "artist": artist.get("name") if isinstance(artist, dict) else None,
            "artist_id": artist.get("id") if isinstance(artist, dict) else None,
            "album": (song.get("album") or {}).get("name") if isinstance(song.get("album"), dict) else None,
            "release_date": song.get("release_date_for_display"),
            "lyrics_state": song.get("lyrics_state"),
        }

    def search(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        if not query.strip():
            raise ValueError("Genius search query must be non-empty")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 20:
            raise ValueError("Genius search limit must be an integer from 1 to 20")
        payload = self._get("/search", {"q": query.strip(), "per_page": limit})
        hits = (payload.get("response") or {}).get("hits", [])
        if not isinstance(hits, list):
            raise AdapterError("Genius search response did not contain hits")
        return [self._song_metadata(hit.get("result", {})) for hit in hits if isinstance(hit, dict) and isinstance(hit.get("result"), dict)][:limit]

    def get_song(self, song_id: int) -> dict[str, Any]:
        if not isinstance(song_id, int) or isinstance(song_id, bool) or song_id <= 0:
            raise ValueError("Genius song_id must be a positive integer")
        payload = self._get(f"/songs/{song_id}")
        song = (payload.get("response") or {}).get("song")
        if not isinstance(song, dict):
            raise AdapterError("Genius song response did not contain a song")
        return self._song_metadata(song)


class LightRAGQueryAdapter:
    """Read-only context-only query adapter for the Songbird lyric corpus."""

    def __init__(self, base_url: str, *, api_key: str | None = None, timeout: float = 180.0, opener=urlopen) -> None:
        if not base_url.strip():
            raise ValueError("LightRAG base_url must be non-empty")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or ""
        self.timeout = timeout
        self._opener = opener

    def query(self, query: str, *, mode: str = "mix") -> dict[str, Any]:
        if not query.strip():
            raise ValueError("LightRAG query must be non-empty")
        body = json.dumps({"query": query.strip(), "mode": mode, "only_need_context": True, "response_type": "string"}).encode("utf-8")
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        try:
            with self._opener(Request(f"{self.base_url}/query", data=body, headers=headers, method="POST"), timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("LightRAG response must be a JSON object")
        except (HTTPError, URLError, TimeoutError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AdapterError(f"LightRAG query failed: {exc}") from exc
        context = payload.get("response") or payload.get("output") or payload.get("context") or ""
        references = payload.get("references") or payload.get("ref_list") or []
        return {"context": context if isinstance(context, str) else str(context), "references": references if isinstance(references, list) else [], "mode": mode}


def _checksum_findings(recorded: str | None, digest: str) -> tuple[bool, list[str]]:
    """Compare a render-time checksum with downloaded bytes; a missing checksum fails closed."""
    findings = [f"sha256={digest}"]
    if recorded is None:
        findings.append("no render-time checksum recorded; integrity cannot be verified")
        return False, findings
    if recorded != digest:
        findings.append("artifact checksum mismatch")
        return False, findings
    return True, findings


def _read_json_response(response: Any) -> dict[str, Any]:
    if hasattr(response, "json"):
        payload = response.json()
    else:
        raw = response.read()
        payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    if not isinstance(payload, dict):
        raise AdapterError("provider response must be a JSON object")
    return payload

@runtime_checkable
class CreativeAdapter(Protocol):
    def generate(self, session: SongSession) -> CreativeStage:
        """Generate the next creative stage without persisting it."""


@runtime_checkable
class QAAdapter(Protocol):
    def evaluate(self, session: SongSession) -> QAReport:
        """Evaluate the current session without changing its state."""


@runtime_checkable
class RenderAdapter(Protocol):
    def render(self, session: SongSession, request: RenderRequest) -> ArtifactRecord:
        """Render an explicitly approved session and return an artifact record."""
        ...


@runtime_checkable
class ArtifactQAAdapter(Protocol):
    def evaluate(self, session: SongSession) -> ArtifactQAReport:
        """Verify a persisted artifact without changing session state."""
        ...


@runtime_checkable
class HealthAdapter(Protocol):
    def check(self) -> ProviderHealthReport:
        """Perform a read-only provider health check."""
        ...


class OllamaMusicDirectionAdapter:
    """Opt-in Ollama adapter matching Songbird's verified `/api/generate` contract."""

    def __init__(self, model: str, base_url: str, *, timeout: float = 60.0, opener=urlopen) -> None:
        if not model.strip():
            raise ValueError("model must be a non-empty string")
        if not base_url.strip():
            raise ValueError("base_url must be a non-empty string")
        self.model = model.strip()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._opener = opener

    def generate(self, session: SongSession) -> CreativeStage:
        prompt = (
            "Return strict JSON with fields tags (string), bpm (integer), and keyscale (string). "
            f"Create a melodic musical direction for genre {session.genre!r}, title {session.title!r}, "
            f"use case {session.use_case!r}. Instrumental only: {session.instrumental_only}."
        )
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
        }
        request = Request(
            f"{self.base_url}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener(request, timeout=self.timeout) as response:
                envelope = json.loads(response.read().decode("utf-8"))
            direction = json.loads(envelope["response"])
            tags = direction["tags"]
            bpm = int(direction["bpm"])
            keyscale = direction["keyscale"]
            if not isinstance(tags, str) or not tags.strip() or not isinstance(keyscale, str) or not keyscale.strip():
                raise ValueError("tags and keyscale must be non-empty strings")
            if bpm <= 0:
                raise ValueError("bpm must be positive")
        except (HTTPError, URLError, TimeoutError, OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise OllamaAdapterError(f"Ollama musical direction request failed: {exc}") from exc
        return CreativeStage(
            name=StageName.MUSICAL_DIRECTION,
            status="generated",
            revision=session.revision + 1,
            content={"tags": tags.strip(), "bpm": bpm, "keyscale": keyscale.strip()},
            updated_at=_now(),
            provenance=[
                ProvenanceRecord(
                    provider="ollama",
                    model=self.model,
                    request_id=None,
                    created_at=_now(),
                    source="ollama_api_generate",
                    metadata={"base_url": self.base_url},
                )
            ],
        )


class OpenAIMusicDirectionAdapter:
    """Opt-in OpenAI-compatible musical-direction adapter for `/v1/chat/completions` servers.

    Matches the verified OpenAI-compatible chat contract used by the sibling Songbird
    project: a non-streaming request with an optional bearer key and an optional
    `response_format` JSON-object hint that is retried without the hint when rejected.
    """

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str | None = None,
        *,
        timeout: float = 120.0,
        opener=urlopen,
    ) -> None:
        if not model.strip():
            raise ValueError("model must be a non-empty string")
        if not base_url.strip():
            raise ValueError("base_url must be a non-empty string")
        self.model = model.strip()
        base = base_url.rstrip("/")
        if not base.endswith("/v1"):
            base = f"{base}/v1"
        self.base_url = base
        self.api_key = api_key or None
        self.timeout = timeout
        self._opener = opener

    def generate(self, session: SongSession) -> CreativeStage:
        prompt = (
            "Return strict JSON with fields tags (string), bpm (integer), and keyscale (string). "
            f"Create a melodic musical direction for genre {session.genre!r}, title {session.title!r}, "
            f"use case {session.use_case!r}. Instrumental only: {session.instrumental_only}."
        )
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "response_format": {"type": "json_object"},
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            content = self._chat(payload, headers)
            direction = json.loads(content)
            tags = direction["tags"]
            bpm = int(direction["bpm"])
            keyscale = direction["keyscale"]
            if not isinstance(tags, str) or not tags.strip() or not isinstance(keyscale, str) or not keyscale.strip():
                raise ValueError("tags and keyscale must be non-empty strings")
            if bpm <= 0:
                raise ValueError("bpm must be positive")
        except (HTTPError, URLError, TimeoutError, OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise OpenAICompatibleAdapterError(f"OpenAI-compatible musical direction request failed: {exc}") from exc
        return CreativeStage(
            name=StageName.MUSICAL_DIRECTION,
            status="generated",
            revision=session.revision + 1,
            content={"tags": tags.strip(), "bpm": bpm, "keyscale": keyscale.strip()},
            updated_at=_now(),
            provenance=[
                ProvenanceRecord(
                    provider="openai_compatible",
                    model=self.model,
                    request_id=None,
                    created_at=_now(),
                    source="openai_chat_completions",
                    metadata={"base_url": self.base_url},
                )
            ],
        )

    def _chat(self, payload: dict[str, Any], headers: dict[str, str]) -> str:
        request = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with self._opener(request, timeout=self.timeout) as response:
                envelope = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code != 400 or "response_format" not in payload:
                raise
            fallback = dict(payload)
            fallback.pop("response_format", None)
            request = Request(
                f"{self.base_url}/chat/completions",
                data=json.dumps(fallback).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with self._opener(request, timeout=self.timeout) as response:
                envelope = json.loads(response.read().decode("utf-8"))
        try:
            content = envelope["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("response did not contain a chat completion message") from exc
        if not isinstance(content, str) or not content.strip():
            raise ValueError("chat completion content is empty")
        return content


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize_filename_prefix(value: str) -> str:
    """Return a filesystem-safe filename prefix.

    ComfyUI runs on Windows hosts where characters such as ``>``, ``:``, ``*`` and
    ``?`` are illegal in file names, so a title-derived prefix must never reach the
    save node verbatim.
    """
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value.strip())
    cleaned = cleaned.strip(" .")
    return cleaned or "songbird"


def _resolve_source_path(candidate: str, root: Path | None) -> Path:
    """Resolve a model-supplied local source path, confined to an operator-configured root.

    Render parameters are agent-controlled, so an unconfined path would let a render
    read (and upload to a remote provider) any file the server process can access.
    """
    if root is None:
        raise AdapterError("local source audio is disabled; configure SONGBIRD_SOURCE_AUDIO_ROOT")
    try:
        path = Path(candidate).resolve()
    except (OSError, ValueError) as exc:
        raise AdapterError("source audio path is invalid") from exc
    if not path.is_relative_to(Path(root).resolve()):
        raise AdapterError("source audio path is outside the allowed source root")
    if not path.is_file():
        raise AdapterError("source audio file not found under the allowed source root")
    return path


def _open_json(opener: Any, request: Request, timeout: float, context: str) -> dict[str, Any]:
    """Call an HTTP opener and decode a JSON object, mapping transport failures to AdapterError."""
    try:
        response = opener(request, timeout=timeout)
        if hasattr(response, "__enter__"):
            with response as opened:
                return _read_json_response(opened)
        return _read_json_response(response)
    except AdapterError:
        raise
    except (HTTPError, URLError, TimeoutError, OSError, TypeError, ValueError) as exc:
        raise AdapterError(f"{context} failed: {exc}") from exc


class SongbirdDryRunAdapter:
    """Map Songbird-compatible session inputs into a side-effect-free render plan."""

    _ALIASES = {
        "ace": "ace_step",
        "ace-step": "ace_step",
        "acestep": "ace_step",
        "ace_step_1.5": "ace_step",
        "ace_step_1_5": "ace_step",
        "m2m": "ace_step_m2m",
        "ace-m2m": "ace_step_m2m",
        "ace_step_m2m": "ace_step_m2m",
        "acestep_m2m": "ace_step_m2m",
        "ace_step_m2m_edit": "ace_step_m2m",
        "mix": "ffmpeg_mix",
        "layer": "ffmpeg_mix",
        "ffmpeg_mix": "ffmpeg_mix",
        "minimax": "minimax_music",
        "minimax-music": "minimax_music",
        "minimax_music_3": "minimax_music",
        "minimax_music3": "minimax_music",
        "music3": "minimax_music",
        "music_3": "minimax_music",
        "demucs": "demucs_stem",
        "htdemucs": "demucs_stem",
        "demucs_stem": "demucs_stem",
    }
    _PROFILES = {
        "ace_step": {
            "label": "ACE-Step 1.5",
            "kind": "ace_step",
            "workflow": "audio_ace_step_1_5_checkpoint.json",
            "supports_negative_text": True,
            # Songbird V2's tested ACE-Step 1.5 POP preset. The exported
            # workflow is a generic fallback (50/euler/CFG 4); leaving those
            # values untouched produces harsher, less coherent vocal renders.
            "default_steps": 16,
            "default_cfg": 1.4,
            "default_sampler": "dpmpp_2m",
            "default_scheduler": "sgm_uniform",
            "default_cfg_scale": 3.5,
            "default_min_p": 0,
            "default_negative_prompt": "low quality, glitch, distorted, clipping, harsh artifacts",
            "default_checkpoint": "ace_step_1.5_turbo_aio.safetensors",
        },
        "ace_step_m2m": {
            "label": "ACE-Step M2M Edit",
            "kind": "ace_step_m2m",
            "workflow": "audio_ace_step_m2m_edit.json",
            "supports_negative_text": False,
        },
        "ffmpeg_mix": {
            "label": "FFmpeg Layer Mix",
            "kind": "ffmpeg_mix",
            "workflow": "local_ffmpeg_mix",
            "supports_negative_text": False,
        },
        "minimax_music": {
            "label": "MiniMax Music 3",
            "kind": "minimax_music",
            "workflow": "audio_minimax_music_3.json",
            "supports_negative_text": False,
            "max_duration_cap": 300,
            "default_steps": 30,
            "default_cfg": 1.7,
            "default_cfg_scale": 1.7,
            "default_sampler": "euler",
            "default_scheduler": "simple",
            "default_top_k": 50,
        },
        "demucs_stem": {
            "label": "HTDemucs Stem Separation",
            "kind": "demucs_stem",
            "workflow": "local_demucs_stem",
            "supports_negative_text": False,
            "stems": ("drums", "bass", "other", "vocals"),
        },
    }

    @staticmethod
    def _minimax_caption(session: SongSession, direction: dict[str, Any]) -> dict[str, Any]:
        vocal_details = direction.get("vocal_details")
        if not isinstance(vocal_details, dict):
            vocal_details = {"direction": str(direction.get("vocal_direction", "")).strip()}
        arrangement = direction.get("arrangement")
        if not isinstance(arrangement, dict):
            arrangement = {"direction": str(arrangement or "").strip()}
        return {
            "Global Metadata": {
                "genre": session.genre,
                "bpm": direction.get("bpm"),
                "key": direction.get("keyscale"),
                "scale": direction.get("scale"),
            },
            "Vocal Details": vocal_details,
            "Arrangement": arrangement,
        }

    def plan(self, session: SongSession, model: str, duration: int, filename_prefix: str, generation_seed: int | None = None) -> RenderPlan:
        raw_model = model.strip().lower().replace(" ", "_")
        profile_id = self._ALIASES.get(raw_model, raw_model)
        if profile_id not in self._PROFILES:
            raise ValueError(f"unsupported Songbird audio model: {model}")
        if duration <= 0:
            raise ValueError("duration must be positive")
        if not filename_prefix or "/" in filename_prefix or "\\" in filename_prefix:
            raise ValueError("filename_prefix must be a non-empty filename-safe value")
        profile = self._PROFILES[profile_id]
        direction = next((stage.content for stage in reversed(session.stages) if stage.name == StageName.MUSICAL_DIRECTION.value), {})
        prompt_name = StageName.INSTRUMENTAL_PROMPT.value if session.instrumental_only else StageName.LYRICS_OR_PROMPT.value
        prompt = next((stage.content for stage in reversed(session.stages) if stage.name == prompt_name), {})
        tags = str(direction.get("tags", ""))
        lyrics_or_prompt = str(prompt.get("text", prompt.get("lyrics", prompt.get("prompt", ""))))
        effective_duration = min(duration, int(profile.get("max_duration_cap", 300)))
        parameters: dict[str, Any] = {
            "supports_negative_text": profile["supports_negative_text"],
            "duration_cap_applied": effective_duration != duration,
        }
        if profile_id == "ace_step":
            parameters.update({
                key: profile[key]
                for key in (
                    "default_steps",
                    "default_cfg",
                    "default_sampler",
                    "default_scheduler",
                    "default_cfg_scale",
                    "default_min_p",
                    "default_negative_prompt",
                    "default_checkpoint",
                )
            })
            parameters["generation_seed"] = generation_seed if generation_seed is not None else session.generation_seed
        elif profile_id == "minimax_music":
            parameters.update({key: profile[key] for key in ("default_steps", "default_cfg", "default_cfg_scale", "default_sampler", "default_scheduler", "default_top_k")})
            parameters["generation_mode"] = "native_long_form" if duration <= profile["max_duration_cap"] else "segmented_fallback_required"
            parameters["structured_caption"] = self._minimax_caption(session, direction)
        elif profile_id == "demucs_stem":
            parameters["stems"] = profile.get("stems", ("drums", "bass", "other", "vocals"))
        if generation_seed is not None:
            parameters["generation_seed"] = generation_seed
        return RenderPlan(
            profile_id=profile_id,
            profile_label=profile["label"],
            workflow=profile["workflow"],
            kind=profile["kind"],
            duration_requested=duration,
            duration_effective=effective_duration,
            filename_prefix=filename_prefix,
            tags=tags,
            lyrics_or_prompt=lyrics_or_prompt,
            bpm=direction.get("bpm"),
            keyscale=direction.get("keyscale"),
            instrumental_only=session.instrumental_only,
            parameters=parameters,
            side_effects=["none: dry-run mapping only; no provider call, file write, queue submission, or artifact creation"],
        )


class ComfyRenderAdapter:
    """Opt-in ComfyUI render adapter with optional in-process artifact export."""

    PROFILES = ("ace_step", "minimax_music")

    def __init__(
        self,
        base_url: str,
        workflow_root: str | Path,
        *,
        output_dir: str | Path | None = None,
        source_root: str | Path | None = None,
        timeout: float = 120.0,
        post_json=urlopen,
        get_json=urlopen,
        sleep=time.sleep,
    ) -> None:
        if not base_url.strip():
            raise ValueError("base_url must be a non-empty string")
        self.base_url = base_url.rstrip("/")
        self.workflow_root = Path(workflow_root)
        self.output_dir = Path(output_dir) if output_dir is not None else None
        self.source_root = Path(source_root) if source_root is not None else None
        self.timeout = timeout
        self._post_json = post_json
        self._get_json = get_json
        self._sleep = sleep
        self._dry_run = SongbirdDryRunAdapter()

    def render(self, session: SongSession, request: RenderRequest) -> ArtifactRecord:
        if session.status != SessionStatus.RENDER_APPROVED.value:
            raise AdapterError("ComfyUI render requires render_approved session")
        if request.session_id != session.session_id or session.render_request != request:
            raise AdapterError("render request identity or idempotency key mismatch")
        plan = self._dry_run.plan(
            session,
            request.backend,
            int(request.duration_seconds),
            _sanitize_filename_prefix(session.title or "songbird"),
            generation_seed=request.parameters.get("seed", session.generation_seed),
        )
        if plan.profile_id not in self.PROFILES:
            raise AdapterError(f"ComfyUI render does not handle backend {request.backend!r}; use its dedicated adapter")
        if plan.parameters.get("supports_negative_text") and request.parameters.get("negative_prompt"):
            plan.parameters["negative_prompt"] = request.parameters["negative_prompt"]
        workflow_path = self.workflow_root / plan.workflow
        try:
            prompt = json.loads(workflow_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AdapterError(f"workflow template unavailable: {workflow_path}") from exc
        self._apply_plan(prompt, plan)
        prompt_id = self._submit_prompt(prompt, session.session_id)
        history = self._wait_for_history(prompt_id)
        file_info = self._extract_file(history, plan.profile_id)
        uri = self._view_uri(file_info)
        data = self._fetch_artifact(uri)
        local_path = self._export_local_artifact(data, file_info, plan.filename_prefix, prompt_id)
        extension = str(file_info.get("filename", "")).lower().rsplit(".", 1)[-1]
        mime_type = {"mp3": "audio/mpeg", "wav": "audio/wav", "flac": "audio/flac"}.get(extension, "audio/octet-stream")
        return ArtifactRecord(
            artifact_id=f"comfy-{prompt_id}",
            kind="audio",
            uri=uri,
            mime_type=mime_type,
            checksum=sha256(data).hexdigest(),
            duration_seconds=request.duration_seconds,
            created_at=_now(),
            provenance=[ProvenanceRecord(provider="comfyui", model=plan.profile_id, request_id=prompt_id, created_at=_now(), source="comfy_render_adapter", metadata={"local_path": str(local_path) if local_path else None})],
        )

    def _submit_prompt(self, prompt: dict[str, Any], client_id: str) -> str:
        payload = _open_json(
            self._post_json,
            Request(f"{self.base_url}/prompt", data=json.dumps({"prompt": prompt, "client_id": client_id}).encode(), headers={"Content-Type": "application/json"}, method="POST"),
            self.timeout,
            "ComfyUI prompt submission",
        )
        prompt_id = payload.get("prompt_id")
        if not prompt_id:
            raise AdapterError("ComfyUI response did not include prompt_id")
        return str(prompt_id)

    def _view_uri(self, file_info: dict[str, Any]) -> str:
        query = urlencode({key: file_info.get(key, "") for key in ("filename", "subfolder", "type")})
        return f"{self.base_url}/view?{query}"

    def _fetch_artifact(self, uri: str) -> bytes:
        """Download the rendered bytes so the artifact carries a render-time checksum."""
        data = self._fetch_bytes(uri)
        if not data:
            raise AdapterError("ComfyUI artifact download returned no bytes")
        return data

    def _export_local_artifact(self, data: bytes, file_info: dict[str, Any], filename_prefix: str, prompt_id: str) -> Path | None:
        if self.output_dir is None:
            return None
        suffix = Path(str(file_info.get("filename", "song.mp3"))).suffix.lower() or ".mp3"
        target = self.output_dir / f"{_sanitize_filename_prefix(filename_prefix)}_{prompt_id}{suffix}"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def _apply_plan(self, prompt: dict[str, Any], plan: RenderPlan) -> None:
        if plan.profile_id == "minimax_music":
            if "37:38" in prompt and plan.parameters.get("generation_seed") is not None:
                prompt["37:38"]["inputs"]["seed"] = int(plan.parameters["generation_seed"])
            if "37:13" in prompt:
                inputs = prompt["37:13"]["inputs"]
                caption = plan.parameters.get("structured_caption") or {"tags": plan.tags, "bpm": plan.bpm or 120, "keyscale": plan.keyscale or "C major"}
                # Instrumental sessions must not feed the verbal prompt into the
                # lyrics field: MiniMax sings the lyrics field's text, which would
                # produce vocals that "sing the musical instructions." Keep the
                # description in the caption only, and pass empty lyrics.
                lyrics = "" if plan.instrumental_only else plan.lyrics_or_prompt
                inputs.update({"caption": json.dumps(caption, ensure_ascii=False), "lyrics": lyrics, "max_duration": plan.duration_effective, "cfg_scale": plan.parameters["default_cfg_scale"], "top_k": plan.parameters["default_top_k"]})
            if "37:9" in prompt:
                prompt["37:9"]["inputs"].update({"steps": plan.parameters["default_steps"], "cfg": plan.parameters["default_cfg"], "sampler_name": plan.parameters["default_sampler"], "scheduler": plan.parameters["default_scheduler"]})
            if "35" in prompt:
                prompt["35"]["inputs"]["filename_prefix"] = f"audio/{plan.filename_prefix}"
        else:
            if "3" in prompt:
                inputs = prompt["3"]["inputs"]
                if plan.parameters.get("generation_seed") is not None:
                    inputs["seed"] = int(plan.parameters["generation_seed"])
                inputs.update({
                    "steps": plan.parameters["default_steps"],
                    "cfg": plan.parameters["default_cfg"],
                    "sampler_name": plan.parameters["default_sampler"],
                    "scheduler": plan.parameters["default_scheduler"],
                })
            if "94" in prompt:
                prompt["94"]["inputs"].update({
                    "tags": plan.tags,
                    "lyrics": "" if plan.instrumental_only else plan.lyrics_or_prompt,
                    "bpm": plan.bpm or 120,
                    "duration": plan.duration_effective,
                    "keyscale": plan.keyscale or "C major",
                    "min_p": plan.parameters.get("default_min_p", 0),
                    "cfg_scale": plan.parameters.get("default_cfg_scale", 3.5),
                })
                if plan.parameters.get("generation_seed") is not None:
                    prompt["94"]["inputs"]["seed"] = int(plan.parameters["generation_seed"])
            if "7" in prompt:
                negative = plan.parameters.get("negative_prompt") or plan.parameters.get("default_negative_prompt", "")
                prompt["7"]["inputs"]["text"] = negative
            if "98" in prompt:
                prompt["98"]["inputs"]["seconds"] = plan.duration_effective
            if "104" in prompt:
                prompt["104"]["inputs"]["filename_prefix"] = f"audio/{plan.filename_prefix}"

    def _wait_for_history(self, prompt_id: str) -> dict[str, Any]:
        # Bound total wall-clock time, not just the attempt count: each poll's own
        # timeout is clipped to whatever remains of the overall budget.
        attempts = max(1, math.ceil(self.timeout / 5) + 1)
        deadline = time.monotonic() + self.timeout
        for _ in range(attempts):
            remaining = max(1.0, deadline - time.monotonic())
            history = _open_json(
                self._get_json,
                Request(f"{self.base_url}/history/{prompt_id}", method="GET"),
                min(self.timeout, remaining),
                "ComfyUI history poll",
            )
            if prompt_id in history:
                return history[prompt_id]
            if time.monotonic() >= deadline:
                break
            self._sleep(5)
        raise AdapterError(f"ComfyUI history timeout for prompt {prompt_id}")

    @staticmethod
    def _extract_file(history: dict[str, Any], profile_id: str) -> dict[str, Any]:
        outputs = history.get("outputs", {})
        preferred = ["35"] if profile_id == "minimax_music" else ["104"]
        for node_id in preferred + list(outputs):
            output = outputs.get(node_id, {})
            files = output.get("audio", []) if isinstance(output, dict) else []
            if files and isinstance(files[0], dict) and files[0].get("filename"):
                return files[0]
        raise AdapterError("ComfyUI history did not contain an audio file")

    def _fetch_bytes(self, uri: str) -> bytes:
        if not uri.startswith(f"{self.base_url}/"):
            return b""
        try:
            with self._get_json(Request(uri, method="GET"), timeout=self.timeout) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError, OSError):
            return b""


class ComfyM2MRenderAdapter(ComfyRenderAdapter):
    """ACE-Step music-to-music style-transfer pass over an existing audio artifact.

    Downloads the approved source audio, uploads it into the ComfyUI input folder,
    then runs the M2M edit workflow (LoadAudio -> VAEEncodeAudio -> KSampler with a
    low denoise) driven by the session's tags/lyrics as the new style caption.
    """

    M2M_WORKFLOW = "audio_ace_step_m2m_edit.json"
    _DEFAULTS = {
        "denoise": 0.3,
        "lyrics_strength": 0.99,
        "steps": 50,
        "cfg": 5.0,
        "sampler_name": "euler",
        "scheduler": "simple",
    }

    def render(self, session: SongSession, request: RenderRequest) -> ArtifactRecord:
        if session.status != SessionStatus.RENDER_APPROVED.value:
            raise AdapterError("ComfyUI M2M render requires render_approved session")
        if request.session_id != session.session_id or session.render_request != request:
            raise AdapterError("render request identity or idempotency key mismatch")
        if request.backend.lower().replace(" ", "_") not in ("ace_step_m2m", "m2m"):
            raise AdapterError("ComfyUI M2M render requires backend ace_step_m2m")
        plan = self._dry_run.plan(
            session,
            "ace_step_m2m",
            int(request.duration_seconds),
            _sanitize_filename_prefix(session.title or "songbird-m2m"),
            generation_seed=request.parameters.get("seed", session.generation_seed),
        )
        source_uri = request.parameters.get("source_audio_uri")
        if not source_uri or not isinstance(source_uri, str):
            raise AdapterError("M2M render requires parameters.source_audio_uri")
        if source_uri.startswith(f"{self.base_url}/view?"):
            source_bytes = self._fetch_bytes(source_uri)
        else:
            try:
                source_bytes = _resolve_source_path(source_uri, self.source_root).read_bytes()
            except OSError as exc:
                raise AdapterError(f"M2M source audio could not be read: {exc}") from exc
        if not source_bytes:
            raise AdapterError("M2M source audio download returned no bytes")
        input_filename = self._upload_input(source_bytes)
        workflow_path = self.workflow_root / self.M2M_WORKFLOW
        try:
            prompt = json.loads(workflow_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AdapterError(f"M2M workflow template unavailable: {workflow_path}") from exc
        parameters = dict(request.parameters)
        if "seed" not in parameters and session.generation_seed is not None:
            parameters["seed"] = session.generation_seed
        self._apply_m2m(prompt, plan, parameters, input_filename)
        prompt_id = self._submit_prompt(prompt, session.session_id)
        history = self._wait_for_history(prompt_id)
        file_info = self._extract_m2m_file(history)
        uri = self._view_uri(file_info)
        data = self._fetch_artifact(uri)
        return ArtifactRecord(
            artifact_id=f"comfy-m2m-{prompt_id}",
            kind="audio",
            uri=uri,
            mime_type="audio/mpeg",
            checksum=sha256(data).hexdigest(),
            duration_seconds=request.duration_seconds,
            created_at=_now(),
            provenance=[ProvenanceRecord(
                provider="comfyui",
                model="ace_step_m2m",
                request_id=prompt_id,
                created_at=_now(),
                source="comfy_m2m_render_adapter",
                metadata={"source_audio_uri": source_uri, "denoise": request.parameters.get("denoise")},
            )],
        )

    def _upload_input(self, source_bytes: bytes) -> str:
        """POST the source audio into ComfyUI's input folder and return its filename."""
        boundary = "----SongbirdM2M" + uuid4().hex
        filename = f"m2m_source_{uuid4().hex[:8]}.mp3"
        body = b"".join([
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{filename}\"\r\nContent-Type: audio/mpeg\r\n\r\n".encode("utf-8"),
            source_bytes,
            f"\r\n--{boundary}--\r\n".encode("utf-8"),
        ])
        request = Request(
            f"{self.base_url}/upload/image",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with self._post_json(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            raise AdapterError(f"M2M input upload failed: {exc}") from exc
        name = payload.get("name")
        if not name:
            raise AdapterError("M2M input upload response did not include a filename")
        return str(name)

    def _apply_m2m(self, prompt: dict[str, Any], plan: RenderPlan, parameters: dict[str, Any], input_filename: str) -> None:
        if "14" in prompt:
            inputs = prompt["14"]["inputs"]
            inputs["tags"] = plan.tags
            # M2M also vocalizes the lyrics field; keep it empty for
            # instrumental edits so the style caption tags drive the pass.
            inputs["lyrics"] = "" if plan.instrumental_only else plan.lyrics_or_prompt
            inputs["lyrics_strength"] = float(parameters.get("lyrics_strength", self._DEFAULTS["lyrics_strength"]))
        if "52" in prompt:
            inputs = prompt["52"]["inputs"]
            for key in ("steps", "cfg", "sampler_name", "scheduler"):
                if key in parameters:
                    inputs[key] = parameters[key]
            inputs.setdefault("steps", self._DEFAULTS["steps"])
            inputs.setdefault("cfg", self._DEFAULTS["cfg"])
            inputs.setdefault("sampler_name", self._DEFAULTS["sampler_name"])
            inputs.setdefault("scheduler", self._DEFAULTS["scheduler"])
            if "denoise" in parameters:
                inputs["denoise"] = float(parameters["denoise"])
            if "seed" in parameters:
                inputs["seed"] = int(parameters["seed"])
        if "64" in prompt:
            prompt["64"]["inputs"]["audio"] = input_filename
        if "59" in prompt:
            prompt["59"]["inputs"]["filename_prefix"] = f"audio/{plan.filename_prefix}"

    @staticmethod
    def _extract_m2m_file(history: dict[str, Any]) -> dict[str, Any]:
        outputs = history.get("outputs", {})
        for node_id in ("59", "104", "35"):
            output = outputs.get(node_id, {})
            files = output.get("audio", []) if isinstance(output, dict) else []
            if files and isinstance(files[0], dict) and files[0].get("filename"):
                return files[0]
        raise AdapterError("ComfyUI history did not contain an M2M audio file")


class ComfyMixRenderAdapter(ComfyRenderAdapter):
    """Layer several ComfyUI audio artifacts together with local ffmpeg.

    Downloads each ``parameters.source_audio_uris`` /view handle, mixes them with
    optional per-track gains, re-encodes to MP3, and uploads the result into the
    ComfyUI output folder so the returned artifact remains a normal verified
    ``/view`` handle. No workflow submission is required.
    """

    _ALIASES = {"ffmpeg_mix": "ffmpeg_mix", "mix": "ffmpeg_mix", "layer": "ffmpeg_mix"}

    def __init__(
        self,
        base_url: str,
        workflow_root: str | Path,
        *,
        timeout: float = 300.0,
        ffmpeg: str = "/usr/bin/ffmpeg",
        post_json=urlopen,
        get_json=urlopen,
        sleep=time.sleep,
        run_cmd=subprocess.run,
    ) -> None:
        super().__init__(base_url, workflow_root, timeout=timeout, post_json=post_json, get_json=get_json, sleep=sleep)
        self._ffmpeg = ffmpeg
        self._run_cmd = run_cmd

    def render(self, session: SongSession, request: RenderRequest) -> ArtifactRecord:
        if session.status != SessionStatus.RENDER_APPROVED.value:
            raise AdapterError("ComfyUI mix render requires render_approved session")
        if request.session_id != session.session_id or session.render_request != request:
            raise AdapterError("render request identity or idempotency key mismatch")
        if self._ALIASES.get(request.backend.lower().replace(" ", "_")) != "ffmpeg_mix":
            raise AdapterError("ComfyUI mix render requires backend ffmpeg_mix")
        source_uris = request.parameters.get("source_audio_uris")
        if not isinstance(source_uris, list) or len(source_uris) < 2:
            raise AdapterError("mix render requires parameters.source_audio_uris with at least two entries")
        if not all(isinstance(uri, str) and uri.startswith(f"{self.base_url}/view?") for uri in source_uris):
            raise AdapterError("mix source_audio_uris must be /view handles on the configured ComfyUI instance")
        gains = request.parameters.get("gains")
        if gains is not None and not (isinstance(gains, list) and len(gains) == len(source_uris) and all(isinstance(g, (int, float)) for g in gains)):
            raise AdapterError("parameters.gains must be a numeric list matching source_audio_uris")
        mode = request.parameters.get("mode", "mix")
        if mode not in ("mix", "concat", "concat_crossfade"):
            raise AdapterError("parameters.mode must be 'mix', 'concat', or 'concat_crossfade'")
        crossfade_seconds = request.parameters.get("crossfade_seconds", 1.0)
        if mode == "concat_crossfade" and (
            not isinstance(crossfade_seconds, (int, float))
            or isinstance(crossfade_seconds, bool)
            or not 0.01 <= float(crossfade_seconds) <= 5.0
        ):
            raise AdapterError("parameters.crossfade_seconds must be between 0.01 and 5.0 seconds")
        payloads = [self._fetch_bytes(uri) for uri in source_uris]
        if not all(payloads):
            raise AdapterError("one or more mix source audio downloads returned no bytes")
        mixed = self._mix(payloads, gains, mode=mode, crossfade_seconds=float(crossfade_seconds))
        if not mixed:
            raise AdapterError("ffmpeg mix produced no output")
        uploaded = self._upload_output(mixed, _sanitize_filename_prefix(session.title or "songbird-mix"))
        return ArtifactRecord(
            artifact_id=f"comfy-mix-{uuid4().hex}",
            kind="audio",
            uri=self._view_uri(uploaded),
            mime_type="audio/mpeg",
            checksum=sha256(mixed).hexdigest(),
            duration_seconds=request.duration_seconds,
            created_at=_now(),
            provenance=[ProvenanceRecord(
                provider="comfyui",
                model="ffmpeg_mix",
                request_id=None,
                created_at=_now(),
                source="comfy_mix_render_adapter",
                metadata={"source_audio_uris": list(source_uris), "gains": list(gains) if gains else None, "mode": mode, "crossfade_seconds": float(crossfade_seconds) if mode == "concat_crossfade" else None},
            )],
        )

    def _mix(self, payloads: list[bytes], gains: list[float] | None, *, mode: str = "mix", crossfade_seconds: float = 1.0) -> bytes | None:
        with tempfile.TemporaryDirectory(prefix="songbird_mix_") as workdir:
            return self._mix_in(Path(workdir), payloads, gains, mode=mode, crossfade_seconds=crossfade_seconds)

    def _mix_in(self, workdir: Path, payloads: list[bytes], gains: list[float] | None, *, mode: str, crossfade_seconds: float) -> bytes | None:
        inputs: list[str] = []
        filter_parts: list[str] = []
        for index, payload in enumerate(payloads):
            path = workdir / f"mix_in_{index}.mp3"
            path.write_bytes(payload)
            inputs.append(str(path))
            label = f"mix{index}"
            if gains and gains[index] != 1.0:
                filter_parts.append(f"[{index}:a]volume={float(gains[index]):.4f}[{label}]")
            else:
                filter_parts.append(f"[{index}:a]anull[{label}]")
        labels = "".join(f"[mix{i}]" for i in range(len(payloads)))
        if mode == "concat":
            filter_parts.append(f"{labels}concat=n={len(payloads)}:v=0:a=1[out]")
        elif mode == "concat_crossfade":
            previous = "mix0"
            for index in range(1, len(payloads)):
                output_label = f"xfade{index}"
                filter_parts.append(f"[{previous}][mix{index}]acrossfade=d={crossfade_seconds:.4f}:c1=tri:c2=tri[{output_label}]")
                previous = output_label
            filter_parts.append(f"[{previous}]anull[out]")
        else:
            filter_parts.append(f"{labels}amix=inputs={len(payloads)}:duration=longest:dropout_transition=0[out]")
        output = workdir / "mix_out.mp3"
        args = [self._ffmpeg, "-y"]
        for path in inputs:
            args.extend(["-i", path])
        args.extend(["-filter_complex", ";".join(filter_parts), "-map", "[out]", "-c:a", "libmp3lame", "-q:a", "2", str(output)])
        try:
            result = self._run_cmd(args, capture_output=True, timeout=self.timeout)
            if result.returncode != 0:
                return None
            mixed = output.read_bytes()
            return mixed if mixed else None
        except (OSError, subprocess.TimeoutExpired, FileNotFoundError):
            return None

    def _upload_output(self, payload: bytes, prefix: str) -> dict[str, Any]:
        boundary = "----SongbirdMix" + uuid4().hex
        filename = f"{prefix}_{uuid4().hex[:8]}.mp3"
        body = b"".join([
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{filename}\"\r\nContent-Type: audio/mpeg\r\n\r\n".encode("utf-8"),
            payload,
            f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"type\"\r\n\r\noutput\r\n".encode("utf-8"),
            f"--{boundary}--\r\n".encode("utf-8"),
        ])
        request = Request(
            f"{self.base_url}/upload/image",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with self._post_json(request, timeout=self.timeout) as response:
                payload_json = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            raise AdapterError(f"mix output upload failed: {exc}") from exc
        name = payload_json.get("name")
        if not name:
            raise AdapterError("mix output upload response did not include a filename")
        return {"filename": str(name), "subfolder": payload_json.get("subfolder", ""), "type": "output"}


class ComfyArtifactQAAdapter:
    """Verify bytes and supported duration metadata for a ComfyUI view artifact."""

    def __init__(self, base_url: str, *, timeout: float = 30.0, getter=urlopen, duration_probe=None) -> None:
        if not base_url.strip():
            raise ValueError("base_url must be a non-empty string")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._getter = getter
        self._duration_probe = duration_probe or self._probe_duration

    def evaluate(self, session: SongSession) -> ArtifactQAReport:
        if session.artifact is None:
            raise AdapterError("artifact QA requires a persisted artifact")
        if not session.artifact.uri.startswith(f"{self.base_url}/view?"):
            raise AdapterError("artifact URI is outside the configured ComfyUI view endpoint")
        try:
            response = self._getter(Request(session.artifact.uri, method="GET"), timeout=self.timeout)
            if hasattr(response, "__enter__"):
                with response as opened:
                    payload = opened.read()
            else:
                payload = response.read()
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise AdapterError(f"artifact download failed: {exc}") from exc
        if not payload:
            return ArtifactQAReport(False, False, False, ["artifact response was empty"], _now())
        digest = sha256(payload).hexdigest()
        checksum_verified, findings = _checksum_findings(session.artifact.checksum, digest)
        duration = self._duration_probe(payload, session.artifact.mime_type)
        requested_duration = session.render_request.duration_seconds if session.render_request else session.artifact.duration_seconds
        submitted_duration = session.artifact.duration_seconds
        duration_verified = (
            duration is not None
            and abs(duration - requested_duration) <= 0.5
            and abs(submitted_duration - requested_duration) <= 0.5
        )
        if duration is None:
            findings.append("duration probe unsupported for artifact format")
        elif not duration_verified:
            findings.append(
                f"duration mismatch: actual={duration:.3f}s requested={requested_duration:.3f}s submitted={submitted_duration:.3f}s"
            )
        else:
            findings.append(
                f"duration verified: actual={duration:.3f}s requested={requested_duration:.3f}s submitted={submitted_duration:.3f}s"
            )
        return ArtifactQAReport(checksum_verified and duration_verified, checksum_verified, duration_verified, findings, _now())

    @staticmethod
    def _probe_duration(payload: bytes, mime_type: str) -> float | None:
        if mime_type == "audio/wav" or payload.startswith(b"RIFF"):
            try:
                with wave.open(BytesIO(payload), "rb") as wav_file:
                    return wav_file.getnframes() / wav_file.getframerate()
            except (wave.Error, EOFError, ZeroDivisionError):
                return None
        format_name = {"audio/mpeg": "mp3", "audio/flac": "flac"}.get(mime_type)
        if format_name is None:
            return None
        try:
            result = subprocess.run(
                ["/usr/bin/ffprobe", "-v", "error", "-f", format_name, "-i", "pipe:0", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1"],
                input=payload,
                capture_output=True,
                check=False,
                timeout=10,
            )
            value = float(result.stdout.decode("utf-8").strip())
            if math.isfinite(value) and value > 0:
                return value
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        if format_name == "mp3":
            return ComfyArtifactQAAdapter._probe_mp3_duration(payload)
        return None

    @staticmethod
    def _probe_mp3_duration(payload: bytes) -> float | None:
        """Determine MP3 duration from the Xing/Info frame header using stdlib only.

        ffprobe cannot seek a non-seekable stdin pipe, so it reports `N/A` for MP3
        duration even when the stream is valid. The Xing/Info tag carries the total
        frame count, which yields a deterministic duration without a temp file.
        """
        size = len(payload)
        if size > 10 and payload[:3] == b"ID3":
            tag_size = (
                (payload[6] << 21) | (payload[7] << 14) | (payload[8] << 7) | payload[9]
            )
            start = 10 + tag_size
        else:
            start = 0
        tag_offset = -1
        for tag in (b"Xing", b"Info"):
            tag_offset = payload.find(tag, start)
            if tag_offset != -1:
                break
        if tag_offset == -1 or tag_offset + 12 > size:
            return None
        flags = int.from_bytes(payload[tag_offset + 4 : tag_offset + 8], "big")
        cursor = tag_offset + 8
        if flags & 0x1:
            if cursor + 4 > size:
                return None
            frame_count = int.from_bytes(payload[cursor : cursor + 4], "big")
            cursor += 4
        else:
            return None
        sample_rate = ComfyArtifactQAAdapter._mp3_sample_rate(payload, start)
        if sample_rate is None or frame_count <= 0:
            return None
        version = ComfyArtifactQAAdapter._mp3_version(payload, start)
        samples_per_frame = 1152 if version == 3 else 576
        return frame_count * samples_per_frame / sample_rate

    @staticmethod
    def _mp3_frame_sync(payload: bytes, start: int) -> int:
        """Return the offset of the first MPEG Layer III frame header (11 sync bits)."""
        cursor = payload.find(b"\xff", start)
        while cursor != -1 and cursor + 2 < len(payload):
            second = payload[cursor + 1]
            if (second & 0xE0) == 0xE0 and (second >> 3) & 0x3 != 1 and (second >> 1) & 0x3 == 1:
                return cursor
            cursor = payload.find(b"\xff", cursor + 1)
        return -1

    @staticmethod
    def _mp3_sample_rate(payload: bytes, start: int) -> int | None:
        sync = ComfyArtifactQAAdapter._mp3_frame_sync(payload, start)
        if sync == -1 or sync + 2 >= len(payload):
            return None
        index = (payload[sync + 2] >> 2) & 0x3
        version = (payload[sync + 1] >> 3) & 0x3
        table = {
            3: {0: 44100, 1: 48000, 2: 32000},
            2: {0: 22050, 1: 24000, 2: 16000},
            0: {0: 11025, 1: 12000, 2: 8000},
        }
        return table.get(version, {}).get(index)

    @staticmethod
    def _mp3_version(payload: bytes, start: int) -> int | None:
        sync = ComfyArtifactQAAdapter._mp3_frame_sync(payload, start)
        if sync == -1:
            return None
        return (payload[sync + 1] >> 3) & 0x3


class ComfyHealthAdapter:
    """Read-only ComfyUI `/system_stats` health adapter."""

    def __init__(self, base_url: str, *, timeout: float = 10.0, opener=urlopen) -> None:
        if not base_url.strip():
            raise ValueError("base_url must be a non-empty string")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._opener = opener

    def check(self) -> ProviderHealthReport:
        request = Request(f"{self.base_url}/system_stats", method="GET")
        checked_at = _now()
        try:
            with self._opener(request, timeout=self.timeout) as response:
                status_code = getattr(response, "status", 200)
                payload = json.loads(response.read().decode("utf-8"))
            system = payload.get("system", {})
            if not isinstance(system, dict):
                raise ValueError("system_stats.system must be an object")
            return ProviderHealthReport(
                provider="comfyui",
                base_url=self.base_url,
                reachable=True,
                status_code=status_code,
                version=system.get("comfyui_version"),
                details={"os": system.get("os"), "system_keys": sorted(system.keys())},
                checked_at=checked_at,
            )
        except (HTTPError, URLError, TimeoutError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return ProviderHealthReport(
                provider="comfyui",
                base_url=self.base_url,
                reachable=False,
                status_code=getattr(exc, "code", None),
                version=None,
                details={"error": str(exc)},
                checked_at=checked_at,
            )


class JsonHTTPHealthAdapter:
    """Read-only JSON probe for a supporting HTTP service."""

    def __init__(
        self,
        provider: str,
        base_url: str,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 10.0,
        opener=urlopen,
    ) -> None:
        if not provider.strip() or not base_url.strip() or not path.strip():
            raise ValueError("provider, base_url, and path must be non-empty strings")
        self.provider = provider.strip()
        self.base_url = base_url.rstrip("/")
        self.path = "/" + path.lstrip("/")
        self.method = method.upper()
        self.payload = payload
        self.headers = {"Accept": "application/json", **(headers or {})}
        self.timeout = timeout
        self._opener = opener

    def check(self) -> ProviderHealthReport:
        body = None
        if self.payload is not None:
            body = json.dumps(self.payload).encode("utf-8")
            self.headers.setdefault("Content-Type", "application/json")
        request = Request(
            f"{self.base_url}{self.path}", data=body, headers=self.headers, method=self.method
        )
        checked_at = _now()
        try:
            with self._opener(request, timeout=self.timeout) as response:
                status_code = getattr(response, "status", 200)
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("service response must be a JSON object")
            version = next(
                (str(payload[key]) for key in ("version", "comfyui_version", "service_version") if payload.get(key)),
                None,
            )
            return ProviderHealthReport(
                provider=self.provider,
                base_url=self.base_url,
                reachable=True,
                status_code=status_code,
                version=version,
                details={"endpoint": self.path, "response_keys": sorted(payload.keys())},
                checked_at=checked_at,
            )
        except (HTTPError, URLError, TimeoutError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return ProviderHealthReport(
                provider=self.provider,
                base_url=self.base_url,
                reachable=False,
                status_code=getattr(exc, "code", None),
                version=None,
                details={"endpoint": self.path, "error": str(exc)},
                checked_at=checked_at,
            )


class StaticCreativeAdapter:
    """Deterministic creative adapter for tests and local contract smoke runs."""

    def __init__(self, stage_name: StageName | str, content: dict[str, Any], *, model: str = "static") -> None:
        self.stage_name = StageName(stage_name)
        self.content = content
        self.model = model

    def generate(self, session: SongSession) -> CreativeStage:
        return CreativeStage(
            name=self.stage_name,
            status="generated",
            revision=session.revision + 1,
            content=self.content,
            updated_at=_now(),
            provenance=[
                ProvenanceRecord(
                    provider="mock",
                    model=self.model,
                    request_id=f"mock-{uuid4().hex}",
                    created_at=_now(),
                    source="static_creative_adapter",
                    metadata={"stage": self.stage_name.value},
                )
            ],
        )


class StaticQAAdapter:
    """Deterministic QA adapter returning a configured report."""

    def __init__(self, report: QAReport) -> None:
        self.report = report

    def evaluate(self, session: SongSession) -> QAReport:
        return QAReport(
            passed=self.report.passed,
            score=self.report.score,
            findings=self.report.findings,
            required_revisions=self.report.required_revisions,
            created_at=_now(),
        )


class StaticRenderAdapter:
    """Deterministic render adapter that produces a mock artifact only."""

    def render(self, session: SongSession, request: RenderRequest) -> ArtifactRecord:
        if session.status != SessionStatus.RENDER_APPROVED.value:
            raise AdapterError("render adapter requires an explicitly approved session")
        if session.render_request is None or session.render_request.idempotency_key != request.idempotency_key:
            raise AdapterError("render request is not the approved session request")
        artifact_id = f"mock-artifact-{sha256(request.idempotency_key.encode()).hexdigest()[:16]}"
        return ArtifactRecord(
            artifact_id=artifact_id,
            kind="audio",
            uri=f"mock://artifacts/{artifact_id}.wav",
            mime_type="audio/wav",
            checksum=sha256(f"{session.session_id}:{request.idempotency_key}".encode()).hexdigest(),
            duration_seconds=request.duration_seconds,
            created_at=_now(),
            provenance=[
                ProvenanceRecord(
                    provider="mock",
                    model="static-render",
                    request_id=request.idempotency_key,
                    created_at=_now(),
                    source="static_render_adapter",
                    metadata={"backend": request.backend},
                )
            ],
        )


class AdapterRegistry:
    """Explicit registry preventing accidental fallback to live providers."""

    def __init__(self) -> None:
        self._creative: dict[str, CreativeAdapter] = {}
        self._qa: dict[str, QAAdapter] = {}
        self._render: dict[str, RenderAdapter] = {}

    def register_creative(self, name: str, adapter: CreativeAdapter) -> None:
        self._creative[name] = adapter

    def register_qa(self, name: str, adapter: QAAdapter) -> None:
        self._qa[name] = adapter

    def register_render(self, name: str, adapter: RenderAdapter) -> None:
        self._render[name] = adapter

    def creative(self, name: str) -> CreativeAdapter:
        try:
            return self._creative[name]
        except KeyError as exc:
            raise AdapterNotConfigured(f"creative adapter {name!r} is not configured") from exc

    def qa(self, name: str) -> QAAdapter:
        try:
            return self._qa[name]
        except KeyError as exc:
            raise AdapterNotConfigured(f"qa adapter {name!r} is not configured") from exc

    def render(self, name: str) -> RenderAdapter:
        try:
            return self._render[name]
        except KeyError as exc:
            raise AdapterNotConfigured(f"render adapter {name!r} is not configured") from exc

    def registered(self) -> dict[str, list[str]]:
        """Return the explicitly registered adapter names by kind."""
        return {
            "creative": sorted(self._creative),
            "qa": sorted(self._qa),
            "render": sorted(self._render),
        }


class LocalDemucsStemAdapter:
    """HTDemucs source separation over a local source audio file.

    Runs the isolated ``demucs`` binary (drums/bass/other/vocals) once per
    source, caches the run per (source digest, fingerprint), and returns the
    selected stem as a local ArtifactRecord with SHA-256 and per-stem duration.

    Selection is driven by ``RenderRequest.parameters.stem`` so each stem can
    be recorded as its own governed artifact session.
    """

    STEMS = ("drums", "bass", "other", "vocals")
    _ALIASES = {"demucs_stem": "demucs_stem", "demucs": "demucs_stem", "htdemucs": "demucs_stem"}

    def __init__(
        self,
        demucs_bin: str,
        ffprobe: str = "/usr/bin/ffprobe",
        *,
        export_dir: str | Path | None = None,
        source_root: str | Path | None = None,
        timeout: float = 1800.0,
        run_cmd=subprocess.run,
    ) -> None:
        self._demucs = demucs_bin
        self._ffprobe = ffprobe
        self._export_dir = Path(export_dir) if export_dir is not None else None
        self._source_root = Path(source_root) if source_root is not None else None
        self.timeout = timeout
        self._run_cmd = run_cmd
        self._cache: dict[str, dict[str, Path]] = {}

    def render(self, session: SongSession, request: RenderRequest) -> ArtifactRecord:
        if session.status != SessionStatus.RENDER_APPROVED.value:
            raise AdapterError("demucs stem render requires render_approved session")
        if request.session_id != session.session_id or session.render_request != request:
            raise AdapterError("render request identity or idempotency key mismatch")
        if self._ALIASES.get(request.backend.lower().replace(" ", "_")) != "demucs_stem":
            raise AdapterError("demucs stem render requires backend demucs_stem")
        source = request.parameters.get("source_audio_uri")
        if not source:
            raise AdapterError("demucs stem render requires parameters.source_audio_uri")
        stem = request.parameters.get("stem")
        if stem not in self.STEMS:
            raise AdapterError(f"parameters.stem must be one of {', '.join(self.STEMS)}")
        if not isinstance(source, str):
            raise AdapterError("parameters.source_audio_uri must be a string path")
        stems = self._separate(_resolve_source_path(source, self._source_root))
        if stem not in stems:
            raise AdapterError(f"demucs produced no {stem!r} stem")
        duration = self._probe_duration(stems[stem])
        if duration <= 0:
            raise AdapterError(f"could not probe duration of demucs {stem!r} stem")
        local = self._export(stems[stem], session, request, stem)
        return ArtifactRecord(
            artifact_id=f"demucs-{stem}-{uuid4().hex}",
            kind="audio",
            uri=f"file://{local}",
            mime_type="audio/mpeg",
            checksum=sha256(local.read_bytes()).hexdigest(),
            duration_seconds=duration,
            created_at=_now(),
            provenance=[ProvenanceRecord(
                provider="demucs",
                model="htdemucs",
                request_id=None,
                created_at=_now(),
                source="local_demucs_stem_adapter",
                metadata={
                    "local_path": str(local),
                    "stem": stem,
                    "source_audio_uri": source,
                    "stems": sorted(stems),
                },
            )],
        )

    def _separate(self, source: Path) -> dict[str, Path]:
        digest = sha256(source.read_bytes()).hexdigest()
        key = f"{digest[:16]}|{source.stat().st_size}"
        cached = self._cache.get(key)
        if cached is not None and all(path.is_file() for path in cached.values()):
            return cached
        # Kept (not auto-deleted) because the per-source cache points into it.
        workdir = Path(tempfile.mkdtemp(prefix="songbird_demucs_"))
        args = [
            self._demucs,
            "-n", "htdemucs",
            "--mp3",
            "--out", str(workdir),
            str(source),
        ]
        try:
            result = self._run_cmd(args, capture_output=True, timeout=self.timeout)
        except (OSError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
            raise AdapterError(f"demucs run failed: {exc}") from exc
        if result.returncode != 0:
            raise AdapterError(f"demucs run failed (rc={result.returncode}): {result.stderr.decode('utf-8', 'replace')[-400:]}")
        model_dir = workdir / "htdemucs" / (Path(source).stem)
        stems: dict[str, Path] = {}
        for stem_name in self.STEMS:
            candidate = model_dir / f"{Path(source).stem}" / f"{stem_name}.mp3"
            fallback = model_dir / f"{stem_name}.mp3"
            path = candidate if candidate.is_file() else fallback if fallback.is_file() else None
            if path is not None:
                stems[stem_name] = path
        self._cache[key] = stems
        return stems

    def _export(self, stem_path: Path, session: SongSession, request: RenderRequest, stem: str) -> Path:
        if self._export_dir is None:
            raise AdapterError("demucs export directory is not configured")
        stem_path = Path(stem_path)
        # Session id + idempotency hash keep same-titled sessions from overwriting each other.
        suffix = sha256(f"{session.session_id}:{request.idempotency_key}".encode()).hexdigest()[:12]
        target = self._export_dir / f"{_sanitize_filename_prefix(session.title or 'stem')}_{stem}_{suffix}.mp3"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(stem_path.read_bytes())
        return target

    def _probe_duration(self, path: Path) -> float:
        try:
            result = self._run_cmd(
                [self._ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                capture_output=True, check=False, timeout=10,
            )
            value = float(result.stdout.decode("utf-8").strip())
            if math.isfinite(value) and value > 0:
                return value
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        return 0.0


class LocalArtifactQAAdapter:
    """Checksum + duration verification for a local file:// artifact."""

    def __init__(
        self,
        ffprobe: str = "/usr/bin/ffprobe",
        *,
        artifact_root: str | Path | None = None,
        run_cmd=subprocess.run,
    ) -> None:
        self._ffprobe = ffprobe
        self._artifact_root = Path(artifact_root) if artifact_root is not None else None
        self._run_cmd = run_cmd

    def evaluate(self, session: SongSession) -> ArtifactQAReport:
        if session.artifact is None:
            raise AdapterError("artifact QA requires a persisted artifact")
        uri = session.artifact.uri
        if not uri.startswith("file://"):
            raise AdapterError("local artifact QA requires a file:// artifact URI")
        if self._artifact_root is None:
            raise AdapterError("local artifact verification is disabled; configure SONGBIRD_DEMUCS_EXPORT_DIR")
        try:
            path = Path(uri[len("file://"):]).resolve()
        except (OSError, ValueError) as exc:
            raise AdapterError("artifact path is invalid") from exc
        if not path.is_relative_to(self._artifact_root.resolve()):
            raise AdapterError("artifact path is outside the configured artifact root")
        if not path.is_file():
            return ArtifactQAReport(False, False, False, ["artifact file missing"], _now())
        digest = sha256(path.read_bytes()).hexdigest()
        checksum_verified, findings = _checksum_findings(session.artifact.checksum, digest)
        duration = self._probe_duration(path)
        requested_duration = session.render_request.duration_seconds if session.render_request else session.artifact.duration_seconds
        submitted_duration = session.artifact.duration_seconds
        duration_verified = (
            duration is not None
            and abs(duration - requested_duration) <= 0.5
            and abs(submitted_duration - requested_duration) <= 0.5
        )
        if duration is None:
            findings.append("duration probe unsupported")
        elif not duration_verified:
            findings.append(f"duration mismatch: actual={duration:.3f}s requested={requested_duration:.3f}s submitted={submitted_duration:.3f}s")
        else:
            findings.append(f"duration verified: actual={duration:.3f}s requested={requested_duration:.3f}s submitted={submitted_duration:.3f}s")
        return ArtifactQAReport(checksum_verified and duration_verified, checksum_verified, duration_verified, findings, _now())

    def _probe_duration(self, path: Path) -> float | None:
        try:
            result = self._run_cmd(
                [self._ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                capture_output=True, check=False, timeout=10,
            )
            value = float(result.stdout.decode("utf-8").strip())
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return None
        # A corrupt file can make ffprobe report nan/inf; treat that as an unsupported probe.
        return value if math.isfinite(value) and value > 0 else None
