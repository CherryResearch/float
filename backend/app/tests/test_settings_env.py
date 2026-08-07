import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def add_backend_to_sys_path():
    backend_dir = Path(__file__).resolve().parents[2]
    backend_dir = str(backend_dir)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)


@pytest.fixture(autouse=True)
def use_temp_dotenv(monkeypatch, tmp_path):
    # Prevent accidental writes to the repo-root `.env` during tests.
    monkeypatch.setenv("FLOAT_ENV_FILE", str(tmp_path / ".env"))
    # Keep tests deterministic even when the developer's repo `.env` uses server mode.
    monkeypatch.setenv("MODE", "api")


@pytest.fixture
def client(add_backend_to_sys_path, monkeypatch):
    from app import routes
    from app.config import DEFAULT_MODELS_DIR
    from app.main import app

    test_config = dict(app.state.config)
    test_config["mode"] = "api"
    test_config["models_folder"] = str(DEFAULT_MODELS_DIR)
    monkeypatch.setattr(app.state, "config", test_config)
    monkeypatch.setattr(routes.llm_service, "mode", "api")
    monkeypatch.setattr(routes.llm_service, "config", test_config)
    return TestClient(app)


def test_app_bootstrap_seeds_stable_mode_into_config(client):
    from app import routes

    assert client.app.state.config["mode"] == "api"
    assert routes.llm_service.mode == "api"
    assert routes.llm_service.config is client.app.state.config


def test_update_settings_creates_dotenv(tmp_path, client):
    env_path = tmp_path / ".env"
    assert not env_path.exists()

    resp = client.post("/settings", json={"api_key": "testkey"})
    assert resp.status_code == 200
    assert env_path.exists()
    content = env_path.read_text()
    assert "OPENAI_API_KEY" in content and "API_KEY" in content
    assert "testkey" in content
    payload = resp.json()
    settings = payload.get("settings") or {}
    assert settings.get("api_key") == ""
    assert settings.get("api_key_set") is True


def test_models_folder_setting(client, tmp_path):
    from app.config import DEFAULT_MODELS_DIR

    resp = client.get("/settings")
    default_dir = DEFAULT_MODELS_DIR
    assert resp.status_code == 200
    assert resp.json()["models_folder"] == str(default_dir)

    new_dir = tmp_path / "custom_models"
    resp2 = client.post("/settings", json={"models_folder": str(new_dir)})
    assert resp2.status_code == 200
    assert resp2.json()["settings"]["models_folder"] == str(new_dir)


def test_mode_setting_updates_config_and_rejects_invalid_values(client):
    resp = client.post("/settings", json={"mode": "local"})
    assert resp.status_code == 200
    assert resp.json()["mode"] == "local"
    assert client.app.state.config["mode"] == "local"

    resp2 = client.get("/settings")
    assert resp2.status_code == 200
    assert resp2.json()["mode"] == "local"

    invalid = client.post("/settings", json={"mode": "definitely-not-a-mode"})
    assert invalid.status_code == 400
    assert invalid.json()["detail"] == "Invalid mode"
    assert client.app.state.config["mode"] == "local"


def test_image_caption_settings_are_explicit_and_use_supported_cloud_default(client):
    initial = client.get("/settings")
    assert initial.status_code == 200
    assert initial.json()["image_caption_engine"] in {"local", "cloud", "off"}
    assert initial.json()["image_caption_cloud_model"] == "gpt-5.4-nano"

    updated = client.post(
        "/settings",
        json={
            "image_caption_engine": "cloud",
            "image_caption_cloud_model": "gpt-5.4-nano",
        },
    )
    assert updated.status_code == 200
    settings = updated.json()["settings"]
    assert settings["image_caption_engine"] == "cloud"
    assert settings["image_caption_cloud_model"] == "gpt-5.4-nano"
    assert client.app.state.config["image_caption_engine"] == "cloud"

    invalid = client.post(
        "/settings",
        json={"image_caption_engine": "automatic"},
    )
    assert invalid.status_code == 400
    assert invalid.json()["detail"] == "Invalid image_caption_engine"


def test_local_provider_settings(client, tmp_path):
    env_path = tmp_path / ".env"
    resp = client.post(
        "/settings",
        json={
            "local_provider": "ollama",
            "local_provider_mode": "remote-unmanaged",
            "local_provider_port": 11434,
            "local_provider_api_token": "provider-secret",
        },
    )
    assert resp.status_code == 200
    settings = resp.json().get("settings") or {}
    assert settings.get("local_provider") == "ollama"
    assert settings.get("local_provider_mode") == "remote-unmanaged"
    assert settings.get("local_provider_api_token") == ""
    assert settings.get("local_provider_api_token_set") is True
    content = env_path.read_text()
    assert "LOCAL_PROVIDER" in content and "ollama" in content
    assert "LOCAL_PROVIDER_MODE" in content and "remote-unmanaged" in content
    assert "LOCAL_PROVIDER_API_TOKEN" in content and "provider-secret" in content


def test_server_presets_persist_metadata_without_secrets(client, tmp_path, monkeypatch):
    monkeypatch.setenv("CUSTOM_INFERENCE_KEY", "environment-secret")
    env_path = tmp_path / ".env"
    response = client.post(
        "/settings",
        json={
            "server_preset_id": "custom-lab",
            "server_url": "https://models.example.test/v1",
            "server_presets": [
                {
                    "id": "custom-lab",
                    "name": "Lab endpoint",
                    "provider": "openai-compatible",
                    "base_url": "https://models.example.test/v1",
                    "api_key_env": "CUSTOM_INFERENCE_KEY",
                    "api_key": "should-be-ignored",
                }
            ],
        },
    )

    assert response.status_code == 200
    settings = response.json().get("settings") or {}
    assert settings["server_preset_id"] == "custom-lab"
    presets = {item["id"]: item for item in settings["server_presets"]}
    assert "tinker" in presets
    assert presets["custom-lab"]["api_key_set"] is True
    assert "api_key" not in presets["custom-lab"]
    content = env_path.read_text()
    assert "SERVER_PRESET_ID" in content and "custom-lab" in content
    assert "SERVER_PRESETS_JSON" in content
    assert "CUSTOM_INFERENCE_KEY" in content
    assert "environment-secret" not in content
    assert "should-be-ignored" not in content


def test_mode_setting_persists_to_env_and_settings(client, tmp_path):
    env_path = tmp_path / ".env"
    resp = client.post("/settings", json={"mode": "local"})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload.get("mode") == "local"
    settings = payload.get("settings") or {}
    assert settings.get("mode") == "local"
    content = env_path.read_text()
    assert "MODE" in content and "local" in content

    resp2 = client.get("/settings")
    assert resp2.status_code == 200
    assert resp2.json()["mode"] == "local"


def test_harmony_format_mode_persists_as_tristate(client, tmp_path):
    env_path = tmp_path / ".env"
    resp = client.post("/settings", json={"harmony_format_mode": "auto"})
    assert resp.status_code == 200
    settings = resp.json().get("settings") or {}
    assert settings.get("harmony_format_mode") == "auto"
    assert settings.get("harmony_format") is True
    content = env_path.read_text()
    assert "HARMONY_FORMAT_MODE" in content and "auto" in content
    assert "HARMONY_FORMAT" in content and "true" in content

    resp2 = client.post("/settings", json={"harmony_format_mode": "disabled"})
    assert resp2.status_code == 200
    settings2 = resp2.json().get("settings") or {}
    assert settings2.get("harmony_format_mode") == "disabled"
    assert settings2.get("harmony_format") is False

    resp3 = client.get("/settings")
    assert resp3.status_code == 200
    assert resp3.json()["harmony_format_mode"] == "disabled"

    invalid = client.post("/settings", json={"harmony_format_mode": "sometimes"})
    assert invalid.status_code == 400


def test_rag_similarity_setting_allows_zero(client, tmp_path):
    env_path = tmp_path / ".env"
    resp = client.post("/settings", json={"rag_chat_min_similarity": 0})
    assert resp.status_code == 200
    settings = resp.json().get("settings") or {}
    assert settings.get("rag_chat_min_similarity") == 0
    content = env_path.read_text()
    assert "RAG_CHAT_MIN_SIMILARITY" in content and "0.0" in content

    resp2 = client.get("/settings")
    assert resp2.status_code == 200
    assert resp2.json()["rag_chat_min_similarity"] == 0


def test_realtime_voice_settings_refresh_service(client, tmp_path):
    env_path = tmp_path / ".env"
    resp = client.post(
        "/settings",
        json={
            "stream_backend": "api",
            "stt_model": "gpt-realtime-whisper",
            "realtime_model": "gpt-realtime-2.1",
            "realtime_voice": "marin",
            "live_agent_mode": "server",
            "live_agent_model": "gemma-4-E4B-it",
            "live_multimodal_model": "gemma-4-26B-A4B-it",
            "realtime_base_url": "https://api.openai.com/v1/realtime/client_secrets",
            "realtime_connect_url": "https://api.openai.com/v1/realtime/calls",
        },
    )
    assert resp.status_code == 200
    settings = resp.json().get("settings") or {}
    assert settings.get("stream_backend") == "api"
    assert settings.get("stt_model") == "gpt-realtime-whisper"
    assert settings.get("realtime_model") == "gpt-realtime-2.1"
    assert settings.get("realtime_voice") == "marin"
    assert settings.get("live_agent_mode") == "server"
    assert settings.get("live_agent_model") == "gemma-4-E4B-it"
    assert settings.get("live_multimodal_model") == "gemma-4-26B-A4B-it"
    assert (
        settings.get("realtime_base_url")
        == "https://api.openai.com/v1/realtime/client_secrets"
    )
    assert (
        settings.get("realtime_connect_url")
        == "https://api.openai.com/v1/realtime/calls"
    )
    content = env_path.read_text()
    assert "FLOAT_STREAM_BACKEND" in content and "api" in content
    assert "STT_MODEL" in content and "gpt-realtime-whisper" in content
    assert "OPENAI_REALTIME_MODEL" in content and "gpt-realtime-2.1" in content
    assert "OPENAI_REALTIME_VOICE" in content and "marin" in content
    assert "OPENAI_REALTIME_TRANSCRIPTION_MODEL" not in content
    assert "FLOAT_LIVE_AGENT_MODE" in content and "server" in content
    assert "FLOAT_LIVE_AGENT_MODEL" in content and "gemma-4-E4B-it" in content
    assert "FLOAT_LIVE_MULTIMODAL_MODEL" in content and "gemma-4-26B-A4B-it" in content
    livekit_service = client.app.state.livekit_service
    assert livekit_service.mode == "api"
    assert livekit_service.realtime_model == "gpt-realtime-2.1"
    assert livekit_service.realtime_voice == "marin"
    assert livekit_service.realtime_transcription_model == "gpt-realtime-whisper"
    assert livekit_service.live_agent_mode == "server"
    assert livekit_service.live_agent_model == "gemma-4-E4B-it"
    assert livekit_service.live_multimodal_model == "gemma-4-26B-A4B-it"


def test_local_live_settings_refresh_service(client, tmp_path):
    env_path = tmp_path / ".env"
    resp = client.post(
        "/settings",
        json={
            "stream_backend": "local",
            "live_agent_mode": "local",
            "live_agent_model": "gemma-4-E2B-it",
            "live_multimodal_model": "gemma-4-E4B-it",
        },
    )
    assert resp.status_code == 200
    settings = resp.json().get("settings") or {}
    assert settings.get("stream_backend") == "local"
    assert settings.get("live_agent_mode") == "local"
    assert settings.get("live_agent_model") == "gemma-4-E2B-it"
    assert settings.get("live_multimodal_model") == "gemma-4-E4B-it"
    content = env_path.read_text()
    assert "FLOAT_STREAM_BACKEND" in content and "local" in content
    assert "FLOAT_LIVE_AGENT_MODE" in content and "local" in content
    assert "FLOAT_LIVE_AGENT_MODEL" in content and "gemma-4-E2B-it" in content
    assert "FLOAT_LIVE_MULTIMODAL_MODEL" in content and "gemma-4-E4B-it" in content
    livekit_service = client.app.state.livekit_service
    assert livekit_service.mode == "local"
    assert livekit_service.live_agent_mode == "local"
    assert livekit_service.live_agent_model == "gemma-4-E2B-it"
    assert livekit_service.live_multimodal_model == "gemma-4-E4B-it"


def test_voice_connect_forwards_chat_thinking_to_realtime_reasoning(
    client, monkeypatch
):
    captured = {}

    class DummyLiveKitService:
        mode = "api"
        realtime_model = "gpt-realtime-2.1"

        def connect(self, identity, room, *, reasoning_effort=None):
            captured["identity"] = identity
            captured["room"] = room
            captured["reasoning_effort"] = reasoning_effort
            return {"provider": "openai-realtime", "client_secret": "test-secret"}

    monkeypatch.setattr(client.app.state, "livekit_service", DummyLiveKitService())

    resp = client.post(
        "/voice/connect",
        json={"identity": "user-1", "room": "float", "thinking": "high"},
    )

    assert resp.status_code == 200
    assert captured == {
        "identity": "user-1",
        "room": "float",
        "reasoning_effort": "high",
    }


def test_voice_connect_uses_workflow_reasoning_when_thinking_is_auto(
    client, monkeypatch
):
    captured = {}

    class DummyLiveKitService:
        mode = "api"
        realtime_model = "gpt-realtime-2.1"

        def connect(self, identity, room, *, reasoning_effort=None):
            captured["reasoning_effort"] = reasoning_effort
            return {"provider": "openai-realtime", "client_secret": "test-secret"}

    monkeypatch.setattr(client.app.state, "livekit_service", DummyLiveKitService())

    resp = client.post(
        "/voice/connect",
        json={
            "identity": "user-1",
            "workflow": "architect_planner",
            "thinking": "auto",
        },
    )

    assert resp.status_code == 200
    assert captured["reasoning_effort"] == "high"


def test_voice_transcribe_uses_requested_stt_model(client, monkeypatch):
    from app import routes
    from app.services.stt_service import SttResult

    captured = {}

    class DummySTTService:
        def transcribe(
            self,
            audio,
            cfg,
            *,
            model=None,
            filename="recording.webm",
            content_type="audio/webm",
        ):
            captured["audio"] = audio
            captured["model"] = model
            captured["filename"] = filename
            captured["content_type"] = content_type
            return SttResult(text="hello", provider="openai", model="whisper-1")

    monkeypatch.setattr(routes, "stt_service", DummySTTService())

    resp = client.post(
        "/voice/transcribe",
        files={"file": ("recording.webm", b"audio-bytes", "audio/webm")},
        data={"model": "whisper-1"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"text": "hello", "model": "whisper-1", "provider": "openai"}
    assert captured["audio"] == b"audio-bytes"
    assert captured["model"] == "whisper-1"


def test_model_search_dirs_includes_custom(tmp_path, monkeypatch):
    from pathlib import Path

    from app.config import model_search_dirs

    monkeypatch.delenv("HF_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    custom = tmp_path / "my_models"
    custom.mkdir()
    dirs = model_search_dirs(str(custom))
    assert custom.resolve() in dirs


def test_load_config_includes_weaviate_settings(monkeypatch):
    from app import config as app_config

    monkeypatch.setenv("FLOAT_WEAVIATE_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("FLOAT_WEAVIATE_GRPC_HOST", "127.0.0.1")
    monkeypatch.setenv("FLOAT_WEAVIATE_GRPC_PORT", "50051")
    monkeypatch.setenv("FLOAT_AUTO_START_WEAVIATE", "true")

    cfg = app_config.load_config()

    assert cfg["weaviate_url"] == "http://127.0.0.1:8080"
    assert cfg["weaviate_grpc_host"] == "127.0.0.1"
    assert cfg["weaviate_grpc_port"] == 50051
    assert cfg["auto_start_weaviate"] is True


def test_legacy_conversations_override_detection():
    from app import config as app_config

    assert app_config._is_legacy_conversations_override("./conversations") is True
    assert app_config._is_legacy_conversations_override("conversations") is True
    assert app_config._is_legacy_conversations_override("./data/conversations") is False


def test_default_system_prompt_mentions_artifact_summaries_and_runtime_checks():
    from app import config as app_config

    cfg = app_config.load_config()
    prompt = cfg["system_prompt"]

    assert "create_task" in prompt
    assert "list_dir" in prompt
    assert "typed working summaries" in prompt
    assert "sandboxed runtime" in prompt
    assert "help/tool_info output" in prompt
    assert "before saying it does not exist" in prompt


def test_default_system_prompt_is_loaded_from_plaintext_asset():
    from app import config as app_config

    prompt_path = (
        Path(app_config.__file__).resolve().parent / "prompts" / "system_prompt.txt"
    )
    assert prompt_path.exists()

    raw_prompt = prompt_path.read_text(encoding="utf-8")
    lines = [line.rstrip() for line in raw_prompt.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    normalized_prompt = "\n".join(lines)

    cfg = app_config.load_config()
    assert cfg["system_prompt"] == normalized_prompt
