from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from fastapi.testclient import TestClient


def test_background_autonomy_routes_publish_console_status(tmp_path, monkeypatch):
    backend_dir = Path(__file__).resolve().parents[2]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    from app import config as app_config
    from app.main import app
    from app.services.background_autonomy_service import BackgroundAutonomyService
    from app.services.reflection_service import ReflectionService
    from app.tools.reflections import set_reflection_service
    from app.utils import calendar_store

    monkeypatch.setattr(calendar_store, "list_events", lambda: [])
    monkeypatch.setattr(app_config, "get_dotenv_path", lambda: tmp_path / ".env")

    reflection = ReflectionService(
        {"reflection_store_path": str(tmp_path / "reflections.sqlite3")},
        llm_generate=lambda *a, **k: None,
    )
    reflection.create_task(
        title="Route visible task",
        question="What should background autonomy inspect?",
        utility=0.70,
        uncertainty=0.65,
    )
    autonomy = BackgroundAutonomyService(
        {
            "background_autonomy_store_path": str(tmp_path / "autonomy.json"),
            "background_autonomy_enabled": False,
            "background_autonomy_sandbox_processes": True,
            "background_autonomy_min_priority": 0.01,
        },
        reflection_service=reflection,
    )
    app.state.reflection_service = reflection
    app.state.background_autonomy_service = autonomy
    app.state.config = {"background_autonomy_enabled": False}
    app.state.agent_console_state = {"agents": {}, "resources": {}}
    app.state.background_autonomy_wakeup = asyncio.Event()
    set_reflection_service(reflection)

    client = TestClient(app)

    status = client.get("/api/background/autonomy/status")
    assert status.status_code == 200
    assert status.json()["autonomy"]["reflection"]["candidate_count"] == 1
    assert status.json()["autonomy"]["configured_mode"] == "overnight"
    assert status.json()["autonomy"]["sandbox_processes"] is True

    tick = client.post("/api/background/autonomy/tick", json={"dry_run": True})
    assert tick.status_code == 200
    assert tick.json()["tick"]["status"] == "planned"

    saved = client.post(
        "/api/settings",
        json={
            "background_autonomy_enabled": True,
            "background_autonomy_sandbox_processes": True,
            "background_autonomy_mode": "basic",
            "background_autonomy_max_runtime_seconds": 1800,
            "background_autonomy_basic_tick_count": 2,
            "background_autonomy_basic_tick_seconds": 300,
            "background_autonomy_satisfied_threshold": 0.8,
        },
    )
    assert saved.status_code == 200
    saved_settings = client.get("/api/settings")
    assert saved_settings.status_code == 200
    assert saved_settings.json()["background_autonomy_enabled"] is True
    assert saved_settings.json()["background_autonomy_sandbox_processes"] is True
    assert saved_settings.json()["background_autonomy_mode"] == "basic"
    assert saved_settings.json()["background_autonomy_max_runtime_seconds"] == 1800
    assert saved_settings.json()["background_autonomy_basic_tick_count"] == 2
    assert app.state.background_autonomy_service.mode() == "basic"
    assert app.state.background_autonomy_wakeup.is_set()
    app.state.background_autonomy_wakeup.clear()

    unchanged = client.post(
        "/api/settings",
        json={
            "background_autonomy_enabled": True,
            "background_autonomy_sandbox_processes": True,
            "background_autonomy_mode": "basic",
            "background_autonomy_max_runtime_seconds": 1800,
            "background_autonomy_basic_tick_count": 2,
            "background_autonomy_basic_tick_seconds": 300,
            "background_autonomy_satisfied_threshold": 0.8,
        },
    )
    assert unchanged.status_code == 200
    assert app.state.background_autonomy_wakeup.is_set() is False

    console = client.get("/api/agents/console")
    assert console.status_code == 200
    agents = {item["id"]: item for item in console.json()["agents"]}
    autonomy_agent = agents["system:background-autonomy"]
    assert autonomy_agent["label"] == "background autonomy"
    assert autonomy_agent["status"] == "idle"
    assert "1 reflection candidate" in autonomy_agent["summary"]
    assert autonomy_agent["resources"]["candidate_count"] == 1
    assert autonomy_agent["controls"]["available"] == []


def test_background_autonomy_console_agent_reports_running_tick_as_active():
    from app import routes

    agent = routes._background_autonomy_console_agent(
        {
            "enabled": True,
            "configured_mode": "basic",
            "state": {"running": True, "last_status": "planned"},
            "reflection": {},
            "scheduled_actions": {},
        }
    )

    assert agent["status"] == "active"
