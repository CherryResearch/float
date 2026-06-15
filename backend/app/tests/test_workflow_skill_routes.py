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


def test_workflow_pack_routes_import_export_module_and_skill(tmp_path, monkeypatch):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    source = tmp_path / "workspace" / "hermes"
    source.mkdir(parents=True)
    (source / "skills").mkdir()
    (source / "config.json").write_text(
        """
        {
          "id": "hermes",
          "modules": [{"id": "hermes_agent", "skill_id": "hermes_agent"}]
        }
        """,
        encoding="utf-8",
    )
    (source / "skills" / "hermes_agent.md").write_text(
        "Hermes summary\n\n# Hermes\n",
        encoding="utf-8",
    )

    app = importlib.import_module("app.main").app
    client = TestClient(app)

    preview = client.post(
        "/api/workflows/module-packs/import",
        json={"source_path": str(source)},
    )
    assert preview.status_code == 200
    assert preview.json()["status"] == "preview"
    assert preview.json()["dry_run"] is True

    imported = client.post(
        "/api/workflows/module-packs/import",
        json={"source_path": str(source), "dry_run": False},
    )
    assert imported.status_code == 200
    assert imported.json()["status"] == "imported"

    catalog = client.get("/api/workflows/catalog").json()
    assert any(item["id"] == "hermes_agent" for item in catalog["modules"])

    export_destination = tmp_path / "workspace" / "exports"
    exported = client.post(
        "/api/workflows/module-packs/hermes/export",
        json={"destination_path": str(export_destination), "dry_run": False},
    )
    assert exported.status_code == 200
    assert exported.json()["status"] == "exported"
    assert (export_destination / "hermes" / "config.json").exists()
    assert (export_destination / "hermes" / "skills" / "hermes_agent.md").exists()

    skill_source = tmp_path / "workspace" / "generic_skill.md"
    skill_source.write_text("Generic skill summary\n", encoding="utf-8")
    skill_import = client.post(
        "/api/workflows/skills/import",
        json={"source_path": str(skill_source), "dry_run": False},
    )
    assert skill_import.status_code == 200
    assert skill_import.json()["skill_id"] == "generic_skill"

    skill_export_destination = tmp_path / "workspace" / "skill-exports"
    skill_export = client.post(
        "/api/workflows/skills/generic_skill/export",
        json={"destination_path": str(skill_export_destination), "dry_run": False},
    )
    assert skill_export.status_code == 200
    assert (skill_export_destination / "generic_skill.md").exists()
