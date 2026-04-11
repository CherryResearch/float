import sys
from pathlib import Path
from types import SimpleNamespace


def test_shutdown_server_resources_stops_runtime_and_model_jobs():
    backend_dir = Path(__file__).resolve().parents[2]
    backend_dir = str(backend_dir)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    from app.utils.server_shutdown import shutdown_server_resources

    class DummyProc:
        def __init__(self):
            self.terminated = False

        def poll(self):
            return None

    class DummyComputerService:
        def shutdown(self):
            return {"status": "stopped", "stopped_sessions": ["browser-session"]}

    class DummyProviderManager:
        def shutdown(self):
            return {"status": "stopped", "providers": {"lmstudio": {}}}

    proc = DummyProc()
    job = {"id": "job-1", "status": "running", "_proc": proc, "pid": 123}
    app = SimpleNamespace(
        state=SimpleNamespace(
            computer_service=DummyComputerService(),
            model_jobs={"job-1": job},
        )
    )
    terminated = []

    def fake_terminate(job_dict):
        terminated.append(job_dict["id"])
        job_dict["_proc"] = None
        job_dict["pid"] = None

    result = shutdown_server_resources(
        app,
        provider_manager=DummyProviderManager(),
        terminate_job_proc=fake_terminate,
    )

    assert result["computer"] == {
        "status": "stopped",
        "stopped_sessions": ["browser-session"],
    }
    assert result["providers"] == {
        "status": "stopped",
        "providers": {"lmstudio": {}},
    }
    assert result["terminated_model_jobs"] == 1
    assert result["errors"] == []
    assert terminated == ["job-1"]
    assert job["status"] == "canceled"
