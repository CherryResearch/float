import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    backend_dir = Path(__file__).resolve().parents[2]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    from app.main import app

    return TestClient(app)


def test_extract_from_text(client, monkeypatch):
    from app import routes

    called = {}

    def fake_from_text(text):
        called["text"] = text
        return [{"class": "msg", "text": "hi", "attributes": {}}]

    monkeypatch.setattr(
        routes.langextract_service,
        "from_text",
        fake_from_text,
    )

    resp = client.post("/extract", json={"text": "hello"})
    assert resp.status_code == 200
    assert called["text"] == "hello"
    assert resp.json() == {
        "summary": [{"class": "msg", "text": "hi", "attributes": {}}]
    }


def test_extract_from_conversation(client, monkeypatch):
    from app import routes

    def fake_load_conv(cid):
        assert cid == "conv1"
        return [{"role": "user", "content": "hi"}]

    captured = {}

    def fake_from_conversation(msgs):
        captured["msgs"] = msgs
        return [
            {
                "class": "msg",
                "text": "hi",
                "attributes": {"speaker": "user"},
            }
        ]

    monkeypatch.setattr(
        routes.conversation_store,
        "load_conversation",
        fake_load_conv,
    )
    monkeypatch.setattr(
        routes.langextract_service, "from_conversation", fake_from_conversation
    )

    resp = client.post("/extract", json={"conversation_id": "conv1"})
    assert resp.status_code == 200
    assert captured["msgs"] == [{"speaker": "user", "text": "hi"}]
    assert resp.json()["summary"] == [
        {"class": "msg", "text": "hi", "attributes": {"speaker": "user"}}
    ]
