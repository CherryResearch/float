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


def test_history_roundtrip(client, tmp_path):
    payload = {
        "sessionId": "s1",
        "history": [{"role": "user", "text": "hi"}],
    }
    r = client.post("/history", json=payload)
    assert r.status_code == 200
    # Ensure conversation persisted
    from app.utils import conversation_store

    conv = conversation_store.load_conversation("s1")
    assert conv[0]["text"] == "hi"

    r2 = client.get("/history/s1")
    assert r2.status_code == 200
    assert r2.json()["history"][0]["text"] == "hi"

    from app.utils import user_settings

    settings = user_settings.load_settings()
    assert "s1" in settings.get("history", [])
