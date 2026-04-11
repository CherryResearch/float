import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    backend_dir = Path(__file__).resolve().parents[2]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    data_root = tmp_path / "data_root"
    monkeypatch.setenv("FLOAT_DATA_DIR", str(data_root))

    from app import routes
    from app.main import app

    monkeypatch.setattr(routes.subprocess, "Popen", lambda *_args, **_kwargs: None)
    return TestClient(app)


def test_memory_list_can_include_archived_items_on_demand(client):
    create_resp = client.post(
        "/memory/archived-note",
        json={
            "value": "remember this archived note",
            "importance": 1.0,
        },
    )
    assert create_resp.status_code == 200

    archive_resp = client.post(
        "/memory/archived-note/archive",
        json={"archived": True},
    )
    assert archive_resp.status_code == 200

    default_resp = client.get("/memory", params={"detailed": True})
    assert default_resp.status_code == 200
    default_keys = {item["key"] for item in default_resp.json()["items"]}
    assert "archived-note" not in default_keys

    archived_resp = client.get(
        "/memory",
        params={"detailed": True, "include_archived": True},
    )
    assert archived_resp.status_code == 200
    archived_items = {
        item["key"]: item for item in archived_resp.json()["items"]
    }
    assert "archived-note" in archived_items
    assert archived_items["archived-note"]["pruned_at"] is not None
    assert "last_accessed_at" in archived_items["archived-note"]
