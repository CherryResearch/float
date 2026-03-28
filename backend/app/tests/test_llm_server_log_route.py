import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client_and_log(tmp_path, monkeypatch):
    backend_dir = Path(__file__).resolve().parents[2]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    from app.main import app
    from app.utils import conversation_store, llm_server_log, user_settings

    conversation_dir = tmp_path / "conversations"
    conversation_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(conversation_store, "CONV_DIR", conversation_dir)
    monkeypatch.setattr(
        user_settings, "USER_SETTINGS_PATH", tmp_path / "user_settings.json"
    )

    log_file = tmp_path / "llm_server.log"
    monkeypatch.setattr(llm_server_log, "LOG_FILE", log_file)

    return TestClient(app), log_file


def test_llm_server_log_requires_dev_mode(client_and_log):
    client, _log_file = client_and_log
    client.app.state.config["dev_mode"] = False

    resp = client.get("/logs/llm-server")
    assert resp.status_code == 404
    assert resp.json().get("detail") == "Dev mode disabled"


def test_llm_server_log_filters_by_session_event_and_time(client_and_log):
    client, log_file = client_and_log
    client.app.state.config["dev_mode"] = True

    entries = [
        {
            "time": "2026-02-24T09:00:00+00:00",
            "event": "request_dispatch",
            "session_id": "sess-a",
            "message_id": "m-1",
        },
        {
            "time": "2026-02-24T09:05:00+00:00",
            "event": "stream_fallback",
            "session_id": "sess-b",
            "message_id": "m-2",
        },
        {
            "time": "2026-02-24T09:10:00+00:00",
            "event": "request_success",
            "session_id": "sess-a",
            "message_id": "m-3",
        },
    ]
    log_file.write_text(
        "\n".join(json.dumps(item) for item in entries) + "\n", encoding="utf-8"
    )

    resp = client.get(
        "/logs/llm-server",
        params={
            "session_id": "sess-a",
            "event": "request_success",
            "since": "2026-02-24T09:08:00+00:00",
            "until": "2026-02-24T09:12:00+00:00",
            "limit": 10,
        },
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert "entries" in payload
    assert len(payload["entries"]) == 1
    assert payload["entries"][0]["event"] == "request_success"
    assert payload["entries"][0]["session_id"] == "sess-a"
