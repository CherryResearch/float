import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def add_backend_to_sys_path():
    backend_dir = Path(__file__).resolve().parents[2]
    backend_dir = str(backend_dir)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)


def test_list_addons_reads_repo_and_local_roots(tmp_path, monkeypatch):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    repo_root = tmp_path / "modules" / "addons"
    local_root = tmp_path / "data" / "modules" / "addons"
    repo_root.mkdir(parents=True, exist_ok=True)
    local_root.mkdir(parents=True, exist_ok=True)

    (repo_root / "repo-addon.json").write_text(
        '{"id": "repo-addon", "label": "Repo addon", "status": "live"}',
        encoding="utf-8",
    )
    (local_root / "local-addon.json").write_text(
        '{"id": "local-addon", "label": "Local addon", "status": "experimental"}',
        encoding="utf-8",
    )

    addons = workflow_profiles.list_addons()

    assert [item["id"] for item in addons] == ["local-addon", "repo-addon"]
    assert next(item for item in addons if item["id"] == "repo-addon")["source"] == "repo"
    assert next(item for item in addons if item["id"] == "local-addon")["source"] == "local"


def test_list_addons_prefers_local_override_for_duplicate_ids(tmp_path, monkeypatch):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    repo_root = tmp_path / "modules" / "addons"
    local_root = tmp_path / "data" / "modules" / "addons"
    repo_root.mkdir(parents=True, exist_ok=True)
    local_root.mkdir(parents=True, exist_ok=True)

    (repo_root / "shared.json").write_text(
        '{"id": "shared", "label": "Repo label", "status": "live"}',
        encoding="utf-8",
    )
    (local_root / "shared.json").write_text(
        '{"id": "shared", "label": "Local label", "status": "experimental"}',
        encoding="utf-8",
    )

    addons = workflow_profiles.list_addons()

    assert addons == [
        {
            "id": "shared",
            "label": "Local label",
            "description": "",
            "status": "experimental",
            "path": str(local_root / "shared.json"),
            "source": "local",
        }
    ]
