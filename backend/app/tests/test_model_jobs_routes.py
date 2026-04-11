import sys
import time
from pathlib import Path

import huggingface_hub
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_list_model_jobs_returns_progress_and_sorts_newest_first(tmp_path, monkeypatch):
    backend_dir = Path(__file__).resolve().parents[2]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    from app.main import app
    from app.utils import calendar_store, conversation_store

    monkeypatch.setattr(conversation_store, "CONV_DIR", tmp_path, raising=False)
    monkeypatch.setattr(
        calendar_store, "EVENTS_DIR", tmp_path / "calendar", raising=False
    )
    calendar_store.EVENTS_DIR.mkdir(parents=True, exist_ok=True)

    older_dir = tmp_path / "older-model"
    older_dir.mkdir(parents=True, exist_ok=True)
    (older_dir / "weights.bin").write_bytes(b"x" * 8)

    newer_dir = tmp_path / "newer-model"
    newer_dir.mkdir(parents=True, exist_ok=True)
    (newer_dir / "weights.bin").write_bytes(b"x" * 12)

    app.state.model_jobs = {
        "older": {
            "id": "older",
            "model": "older-model",
            "path": str(older_dir),
            "status": "paused",
            "total": 16,
            "started_at": time.time() - 100,
            "updated_at": time.time() - 50,
        },
        "newer": {
            "id": "newer",
            "model": "newer-model",
            "path": str(newer_dir),
            "status": "running",
            "total": 24,
            "started_at": time.time() - 10,
            "updated_at": time.time() - 5,
        },
    }

    client = TestClient(app)
    resp = client.get("/api/models/jobs")

    assert resp.status_code == 200
    jobs = resp.json()["jobs"]
    assert [job["id"] for job in jobs] == ["newer", "older"]
    assert jobs[0]["downloaded"] == 12
    assert jobs[0]["total"] == 24
    assert jobs[1]["downloaded"] == 8


def test_list_model_jobs_can_filter_finished_jobs(tmp_path, monkeypatch):
    backend_dir = Path(__file__).resolve().parents[2]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    from app.main import app
    from app.utils import calendar_store, conversation_store

    monkeypatch.setattr(conversation_store, "CONV_DIR", tmp_path, raising=False)
    monkeypatch.setattr(
        calendar_store, "EVENTS_DIR", tmp_path / "calendar", raising=False
    )
    calendar_store.EVENTS_DIR.mkdir(parents=True, exist_ok=True)

    model_dir = tmp_path / "done-model"
    model_dir.mkdir(parents=True, exist_ok=True)

    app.state.model_jobs = {
        "done": {
            "id": "done",
            "model": "done-model",
            "path": str(model_dir),
            "status": "completed",
            "total": 1,
            "started_at": time.time() - 100,
            "updated_at": time.time() - 50,
        }
    }

    client = TestClient(app)
    resp = client.get("/api/models/jobs", params={"include_finished": "false"})

    assert resp.status_code == 200
    assert resp.json()["jobs"] == []


class _DummyInfoApi:
    def model_info(self, repo_id: str, files_metadata: bool = False):
        class _Info:
            siblings = []

        return _Info()


class _DummyProc:
    pid = 12345


def test_model_info_reports_gemma4_metadata(tmp_path, monkeypatch):
    backend_dir = Path(__file__).resolve().parents[2]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    from app import routes

    monkeypatch.setattr(huggingface_hub, "HfApi", lambda token=None: _DummyInfoApi())

    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    app.state.config = {"models_folder": str(tmp_path / "models")}
    client = TestClient(app)

    resp = client.get("/api/models/info/gemma-4-E2B-it")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["repo_id"] == "google/gemma-4-E2B-it"
    assert payload["downloadable"] is True
    assert payload["lane"] == "local"
    assert payload["local_loader"] == "image_text_to_text"
    assert payload["supports_images"] is True


def test_downloadable_models_include_provider_first_gemma4(tmp_path):
    backend_dir = Path(__file__).resolve().parents[2]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    from app import routes

    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    app.state.config = {"models_folder": str(tmp_path / "models")}
    client = TestClient(app)

    resp = client.get("/api/models/downloadable")
    assert resp.status_code == 200
    assert "gemma-4-E4B-it" in resp.json()["models"]


def test_create_model_job_accepts_downloadable_provider_first_gemma4(
    tmp_path, monkeypatch
):
    backend_dir = Path(__file__).resolve().parents[2]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    from app import routes

    monkeypatch.setattr(huggingface_hub, "HfApi", lambda token=None: _DummyInfoApi())
    monkeypatch.setattr(routes, "_start_download_process", lambda *args: _DummyProc())

    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    app.state.config = {"models_folder": str(tmp_path / "models")}
    client = TestClient(app)

    resp = client.post("/api/models/jobs", json={"model": "gemma-4-E4B-it"})
    assert resp.status_code == 200
    payload = resp.json()["job"]
    assert payload["model"] == "gemma-4-E4B-it"
    assert payload["repo_id"] == "google/gemma-4-E4B-it"
