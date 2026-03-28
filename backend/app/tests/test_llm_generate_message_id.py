import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    backend_dir = Path(__file__).resolve().parents[2]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    from app.main import app
    from app.utils import conversation_store, user_settings

    monkeypatch.setattr(conversation_store, "CONV_DIR", tmp_path)
    settings_file = tmp_path / "user_settings.json"
    monkeypatch.setattr(user_settings, "USER_SETTINGS_PATH", settings_file)
    return TestClient(app)


def _recv_until(ws, predicate, *, max_messages=40):
    for _ in range(max_messages):
        msg = ws.receive_json()
        if predicate(msg):
            return msg
    raise AssertionError("did not receive expected stream event")


def test_llm_generate_publishes_content_events_with_message_id(client, monkeypatch):
    from app import routes as routes_module

    session_id = "sess-123"
    message_id = "mid-123"

    def fake_generate(
        prompt,
        *,
        session_id: str = "default",
        stream_consumer=None,
        stream_message_id=None,
        **kwargs,
    ):
        assert session_id == "sess-123"
        assert stream_message_id == "mid-123"
        if stream_consumer:
            stream_consumer({"type": "content", "content": "hel"})
            stream_consumer({"type": "content", "content": "lo"})
        return {"text": "hello", "metadata": {}}

    monkeypatch.setattr(routes_module.llm_service, "generate", fake_generate)

    with client.websocket_connect("/api/ws/thoughts") as ws:
        r = client.post(
            "/api/llm/generate",
            json={
                "prompt": "hi",
                "mode": "server",
                "session_id": session_id,
                "message_id": message_id,
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "success"
        assert body["response"]["text"] == "hello"
        assert body["response"]["metadata"]["message_id"] == message_id
        assert body["response"]["metadata"]["session_id"] == session_id

        ev = _recv_until(
            ws,
            lambda m: m.get("type") == "content" and m.get("message_id") == message_id,
        )
        assert ev["session_id"] == session_id

