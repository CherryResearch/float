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
    from app.utils import conversation_store, history_store, user_settings

    monkeypatch.setattr(conversation_store, "CONV_DIR", tmp_path)
    history_dir = tmp_path / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(history_store, "HISTORY_DIR", history_dir)
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
    from app.utils import history_store

    r2 = client.get("/history/s1")
    assert r2.status_code == 200
    assert r2.json()["history"][0]["text"] == "hi"
    saved = history_store.load_history("s1")
    assert saved[0]["text"] == "hi"

    from app.utils import user_settings

    settings = user_settings.load_settings()
    assert "s1" in settings.get("history", [])


def test_history_post_does_not_overwrite_canonical_conversation(client):
    from app.utils import conversation_store

    conversation_store.save_conversation(
        "s1",
        [
            {"id": "m1:user", "role": "user", "text": "full user"},
            {"id": "m1", "role": "ai", "text": "full assistant"},
        ],
    )

    r = client.post(
        "/history",
        json={
            "sessionId": "s1",
            "history": [{"role": "user", "text": "short ui mirror"}],
        },
    )
    assert r.status_code == 200

    canonical = conversation_store.load_conversation("s1")
    assert canonical == [
        {"id": "m1:user", "role": "user", "text": "full user"},
        {"id": "m1", "role": "ai", "text": "full assistant"},
    ]


def test_history_get_falls_back_to_canonical_conversation(client):
    from app.utils import conversation_store

    conversation_store.save_conversation(
        "legacy",
        [
            {"role": "user", "text": "hello from canonical"},
            {"role": "assistant", "text": "reply from canonical"},
            {"role": "system", "text": "ignore me"},
            {"role": "ai", "text": ""},
        ],
    )

    r = client.get("/history/legacy")
    assert r.status_code == 200
    assert r.json()["history"] == [
        {"role": "user", "text": "hello from canonical"},
        {"role": "ai", "text": "reply from canonical"},
    ]
