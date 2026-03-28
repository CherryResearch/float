import asyncio
import time
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_model_download_and_verify():
    # Use a small model for testing
    model_name = "kokoro"
    filename_to_check = "config.json"

    # 1. Start the download
    response = client.post(f"/api/models/jobs", json={"model": model_name})
    assert response.status_code == 200
    job = response.json().get("job")
    assert job is not None
    job_id = job.get("id")
    assert job_id is not None

    # 2. Poll for completion
    timeout = 300  # 5 minutes
    start_time = time.time()
    while time.time() - start_time < timeout:
        response = client.get(f"/api/models/jobs/{job_id}")
        assert response.status_code == 200
        job_status = response.json().get("job", {}).get("status")
        if job_status == "completed":
            break
        elif job_status == "error":
            assert False, f"Download job failed: {response.json().get('job', {}).get('error')}"
        time.sleep(1)
    else:
        assert False, "Download job timed out"

    # 3. Verify that the model is now verified
    response = client.get(f"/api/models/verify/{model_name}")
    assert response.status_code == 200
    assert response.json().get("verified") is True, "Model not verified after download"

    # 4. Clean up
    response = client.delete(f"/api/models/{model_name}")
    assert response.status_code == 200
    assert response.json().get("status") == "deleted"
