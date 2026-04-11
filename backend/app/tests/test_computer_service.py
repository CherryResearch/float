import sys
from pathlib import Path

import pytest


def test_computer_service_shutdown_stops_sessions_and_runtimes():
    backend_dir = Path(__file__).resolve().parents[2]
    backend_dir = str(backend_dir)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    from app.computer.types import ComputerSessionState
    from app.services.computer_service import ComputerService

    class DummyRuntime:
        def __init__(self, name):
            self.name = name
            self.stopped = []
            self.shutdown_calls = 0

        def stop_session(self, session):
            self.stopped.append(session.id)
            return {"status": "stopped", "session_id": session.id}

        def shutdown(self):
            self.shutdown_calls += 1
            return {"status": "stopped", "runtime": self.name}

    service = ComputerService(config={})
    browser_runtime = DummyRuntime("browser")
    windows_runtime = DummyRuntime("windows")
    service.runtimes = {
        "browser": browser_runtime,
        "windows": windows_runtime,
    }
    service.store.put(
        ComputerSessionState(
            id="browser-session",
            runtime="browser",
            status="active",
            width=800,
            height=600,
            created_at=1.0,
            updated_at=1.0,
        )
    )
    service.store.put(
        ComputerSessionState(
            id="windows-session",
            runtime="windows",
            status="active",
            width=800,
            height=600,
            created_at=1.0,
            updated_at=1.0,
        )
    )

    result = service.shutdown()

    assert result["status"] == "stopped"
    assert result["stopped_sessions"] == ["browser-session", "windows-session"]
    assert result["errors"] == []
    assert browser_runtime.stopped == ["browser-session"]
    assert windows_runtime.stopped == ["windows-session"]
    assert browser_runtime.shutdown_calls == 1
    assert windows_runtime.shutdown_calls == 1
    assert service.store.all() == {}


def test_computer_service_construction_survives_windows_runtime_import_failure(
    monkeypatch,
):
    backend_dir = Path(__file__).resolve().parents[2]
    backend_dir = str(backend_dir)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    from app.services import computer_service as service_mod

    original_import_module = service_mod.importlib.import_module

    def fake_import_module(name, package=None):
        if name == "app.computer.windows_runtime":
            raise ImportError("simulated pywinauto import failure")
        return original_import_module(name, package)

    monkeypatch.setattr(service_mod.importlib, "import_module", fake_import_module)

    service = service_mod.ComputerService(config={})

    assert service.runtimes["browser"].name == "browser"
    assert service.runtimes["windows"].available() is False
    with pytest.raises(RuntimeError, match="Windows desktop control is unavailable"):
        service.start_session(runtime="windows", session_id="win-session")
