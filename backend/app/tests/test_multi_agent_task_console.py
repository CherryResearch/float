import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def add_backend_to_sys_path():
    backend_dir = Path(__file__).resolve().parents[2]
    backend_dir = str(backend_dir)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)


@pytest.fixture
def client(add_backend_to_sys_path):
    from app.main import app

    app.state.agent_console_state = {"agents": {}, "resources": {}}
    return TestClient(app)


def test_multi_agent_task_chain_publishes_console_events(monkeypatch, client):
    from app import routes

    captured = {}

    class SubmittedTask:
        id = "task-123"

    class StoredTask:
        state = "SUCCESS"
        result = {"ok": True}

        def ready(self):
            return True

    def fake_plan_and_execute(steps):
        captured["steps"] = steps
        return SubmittedTask()

    def fake_result(task_id):
        captured["task_id"] = task_id
        return StoredTask()

    monkeypatch.setattr(routes.engine, "plan_and_execute", fake_plan_and_execute)
    monkeypatch.setattr(routes.engine, "result", fake_result)

    queued = client.post(
        "/api/tasks/",
        json={
            "workflow": "architect_planner",
            "handoff": {
                "summary": "Verify the search result before responding.",
                "recent_turn_ids": ["msg-1"],
                "open_goals": [
                    {
                        "id": "goal-1",
                        "title": "verify search result",
                        "status": "open",
                    }
                ],
            },
            "provenance": {
                "kind": "task_chain",
                "parent_session_id": "sess-1",
                "parent_message_id": "msg-1",
                "label": "Delegated from chat",
            },
            "steps": [
                {
                    "agent": "execute_tool",
                    "args": ["search"],
                    "kwargs": {
                        "session_id": "sess-1",
                        "message_id": "msg-1",
                        "chain_id": "chain-1",
                    },
                },
                {"agent": "long_running_task"},
            ],
        },
    )

    assert queued.status_code == 200
    assert queued.json() == {"task_id": "task-123"}
    assert captured["steps"][0]["kwargs"]["session_id"] == "sess-1"

    first_snapshot = client.get("/api/agents/console").json()
    agent = next(
        item for item in first_snapshot["agents"] if item["id"] == "task:task-123"
    )
    assert agent["status"] == "queued"
    assert agent["summary"] == (
        "Queued 2-step task chain: execute_tool -> long_running_task"
    )
    assert agent["workflow"]["id"] == "architect_planner"
    assert agent["workflow"]["role"] == "architect"
    assert agent["provenance"]["parent_session_id"] == "sess-1"
    assert agent["handoff"]["summary"] == "Verify the search result before responding."
    assert agent["controls"]["available"] == ["stop"]
    assert agent["events"][0]["session_id"] == "sess-1"
    assert agent["events"][0]["message_id"] == "msg-1"
    assert agent["events"][0]["chain_id"] == "chain-1"

    status = client.get("/api/tasks/task-123")

    assert status.status_code == 200
    assert status.json() == {"state": "SUCCESS", "result": {"ok": True}}
    assert captured["task_id"] == "task-123"

    second_snapshot = client.get("/api/agents/console").json()
    agent = next(
        item for item in second_snapshot["agents"] if item["id"] == "task:task-123"
    )
    assert agent["status"] == "complete"
    assert agent["summary"] == "Task chain state: SUCCESS"
    assert agent["controls"]["available"] == []
    assert [event["status"] for event in agent["events"]] == ["queued", "success"]


def test_agent_console_only_stops_real_delegated_tasks(monkeypatch, client):
    from app import routes

    class ParentTask:
        id = "task-upstream"
        parent = None

    class SubmittedTask:
        id = "task-ctrl"
        parent = ParentTask()

    def fake_plan_and_execute(_steps):
        return SubmittedTask()

    revoked = []

    async def fake_stop_task(task_id):
        revoked.append(task_id)

    monkeypatch.setattr(routes.engine, "plan_and_execute", fake_plan_and_execute)
    monkeypatch.setattr(routes, "_console_stop_task", fake_stop_task)

    queued = client.post(
        "/api/tasks/",
        json={
            "provenance": {
                "kind": "task_chain",
                "task_id": "unrelated-client-task",
            },
            "steps": [{"agent": "long_running_task"}],
        },
    )
    assert queued.status_code == 200

    paused = client.post("/api/agents/console/task%3Atask-ctrl/pause", json={})
    assert paused.status_code == 409
    assert "not supported" in paused.json()["detail"]

    redirected = client.post(
        "/api/agents/console/task%3Atask-ctrl/redirect",
        json={"note": "Switch this to the verifier pass.", "workflow": "default"},
    )
    assert redirected.status_code == 409
    assert "not supported" in redirected.json()["detail"]

    resumed = client.post("/api/agents/console/task%3Atask-ctrl/resume", json={})
    assert resumed.status_code == 409
    assert "not supported" in resumed.json()["detail"]

    stopped = client.post("/api/agents/console/task%3Atask-ctrl/stop", json={})
    assert stopped.status_code == 200
    assert stopped.json()["agent"]["status"] == "stopped"
    assert revoked == ["task-ctrl", "task-upstream"]
    assert "unrelated-client-task" not in revoked

    class PendingTask:
        state = "PENDING"

        def ready(self):
            return False

    monkeypatch.setattr(routes.engine, "result", lambda _task_id: PendingTask())
    assert client.get("/api/tasks/task-ctrl").status_code == 200
    snapshot = client.get("/api/agents/console").json()
    task_agent = next(
        item for item in snapshot["agents"] if item["id"] == "task:task-ctrl"
    )
    assert task_agent["status"] == "stopped"
    assert task_agent["controls"]["available"] == []


def test_agent_console_reports_stop_transport_failures(monkeypatch, client):
    from app import routes
    from app.main import app

    app.state.agent_console_state = {
        "agents": {
            "task:task-fail": {
                "id": "task:task-fail",
                "label": "Celery task chain",
                "status": "active",
                "controls": {"available": ["stop"]},
                "provenance": {"task_id": "task-fail"},
                "events": [],
            }
        },
        "resources": {},
    }

    async def fail_stop(_task_id):
        raise RuntimeError("broker offline")

    monkeypatch.setattr(routes, "_console_stop_task", fail_stop)
    response = client.post("/api/agents/console/task%3Atask-fail/stop", json={})

    assert response.status_code == 503
    assert "broker offline" in response.json()["detail"]
    assert app.state.agent_console_state["agents"]["task:task-fail"]["status"] == (
        "active"
    )


def test_revoked_task_status_maps_to_stopped():
    from app import routes

    assert routes._task_console_status("REVOKED") == "stopped"


def test_agent_console_rejects_stop_without_celery_task_id(client):
    from app.main import app

    app.state.agent_console_state = {
        "agents": {
            "background:synthetic": {
                "id": "background:synthetic",
                "label": "Synthetic background status",
                "status": "active",
                "controls": {"available": ["stop"]},
                "events": [],
            }
        },
        "resources": {},
    }

    response = client.post(
        "/api/agents/console/background%3Asynthetic/stop",
        json={},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Stop requires a live Celery task id"


def test_agent_console_includes_celery_worker_snapshot(monkeypatch, client):
    from app import routes

    async def fake_celery_status():
        return {
            "online": True,
            "workers": ["celery@devbox"],
            "details": {
                "active": {"celery@devbox": 1},
                "scheduled": {"celery@devbox": 1},
                "reserved": {"celery@devbox": 0},
                "queues": {"celery@devbox": ["celery"]},
                "stats": {
                    "celery@devbox": {
                        "pid": 4242,
                        "uptime": 91,
                        "process_count": 4,
                        "max_concurrency": 8,
                    }
                },
            },
        }

    async def fake_celery_tasks(state="active", limit=50):
        assert state == "all"
        return {
            "state": state,
            "tasks": [
                {
                    "worker": "celery@devbox",
                    "id": "run-12345678",
                    "name": "app.tasks.execute_tool",
                    "state": "active",
                },
                {
                    "worker": "celery@devbox",
                    "id": "run-87654321",
                    "name": "app.tasks.long_running_task",
                    "state": "scheduled",
                },
            ],
        }

    monkeypatch.setattr(routes, "celery_status", fake_celery_status)
    monkeypatch.setattr(routes, "celery_tasks", fake_celery_tasks)

    snapshot = client.get("/api/agents/console")

    assert snapshot.status_code == 200
    agents = {item["id"]: item for item in snapshot.json()["agents"]}

    system_agent = agents["system:celery"]
    assert system_agent["status"] == "active"
    assert "1 worker" in system_agent["summary"]
    assert "1 active task" in system_agent["summary"]
    assert "1 scheduled task" in system_agent["summary"]

    worker_agent = agents["worker:celery@devbox"]
    assert worker_agent["label"] == "worker celery"
    assert worker_agent["status"] == "active"
    assert "queues: celery" in worker_agent["summary"]
    assert worker_agent["resources"]["pid"] == 4242
    assert [event["content"] for event in worker_agent["events"][1:]] == [
        "app.tasks.execute_tool (run-1234)",
        "app.tasks.long_running_task (run-8765)",
    ]


def test_celery_broker_probe_reports_unreachable_endpoint(monkeypatch):
    from app import routes

    def fake_create_connection(*_args, **_kwargs):
        raise OSError("refused")

    monkeypatch.setattr(routes.socket, "create_connection", fake_create_connection)

    assert (
        routes._celery_broker_unreachable_error("redis://127.0.0.1:6380/0")
        == "broker unreachable (127.0.0.1:6380)"
    )


def test_celery_broker_probe_allows_reachable_endpoint(monkeypatch):
    from app import routes

    class _SocketContext:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        routes.socket,
        "create_connection",
        lambda *_args, **_kwargs: _SocketContext(),
    )

    assert routes._celery_broker_unreachable_error("redis://127.0.0.1:6380/0") is None
