import sys
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

CATALOG_TEMPLATE = {
    "provider": "livekit",
    "models": {
        "speech": {"api": "api", "local": "local"},
        "vision": {"api": "api", "local": "local"},
        "llm": {"api": "api", "local": "local"},
    },
}


@pytest.fixture(autouse=True)
def add_backend_to_sys_path():
    backend_dir = Path(__file__).resolve().parents[2]
    backend_dir = str(backend_dir)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)


def write_catalog(tmp_path: Path, provider: str) -> Path:
    data = CATALOG_TEMPLATE.copy()
    data["provider"] = provider
    path = tmp_path / f"{provider}.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def create_client() -> TestClient:
    from api.live import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_session_negotiation(monkeypatch, tmp_path):
    catalog = write_catalog(tmp_path, "pipecat")
    monkeypatch.setenv("MODEL_CATALOG_PATH", str(catalog))
    client = create_client()
    resp = client.post("/live/session")
    assert resp.status_code == 200
    assert resp.json()["provider"] == "pipecat"


def test_stream_routing_and_provider_switch(monkeypatch, tmp_path):
    livekit_cfg = write_catalog(tmp_path, "livekit")
    pipecat_cfg = write_catalog(tmp_path, "pipecat")
    client = create_client()

    monkeypatch.setenv("MODEL_CATALOG_PATH", str(livekit_cfg))
    with client.websocket_connect("/live/ws") as ws:
        assert ws.receive_text() == "livekit stream"

    monkeypatch.setenv("MODEL_CATALOG_PATH", str(pipecat_cfg))
    with client.websocket_connect("/live/ws") as ws:
        assert ws.receive_text() == "pipecat stream"
