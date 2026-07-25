import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def add_backend_to_sys_path():
    backend_dir = Path(__file__).resolve().parents[2]
    backend_dir = str(backend_dir)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)


def _make_client():
    from app import routes

    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    app.state.config = {
        "api_key": "test-key",
        "api_url": "https://api.openai.com/v1/responses",
    }
    return TestClient(app)


def _clear_model_inventory_cache():
    from app.services import model_inventory_service

    model_inventory_service.openai_models_cache.clear()


def test_openai_models_route_uses_ttl_cache(monkeypatch):
    from app.routers import model_catalog

    class DummyResponse:
        status_code = 200
        text = ""

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": [
                    {"id": "gpt-5.4"},
                    {"id": "gpt-5.4-pro"},
                    {"id": "gpt-4.1-mini"},
                    {"id": "gpt-5.4"},
                    {"id": "deepseek-chat"},
                    {"id": "text-embedding-3-large"},
                    {"id": "gpt-3.5-turbo-instruct"},
                    {"id": "gpt-image-1"},
                    {"id": "gpt-4o-mini-tts"},
                    {"id": "gpt-4o-mini-transcribe"},
                    {"id": "omni-moderation-latest"},
                    {"id": "computer-use-preview"},
                    {"id": "sora-2"},
                    {"id": "davinci-002"},
                    {"id": "text-davinci-003"},
                ]
            }

    call_urls = []

    def fake_get(url, headers=None, timeout=None):
        call_urls.append(url)
        return DummyResponse()

    _clear_model_inventory_cache()
    monkeypatch.setattr(model_catalog.http_session, "get", fake_get)
    client = _make_client()

    first = client.get("/api/openai/models")
    second = client.get("/api/openai/models")
    full = client.get("/api/openai/models", params={"include_non_chat": True})

    assert first.status_code == 200
    assert second.status_code == 200
    assert full.status_code == 200
    assert first.json()["models"] == [
        "gpt-5.4",
        "gpt-5.4-pro",
        "gpt-4.1-mini",
        "deepseek-chat",
    ]
    assert second.json()["models"] == first.json()["models"]
    assert full.json()["models"] == [
        "gpt-5.4",
        "gpt-5.4-pro",
        "gpt-4.1-mini",
        "gpt-4o-mini-transcribe",
        "gpt-4o-mini-tts",
        "gpt-3.5-turbo-instruct",
        "computer-use-preview",
        "davinci-002",
        "deepseek-chat",
        "gpt-image-1",
        "omni-moderation-latest",
        "sora-2",
        "text-davinci-003",
        "text-embedding-3-large",
    ]
    assert call_urls == ["https://api.openai.com/v1/models"]


def test_openai_models_cache_is_keyed_by_provider_config(monkeypatch):
    from app.routers import model_catalog

    class DummyResponse:
        status_code = 200
        text = ""

        def __init__(self, model_id):
            self._model_id = model_id

        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"id": self._model_id}]}

    call_urls = []

    def fake_get(url, headers=None, timeout=None):
        call_urls.append((url, headers.get("Authorization")))
        if headers.get("Authorization") == "Bearer test-key":
            return DummyResponse("gpt-5.4")
        return DummyResponse("other-model")

    _clear_model_inventory_cache()
    monkeypatch.setattr(model_catalog.http_session, "get", fake_get)
    client = _make_client()

    first = client.get("/api/openai/models")
    client.app.state.config["api_key"] = "other-key"
    second = client.get("/api/openai/models")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["models"] == ["gpt-5.4"]
    assert second.json()["models"] == ["other-model"]
    assert call_urls == [
        ("https://api.openai.com/v1/models", "Bearer test-key"),
        ("https://api.openai.com/v1/models", "Bearer other-key"),
    ]


def test_openai_models_route_reports_latest_alias_metadata(monkeypatch):
    from app.routers import model_catalog

    class DummyResponse:
        status_code = 200
        text = ""

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": [
                    {"id": "chat-latest"},
                    {"id": "gpt-5.5-2026-07-01"},
                    {"id": "gpt-5.5-pro"},
                    {"id": "gpt-5.5"},
                    {"id": "gpt-5.4-mini"},
                ]
            }

    _clear_model_inventory_cache()
    monkeypatch.setattr(
        model_catalog.http_session, "get", lambda *args, **kwargs: DummyResponse()
    )
    client = _make_client()

    resp = client.get("/api/openai/models")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["models"][:4] == [
        "chat-latest",
        "gpt-5.5",
        "gpt-5.5-pro",
        "gpt-5.5-2026-07-01",
    ]
    assert payload["model_aliases"]["chat-latest"] == {
        "label": "GPT latest",
        "display_label": "GPT latest (gpt-5.5)",
        "target_model": "gpt-5.5",
    }


def test_openai_models_route_hides_removed_models_from_new_selection(monkeypatch):
    from app.routers import model_catalog

    class DummyResponse:
        status_code = 200
        text = ""

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": [
                    {"id": "chat-latest"},
                    {"id": "gpt-5-chat-latest"},
                    {"id": "gpt-5.4-mini"},
                ]
            }

    _clear_model_inventory_cache()
    monkeypatch.setattr(
        model_catalog.http_session, "get", lambda *args, **kwargs: DummyResponse()
    )
    client = _make_client()

    response = client.get(
        "/api/openai/models",
        params={"selected_model": "gpt-5-chat-latest"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["models"] == [
        "chat-latest",
        "gpt-5.4-mini",
        "gpt-5-chat-latest",
    ]
    assert payload["selectable_models"] == ["chat-latest", "gpt-5.4-mini"]
    assert payload["selection"]["status"] == "removed"
    assert payload["migration"] == {
        "from": "gpt-5-chat-latest",
        "to": "gpt-5.5",
        "kind": "required",
        "required": True,
        "shutdown_at": "2026-07-23",
    }
