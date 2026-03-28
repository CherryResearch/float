import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def add_backend_to_sys_path():
    backend_dir = Path(__file__).resolve().parents[2]
    backend_dir = str(backend_dir)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)


class DummyProc:
    def __init__(self, pid: int):
        self.pid = pid
        self._exit_code = None

    def poll(self):
        return self._exit_code

    def terminate(self):
        self._exit_code = -15

    def kill(self):
        self._exit_code = -9


class DummyHfApi:
    def model_info(self, repo_id: str):
        raise RuntimeError("offline")


def test_model_job_create_dedupes_and_resumes(monkeypatch, tmp_path: Path):
    from app import routes

    start_calls = {"count": 0, "pid": 1000}

    def fake_start_download_process(repo_id: str, target_dir: Path, model_alias: str | None = None):
        start_calls["count"] += 1
        start_calls["pid"] += 1
        return DummyProc(start_calls["pid"])

    monkeypatch.setattr(routes, "_start_download_process", fake_start_download_process)

    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "HfApi", lambda: DummyHfApi())

    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    app.state.config = {"models_folder": str(tmp_path / "models")}
    client = TestClient(app)

    resp1 = client.post("/api/models/jobs", json={"model": "kokoro"})
    assert resp1.status_code == 200
    job1 = resp1.json().get("job") or {}
    job_id = job1.get("id")
    assert job_id
    assert start_calls["count"] == 1

    resp2 = client.post("/api/models/jobs", json={"model": "kokoro"})
    assert resp2.status_code == 200
    job2 = resp2.json().get("job") or {}
    assert job2.get("id") == job_id
    assert start_calls["count"] == 1  # no duplicate running process

    paused = client.post(f"/api/models/jobs/{job_id}/pause")
    assert paused.status_code == 200
    assert (paused.json().get("job") or {}).get("status") == "paused"

    resp3 = client.post("/api/models/jobs", json={"model": "kokoro"})
    assert resp3.status_code == 200
    job3 = resp3.json().get("job") or {}
    assert job3.get("id") == job_id
    assert job3.get("status") == "running"
    assert start_calls["count"] == 2  # restarted after pause
