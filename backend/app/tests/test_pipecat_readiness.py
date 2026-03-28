import sys
from pathlib import Path

import yaml
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def add_backend_to_sys_path():
    backend_dir = Path(__file__).resolve().parents[2]
    backend_dir = str(backend_dir)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)


def create_client() -> TestClient:
    from api.live import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_pipecat_negotiate_surfaces_readiness(tmp_path: Path, monkeypatch):
    catalog_yaml = {
        "provider": "pipecat",
        "models": {
            "llm": {"local": "transformers"},
            "speech": {"api": "https://api.speech/v1"},
        },
        "defaults": {"chat": {"llm": "local"}},
        "workflows": {"chat_basic": {"requires": ["llm"]}},
    }
    path = tmp_path / "catalog.yaml"
    path.write_text(yaml.safe_dump(catalog_yaml))
    monkeypatch.setenv("MODEL_CATALOG_PATH", str(path))

    client = create_client()
    resp = client.post("/live/session")
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "pipecat"
    # Should include readiness for workflows and defaults
    assert "readiness" in body
    assert "chat_basic" in body["readiness"]
    assert body["readiness"]["chat_basic"]["ready"] is True

