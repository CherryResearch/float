import sys
from importlib import reload
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    backend_dir = Path(__file__).resolve().parents[2]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    from app.main import app
    from app.utils import user_settings

    test_file = tmp_path / "user_settings.json"
    monkeypatch.setattr(user_settings, "USER_SETTINGS_PATH", test_file)
    return TestClient(app)


def test_user_settings_persist(client, tmp_path, monkeypatch):
    payload = {
        "history": ["sess-1"],
        "approval_level": "auto",
        "theme": "dark",
        "user_timezone": "America/New_York",
        "live_transcript_enabled": False,
        "live_camera_default_enabled": True,
        "tool_resolution_notifications": False,
    }
    r = client.post("/user-settings", json=payload)
    assert r.status_code == 200

    # Simulate repo update by reloading module
    from app.utils import user_settings

    reload(user_settings)
    monkeypatch.setattr(
        user_settings, "USER_SETTINGS_PATH", tmp_path / "user_settings.json"
    )
    data = user_settings.load_settings()
    for key, value in payload.items():
        assert data[key] == value
    assert data["export_default_format"] == "md"
    assert data["export_default_include_chat"] is True
    assert data["export_default_include_thoughts"] is True
    assert data["export_default_include_tools"] is True
    assert data["live_transcript_enabled"] is False
    assert data["live_camera_default_enabled"] is True
    assert data["tool_resolution_notifications"] is False

    r2 = client.get("/user-settings")
    assert r2.status_code == 200
    assert r2.json()["history"] == ["sess-1"]
    assert r2.json()["system_prompt_custom"] == ""
    assert r2.json()["user_timezone"] == "America/New_York"
    assert r2.json()["live_transcript_enabled"] is False
    assert r2.json()["live_camera_default_enabled"] is True
    assert r2.json()["tool_resolution_notifications"] is False


def test_user_settings_default_tool_review_notifications_enabled(tmp_path, monkeypatch):
    from app.utils import user_settings

    monkeypatch.setattr(
        user_settings, "USER_SETTINGS_PATH", tmp_path / "user_settings.json"
    )

    data = user_settings.load_settings()
    assert data["tool_resolution_notifications"] is True
