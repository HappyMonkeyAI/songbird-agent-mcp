"""Provider-neutral adapter interfaces and deterministic test doubles."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
import math
import re
import subprocess
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
    }
    _PROFILES = {
        "ace_step": {
            "label": "ACE-Step 1.5",
            "kind": "ace_step",
            "workflow": "audio_ace_step_1_5_checkpoint.json",
            "supports_negative_text": True,
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
        direction = next((stage.content for stage in session.stages if stage.name == StageName.MUSICAL_DIRECTION.value), {})
        prompt_name = StageName.INSTRUMENTAL_PROMPT.value if session.instrumental_only else StageName.LYRICS_OR_PROMPT.value
        prompt = next((stage.content for stage in session.stages if stage.name == prompt_name), {})
        tags = str(direction.get("tags", ""))
        lyrics_or_prompt = str(prompt.get("text", prompt.get("lyrics", prompt.get("prompt", ""))))
        effective_duration = min(duration, int(profile.get("max_duration_cap", 300)))
        parameters: dict[str, Any] = {
            "supports_negative_text": profile["supports_negative_text"],
            "duration_cap_applied": effective_duration != duration,
        }
        if profile_id == "minimax_music":
            parameters.update({key: profile[key] for key in ("default_steps", "default_cfg", "default_cfg_scale", "default_sampler", "default_scheduler", "default_top_k")})
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
            parameters=parameters,
            side_effects=["none: dry-run mapping only; no provider call, file write, queue submission, or artifact creation"],
        )


class ComfyRenderAdapter:
    """Opt-in ComfyUI prompt/history render adapter; never writes local artifacts."""

    def __init__(
        self,
        base_url: str,
        workflow_root: str | Path,
        *,
        timeout: float = 120.0,
        post_json=urlopen,
        get_json=urlopen,
        sleep=time.sleep,
    ) -> None:
        if not base_url.strip():
            raise ValueError("base_url must be a non-empty string")
        self.base_url = base_url.rstrip("/")
        self.workflow_root = Path(workflow_root)
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
        workflow_path = self.workflow_root / plan.workflow
        try:
            prompt = json.loads(workflow_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AdapterError(f"workflow template unavailable: {workflow_path}") from exc
        self._apply_plan(prompt, plan)
        response = self._post_json(
            Request(f"{self.base_url}/prompt", data=json.dumps({"prompt": prompt, "client_id": session.session_id}).encode(), headers={"Content-Type": "application/json"}, method="POST"),
            timeout=self.timeout,
        )
        payload = _read_json_response(response)
        prompt_id = payload.get("prompt_id")
        if not prompt_id:
            raise AdapterError("ComfyUI response did not include prompt_id")
        history = self._wait_for_history(prompt_id)
        file_info = self._extract_file(history, plan.profile_id)
        query = urlencode({key: file_info.get(key, "") for key in ("filename", "subfolder", "type")})
        extension = str(file_info.get("filename", "")).lower().rsplit(".", 1)[-1]
        mime_type = {"mp3": "audio/mpeg", "wav": "audio/wav", "flac": "audio/flac"}.get(extension, "audio/octet-stream")
        return ArtifactRecord(
            artifact_id=f"comfy-{prompt_id}",
            kind="audio",
            uri=f"{self.base_url}/view?{query}",
            mime_type=mime_type,
            checksum=None,
            duration_seconds=request.duration_seconds,
            created_at=_now(),
            provenance=[ProvenanceRecord(provider="comfyui", model=plan.profile_id, request_id=prompt_id, created_at=_now(), source="comfy_render_adapter")],
        )

    def _apply_plan(self, prompt: dict[str, Any], plan: RenderPlan) -> None:
        if plan.profile_id == "minimax_music":
            if "37:38" in prompt and plan.parameters.get("generation_seed") is not None:
                prompt["37:38"]["inputs"]["seed"] = int(plan.parameters["generation_seed"])
            if "37:13" in prompt:
                inputs = prompt["37:13"]["inputs"]
                inputs.update({"caption": json.dumps({"tags": plan.tags, "bpm": plan.bpm or 120, "keyscale": plan.keyscale or "C major"}), "lyrics": plan.lyrics_or_prompt, "max_duration": plan.duration_effective, "cfg_scale": plan.parameters["default_cfg_scale"], "top_k": plan.parameters["default_top_k"]})
            if "37:9" in prompt:
                prompt["37:9"]["inputs"].update({"steps": plan.parameters["default_steps"], "cfg": plan.parameters["default_cfg"], "sampler_name": plan.parameters["default_sampler"], "scheduler": plan.parameters["default_scheduler"]})
            if "35" in prompt:
                prompt["35"]["inputs"]["filename_prefix"] = f"audio/{plan.filename_prefix}"
        else:
            if "94" in prompt:
                prompt["94"]["inputs"].update({"tags": plan.tags, "lyrics": plan.lyrics_or_prompt, "bpm": plan.bpm or 120, "duration": plan.duration_effective, "keyscale": plan.keyscale or "C major"})
            if "98" in prompt:
                prompt["98"]["inputs"]["seconds"] = plan.duration_effective
            if "104" in prompt:
                prompt["104"]["inputs"]["filename_prefix"] = f"audio/{plan.filename_prefix}"

    def _wait_for_history(self, prompt_id: str) -> dict[str, Any]:
        attempts = max(1, int(self.timeout / 5))
        for _ in range(attempts):
            response = self._get_json(Request(f"{self.base_url}/history/{prompt_id}", method="GET"), timeout=self.timeout)
            history = _read_json_response(response)
            if prompt_id in history:
                return history[prompt_id]
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
        if not source_uri:
            raise AdapterError("M2M render requires parameters.source_audio_uri")
        if not source_uri.startswith(f"{self.base_url}/view?"):
            raise AdapterError("M2M source_audio_uri must be on the configured ComfyUI instance")
        source_bytes = self._fetch_bytes(source_uri)
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
        response = self._post_json(
            Request(f"{self.base_url}/prompt", data=json.dumps({"prompt": prompt, "client_id": session.session_id}).encode(), headers={"Content-Type": "application/json"}, method="POST"),
            timeout=self.timeout,
        )
        payload = _read_json_response(response)
        prompt_id = payload.get("prompt_id")
        if not prompt_id:
            raise AdapterError("ComfyUI response did not include prompt_id")
        history = self._wait_for_history(prompt_id)
        file_info = self._extract_m2m_file(history)
        query = urlencode({key: file_info.get(key, "") for key in ("filename", "subfolder", "type")})
        return ArtifactRecord(
            artifact_id=f"comfy-m2m-{prompt_id}",
            kind="audio",
            uri=f"{self.base_url}/view?{query}",
            mime_type="audio/mpeg",
            checksum=None,
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
            inputs["lyrics"] = plan.lyrics_or_prompt
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
        payloads = [self._fetch_bytes(uri) for uri in source_uris]
        if not all(payloads):
            raise AdapterError("one or more mix source audio downloads returned no bytes")
        mixed = self._mix(payloads, gains)
        if not mixed:
            raise AdapterError("ffmpeg mix produced no output")
        uploaded = self._upload_output(mixed, _sanitize_filename_prefix(session.title or "songbird-mix"))
        query = urlencode({key: uploaded.get(key, "") for key in ("filename", "subfolder", "type")})
        return ArtifactRecord(
            artifact_id=f"comfy-mix-{uuid4().hex}",
            kind="audio",
            uri=f"{self.base_url}/view?{query}",
            mime_type="audio/mpeg",
            checksum=None,
            duration_seconds=request.duration_seconds,
            created_at=_now(),
            provenance=[ProvenanceRecord(
                provider="comfyui",
                model="ffmpeg_mix",
                request_id=None,
                created_at=_now(),
                source="comfy_mix_render_adapter",
                metadata={"source_audio_uris": list(source_uris), "gains": list(gains) if gains else None},
            )],
        )

    def _mix(self, payloads: list[bytes], gains: list[float] | None) -> bytes | None:
        inputs: list[str] = []
        filter_parts: list[str] = []
        for index, payload in enumerate(payloads):
            path = Path("/tmp") / f"mix_in_{index}_{uuid4().hex[:8]}.mp3"
            path.write_bytes(payload)
            inputs.append(str(path))
            label = f"mix{index}"
            if gains and gains[index] != 1.0:
                filter_parts.append(f"[{index}:a]volume={float(gains[index]):.4f}[{label}]")
            else:
                filter_parts.append(f"[{index}:a]anull[{label}]")
        labels = "".join(f"[mix{i}]" for i in range(len(payloads)))
        filter_parts.append(f"{labels}amix=inputs={len(payloads)}:duration=longest:dropout_transition=0[out]")
        output = Path("/tmp") / f"mix_out_{uuid4().hex[:8]}.mp3"
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
        finally:
            for path in inputs + [str(output)]:
                try:
                    Path(path).unlink(missing_ok=True)
                except OSError:
                    pass

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
        response = self._getter(Request(session.artifact.uri, method="GET"), timeout=self.timeout)
        payload = response.read()
        if not payload:
            return ArtifactQAReport(False, False, False, ["artifact response was empty"], _now())
        digest = sha256(payload).hexdigest()
        checksum_verified = session.artifact.checksum is None or session.artifact.checksum == digest
        findings = [f"sha256={digest}"]
        if not checksum_verified:
            findings.append("artifact checksum mismatch")
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
    def _mp3_sample_rate(payload: bytes, start: int) -> int | None:
        sync = payload.find(b"\xff\xfb", start)
        if sync == -1 or sync + 2 > len(payload):
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
        sync = payload.find(b"\xff\xfb", start)
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
