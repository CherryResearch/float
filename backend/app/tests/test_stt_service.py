import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def add_backend_to_sys_path():
    backend_dir = Path(__file__).resolve().parents[2]
    backend_dir = str(backend_dir)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)


def test_api_stt_posts_selected_transcription_model(monkeypatch):
    from app.services import stt_service
    from app.services.stt_service import STTService

    captured = {}

    class DummyResponse:
        status_code = 200

        def json(self):
            return {"text": "hello from api"}

    def fake_post(url, headers=None, files=None, data=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["files"] = files
        captured["data"] = data
        captured["timeout"] = timeout
        return DummyResponse()

    monkeypatch.setattr(stt_service.requests, "post", fake_post)

    result = STTService().transcribe(
        b"audio",
        {"api_key": "test-key", "stt_model": "gpt-4o-mini-transcribe"},
        filename="recording.webm",
        content_type="audio/webm",
    )

    assert captured["url"] == "https://api.openai.com/v1/audio/transcriptions"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["files"]["file"] == ("recording.webm", b"audio", "audio/webm")
    assert captured["data"] == {"model": "gpt-4o-mini-transcribe"}
    assert result.provider == "openai"
    assert result.model == "gpt-4o-mini-transcribe"
    assert result.text == "hello from api"


def test_api_stt_maps_realtime_whisper_for_recorded_uploads(monkeypatch):
    from app.services import stt_service
    from app.services.stt_service import STTService

    captured = {}

    class DummyResponse:
        status_code = 200

        def json(self):
            return {"text": "hello from recorded mic"}

    def fake_post(url, headers=None, files=None, data=None, timeout=None):
        captured["data"] = data
        return DummyResponse()

    monkeypatch.setattr(stt_service.requests, "post", fake_post)

    result = STTService().transcribe(
        b"audio",
        {"api_key": "test-key", "stt_model": "gpt-realtime-whisper"},
        filename="recording.webm",
        content_type="audio/webm",
    )

    assert captured["data"] == {"model": "gpt-4o-mini-transcribe"}
    assert result.provider == "openai"
    assert result.model == "gpt-4o-mini-transcribe"
    assert result.text == "hello from recorded mic"


def test_local_stt_uses_local_pipeline_without_api_key(monkeypatch, tmp_path):
    from app.services import stt_service
    from app.services.stt_service import STTService

    seen = {}

    def fail_post(*args, **kwargs):  # pragma: no cover - assertion helper
        raise AssertionError("local STT should not call OpenAI")

    def fake_search_dirs(custom_path=None):
        return [tmp_path]

    def fake_load_pipeline(model_id, **kwargs):
        seen["model_id"] = model_id
        seen["kwargs"] = kwargs

        def pipe(path):
            assert Path(path).exists()
            return {"text": "hello from local"}

        return pipe

    monkeypatch.setattr(stt_service.requests, "post", fail_post)
    monkeypatch.setattr(stt_service.app_config, "model_search_dirs", fake_search_dirs)
    monkeypatch.setattr(stt_service, "_load_pipeline", fake_load_pipeline)

    result = STTService().transcribe(
        b"audio",
        {"stt_model": "whisper-small", "allow_remote_code": False},
        filename="recording.wav",
        content_type="audio/wav",
    )

    assert seen["model_id"] == "openai/whisper-small"
    assert seen["kwargs"]["local_files_only"] is True
    assert seen["kwargs"]["trust_remote_code"] is False
    assert result.provider == "local"
    assert result.model == "whisper-small"
    assert result.text == "hello from local"
