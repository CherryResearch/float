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
    from app.utils import theme_store, user_settings

    monkeypatch.setattr(user_settings, "USER_SETTINGS_PATH", tmp_path / "user_settings.json")
    monkeypatch.setattr(theme_store, "THEMES_DIR", tmp_path / "themes")
    return TestClient(app)


def test_theme_routes_round_trip(client):
    payload = {
        "label": "Forest Glass",
        "slots": {
            "c1Light": "#d6f5dd",
            "c1Med": "#3c8f5a",
            "c1Dark": "#173927",
            "c2Light": "#f4efc7",
            "c2Med": "#c6a93e",
            "c2Dark": "#5e4b12",
            "veryLight": "#fcfff8",
            "veryDark": "#08110a",
        },
    }

    create_response = client.post("/themes", json=payload)
    assert create_response.status_code == 200
    created = create_response.json()["theme"]
    assert created["label"] == "Forest Glass"
    assert created["id"].startswith("forest-glass")

    list_response = client.get("/themes")
    assert list_response.status_code == 200
    assert list_response.json()["themes"] == [created]

    update_response = client.post(
        "/themes",
        json={
            "id": created["id"],
            "label": "Forest Glass Renamed",
            "slots": payload["slots"],
        },
    )
    assert update_response.status_code == 200
    updated = update_response.json()["theme"]
    assert updated["id"] == created["id"]
    assert updated["label"] == "Forest Glass Renamed"

    delete_response = client.delete(f"/themes/{created['id']}")
    assert delete_response.status_code == 200
    assert delete_response.json()["status"] == "deleted"
    assert client.get("/themes").json()["themes"] == []
