import builtins
import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def add_backend_to_sys_path():
    backend_dir = Path(__file__).resolve().parents[2]
    backend_dir = str(backend_dir)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)


def test_openai_tts_invalid_voice_falls_back_to_alloy(monkeypatch):
    from app.services import tts_service
    from app.services.tts_service import TTSService

    captured = {}

    class DummyResponse:
        content = b"audio-bytes"
        headers = {"Content-Type": "audio/mpeg"}

        def raise_for_status(self):
            return None

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return DummyResponse()

    monkeypatch.setattr(tts_service.requests, "post", fake_post)

    result = TTSService().synthesize(
        "Hello world",
        cfg={
            "api_key": "test-key",
            "tts_model": "tts-1",
            "voice_model": "kitten",
        },
        model="tts-1",
        voice="kitten",
        audio_format="mp3",
    )

    assert captured["url"] == "https://api.openai.com/v1/audio/speech"
    assert captured["json"]["model"] == "tts-1"
    assert captured["json"]["voice"] == "alloy"
    assert captured["json"]["response_format"] == "mp3"
    assert result.provider == "openai"
    assert result.voice == "alloy"


def test_openai_gpt4o_mini_tts_routes_to_api(monkeypatch):
    from app.services import tts_service
    from app.services.tts_service import TTSService

    captured = {}

    class DummyResponse:
        content = b"audio-bytes"
        headers = {"Content-Type": "audio/wav"}

        def raise_for_status(self):
            return None

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return DummyResponse()

    monkeypatch.setattr(tts_service.requests, "post", fake_post)

    result = TTSService().synthesize(
        "Hello world",
        cfg={"api_key": "test-key", "tts_model": "gpt-4o-mini-tts"},
        model="gpt-4o-mini-tts",
        voice="nova",
        audio_format="wav",
    )

    assert captured["url"] == "https://api.openai.com/v1/audio/speech"
    assert captured["json"]["model"] == "gpt-4o-mini-tts"
    assert captured["json"]["voice"] == "nova"
    assert result.provider == "openai"
    assert result.model == "gpt-4o-mini-tts"


def test_openai_gpt4o_mini_tts_allows_expanded_voice_set(monkeypatch):
    from app.services import tts_service
    from app.services.tts_service import TTSService

    captured = {}

    class DummyResponse:
        content = b"audio-bytes"
        headers = {"Content-Type": "audio/wav"}

        def raise_for_status(self):
            return None

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return DummyResponse()

    monkeypatch.setattr(tts_service.requests, "post", fake_post)

    result = TTSService().synthesize(
        "Hello world",
        cfg={"api_key": "test-key", "tts_model": "gpt-4o-mini-tts"},
        model="gpt-4o-mini-tts",
        voice="marin",
        audio_format="pcm",
    )

    assert captured["json"]["voice"] == "marin"
    assert captured["json"]["response_format"] == "pcm"
    assert result.voice == "marin"


def test_legacy_openai_tts_rejects_expanded_voice_to_alloy(monkeypatch):
    from app.services import tts_service
    from app.services.tts_service import TTSService

    captured = {}

    class DummyResponse:
        content = b"audio-bytes"
        headers = {"Content-Type": "audio/wav"}

        def raise_for_status(self):
            return None

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return DummyResponse()

    monkeypatch.setattr(tts_service.requests, "post", fake_post)

    result = TTSService().synthesize(
        "Hello world",
        cfg={"api_key": "test-key", "tts_model": "tts-1"},
        model="tts-1",
        voice="marin",
        audio_format="wav",
    )

    assert captured["json"]["voice"] == "alloy"
    assert result.voice == "alloy"


def test_tts_rejects_unsupported_audio_formats_before_provider_call(monkeypatch):
    from app.services import tts_service
    from app.services.tts_service import TTSService

    def fail_post(*_args, **_kwargs):
        raise AssertionError("OpenAI request should not be sent")

    monkeypatch.setattr(tts_service.requests, "post", fail_post)

    with pytest.raises(ValueError, match="Unsupported OpenAI TTS audio_format"):
        TTSService().synthesize(
            "Hello world",
            cfg={"api_key": "test-key", "tts_model": "tts-1"},
            model="tts-1",
            audio_format="webm",
        )

    with pytest.raises(ValueError, match="Local TTS only supports wav output"):
        TTSService().synthesize(
            "Hello world",
            cfg={"tts_model": "kitten"},
            model="kitten",
            audio_format="mp3",
        )


def test_kitten_tts_missing_dependency_has_actionable_error(monkeypatch):
    from app.services.tts_service import TTSService

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "kittentts":
            raise ImportError("missing kittentts")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(RuntimeError, match="Kitten TTS requires.*kittentts"):
        TTSService().synthesize(
            "Hello world",
            cfg={"tts_model": "kitten"},
            model="kitten",
            audio_format="wav",
        )
