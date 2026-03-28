import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient


def test_list_model_jobs_returns_progress_and_sorts_newest_first(tmp_path, monkeypatch):
    backend_dir = Path(__file__).resolve().parents[2]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    from app.main import app
    from app.utils import calendar_store, conversation_store

    monkeypatch.setattr(conversation_store, "CONV_DIR", tmp_path, raising=False)
    monkeypatch.setattr(calendar_store, "EVENTS_DIR", tmp_path / "calendar", raising=False)
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
    monkeypatch.setattr(calendar_store, "EVENTS_DIR", tmp_path / "calendar", raising=False)
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
