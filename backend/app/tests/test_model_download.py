import os
import subprocess
import time
from pathlib import Path

import pytest

if os.getenv("FLOAT_RUN_MODEL_DOWNLOAD_TEST") != "1":
    pytest.skip(
        "Live model download requires FLOAT_RUN_MODEL_DOWNLOAD_TEST=1",
        allow_module_level=True,
    )


def test_model_download_and_verify(tmp_path, monkeypatch):
    # Keep both the download worker and model lookup away from installed models.
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    env_file = tmp_path / "download.env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("FLOAT_ENV_FILE", str(env_file))
    monkeypatch.setenv("FLOAT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "huggingface"))
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "huggingface" / "hub"))
    monkeypatch.setenv("HF_XET_CACHE", str(tmp_path / "huggingface" / "xet"))

    from app import config as app_config
    from app.routers import model_filesystem, model_jobs
    from app.utils import user_settings
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setattr(user_settings, "USER_SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(app_config, "model_search_dirs", lambda *a, **k: [models_dir])
    app = FastAPI()
    app.state.config = {"models_folder": str(models_dir)}
    app.state.model_jobs = {}
    app.include_router(model_jobs.router, prefix="/api")
    app.include_router(model_filesystem.router, prefix="/api")
    model_name = "kokoro"

    with TestClient(app) as client:
        try:
            response = client.post(
                "/api/models/jobs",
                json={"model": model_name, "path": str(models_dir)},
            )
            assert response.status_code == 200
            job = response.json().get("job")
            assert job is not None
            job_id = job.get("id")
            assert job_id is not None
            assert Path(job["path"]).resolve() == models_dir / model_name

            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                response = client.get(f"/api/models/jobs/{job_id}")
                assert response.status_code == 200
                job_status = response.json().get("job", {}).get("status")
                if job_status == "completed":
                    break
                if job_status == "error":
                    pytest.fail(
                        "Download job failed: "
                        f"{response.json().get('job', {}).get('error')}"
                    )
                time.sleep(1)
            else:
                pytest.fail("Download job timed out")

            response = client.get(
                f"/api/models/verify/{model_name}", params={"path": str(models_dir)}
            )
            assert response.status_code == 200
            assert response.json().get("verified") is True
            assert response.json().get("installed_bytes", 0) > 0
        finally:
            # Stop only this test's children before pytest cleans its temp files.
            for job in app.state.model_jobs.values():
                process = job.get("_proc")
                if process is not None and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
