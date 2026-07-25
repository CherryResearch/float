import hashlib
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_client(tmp_path: Path, monkeypatch) -> tuple[TestClient, Path]:
    from app import config as app_config
    from app import routes

    models_root = tmp_path / "models"
    models_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        app_config,
        "model_search_dirs",
        lambda custom_path=None: [models_root],
    )
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    app.state.config = {"models_folder": str(models_root)}
    app.state.model_jobs = {}
    return TestClient(app), models_root


def test_model_local_size_and_summary_use_resolved_directory(tmp_path, monkeypatch):
    from app.routers import model_filesystem

    client, models_root = _make_client(tmp_path, monkeypatch)
    model_dir = models_root / "fixture-model"
    model_dir.mkdir()
    (model_dir / "weights.bin").write_bytes(b"weights")
    monkeypatch.setattr(
        model_filesystem,
        "resolve_model_alias",
        lambda _model_name: "TODO: unsupported",
    )

    size = client.get("/api/models/local-size/fixture-model")
    summary = client.get("/api/models/summary/fixture-model")

    assert size.status_code == 200
    assert size.json() == {"exists": True, "size": 7}
    assert summary.status_code == 200
    payload = summary.json()
    assert payload["exists"] is True
    assert payload["path"] == str(model_dir)
    assert payload["installed_bytes"] == 7
    assert payload["expected_bytes"] == 0
    assert payload["verified"] is None


def test_verify_model_checks_manifest_sizes_and_hashes(tmp_path, monkeypatch):
    from app.routers import model_filesystem

    client, models_root = _make_client(tmp_path, monkeypatch)
    model_dir = models_root / "fixture-model"
    model_dir.mkdir()
    weights = model_dir / "weights.bin"
    weights.write_bytes(b"verified weights")
    expected_sha = hashlib.sha256(weights.read_bytes()).hexdigest()
    monkeypatch.setattr(
        model_filesystem,
        "resolve_model_alias",
        lambda _model_name: "fixture/repo",
    )
    monkeypatch.setattr(
        model_filesystem,
        "_remote_manifest",
        lambda *_args: (
            [
                {
                    "path": "weights.bin",
                    "size": weights.stat().st_size,
                    "sha256": expected_sha,
                }
            ],
            weights.stat().st_size,
            "fixture-commit",
        ),
    )

    response = client.get("/api/models/verify/fixture-model")

    assert response.status_code == 200
    payload = response.json()
    assert payload["exists"] is True
    assert payload["verified"] is True
    assert payload["checked_files"] == 1
    assert payload["installed_bytes"] == weights.stat().st_size
    assert payload["expected_bytes"] == weights.stat().st_size


def test_verify_model_falls_back_to_completed_download_job(tmp_path, monkeypatch):
    from app.routers import model_filesystem

    client, models_root = _make_client(tmp_path, monkeypatch)
    model_dir = models_root / "fixture-model"
    model_dir.mkdir()
    weights = model_dir / "weights.bin"
    weights.write_bytes(b"downloaded")
    client.app.state.model_jobs = {
        "job-1": {
            "id": "job-1",
            "model": "fixture-model",
            "path": str(model_dir),
            "status": "completed",
            "total": weights.stat().st_size,
        }
    }
    monkeypatch.setattr(
        model_filesystem,
        "resolve_model_alias",
        lambda _model_name: "fixture/repo",
    )
    monkeypatch.setattr(
        model_filesystem,
        "_remote_manifest",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("offline")),
    )

    response = client.get("/api/models/verify/fixture-model")

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "exists": True,
        "verified": True,
        "expected_bytes": weights.stat().st_size,
        "installed_bytes": weights.stat().st_size,
        "checked_files": 1,
    }


def test_model_integrity_delegates_to_shared_runtime(tmp_path, monkeypatch):
    from app import routes

    client, _models_root = _make_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        routes.llm_service,
        "verify_local_model",
        lambda model_name: {"model": model_name, "ok": True},
    )

    response = client.get("/api/models/integrity/fixture-model")

    assert response.status_code == 200
    assert response.json() == {"integrity": {"model": "fixture-model", "ok": True}}


def test_reveal_model_directory_uses_host_file_manager(tmp_path, monkeypatch):
    from app.routers import model_filesystem

    client, models_root = _make_client(tmp_path, monkeypatch)
    model_dir = models_root / "fixture-model"
    model_dir.mkdir()
    calls = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(model_filesystem.subprocess, "Popen", calls.append)

    response = client.get("/api/models/reveal/fixture-model")

    assert response.status_code == 200
    assert response.json() == {"path": str(model_dir), "opened": True}
    assert calls == [["explorer.exe", f"/select,{model_dir}"]]


def test_delete_model_removes_only_a_managed_search_root_entry(tmp_path, monkeypatch):
    client, models_root = _make_client(tmp_path, monkeypatch)
    model_dir = models_root / "fixture-model"
    model_dir.mkdir()
    (model_dir / "weights.bin").write_bytes(b"weights")

    response = client.delete("/api/models/fixture-model")

    assert response.status_code == 200
    assert response.json() == {"status": "deleted"}
    assert not model_dir.exists()
