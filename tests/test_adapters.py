import json
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import pytest
import wave
from urllib.error import HTTPError

from songbird_agent_mcp.adapters import (
    AdapterError,
    AdapterNotConfigured,
    AdapterRegistry,
    ComfyArtifactQAAdapter,
    ComfyHealthAdapter,
    JsonHTTPHealthAdapter,
    LocalArtifactQAAdapter,
    LocalDemucsStemAdapter,
    ComfyM2MRenderAdapter,
    ComfyMixRenderAdapter,
    ComfyRenderAdapter,
    GeniusAPIAdapter,
    LightRAGQueryAdapter,
    HealthAdapter,
    _sanitize_filename_prefix,
    OllamaAdapterError,
    OllamaMusicDirectionAdapter,
    OpenAICompatibleAdapterError,
    OpenAIMusicDirectionAdapter,
    StaticCreativeAdapter,
    StaticQAAdapter,
    StaticRenderAdapter,
    SongbirdDryRunAdapter,
)
from songbird_agent_mcp.contracts import ArtifactRecord, CreativeStage, QAReport, RenderRequest, SessionStatus, StageName
from songbird_agent_mcp.service import SessionService
from songbird_agent_mcp.session_store import SessionStore


def build_xing_mp3(*, frame_count: int = 2298, sample_rate: int = 44100) -> bytes:
    """Synthetic MP3 frame header + Xing header matching the MiniMax output layout.

    The real MiniMax MP3 carries a Xing header at frame+36 with big-endian flags
    `0x0000000f` and the total frame count. Frame duration for MPEG-1 Layer III is
    1152 samples; 2298 frames at 44.1 kHz is a 60.0s track.
    """
    if sample_rate not in {44100, 48000, 32000, 22050, 24000, 16000}:
        raise ValueError("sample_rate must match an MPEG-1 table entry")
    sr_index = {44100: 0, 48000: 1, 32000: 2}[sample_rate]
    frame_header = bytes([0xFF, 0xFB, 0x90 | (sr_index << 2), 0x00])
    version_flags = b"\x00\x00\x00\x0f"
    return frame_header + b"\x00" * 32 + b"Xing" + version_flags + frame_count.to_bytes(4, "big")


XING_MP3 = build_xing_mp3()


def make_session():
    store = SessionStore()
    service = SessionService(store)
    session = service.create_session(use_case="song", genre="SYNTHWAVE", title="Adapter Smoke")
    return service, store, session


def test_static_creative_adapter_returns_typed_stage_with_provenance():
    _, store, session = make_session()
    stage = StaticCreativeAdapter("brief", {"text": "hello"}).generate(session)
    assert stage.name == "brief"
    assert stage.content == {"text": "hello"}
    assert stage.provenance[0].provider == "mock"
    store.close()


def test_static_qa_adapter_returns_copy_with_fresh_timestamp():
    _, store, session = make_session()
    report = StaticQAAdapter(QAReport(True, 92, [], [], "old")).evaluate(session)
    assert report.passed is True
    assert report.score == 92
    assert report.created_at != "old"
    store.close()


def test_static_render_adapter_requires_explicit_approval():
    service, store, session = make_session()
    request = RenderRequest(session.session_id, "mock", 120, "idem-1", "now", "operator")
    with pytest.raises(AdapterError, match="approved"):
        StaticRenderAdapter().render(session, request)
    store.close()


def test_static_render_adapter_returns_deterministic_artifact_for_approved_session():
    service, store, session = make_session()
    session.status = SessionStatus.RENDER_APPROVED.value
    session.render_request = RenderRequest(session.session_id, "mock", 120, "idem-1", "now", "operator")
    artifact = StaticRenderAdapter().render(session, session.render_request)
    assert artifact.kind == "audio"
    assert artifact.uri.startswith("mock://")
    assert artifact.duration_seconds == 120
    assert artifact.provenance[0].source == "static_render_adapter"
    store.close()


def test_render_adapter_rejects_unapproved_request_identity():
    _, store, session = make_session()
    session.status = SessionStatus.RENDER_APPROVED.value
    session.render_request = RenderRequest(session.session_id, "mock", 120, "approved", "now", "operator")
    request = RenderRequest(session.session_id, "mock", 120, "different", "now", "operator")
    with pytest.raises(AdapterError, match="approved session request"):
        StaticRenderAdapter().render(session, request)
    store.close()


def test_registry_requires_explicit_registration():
    registry = AdapterRegistry()
    with pytest.raises(AdapterNotConfigured, match="creative adapter"):
        registry.creative("live_songbird")
    adapter = StaticCreativeAdapter("brief", {"text": "hello"})
    registry.register_creative("mock", adapter)
    assert registry.creative("mock") is adapter


def test_protocols_are_runtime_checkable_for_mocks():
    from songbird_agent_mcp.adapters import CreativeAdapter, QAAdapter, RenderAdapter
    assert isinstance(StaticCreativeAdapter("brief", {}), CreativeAdapter)
    assert isinstance(StaticQAAdapter(QAReport(True, None, [], [], "now")), QAAdapter)
    assert isinstance(StaticRenderAdapter(), RenderAdapter)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


def test_ollama_music_direction_adapter_uses_verified_generate_contract():
    _, store, session = make_session()
    calls = []

    def opener(request, timeout):
        calls.append((request, timeout))
        return FakeResponse(b'{"response":"{\\"tags\\":\\"melodic synths\\",\\"bpm\\":118,\\"keyscale\\":\\"C major\\"}"}')

    stage = OllamaMusicDirectionAdapter("qwen", "http://ollama:11434/", timeout=7, opener=opener).generate(session)
    request, timeout = calls[0]
    assert request.full_url == "http://ollama:11434/api/generate"
    assert timeout == 7
    assert stage.name == "musical_direction"
    assert stage.content == {"tags": "melodic synths", "bpm": 118, "keyscale": "C major"}
    assert stage.provenance[0].provider == "ollama"
    store.close()


def test_ollama_music_direction_adapter_rejects_malformed_response():
    _, store, session = make_session()

    def opener(request, timeout):
        return FakeResponse(b'{"response":"not-json"}')

    with pytest.raises(OllamaAdapterError, match="request failed"):
        OllamaMusicDirectionAdapter("qwen", "http://ollama:11434", opener=opener).generate(session)
    store.close()


def test_openai_music_direction_adapter_uses_chat_completions_contract():
    _, store, session = make_session()
    calls = []

    def opener(request, timeout):
        calls.append((request, timeout))
        assert request.get_header("Authorization") == "Bearer sk-test"
        return FakeResponse(b'{"choices":[{"message":{"content":"{\\"tags\\":\\"distorted guitars\\",\\"bpm\\":142,\\"keyscale\\":\\"E minor\\"}"}}]}')

    stage = OpenAIMusicDirectionAdapter(
        "unsloth/Qwen3.8-27B-GGUF",
        "https://tunnel.example.com",
        api_key="sk-test",
        timeout=7,
        opener=opener,
    ).generate(session)
    request, timeout = calls[0]
    assert request.full_url == "https://tunnel.example.com/v1/chat/completions"
    assert timeout == 7
    assert json.loads(request.data)["model"] == "unsloth/Qwen3.8-27B-GGUF"
    assert json.loads(request.data)["response_format"] == {"type": "json_object"}
    assert stage.name == "musical_direction"
    assert stage.content == {"tags": "distorted guitars", "bpm": 142, "keyscale": "E minor"}
    assert stage.provenance[0].provider == "openai_compatible"
    store.close()


def test_openai_music_direction_adapter_normalizes_v1_base_url():
    _, store, session = make_session()

    def opener(request, timeout):
        return FakeResponse(b'{"choices":[{"message":{"content":"{\\"tags\\":\\"rock\\",\\"bpm\\":120,\\"keyscale\\":\\"C major\\"}"}}]}')

    stage = OpenAIMusicDirectionAdapter("m", "https://tunnel.example.com/v1/", opener=opener).generate(session)
    assert stage.content["bpm"] == 120
    store.close()


def test_openai_music_direction_adapter_retries_without_response_format_on_400():
    _, store, session = make_session()
    attempts = []

    def opener(request, timeout):
        attempts.append(json.loads(request.data))
        if len(attempts) == 1:
            raise HTTPError(request.full_url, 400, "response_format unsupported", None, BytesIO(b"{}"))
        return FakeResponse(b'{"choices":[{"message":{"content":"{\\"tags\\":\\"blues rock\\",\\"bpm\\":108,\\"keyscale\\":\\"A major\\"}"}}]}')

    stage = OpenAIMusicDirectionAdapter("m", "http://tunnel:8000", opener=opener).generate(session)
    assert len(attempts) == 2
    assert "response_format" not in attempts[1]
    assert stage.content["tags"] == "blues rock"
    store.close()


def test_openai_music_direction_adapter_rejects_malformed_response():
    _, store, session = make_session()

    def opener(request, timeout):
        return FakeResponse(b'{"choices":[{"message":{"content":"not-json"}}]}')

    with pytest.raises(OpenAICompatibleAdapterError, match="request failed"):
        OpenAIMusicDirectionAdapter("m", "http://tunnel:8000", opener=opener).generate(session)
    store.close()


def test_openai_music_direction_adapter_requires_bearer_key_when_configured():
    _, store, session = make_session()

    def opener(request, timeout):
        return FakeResponse(b'{"choices":[{"message":{"content":"{\\"tags\\":\\"rock\\",\\"bpm\\":120,\\"keyscale\\":\\"C major\\"}"}}]}')

    OpenAIMusicDirectionAdapter("m", "http://tunnel:8000", api_key=None, opener=opener).generate(session)
    store.close()


def test_comfy_health_adapter_reads_system_stats_without_side_effects():
    calls = []

    def opener(request, timeout):
        calls.append((request, timeout))
        return FakeResponse(b'{"system":{"os":"linux","comfyui_version":"0.3.0","devices":[]}}')

    report = ComfyHealthAdapter("http://comfy:8188/", timeout=4, opener=opener).check()
    assert report.reachable is True
    assert report.status_code == 200
    assert report.version == "0.3.0"
    assert calls[0][0].full_url == "http://comfy:8188/system_stats"
    assert isinstance(ComfyHealthAdapter("http://comfy:8188", opener=opener), HealthAdapter)


def test_comfy_health_adapter_returns_unreachable_report_on_transport_failure():
    def opener(request, timeout):
        raise OSError("connection refused")

    report = ComfyHealthAdapter("http://comfy:8188", opener=opener).check()
    assert report.reachable is False
    assert report.status_code is None
    assert "connection refused" in report.details["error"]


def test_json_http_health_adapter_posts_read_only_probe():
    calls = []

    def opener(request, timeout):
        calls.append((request, timeout))
        return FakeResponse(b'{"response":"context", "references": []}')

    report = JsonHTTPHealthAdapter(
        "lightrag", "http://rag:9621", "/query", method="POST",
        payload={"only_need_context": True}, opener=opener,
    ).check()
    assert report.reachable is True
    assert report.details["endpoint"] == "/query"
    assert calls[0][0].full_url == "http://rag:9621/query"
    assert json.loads(calls[0][0].data) == {"only_need_context": True}


def test_genius_adapter_authenticates_searches_and_returns_safe_song_metadata():
    calls = []

    def opener(request, timeout):
        calls.append(request)
        if request.full_url.endswith("/oauth/token"):
            return FakeResponse(b'{"access_token":"token-123"}')
        return FakeResponse(json.dumps({"response": {"hits": [{"result": {
            "id": 123, "title": "Neon Signal", "url": "https://genius.com/x",
            "primary_artist": {"id": 9, "name": "Test Artist"},
        }}]}}).encode())

    results = GeniusAPIAdapter("client", "secret", opener=opener).search("neon signal", limit=3)
    assert results == [{"id": 123, "title": "Neon Signal", "url": "https://genius.com/x", "artist": "Test Artist", "artist_id": 9, "album": None, "release_date": None, "lyrics_state": None}]
    assert calls[0].full_url.endswith("/oauth/token")
    assert calls[1].full_url.startswith("https://api.genius.com/search?q=neon+signal")
    assert calls[1].get_header("Authorization") == "Bearer token-123"


def test_genius_adapter_accepts_existing_access_token_without_oauth_call():
    calls = []

    def opener(request, timeout):
        calls.append(request)
        return FakeResponse(b'{"response":{"hits":[]}}')

    GeniusAPIAdapter(access_token="existing-token", opener=opener).search("neon")
    assert len(calls) == 1
    assert calls[0].get_header("Authorization") == "Bearer existing-token"


def test_lightrag_query_adapter_requests_context_only():
    calls = []

    def opener(request, timeout):
        calls.append(request)
        return FakeResponse(b'{"response":"neon rain", "references":[{"file_path":"CYBERPUNK/a.txt"}]}')

    result = LightRAGQueryAdapter("http://rag:9621", api_key="rag-key", opener=opener).query("neon")
    assert result["context"] == "neon rain"
    assert result["references"] == [{"file_path": "CYBERPUNK/a.txt"}]
    assert json.loads(calls[0].data)["only_need_context"] is True
    assert calls[0].get_header("X-api-key") == "rag-key"


def test_comfy_render_adapter_submits_approved_workflow_and_returns_remote_artifact(tmp_path):
    service, store, session = make_session()
    session.status = SessionStatus.RENDER_APPROVED.value
    request = RenderRequest(session.session_id, "minimax_music", 180, "idem-comfy", "now", "operator")
    session.render_request = request
    (tmp_path / "audio_minimax_music_3.json").write_text(
        '{"37:13":{"inputs":{}},"37:9":{"inputs":{}},"35":{"inputs":{}}}', encoding="utf-8"
    )
    posts = []

    def post_json(req, timeout):
        posts.append(json.loads(req.data.decode("utf-8")))
        return FakeResponse(b'{"prompt_id":"prompt-123"}')

    def get_json(req, timeout):
        if "/view?" in req.full_url:
            return FakeResponse(b"MP3BYTES")
        return FakeResponse(b'{"prompt-123":{"outputs":{"35":{"audio":[{"filename":"song.mp3","subfolder":"audio","type":"output"}]}}}}')

    export_dir = tmp_path / "exports"
    adapter = ComfyRenderAdapter("http://comfy:8188", tmp_path, output_dir=export_dir, timeout=5, post_json=post_json, get_json=get_json)
    artifact = adapter.render(session, request)
    assert artifact.uri == "http://comfy:8188/view?filename=song.mp3&subfolder=audio&type=output"
    assert artifact.mime_type == "audio/mpeg"
    assert artifact.provenance[0].request_id == "prompt-123"
    assert posts[0]["prompt"]["35"]["inputs"]["filename_prefix"] == "audio/Adapter Smoke"
    assert artifact.checksum == sha256(b"MP3BYTES").hexdigest()
    exported = list(export_dir.glob("*.mp3"))
    assert len(exported) == 1
    assert exported[0].read_bytes() == b"MP3BYTES"
    store.close()


def test_comfy_render_minimax_instrumental_passes_empty_lyrics(tmp_path):
    """Instrumental MiniMax renders must not feed the verbal prompt into the
    lyrics field, or the model sings the instructions as vocals."""
    service, store, session = make_session()
    session.instrumental_only = True
    session.status = SessionStatus.RENDER_APPROVED.value
    request = RenderRequest(session.session_id, "minimax_music", 180, "idem-instr-lyrics", "now", "operator")
    session.render_request = request
    (tmp_path / "audio_minimax_music_3.json").write_text(
        '{"37:38":{"inputs":{"seed":0}},"37:13":{"inputs":{}},"37:9":{"inputs":{}},"35":{"inputs":{}}}',
        encoding="utf-8",
    )
    posts = []

    def post_json(req, timeout):
        posts.append(json.loads(req.data.decode("utf-8")))
        return FakeResponse(b'{"prompt_id":"prompt-instr-lyrics"}')

    def get_json(req, timeout):
        return FakeResponse(b'{"prompt-instr-lyrics":{"outputs":{"35":{"audio":[{"filename":"song.mp3","subfolder":"audio","type":"output"}]}}}}')

    adapter = ComfyRenderAdapter("http://comfy:8188", tmp_path, timeout=5, post_json=post_json, get_json=get_json)
    adapter.render(session, request)
    submitted = posts[0]["prompt"]["37:13"]["inputs"]
    assert submitted["lyrics"] == ""
    assert json.loads(submitted["caption"])["Global Metadata"]["genre"] == "SYNTHWAVE"
    store.close()


def test_comfy_render_minimax_song_keeps_lyrics_field(tmp_path):
    """A vocal song session keeps lyrics in the workflow lyrics field."""
    service, store, session = make_session()  # instrumental_only defaults False
    session.status = SessionStatus.RENDER_APPROVED.value
    request = RenderRequest(session.session_id, "minimax_music", 180, "idem-vocal-lyrics", "now", "operator")
    session.render_request = request
    session.stages = [CreativeStage(
        name=StageName.LYRICS_OR_PROMPT.value,
        status="draft",
        revision=1,
        content={"text": "[Chorus]\nTurn the signal on"},
        updated_at="now",
    )]
    (tmp_path / "audio_minimax_music_3.json").write_text(
        '{"37:38":{"inputs":{"seed":0}},"37:13":{"inputs":{}},"37:9":{"inputs":{}},"35":{"inputs":{}}}',
        encoding="utf-8",
    )
    posts = []

    def post_json(req, timeout):
        posts.append(json.loads(req.data.decode("utf-8")))
        return FakeResponse(b'{"prompt_id":"prompt-vocal-lyrics"}')

    def get_json(req, timeout):
        return FakeResponse(b'{"prompt-vocal-lyrics":{"outputs":{"35":{"audio":[{"filename":"song.mp3","subfolder":"audio","type":"output"}]}}}}')

    adapter = ComfyRenderAdapter("http://comfy:8188", tmp_path, timeout=5, post_json=post_json, get_json=get_json)
    adapter.render(session, request)
    submitted = posts[0]["prompt"]["37:13"]["inputs"]
    assert submitted["lyrics"] == "[Chorus]\nTurn the signal on"
    store.close()


def test_comfy_render_adapter_uses_a_real_polling_delay_by_default():
    import time as time_module

    adapter = ComfyRenderAdapter("http://comfy:8188", "/tmp")
    assert adapter._sleep is time_module.sleep
    assert callable(adapter._sleep)


def test_comfy_render_adapter_writes_generation_seed_to_seed_node(tmp_path):
    service, store, session = make_session()
    session.status = SessionStatus.RENDER_APPROVED.value
    request = RenderRequest(
        session.session_id, "minimax_music", 60, "idem-seed", "now", "operator",
        parameters={"seed": 2024},
    )
    session.render_request = request
    (tmp_path / "audio_minimax_music_3.json").write_text(
        '{"37:38":{"inputs":{"seed":0}},"37:13":{"inputs":{}},"37:9":{"inputs":{}},"35":{"inputs":{}}}',
        encoding="utf-8",
    )
    posts = []

    def post_json(req, timeout):
        posts.append(json.loads(req.data.decode("utf-8")))
        return FakeResponse(b'{"prompt_id":"prompt-seed-1"}')

    def get_json(req, timeout):
        return FakeResponse(b'{"prompt-seed-1":{"outputs":{"35":{"audio":[{"filename":"song.mp3","subfolder":"audio","type":"output"}]}}}}')

    adapter = ComfyRenderAdapter("http://comfy:8188", tmp_path, timeout=5, post_json=post_json, get_json=get_json)
    adapter.render(session, request)
    assert posts[0]["prompt"]["37:38"]["inputs"]["seed"] == 2024
    store.close()


def test_comfy_render_adapter_uses_session_seed_when_not_overridden(tmp_path):
    service, store, session = make_session()
    session.status = SessionStatus.RENDER_APPROVED.value
    session.generation_seed = 31337
    request = RenderRequest(session.session_id, "minimax_music", 60, "idem-seed2", "now", "operator")
    session.render_request = request
    (tmp_path / "audio_minimax_music_3.json").write_text(
        '{"37:38":{"inputs":{"seed":0}},"37:13":{"inputs":{}},"37:9":{"inputs":{}},"35":{"inputs":{}}}',
        encoding="utf-8",
    )
    posts = []

    def post_json(req, timeout):
        posts.append(json.loads(req.data.decode("utf-8")))
        return FakeResponse(b'{"prompt_id":"prompt-seed-2"}')

    def get_json(req, timeout):
        return FakeResponse(b'{"prompt-seed-2":{"outputs":{"35":{"audio":[{"filename":"song.mp3","subfolder":"audio","type":"output"}]}}}}')

    adapter = ComfyRenderAdapter("http://comfy:8188", tmp_path, timeout=5, post_json=post_json, get_json=get_json)
    adapter.render(session, request)
    assert posts[0]["prompt"]["37:38"]["inputs"]["seed"] == 31337
    store.close()


def test_ace_step_plan_exposes_v2_pop_quality_defaults():
    _, store, session = make_session()
    session.genre = "POP"
    plan = SongbirdDryRunAdapter().plan(session, "ace_step", 240, "pop-quality", 2026)
    assert plan.duration_effective == 240
    assert plan.parameters["default_steps"] == 16
    assert plan.parameters["default_cfg"] == 1.4
    assert plan.parameters["default_sampler"] == "dpmpp_2m"
    assert plan.parameters["default_scheduler"] == "sgm_uniform"
    assert plan.parameters["default_cfg_scale"] == 3.5
    assert plan.parameters["generation_seed"] == 2026
    store.close()


def test_comfy_render_adapter_applies_v2_ace_settings_to_workflow(tmp_path):
    service, store, session = make_session()
    session.genre = "POP"
    session.status = SessionStatus.RENDER_APPROVED.value
    request = RenderRequest(
        session.session_id, "ace_step", 240, "idem-ace-v2", "now", "operator",
        parameters={"seed": 2026},
    )
    session.render_request = request
    (tmp_path / "audio_ace_step_1_5_checkpoint.json").write_text(
        json.dumps({
            "3": {"inputs": {"seed": 0, "steps": 50, "cfg": 4, "sampler_name": "euler", "scheduler": "sgm_uniform"}},
            "7": {"inputs": {"text": ""}},
            "94": {"inputs": {}},
            "98": {"inputs": {"seconds": 120}},
            "104": {"inputs": {"filename_prefix": "audio/old"}},
        }),
        encoding="utf-8",
    )
    posts = []

    def post_json(req, timeout):
        posts.append(json.loads(req.data.decode("utf-8")))
        return FakeResponse(b'{"prompt_id":"prompt-ace-v2"}')

    def get_json(req, timeout):
        return FakeResponse(b'{"prompt-ace-v2":{"outputs":{"104":{"audio":[{"filename":"song.mp3","subfolder":"audio","type":"output"}]}}}}')

    adapter = ComfyRenderAdapter("http://comfy:8188", tmp_path, timeout=5, post_json=post_json, get_json=get_json)
    adapter.render(session, request)
    prompt = posts[0]["prompt"]
    assert prompt["3"]["inputs"] == {
        "seed": 2026,
        "steps": 16,
        "cfg": 1.4,
        "sampler_name": "dpmpp_2m",
        "scheduler": "sgm_uniform",
    }
    assert prompt["94"]["inputs"]["cfg_scale"] == 3.5
    assert prompt["98"]["inputs"]["seconds"] == 240
    assert prompt["7"]["inputs"]["text"]
    store.close()


def test_comfy_render_adapter_ace_step_negative_prompt_override(tmp_path):
    service, store, session = make_session()
    session.genre = "POP"
    session.status = SessionStatus.RENDER_APPROVED.value
    request = RenderRequest(
        session.session_id, "ace_step", 16, "idem-ace-neg", "now", "operator",
        parameters={"seed": 2026, "negative_prompt": "vocals, singing, voice, crowd, noise"},
    )
    session.render_request = request
    (tmp_path / "audio_ace_step_1_5_checkpoint.json").write_text(
        json.dumps({
            "3": {"inputs": {"seed": 0, "steps": 50, "cfg": 4}},
            "7": {"inputs": {"text": ""}},
            "94": {"inputs": {}},
            "98": {"inputs": {"seconds": 120}},
            "104": {"inputs": {"filename_prefix": "audio/old"}},
        }),
        encoding="utf-8",
    )
    posts = []

    def post_json(req, timeout):
        posts.append(json.loads(req.data.decode("utf-8")))
        return FakeResponse(b'{"prompt_id":"prompt-ace-neg"}')

    def get_json(req, timeout):
        return FakeResponse(b'{"prompt-ace-neg":{"outputs":{"104":{"audio":[{"filename":"song.mp3","subfolder":"audio","type":"output"}]}}}}')

    adapter = ComfyRenderAdapter("http://comfy:8188", tmp_path, timeout=5, post_json=post_json, get_json=get_json)
    adapter.render(session, request)
    prompt = posts[0]["prompt"]
    assert prompt["7"]["inputs"]["text"] == "vocals, singing, voice, crowd, noise"
    store.close()


def test_comfy_m2m_adapter_uses_session_seed_when_not_overridden(tmp_path):
    service, store, session = make_session()
    session.status = SessionStatus.RENDER_APPROVED.value
    session.generation_seed = 777
    request = RenderRequest(
        session.session_id, "ace_step_m2m", 161, "idem-m2m-seed", "now", "operator",
        parameters={"source_audio_uri": "http://comfy:8188/view?filename=src.mp3&subfolder=audio&type=output"},
    )
    session.render_request = request
    (tmp_path / "audio_ace_step_m2m_edit.json").write_text(
        '{"14":{"inputs":{}},"52":{"inputs":{}},"64":{"inputs":{"audio":""}},"59":{"inputs":{}}}', encoding="utf-8"
    )
    posts = []

    def get_json(req, timeout):
        if "/history/" in req.full_url:
            return FakeResponse(b'{"prompt-m2m-seed":{"outputs":{"59":{"audio":[{"filename":"res.mp3","subfolder":"audio","type":"output"}]}}}}')
        return FakeResponse(b"MP3BYTES")

    def post_json(req, timeout):
        if req.full_url.endswith("/upload/image"):
            return FakeResponse(b'{"name":"m2m_source_abcdef01.mp3","subfolder":"","type":"input"}')
        posts.append(json.loads(req.data.decode("utf-8")))
        return FakeResponse(b'{"prompt_id":"prompt-m2m-seed"}')

    adapter = ComfyM2MRenderAdapter("http://comfy:8188", tmp_path, timeout=5, post_json=post_json, get_json=get_json)
    adapter.render(session, request)
    assert posts[0]["prompt"]["52"]["inputs"]["seed"] == 777
    store.close()


def test_sanitize_filename_prefix_strips_windows_illegal_characters():
    assert _sanitize_filename_prefix('Teen Spirit (Adele) -> Kawaii M2M') == 'Teen Spirit (Adele) -_ Kawaii M2M'
    assert _sanitize_filename_prefix('a:b*c?d"e<f>g|h') == 'a_b_c_d_e_f_g_h'
    assert _sanitize_filename_prefix('   ') == 'songbird'
    assert _sanitize_filename_prefix('..') == 'songbird'


def test_comfy_m2m_render_adapter_uploads_source_and_submits_m2m_workflow(tmp_path):
    service, store, session = make_session()
    session.status = SessionStatus.RENDER_APPROVED.value
    request = RenderRequest(
        session.session_id,
        "ace_step_m2m",
        161,
        "idem-m2m",
        "now",
        "operator",
        parameters={"source_audio_uri": "http://comfy:8188/view?filename=src.mp3&subfolder=audio&type=output", "denoise": 0.3},
    )
    session.render_request = request
    (tmp_path / "audio_ace_step_m2m_edit.json").write_text(
        '{"14":{"inputs":{}},"52":{"inputs":{}},"64":{"inputs":{"audio":""}},"59":{"inputs":{}}}', encoding="utf-8"
    )
    uploads = []
    posts = []

    def get_json(req, timeout):
        if "/history/" in req.full_url:
            return FakeResponse(b'{"prompt-m2m-123":{"outputs":{"59":{"audio":[{"filename":"res.mp3","subfolder":"audio","type":"output"}]}}}}')
        return FakeResponse(b"MP3BYTES")

    def post_json(req, timeout):
        if req.full_url.endswith("/upload/image"):
            body = req.data
            uploads.append(body)
            return FakeResponse(b'{"name":"m2m_source_abcdef01.mp3","subfolder":"","type":"input"}')
        posts.append(json.loads(req.data.decode("utf-8")))
        return FakeResponse(b'{"prompt_id":"prompt-m2m-123"}')

    adapter = ComfyM2MRenderAdapter("http://comfy:8188", tmp_path, timeout=5, post_json=post_json, get_json=get_json)
    artifact = adapter.render(session, request)
    assert artifact.uri == "http://comfy:8188/view?filename=res.mp3&subfolder=audio&type=output"
    assert artifact.provenance[0].model == "ace_step_m2m"
    assert artifact.provenance[0].metadata["source_audio_uri"] == request.parameters["source_audio_uri"]
    assert uploads, "M2M source should be uploaded to ComfyUI input"
    prompt = posts[0]["prompt"]
    assert prompt["64"]["inputs"]["audio"].startswith("m2m_source_")
    assert prompt["14"]["inputs"]["lyrics_strength"] == 0.99
    assert prompt["52"]["inputs"]["steps"] == 50
    store.close()


def test_comfy_m2m_render_adapter_accepts_local_source_file(tmp_path):
    service, store, session = make_session()
    session.status = SessionStatus.RENDER_APPROVED.value
    session.instrumental_only = True
    src = tmp_path / "AceLoopRez.mp3"
    src.write_bytes(b"LOCALMP3BYTES")
    request = RenderRequest(
        session.session_id,
        "ace_step_m2m",
        51,
        "idem-m2m-local",
        "now",
        "operator",
        parameters={"source_audio_uri": str(src), "denoise": 0.3},
    )
    session.render_request = request
    (tmp_path / "audio_ace_step_m2m_edit.json").write_text(
        '{"14":{"inputs":{}},"52":{"inputs":{}},"64":{"inputs":{"audio":""}},"59":{"inputs":{}}}', encoding="utf-8"
    )
    posts = []
    uploads = []

    def get_json(req, timeout):
        if "/history/" in req.full_url:
            return FakeResponse(b'{"prompt-m2m-local":{"outputs":{"59":{"audio":[{"filename":"res.mp3","subfolder":"audio","type":"output"}]}}}}')
        return FakeResponse(b"MP3BYTES")

    def post_json(req, timeout):
        if req.full_url.endswith("/upload/image"):
            body = req.data
            uploads.append(body)
            return FakeResponse(b'{"name":"m2m_source_localhash.mp3","subfolder":"","type":"input"}')
        posts.append(json.loads(req.data.decode("utf-8")))
        return FakeResponse(b'{"prompt_id":"prompt-m2m-local"}')

    adapter = ComfyM2MRenderAdapter("http://comfy:8188", tmp_path, source_root=tmp_path, timeout=5, post_json=post_json, get_json=get_json)
    adapter.render(session, request)
    assert uploads and b"LOCALMP3BYTES" in uploads[0]
    prompt = posts[0]["prompt"]
    assert prompt["64"]["inputs"]["audio"].startswith("m2m_source_")
    assert prompt["14"]["inputs"]["lyrics"] == ""  # instrumental_only -> empty lyrics
    store.close()


def test_local_demucs_stem_adapter_returns_selected_stem_artifact(tmp_path):
    service, store, session = make_session()
    session.status = SessionStatus.RENDER_APPROVED.value
    session.instrumental_only = True
    source = tmp_path / "track.mp3"
    source.write_bytes(b"MP3SOURCEDATA")
    export = tmp_path / "stems"

    class FakeResult:
        returncode = 0
        stdout = b""
        stderr = b""

    runs = []

    def fake_run(args, **kwargs):
        runs.append(args)
        if args[0].endswith("demucs"):
            out_idx = args.index("--out")
            model_dir = Path(args[out_idx + 1]) / "htdemucs" / "track"
            model_dir.mkdir(parents=True, exist_ok=True)
            for stem in ("drums", "bass", "other", "vocals"):
                (model_dir / f"{stem}.mp3").write_bytes(b"STEM" + stem.encode())
            return FakeResult()
        return type("R", (), {"returncode": 0, "stdout": b"16.0"})

    request = RenderRequest(
        session.session_id, "demucs_stem", 17, "idem-stem", "now", "operator",
        parameters={"source_audio_uri": str(source), "stem": "drums"},
    )
    session.render_request = request
    adapter = LocalDemucsStemAdapter("demucs", export_dir=export, source_root=tmp_path, run_cmd=fake_run)
    artifact = adapter.render(session, request)
    assert artifact.kind == "audio"
    assert artifact.uri.startswith("file://")
    assert artifact.provenance[0].model == "htdemucs"
    assert artifact.provenance[0].metadata["stem"] == "drums"
    assert artifact.checksum
    store.close()


def test_local_artifact_qa_adapter_verifies_checksum_and_duration(tmp_path):
    from songbird_agent_mcp.adapters import LocalArtifactQAAdapter
    from songbird_agent_mcp.contracts import ArtifactRecord, ProvenanceRecord

    service, store, session = make_session()
    session.status = SessionStatus.RENDER_APPROVED.value
    session.instrumental_only = True
    request = RenderRequest(session.session_id, "demucs_stem", 16, "idem-stem-qa", "now", "operator")
    session.render_request = request
    stem = tmp_path / "drums.mp3"
    stem.write_bytes(b"STEMDATA")

    def fake_run(args, **kwargs):
        assert args[0] == "/usr/bin/ffprobe"
        return type("R", (), {"returncode": 0, "stdout": b"16.0"})

    session.status = SessionStatus.RENDERING.value
    session.artifact = ArtifactRecord(
        artifact_id="demucs-drums", kind="audio", uri=f"file://{stem}", mime_type="audio/mpeg",
        checksum=sha256(b"STEMDATA").hexdigest(), duration_seconds=16.0, created_at="now",
        provenance=[ProvenanceRecord(provider="demucs", model="htdemucs", request_id=None, created_at="now", source="test", metadata={})],
    )
    report = LocalArtifactQAAdapter(artifact_root=tmp_path, run_cmd=fake_run).evaluate(session)
    assert report.passed is True
    assert report.checksum_verified is True
    assert report.duration_verified is True
    store.close()


def test_comfy_m2m_render_adapter_rejects_unapproved_session_and_identity(tmp_path):
    service, store, session = make_session()
    request = RenderRequest(
        session.session_id, "ace_step_m2m", 161, "idem-m2m", "now", "operator",
        parameters={"source_audio_uri": "http://comfy:8188/view?filename=src.mp3&subfolder=audio&type=output"},
    )
    session.render_request = request
    adapter = ComfyM2MRenderAdapter("http://comfy:8188", tmp_path, timeout=5)
    with pytest.raises(AdapterError, match="render_approved"):
        adapter.render(session, request)
    session.status = SessionStatus.RENDER_APPROVED.value
    other = RenderRequest(session.session_id, "ace_step_m2m", 161, "different-idem", "now", "operator", parameters={"source_audio_uri": "x"})
    with pytest.raises(AdapterError, match="idempotency"):
        adapter.render(session, other)
    store.close()


def test_comfy_m2m_render_adapter_requires_configured_source_uri(tmp_path):
    service, store, session = make_session()
    session.status = SessionStatus.RENDER_APPROVED.value
    request = RenderRequest(session.session_id, "ace_step_m2m", 161, "idem-m2m", "now", "operator", parameters={})
    session.render_request = request
    adapter = ComfyM2MRenderAdapter("http://comfy:8188", tmp_path, timeout=5)
    with pytest.raises(AdapterError, match="source_audio_uri"):
        adapter.render(session, request)
    store.close()


class FakeCompletedProcess:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.stdout = b""
        self.stderr = b""


def test_comfy_mix_render_adapter_downloads_mixes_and_uploads_output(tmp_path):
    service, store, session = make_session()
    session.status = SessionStatus.RENDER_APPROVED.value
    request = RenderRequest(
        session.session_id,
        "ffmpeg_mix",
        161,
        "idem-mix",
        "now",
        "operator",
        parameters={
            "source_audio_uris": [
                "http://comfy:8188/view?filename=a.mp3&subfolder=audio&type=output",
                "http://comfy:8188/view?filename=b.mp3&subfolder=audio&type=output",
            ],
            "gains": [0.5, 0.5],
        },
    )
    session.render_request = request
    uploads = []
    downloads = []

    def get_json(req, timeout):
        downloads.append(req.full_url)
        return FakeResponse(b"MP3BYTES")

    def post_json(req, timeout):
        assert req.full_url.endswith("/upload/image")
        assert b'name="type"\r\n\r\noutput' in req.data
        uploads.append(req.data)
        return FakeResponse(b'{"name":"MIX_out_0000.mp3","subfolder":"","type":"output"}')

    def run_cmd(args, capture_output=True, timeout=0):
        assert "ffmpeg" in args[0]
        Path(args[-1]).write_bytes(b"MIXEDBYTES")
        return FakeCompletedProcess(0)

    adapter = ComfyMixRenderAdapter(
        "http://comfy:8188", tmp_path, timeout=5, post_json=post_json, get_json=get_json, run_cmd=run_cmd
    )
    artifact = adapter.render(session, request)
    assert len(downloads) == 2
    assert uploads, "mixed audio should be uploaded to ComfyUI output"
    assert artifact.uri == "http://comfy:8188/view?filename=MIX_out_0000.mp3&subfolder=&type=output"
    assert artifact.provenance[0].model == "ffmpeg_mix"
    assert artifact.provenance[0].metadata["gains"] == [0.5, 0.5]
    store.close()


def test_comfy_mix_render_adapter_requires_two_sources_on_same_instance(tmp_path):
    service, store, session = make_session()
    session.status = SessionStatus.RENDER_APPROVED.value
    request = RenderRequest(
        session.session_id, "ffmpeg_mix", 161, "idem-mix", "now", "operator",
        parameters={"source_audio_uris": ["http://comfy:8188/view?filename=a.mp3&subfolder=audio&type=output"]},
    )
    session.render_request = request
    adapter = ComfyMixRenderAdapter("http://comfy:8188", tmp_path, timeout=5)
    with pytest.raises(AdapterError, match="at least two"):
        adapter.render(session, request)
    request.parameters["source_audio_uris"] = [
        "http://comfy:8188/view?filename=a.mp3&subfolder=audio&type=output",
        "http://evil:8188/view?filename=b.mp3&subfolder=audio&type=output",
    ]
    with pytest.raises(AdapterError, match="configured ComfyUI"):
        adapter.render(session, request)
    store.close()


def test_comfy_mix_render_adapter_rejects_gain_length_mismatch(tmp_path):
    service, store, session = make_session()
    session.status = SessionStatus.RENDER_APPROVED.value
    request = RenderRequest(
        session.session_id, "ffmpeg_mix", 161, "idem-mix", "now", "operator",
        parameters={
            "source_audio_uris": [
                "http://comfy:8188/view?filename=a.mp3&subfolder=audio&type=output",
                "http://comfy:8188/view?filename=b.mp3&subfolder=audio&type=output",
            ],
            "gains": [0.5],
        },
    )
    session.render_request = request
    adapter = ComfyMixRenderAdapter("http://comfy:8188", tmp_path, timeout=5)
    with pytest.raises(AdapterError, match="gains"):
        adapter.render(session, request)
    store.close()


def test_comfy_mix_render_adapter_concat_mode_uses_concat_filter(tmp_path):
    service, store, session = make_session()
    session.status = SessionStatus.RENDER_APPROVED.value
    request = RenderRequest(
        session.session_id,
        "ffmpeg_mix",
        300,
        "idem-concat",
        "now",
        "operator",
        parameters={
            "source_audio_uris": [
                "http://comfy:8188/view?filename=a.mp3&subfolder=audio&type=output",
                "http://comfy:8188/view?filename=b.mp3&subfolder=audio&type=output",
            ],
            "mode": "concat",
        },
    )
    session.render_request = request
    captured = []

    def get_json(req, timeout):
        return FakeResponse(b"MP3BYTES")

    def post_json(req, timeout):
        return FakeResponse(b'{"name":"MIX_concat_0000.mp3","subfolder":"","type":"output"}')

    def run_cmd(args, capture_output=True, timeout=0):
        captured.append(args)
        Path(args[-1]).write_bytes(b"CONCATBYTES")
        return FakeCompletedProcess(0)

    adapter = ComfyMixRenderAdapter(
        "http://comfy:8188", tmp_path, timeout=5, post_json=post_json, get_json=get_json, run_cmd=run_cmd
    )
    artifact = adapter.render(session, request)
    filter_idx = captured[0].index("-filter_complex")
    filter_complex = captured[0][filter_idx + 1]
    assert "concat=n=2:v=0:a=1[out]" in filter_complex
    assert "amix" not in filter_complex
    assert artifact.provenance[0].metadata["mode"] == "concat"
    assert artifact.uri == "http://comfy:8188/view?filename=MIX_concat_0000.mp3&subfolder=&type=output"
    store.close()


def test_comfy_mix_render_adapter_rejects_invalid_mode(tmp_path):
    service, store, session = make_session()
    session.status = SessionStatus.RENDER_APPROVED.value
    request = RenderRequest(
        session.session_id, "ffmpeg_mix", 161, "idem-mode", "now", "operator",
        parameters={
            "source_audio_uris": [
                "http://comfy:8188/view?filename=a.mp3&subfolder=audio&type=output",
                "http://comfy:8188/view?filename=b.mp3&subfolder=audio&type=output",
            ],
            "mode": "fade",
        },
    )
    session.render_request = request
    adapter = ComfyMixRenderAdapter("http://comfy:8188", tmp_path, timeout=5)
    with pytest.raises(AdapterError, match="mode"):
        adapter.render(session, request)
    store.close()


def test_comfy_mix_render_adapter_concat_crossfade_mode_uses_chained_crossfades(tmp_path):
    service, store, session = make_session()
    session.status = SessionStatus.RENDER_APPROVED.value
    request = RenderRequest(
        session.session_id,
        "ffmpeg_mix",
        300,
        "idem-crossfade",
        "now",
        "operator",
        parameters={
            "source_audio_uris": [
                "http://comfy:8188/view?filename=a.mp3&subfolder=audio&type=output",
                "http://comfy:8188/view?filename=b.mp3&subfolder=audio&type=output",
                "http://comfy:8188/view?filename=c.mp3&subfolder=audio&type=output",
            ],
            "mode": "concat_crossfade",
            "crossfade_seconds": 1.25,
        },
    )
    session.render_request = request
    captured = []

    def get_json(req, timeout):
        return FakeResponse(b"MP3BYTES")

    def post_json(req, timeout):
        return FakeResponse(b'{"name":"MIX_crossfade_0000.mp3","subfolder":"","type":"output"}')

    def run_cmd(args, capture_output=True, timeout=0):
        captured.append(args)
        Path(args[-1]).write_bytes(b"CROSSFADEBYTES")
        return FakeCompletedProcess(0)

    adapter = ComfyMixRenderAdapter(
        "http://comfy:8188", tmp_path, timeout=5, post_json=post_json, get_json=get_json, run_cmd=run_cmd
    )
    artifact = adapter.render(session, request)
    filter_idx = captured[0].index("-filter_complex")
    filter_complex = captured[0][filter_idx + 1]
    assert filter_complex.count("acrossfade=d=1.2500") == 2
    assert "concat=n=3" not in filter_complex
    assert artifact.provenance[0].metadata["mode"] == "concat_crossfade"
    assert artifact.provenance[0].metadata["crossfade_seconds"] == 1.25
    store.close()


def test_comfy_artifact_qa_verifies_wav_bytes_and_duration():
    _, store, session = make_session()
    session.status = SessionStatus.RENDERING.value
    session.artifact = ArtifactRecord(
        artifact_id="comfy-prompt-1",
        kind="audio",
        uri="http://comfy:8188/view?filename=song.wav&subfolder=audio&type=output",
        mime_type="audio/wav",
        checksum=None,
        duration_seconds=1.0,
        created_at="now",
    )
    output = BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(8000)
        wav_file.writeframes(b"\x00\x00" * 8000)
    session.artifact.checksum = sha256(output.getvalue()).hexdigest()

    report = ComfyArtifactQAAdapter("http://comfy:8188", getter=lambda request, timeout: FakeResponse(output.getvalue())).evaluate(session)
    assert report.passed is True
    assert report.checksum_verified is True
    assert report.duration_verified is True
    assert report.findings[0].startswith("sha256=")
    store.close()


def test_comfy_artifact_qa_blocks_unsupported_mp3_duration():
    _, store, session = make_session()
    session.artifact = ArtifactRecord(
        artifact_id="comfy-prompt-2",
        kind="audio",
        uri="http://comfy:8188/view?filename=song.mp3&subfolder=audio&type=output",
        mime_type="audio/mpeg",
        checksum=None,
        duration_seconds=1.0,
        created_at="now",
    )
    report = ComfyArtifactQAAdapter("http://comfy:8188", getter=lambda request, timeout: FakeResponse(b"ID3bytes")).evaluate(session)
    assert report.passed is False
    assert report.duration_verified is False
    assert "unsupported" in report.findings[-1]
    store.close()


def test_comfy_artifact_qa_mp3_duration_falls_back_to_xing_header():
    _, store, session = make_session()
    session.status = SessionStatus.RENDERING.value
    session.render_request = RenderRequest(session.session_id, "minimax_music", 60.0, "idem-xing", "now", "operator")
    session.artifact = ArtifactRecord(
        artifact_id="comfy-prompt-xing",
        kind="audio",
        uri="http://comfy:8188/view?filename=song.mp3&subfolder=audio&type=output",
        mime_type="audio/mpeg",
        checksum=sha256(XING_MP3).hexdigest(),
        duration_seconds=60.0,
        created_at="now",
    )
    verifier = ComfyArtifactQAAdapter(
        "http://comfy:8188",
        getter=lambda request, timeout: FakeResponse(XING_MP3),
        duration_probe=ComfyArtifactQAAdapter._probe_duration,
    )
    report = verifier.evaluate(session)
    assert report.passed is True
    assert report.duration_verified is True
    store.close()


def test_comfy_artifact_qa_mp3_xing_probe_matches_real_minimax_duration():
    adapter = ComfyArtifactQAAdapter("http://comfy:8188")
    duration = adapter._probe_mp3_duration(XING_MP3)
    assert duration is not None
    assert abs(duration - 60.0) <= 1.0


def test_comfy_artifact_qa_blocks_requested_submitted_duration_mismatch():
    _, store, session = make_session()
    session.render_request = RenderRequest(session.session_id, "comfyui", 3.0, "idem-duration", "now", "operator")
    session.artifact = ArtifactRecord(
        artifact_id="comfy-prompt-3",
        kind="audio",
        uri="http://comfy:8188/view?filename=song.flac&subfolder=audio&type=output",
        mime_type="audio/flac",
        checksum=None,
        duration_seconds=2.0,
        created_at="now",
    )
    verifier = ComfyArtifactQAAdapter(
        "http://comfy:8188",
        getter=lambda request, timeout: FakeResponse(b"flac-bytes"),
        duration_probe=lambda payload, mime_type: 3.0,
    )
    report = verifier.evaluate(session)
    assert report.passed is False
    assert report.duration_verified is False
    assert "submitted=2.000s" in report.findings[-1]
    store.close()


def test_comfy_artifact_qa_accepts_injected_mp3_duration_probe():
    _, store, session = make_session()
    session.render_request = RenderRequest(session.session_id, "comfyui", 4.0, "idem-mp3", "now", "operator")
    session.artifact = ArtifactRecord(
        artifact_id="comfy-prompt-4",
        kind="audio",
        uri="http://comfy:8188/view?filename=song.mp3&subfolder=audio&type=output",
        mime_type="audio/mpeg",
        checksum=sha256(b"mp3-bytes").hexdigest(),
        duration_seconds=4.0,
        created_at="now",
    )
    verifier = ComfyArtifactQAAdapter(
        "http://comfy:8188",
        getter=lambda request, timeout: FakeResponse(b"mp3-bytes"),
        duration_probe=lambda payload, mime_type: 4.0,
    )
    report = verifier.evaluate(session)
    assert report.passed is True
    assert report.duration_verified is True
    store.close()


def test_comfy_artifact_qa_missing_render_checksum_fails_closed():
    _, store, session = make_session()
    session.render_request = RenderRequest(session.session_id, "comfyui", 4.0, "idem-nochk", "now", "operator")
    session.artifact = ArtifactRecord(
        artifact_id="comfy-prompt-nochk",
        kind="audio",
        uri="http://comfy:8188/view?filename=song.mp3&subfolder=audio&type=output",
        mime_type="audio/mpeg",
        checksum=None,
        duration_seconds=4.0,
        created_at="now",
    )
    verifier = ComfyArtifactQAAdapter(
        "http://comfy:8188",
        getter=lambda request, timeout: FakeResponse(b"mp3-bytes"),
        duration_probe=lambda payload, mime_type: 4.0,
    )
    report = verifier.evaluate(session)
    assert report.passed is False
    assert report.checksum_verified is False
    assert any("no render-time checksum" in finding for finding in report.findings)
    store.close()


def test_comfy_artifact_qa_maps_transport_failure_to_adapter_error():
    from urllib.error import URLError

    _, store, session = make_session()
    session.artifact = ArtifactRecord(
        "comfy-x", "audio", "http://comfy:8188/view?filename=a.mp3&subfolder=&type=output", "audio/mpeg", "abc", 4.0, "now",
    )

    def getter(request, timeout):
        raise URLError("connection refused")

    with pytest.raises(AdapterError, match="artifact download failed"):
        ComfyArtifactQAAdapter("http://comfy:8188", getter=getter).evaluate(session)
    store.close()


def test_comfy_render_adapter_maps_transport_failure_to_adapter_error(tmp_path):
    from urllib.error import URLError

    _, store, session = make_session()
    session.status = SessionStatus.RENDER_APPROVED.value
    request = RenderRequest(session.session_id, "minimax_music", 60, "idem-down", "now", "operator")
    session.render_request = request
    (tmp_path / "audio_minimax_music_3.json").write_text("{}", encoding="utf-8")

    def post_json(req, timeout):
        raise URLError("connection refused")

    adapter = ComfyRenderAdapter("http://comfy:8188", tmp_path, timeout=5, post_json=post_json, get_json=post_json)
    with pytest.raises(AdapterError, match="prompt submission failed"):
        adapter.render(session, request)
    store.close()


def test_comfy_render_adapter_rejects_backends_owned_by_other_adapters(tmp_path):
    _, store, session = make_session()
    session.status = SessionStatus.RENDER_APPROVED.value
    request = RenderRequest(session.session_id, "demucs", 60, "idem-wrong", "now", "operator")
    session.render_request = request
    adapter = ComfyRenderAdapter("http://comfy:8188", tmp_path, timeout=5)
    with pytest.raises(AdapterError, match="dedicated adapter"):
        adapter.render(session, request)
    store.close()


@pytest.mark.parametrize("configure_root", [False, True])
def test_comfy_m2m_rejects_local_source_outside_allowed_root(tmp_path, configure_root):
    _, store, session = make_session()
    session.status = SessionStatus.RENDER_APPROVED.value
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"PRIVATE")
    request = RenderRequest(
        session.session_id, "ace_step_m2m", 30, "idem-escape", "now", "operator",
        parameters={"source_audio_uri": str(allowed / ".." / "secret.txt")},
    )
    session.render_request = request
    uploads = []

    def post_json(req, timeout):
        uploads.append(req.data)
        raise AssertionError("nothing may be uploaded")

    adapter = ComfyM2MRenderAdapter(
        "http://comfy:8188", tmp_path, source_root=allowed if configure_root else None, timeout=5, post_json=post_json,
    )
    with pytest.raises(AdapterError, match="source"):
        adapter.render(session, request)
    assert uploads == []
    store.close()


def test_local_demucs_rejects_source_outside_allowed_root(tmp_path):
    _, store, session = make_session()
    session.status = SessionStatus.RENDER_APPROVED.value
    outside = tmp_path / "outside.mp3"
    outside.write_bytes(b"MP3")
    request = RenderRequest(
        session.session_id, "demucs_stem", 30, "idem-demucs-escape", "now", "operator",
        parameters={"source_audio_uri": str(outside), "stem": "drums"},
    )
    session.render_request = request
    runs = []
    adapter = LocalDemucsStemAdapter(
        "demucs", export_dir=tmp_path / "out", source_root=tmp_path / "allowed", run_cmd=lambda *a, **k: runs.append(a),
    )
    with pytest.raises(AdapterError, match="outside the allowed source root"):
        adapter.render(session, request)
    assert runs == []
    store.close()


def test_mp3_xing_probe_accepts_mpeg2_frame_sync():
    # MPEG-2 Layer III, no CRC (0xFF 0xF3), 24 kHz, 576 samples per frame.
    header = bytes([0xFF, 0xF3, 0x90 | (1 << 2), 0x00])
    payload = header + b"\x00" * 32 + b"Xing" + b"\x00\x00\x00\x0f" + (2500).to_bytes(4, "big")
    duration = ComfyArtifactQAAdapter._probe_mp3_duration(payload)
    assert duration is not None
    assert abs(duration - 2500 * 576 / 24000) < 1e-6


def _local_artifact_session(path, checksum):
    _, store, session = make_session()
    session.status = SessionStatus.RENDERING.value
    session.render_request = RenderRequest(session.session_id, "demucs_stem", 16, "idem-local", "now", "operator")
    session.artifact = ArtifactRecord(
        artifact_id="demucs-x", kind="audio", uri=f"file://{path}", mime_type="audio/mpeg",
        checksum=checksum, duration_seconds=16.0, created_at="now",
    )
    return store, session


@pytest.mark.parametrize("configure_root", [False, True])
def test_local_artifact_qa_refuses_paths_outside_artifact_root(tmp_path, configure_root):
    outside = tmp_path / "secret.mp3"
    outside.write_bytes(b"PRIVATE")
    store, session = _local_artifact_session(outside, sha256(b"PRIVATE").hexdigest())
    runs = []
    verifier = LocalArtifactQAAdapter(
        artifact_root=(tmp_path / "exports") if configure_root else None,
        run_cmd=lambda *a, **k: runs.append(a),
    )
    with pytest.raises(AdapterError, match="artifact root|disabled"):
        verifier.evaluate(session)
    assert runs == []
    store.close()


@pytest.mark.parametrize("probe_output", [b"nan", b"inf", b"-1"])
def test_local_artifact_qa_treats_non_finite_duration_as_unsupported(tmp_path, probe_output):
    stem = tmp_path / "drums.mp3"
    stem.write_bytes(b"STEM")
    store, session = _local_artifact_session(stem, sha256(b"STEM").hexdigest())
    verifier = LocalArtifactQAAdapter(
        artifact_root=tmp_path,
        run_cmd=lambda *a, **k: type("R", (), {"returncode": 0, "stdout": probe_output}),
    )
    report = verifier.evaluate(session)
    assert report.passed is False
    assert report.duration_verified is False
    assert report.findings[-1] == "duration probe unsupported"
    store.close()
