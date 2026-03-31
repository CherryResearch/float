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


def test_openai_models_route_uses_ttl_cache(monkeypatch):
    from app import routes

    class DummyResponse:
        status_code = 200
        text = ""

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": [
                    {"id": "gpt-5.4"},
                    {"id": "gpt-4.1-mini"},
                    {"id": "gpt-5.4"},
                ]
            }

    call_urls = []

    def fake_get(url, headers=None, timeout=None):
        call_urls.append(url)
        return DummyResponse()

    routes._openai_models_cache.clear()
    monkeypatch.setattr(routes.http_session, "get", fake_get)
    client = _make_client()

    first = client.get("/api/openai/models")
    second = client.get("/api/openai/models")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == {"models": ["gpt-4.1-mini", "gpt-5.4"]}
    assert second.json() == {"models": ["gpt-4.1-mini", "gpt-5.4"]}
    assert call_urls == ["https://api.openai.com/v1/models"]


def test_openai_models_cache_is_keyed_by_provider_config(monkeypatch):
    from app import routes

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

    routes._openai_models_cache.clear()
    monkeypatch.setattr(routes.http_session, "get", fake_get)
    client = _make_client()

    first = client.get("/api/openai/models")
    client.app.state.config["api_key"] = "other-key"
    second = client.get("/api/openai/models")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == {"models": ["gpt-5.4"]}
    assert second.json() == {"models": ["other-model"]}
    assert call_urls == [
        ("https://api.openai.com/v1/models", "Bearer test-key"),
        ("https://api.openai.com/v1/models", "Bearer other-key"),
    ]
