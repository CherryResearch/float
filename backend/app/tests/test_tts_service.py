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
