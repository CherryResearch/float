import sys
from pathlib import Path

import pytest
import requests
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def add_backend_to_sys_path():
    backend_dir = Path(__file__).resolve().parents[2]
    backend_dir = str(backend_dir)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)


@pytest.fixture
def client(add_backend_to_sys_path):
    from app.main import app

    return TestClient(app)


def test_provider_status_endpoint(monkeypatch, client):
    from app import routes

    def fake_status(provider, quick=False):
        assert provider == "lmstudio"
        assert quick is False
        return {
            "provider": "lmstudio",
            "installed": True,
            "server_running": True,
            "model_loaded": True,
            "loaded_model": "gpt-oss-20b",
            "context_length": 8192,
            "base_url": "http://127.0.0.1:1234/v1",
            "last_error": None,
            "capabilities": {"start_stop": True, "context_length": True},
        }

    monkeypatch.setattr(routes.provider_manager, "provider_status", fake_status)
    response = client.get("/llm/provider/status", params={"provider": "lmstudio"})
    assert response.status_code == 200
    runtime = response.json().get("runtime") or {}
    assert runtime.get("provider") == "lmstudio"
    assert runtime.get("model_loaded") is True


def test_local_status_with_provider_marker_maps_runtime(monkeypatch, client):
    from app import routes

    def fake_status(provider, quick=False):
        assert provider == "lmstudio"
        assert quick is True
        return {
            "provider": "lmstudio",
            "installed": True,
            "server_running": True,
            "model_loaded": False,
            "loaded_model": None,
            "context_length": None,
            "base_url": "http://127.0.0.1:1234/v1",
            "last_error": None,
            "capabilities": {"start_stop": True, "context_length": True},
        }

    monkeypatch.setattr(routes.provider_manager, "provider_status", fake_status)
    response = client.get("/llm/local-status", params={"model": "lmstudio"})
    assert response.status_code == 200
    runtime = response.json().get("runtime") or {}
    assert runtime.get("active_backend") == "provider"
    assert runtime.get("model") == "lmstudio"
    assert runtime.get("loaded") is False


def test_load_local_provider_marker_uses_provider_load(monkeypatch, client):
    from app import routes

    captured = {}

    def fake_load(*, provider=None, model=None, context_length=None):
        captured["provider"] = provider
        captured["model"] = model
        captured["context_length"] = context_length
        return {
            "ok": True,
            "result": {"ok": True},
            "runtime": {
                "provider": provider,
                "installed": True,
                "server_running": True,
                "model_loaded": True,
                "loaded_model": "gpt-oss-20b",
                "context_length": 4096,
                "base_url": "http://127.0.0.1:1234/v1",
                "last_error": None,
                "capabilities": {"start_stop": True, "context_length": True},
            },
        }

    monkeypatch.setattr(routes.provider_manager, "provider_load", fake_load)
    response = client.post(
        "/llm/load-local",
        json={"provider": "lmstudio", "model": "lmstudio", "context_length": 4096},
    )
    assert response.status_code == 200
    assert captured["provider"] == "lmstudio"
    # Marker models resolve provider only; concrete model is loaded via preferred/loaded runtime.
    assert captured["model"] is None
    runtime = response.json().get("runtime") or {}
    assert runtime.get("active_backend") == "provider"
    assert runtime.get("loaded") is True


def test_generate_local_provider_marker_routes_to_server(monkeypatch, client):
    from app import routes

    captured = {}

    def fake_resolve(*, provider, requested_model, allow_auto_start=True):
        assert provider == "lmstudio"
        assert requested_model == "lmstudio"
        return {
            "provider": "lmstudio",
            "model": "gpt-oss-20b",
            "base_url": "http://127.0.0.1:1234/v1",
            "api_token": "provider-token",
            "runtime": {"server_running": True, "model_loaded": True},
        }

    def fake_generate(prompt, session_id="default", **kwargs):
        captured["prompt"] = prompt
        captured["session_id"] = session_id
        captured["kwargs"] = dict(kwargs)
        return {"text": "ok", "thought": "", "tools_used": [], "metadata": {}}

    monkeypatch.setattr(
        routes.provider_manager,
        "resolve_inference_target",
        fake_resolve,
    )
    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)
    response = client.post(
        "/llm/generate",
        json={
            "prompt": "hello",
            "mode": "local",
            "model": "lmstudio",
            "session_id": "provider-test",
        },
    )
    assert response.status_code == 200
    kwargs = captured.get("kwargs") or {}
    assert kwargs.get("model") == "gpt-oss-20b"
    assert kwargs.get("server_url") == "http://127.0.0.1:1234/v1"
    assert kwargs.get("api_key") == "provider-token"


def test_generate_blank_local_model_uses_configured_provider(monkeypatch, client):
    from app import routes

    captured = {}

    monkeypatch.setitem(client.app.state.config, "local_provider", "lmstudio")

    def fake_resolve(*, provider, requested_model, allow_auto_start=True):
        assert provider == "lmstudio"
        assert requested_model == ""
        return {
            "provider": "lmstudio",
            "model": "gpt-oss-20b",
            "base_url": "http://127.0.0.1:1234/v1",
            "api_token": "provider-token",
            "runtime": {"server_running": True, "model_loaded": True},
        }

    def fake_generate(prompt, session_id="default", **kwargs):
        captured["prompt"] = prompt
        captured["session_id"] = session_id
        captured["kwargs"] = dict(kwargs)
        return {"text": "ok", "thought": "", "tools_used": [], "metadata": {}}

    monkeypatch.setattr(
        routes.provider_manager,
        "resolve_inference_target",
        fake_resolve,
    )
    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)
    response = client.post(
        "/llm/generate",
        json={
            "prompt": "hello",
            "mode": "local",
            "model": "",
            "session_id": "provider-test",
        },
    )
    assert response.status_code == 200
    kwargs = captured.get("kwargs") or {}
    assert kwargs.get("model") == "gpt-oss-20b"
    assert kwargs.get("server_url") == "http://127.0.0.1:1234/v1"
    assert kwargs.get("api_key") == "provider-token"


def test_provider_marker_mismatch_is_ignored():
    from app import routes

    metadata = {
        "model_mismatch": True,
        "model_requested": "lmstudio",
        "model_received": "openai/gpt-oss-20b",
    }

    message = routes._apply_model_mismatch_error(
        metadata,
        mode="server",
        provider="lmstudio",
    )

    assert message is None
    assert "error" not in metadata
    assert metadata["model_mismatch"] is True


def test_provider_models_endpoint_returns_effective_model(monkeypatch, client):
    from app import routes

    def fake_provider_models(provider, refresh=False):
        assert provider == "custom-openai-compatible"
        assert refresh is False
        return {
            "provider": provider,
            "models": ["gemma-4-E2B-it", "gemma-4-E4B-it"],
            "runtime": {
                "provider": provider,
                "installed": False,
                "server_running": True,
                "model_loaded": False,
                "loaded_model": None,
                "effective_model": "gemma-4-E2B-it",
                "preferred_model": "gemma-4-E2B-it",
                "checked_at": 1_775_000_000,
                "capabilities": {
                    "start_stop": False,
                    "load_unload": False,
                    "context_length": True,
                },
            },
        }

    monkeypatch.setattr(
        routes.provider_manager, "provider_models", fake_provider_models
    )
    response = client.get(
        "/llm/provider/models",
        params={"provider": "custom-openai-compatible"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["models"] == ["gemma-4-E2B-it", "gemma-4-E4B-it"]
    runtime = payload.get("runtime") or {}
    assert runtime.get("model") == "custom-openai-compatible"
    assert runtime.get("effective_model_id") == "gemma-4-E2B-it"
    assert runtime.get("active_backend") == "provider"


def test_provider_models_endpoint_can_force_refresh(monkeypatch, client):
    from app import routes

    calls = []

    def fake_provider_models(provider, refresh=False):
        calls.append((provider, refresh))
        return {
            "provider": provider,
            "models": ["gpt-oss-20b"],
            "runtime": {
                "provider": provider,
                "server_running": True,
                "model_loaded": True,
            },
        }

    monkeypatch.setattr(
        routes.provider_manager, "provider_models", fake_provider_models
    )
    response = client.get(
        "/llm/provider/models", params={"provider": "lmstudio", "refresh": True}
    )
    assert response.status_code == 200
    assert calls == [("lmstudio", True)]


def test_server_models_endpoint_polls_lmstudio_inventory(monkeypatch, client):
    from app import routes

    called_urls = []

    class DummyResponse:
        status_code = 200
        text = ""

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def fake_get(url, **kwargs):
        called_urls.append(url)
        assert kwargs.get("timeout") <= 1.0
        if url.endswith("/api/v0/models"):
            return DummyResponse(
                {
                    "data": [
                        {"id": "text-embedding-nomic", "state": "not-loaded"},
                        {"id": "gemma-4-26B-A4B-it", "state": "loaded"},
                    ]
                }
            )
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(routes, "_server_model_probe_request", fake_get)
    response = client.get(
        "/llm/server/models",
        params={"server_url": "http://127.0.0.1:1234"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["models"] == ["gemma-4-26B-A4B-it", "text-embedding-nomic"]
    assert payload["loaded_model"] == "gemma-4-26B-A4B-it"
    assert payload["reachable"] is True
    assert called_urls == ["http://127.0.0.1:1234/api/v0/models"]


def test_server_models_probe_has_total_timeout_budget(monkeypatch):
    from app import routes

    ticks = [1000.0]
    called_urls = []

    def fake_monotonic():
        return ticks[0]

    def fake_get(url, **kwargs):
        called_urls.append(url)
        ticks[0] += 1.1
        raise requests.ConnectTimeout("slow provider")

    monkeypatch.setattr(routes.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(routes, "_server_model_probe_request", fake_get)

    payload = routes._probe_server_model_inventory("http://127.0.0.1:1234")

    assert payload["status"] == "success"
    assert payload["reachable"] is False
    assert payload["models"] == []
    assert payload["error"] == "probe timed out"
    assert called_urls == [
        "http://127.0.0.1:1234/api/v0/models",
        "http://127.0.0.1:1234/api/v1/models",
    ]
