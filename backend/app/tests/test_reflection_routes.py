from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient


def test_reflection_routes_create_run_and_emit_console(tmp_path, monkeypatch):
    backend_dir = Path(__file__).resolve().parents[2]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    from app.main import app
    from app.services import reflection_service as reflection_module
    from app.services.reflection_service import ReflectionService
    from app.tools.reflections import set_reflection_service
    from app.utils import conversation_store

    monkeypatch.setattr(reflection_module, "try_ingest_text", lambda *a, **k: "doc-1")
    monkeypatch.setattr(reflection_module, "get_rag_service", lambda *a, **k: None)
    monkeypatch.setattr(conversation_store, "CONV_DIR", tmp_path / "conversations")
    conversation_store.CONV_DIR.mkdir(parents=True, exist_ok=True)

    def fake_generate(prompt, **kwargs):
        if kwargs.get("session_id") == "reflection-task-scorer":
            return {"text": '{"utility": 0.75, "uncertainty": 0.70}'}
        if kwargs.get("response_format") == "json_object":
            return {
                "text": (
                    '{"novelty": 0.90, "usefulness": 0.86, '
                    '"uncertainty_delta": 0.40, "repetition": 0.05, '
                    '"continue": false, "should_surface_to_user": true, '
                    '"cooldown_seconds": 0}'
                )
            }
        return {
            "text": (
                "Current best synthesis: this reflection should be visible.\n"
                "What changed: it became actionable.\n"
                "Remaining uncertainty: low.\n"
                "Another pass: no.\n"
                "Surface to user: yes."
            )
        }

    service = ReflectionService(
        {"reflection_store_path": str(tmp_path / "reflections.sqlite3")},
        llm_generate=fake_generate,
    )
    app.state.reflection_service = service
    app.state.config = {"reflection_scheduler_enabled": False}
    app.state.agent_console_state = {"agents": {}, "resources": {}}
    set_reflection_service(service)

    client = TestClient(app)
    response = client.post(
        "/api/reflections/tasks",
        json={
            "question": "What is the most useful next reflection?",
            "patience_budget": {
                "profile": "custom",
                "max_reasoning_turns": 3,
                "max_runtime_seconds": 600,
                "max_context_tokens": 24000,
            },
            "run_now": True,
            "metadata": {"source_mode": "manual"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ran"
    assert payload["run"]["reflection_conversation"].startswith("reflections/")
    task_id = payload["task"]["id"]
    assert service.get_task(task_id)["status"] == "resolved"
    assert f"reflection:{task_id}" in app.state.agent_console_state["agents"]
    console_record = app.state.agent_console_state["agents"][f"reflection:{task_id}"]
    assert "Current best synthesis" in console_record["summary"]
    latest_event = console_record["events"][-1]
    assert latest_event["type"] == "thought"
    assert "Current best synthesis" in latest_event["content"]
    assert latest_event["metadata"]["depth_budget"] == 3
    assert latest_event["metadata"]["run_count"] == 1
    assert latest_event["metadata"]["input_context_count"] >= 0
    assert latest_event["metadata"]["should_surface_to_user"] is True

    listed = client.get("/api/reflections/tasks", params={"include_runs": "true"})
    assert listed.status_code == 200
    assert listed.json()["count"] == 1
    detail = client.get(f"/api/reflections/tasks/{task_id}")
    assert detail.status_code == 200
    assert len(detail.json()["runs"]) == 1


def test_reflection_tick_blocks_routine_mode_by_default(tmp_path, monkeypatch):
    backend_dir = Path(__file__).resolve().parents[2]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    from app.main import app
    from app.services.reflection_service import ReflectionService
    from app.tools.reflections import set_reflection_service

    service = ReflectionService(
        {"reflection_store_path": str(tmp_path / "reflections.sqlite3")},
        llm_generate=lambda *a, **k: None,
    )
    app.state.reflection_service = service
    app.state.config = {"reflection_scheduler_enabled": False}
    app.state.agent_console_state = {"agents": {}, "resources": {}}
    set_reflection_service(service)

    client = TestClient(app)
    response = client.post("/api/reflections/tick", json={"mode": "nightly"})

    assert response.status_code == 200
    assert response.json()["tick"]["status"] == "disabled"
