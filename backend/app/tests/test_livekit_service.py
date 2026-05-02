import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def add_backend_to_sys_path():
    backend_dir = Path(__file__).resolve().parents[2]
    backend_dir = str(backend_dir)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)


def test_openai_realtime_connect_uses_client_secret_flow(monkeypatch):
    from app.services import livekit_service
    from app.services.livekit_service import LiveKitService

    captured = {}

    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "value": "ephemeral-secret",
                "expires_at": 1_234_567_890,
                "session": {
                    "id": "sess_123",
                    "model": "gpt-realtime",
                },
            }

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return DummyResponse()

    monkeypatch.setattr(livekit_service.requests, "post", fake_post)

    result = LiveKitService(
        {
            "stream_backend": "api",
            "api_key": "test-key",
            "realtime_model": "gpt-realtime",
            "voice_model": "kitten",
            "realtime_voice": "marin",
            "realtime_base_url": "https://api.openai.com/v1/realtime/client_secrets",
            "realtime_connect_url": "https://api.openai.com/v1/realtime/calls",
            "system_prompt": "You are float.",
        }
    ).connect("user-1", "float")

    assert captured["url"] == "https://api.openai.com/v1/realtime/client_secrets"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["json"]["session"]["type"] == "realtime"
    assert captured["json"]["session"]["model"] == "gpt-realtime"
    assert captured["json"]["session"]["audio"]["output"]["voice"] == "marin"
    assert (
        captured["json"]["session"]["audio"]["input"]["turn_detection"]["type"]
        == "server_vad"
    )
    assert (
        captured["json"]["session"]["audio"]["input"]["turn_detection"][
            "create_response"
        ]
        is False
    )
    assert captured["json"]["session"]["audio"]["input"]["transcription"] == {
        "model": "gpt-4o-mini-transcribe"
    }
    assert captured["json"]["expires_after"] == {
        "anchor": "created_at",
        "seconds": 600,
    }
    assert captured["json"]["session"]["instructions"] == "You are float."
    assert result == {
        "provider": "openai-realtime",
        "url": "https://api.openai.com/v1/realtime/calls",
        "client_secret": "ephemeral-secret",
        "expires_at": 1_234_567_890,
        "model": "gpt-realtime",
        "response_model": "gpt-realtime",
        "multimodal_model": "",
        "caption_model": "",
        "session": {
            "id": "sess_123",
            "model": "gpt-realtime",
        },
        "session_id": "sess_123",
        "source": "live",
        "transport": "webrtc",
        "mode": "api",
        "runtime": {
            "source": "live",
            "transport_backend": "api",
            "provider": "openai-realtime",
            "mode": "api",
            "response_model": "gpt-realtime",
            "multimodal_model": "",
            "caption_model": "",
            "stt_model": "",
            "tts_model": "",
            "voice_model": "marin",
            "supports_visual_input": False,
        },
        "voice": "marin",
    }


def test_livekit_connect_returns_room_token():
    from app.services.livekit_service import LiveKitService

    service = LiveKitService(
        {
            "stream_backend": "livekit",
            "livekit_api_key": "api-key",
            "livekit_secret": "secret",
            "livekit_url": "ws://localhost:7880",
        }
    )
    service.generate_token = lambda identity, room: f"token-for:{identity}:{room}"

    result = service.connect("user-1", "float")

    assert result["provider"] == "livekit"
    assert result["url"] == "ws://localhost:7880"
    assert result["token"] == "token-for:user-1:float"
    assert result["runtime"]["provider"] == "livekit"
    assert result["runtime"]["mode"] == "local"
    assert result["response_model"] == ""
    assert result["multimodal_model"] == ""
    assert "float" in service.rooms


def test_local_bridge_connect_returns_runtime_metadata():
    from app.services.livekit_service import LiveKitService

    service = LiveKitService(
        {
            "stream_backend": "local",
            "live_agent_mode": "local",
            "live_agent_model": "gemma-4-E2B-it",
            "live_multimodal_model": "gemma-4-E4B-it",
            "vision_model": "google/paligemma2-3b-pt-224",
        }
    )

    result = service.connect("user-1", "float")

    assert result["provider"] == "float-local-live"
    assert result["transport"] == "local-bridge"
    assert result["status"] == "planned"
    assert result["detail"] == (
        "Local live bridge is selected, but browser duplex audio is not wired yet."
    )
    assert result["mode"] == "local"
    assert result["model"] == "gemma-4-E2B-it"
    assert result["response_model"] == "gemma-4-E2B-it"
    assert result["multimodal_model"] == "gemma-4-E4B-it"
    assert result["caption_model"] == "google/paligemma2-3b-pt-224"
    assert result["session_id"].startswith("local-live-")
    assert result["runtime"] == {
        "source": "live",
        "transport_backend": "local",
        "provider": "float-local-live",
        "mode": "local",
        "response_model": "gemma-4-E2B-it",
        "multimodal_model": "gemma-4-E4B-it",
        "caption_model": "google/paligemma2-3b-pt-224",
        "stt_model": "",
        "tts_model": "",
        "voice_model": "",
        "supports_visual_input": True,
    }


def test_livekit_connect_reports_live_runtime_models():
    from app.services.livekit_service import LiveKitService

    service = LiveKitService(
        {
            "stream_backend": "livekit",
            "livekit_api_key": "api-key",
            "livekit_secret": "secret",
            "livekit_url": "ws://localhost:7880",
            "live_agent_mode": "server",
            "live_agent_model": "gemma-4-E4B-it",
            "live_multimodal_model": "gemma-4-26B-A4B-it",
            "vision_model": "google/paligemma2-3b-pt-224",
        }
    )
    service.generate_token = lambda identity, room: f"token-for:{identity}:{room}"

    result = service.connect("user-1", "float")

    assert result["mode"] == "server"
    assert result["model"] == "gemma-4-E4B-it"
    assert result["response_model"] == "gemma-4-E4B-it"
    assert result["multimodal_model"] == "gemma-4-26B-A4B-it"
    assert result["caption_model"] == "google/paligemma2-3b-pt-224"
    assert result["runtime"] == {
        "source": "live",
        "transport_backend": "livekit",
        "provider": "livekit",
        "mode": "server",
        "response_model": "gemma-4-E4B-it",
        "multimodal_model": "gemma-4-26B-A4B-it",
        "caption_model": "google/paligemma2-3b-pt-224",
        "stt_model": "",
        "tts_model": "",
        "voice_model": "",
        "supports_visual_input": True,
    }


def test_livekit_runtime_defaults_multimodal_to_vision_capable_response_model():
    from app.services.livekit_service import LiveKitService

    service = LiveKitService(
        {
            "stream_backend": "livekit",
            "livekit_api_key": "api-key",
            "livekit_secret": "secret",
            "livekit_url": "ws://localhost:7880",
            "live_agent_mode": "server",
            "live_agent_model": "gemma-4-E4B-it",
            "vision_model": "google/paligemma2-3b-pt-224",
        }
    )
    service.generate_token = lambda identity, room: f"token-for:{identity}:{room}"

    result = service.connect("user-1", "float")

    assert result["multimodal_model"] == "gemma-4-E4B-it"
    assert result["runtime"]["supports_visual_input"] is True
