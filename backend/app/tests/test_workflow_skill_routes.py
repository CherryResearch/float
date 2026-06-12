import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def add_backend_to_sys_path():
    backend_dir = Path(__file__).resolve().parents[2]
    backend_dir = str(backend_dir)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)


def test_workflow_skill_doc_routes_manage_local_override(tmp_path, monkeypatch):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    repo_root = tmp_path / "modules" / "skills"
    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "computer_use.md").write_text(
        "Repo summary\n\n# Computer Use\n",
        encoding="utf-8",
    )

    app = importlib.import_module("app.main").app
    client = TestClient(app)

    initial = client.get("/api/workflows/skills/computer_use")
    assert initial.status_code == 200
    assert initial.json()["active"]["source"] == "repo"

    saved = client.put(
        "/api/workflows/skills/computer_use",
        json={"body": "Local summary\n\n# Local Computer Use\n"},
    )
    assert saved.status_code == 200
    saved_payload = saved.json()
    assert saved_payload["local_exists"] is True
    assert saved_payload["active"]["source"] == "local"
    assert saved_payload["active"]["body"] == "Local summary\n\n# Local Computer Use"

    deleted = client.delete("/api/workflows/skills/computer_use")
    assert deleted.status_code == 200
    deleted_payload = deleted.json()
    assert deleted_payload["local_exists"] is False
    assert deleted_payload["active"]["source"] == "repo"


def test_workflow_skill_doc_routes_reject_invalid_ids(tmp_path, monkeypatch):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)

    app = importlib.import_module("app.main").app
    client = TestClient(app)

    assert client.get("/api/workflows/skills/bad$id").status_code == 400
    assert (
        client.put(
            "/api/workflows/skills/bad$id",
            json={"body": "Nope"},
        ).status_code
        == 400
    )
