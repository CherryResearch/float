import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    backend_dir = Path(__file__).resolve().parents[2]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    from app.main import app
    from app.utils import calendar_store, conversation_store, user_settings

    monkeypatch.setattr(conversation_store, "CONV_DIR", tmp_path, raising=False)
    monkeypatch.setattr(
        calendar_store, "EVENTS_DIR", tmp_path / "calendar", raising=False
    )
    monkeypatch.setattr(
        user_settings,
        "USER_SETTINGS_PATH",
        tmp_path / "user_settings.json",
        raising=False,
    )
    calendar_store.EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    app.state.pending_tools = {}
    app.state.agent_console_state = {"agents": {}}
    return TestClient(app)


def test_tool_schedule_marks_scheduled_and_links_event(client):
    from app.main import app
    from app.utils import calendar_store, conversation_store

    session_id = "s1"
    message_id = "m1"
    request_id = "rid-1"
    event_id = "calendar-123"

    conversation_store.save_conversation(
        session_id,
        [
            {
                "id": message_id,
                "role": "ai",
                "text": "Requested tool remember.",
                "tools": [
                    {
                        "id": request_id,
                        "name": "remember",
                        "args": {"key": "k", "value": "v"},
                        "status": "proposed",
                        "result": None,
                    }
                ],
            }
        ],
    )

    app.state.pending_tools[request_id] = {
        "id": request_id,
        "name": "remember",
        "args": {"key": "k", "value": "v"},
        "session_id": session_id,
        "message_id": message_id,
        "chain_id": message_id,
        "status": "proposed",
    }

    resp = client.post(
        "/api/tools/schedule",
        json={
            "request_id": request_id,
            "event_id": event_id,
            "prompt": "When this runs, summarize the result.",
            "conversation_mode": "new_chat",
            "session_id": session_id,
            "message_id": message_id,
            "chain_id": message_id,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "scheduled"
    assert request_id not in app.state.pending_tools

    stored = conversation_store.load_conversation(session_id)
    assert stored[0]["id"] == message_id
    tool = stored[0]["tools"][0]
    assert tool["id"] == request_id
    assert tool["status"] == "scheduled"
    assert tool["result"]["scheduled_event_id"] == event_id

    snap = client.get("/api/agents/console")
    assert snap.status_code == 200
    agents = snap.json().get("agents") or []
    assert agents
    flattened = []
    for agent in agents:
        flattened.extend(agent.get("events") or [])
    scheduled = [
        e for e in flattened if e.get("type") == "tool" and e.get("id") == request_id
    ]
    assert scheduled
    assert scheduled[-1].get("status") == "scheduled"
    assert scheduled[-1].get("scheduled_event_id") == event_id

    stored_event = calendar_store.load_event(event_id)
    assert stored_event
    assert stored_event.get("status") == "scheduled"
    assert "remember" in (stored_event.get("title") or "")
    actions = stored_event.get("actions") or []
    assert isinstance(actions, list)
    action = next(
        (
            item
            for item in actions
            if isinstance(item, dict)
            and str(item.get("request_id") or item.get("id") or "") == request_id
        ),
        None,
    )
    assert action is not None
    assert action.get("kind") == "tool"
    assert action.get("name") == "remember"
    assert action.get("status") == "scheduled"
    assert action.get("prompt") == "When this runs, summarize the result."
    assert action.get("conversation_mode") == "new_chat"


def test_tool_propose_dedupes_same_signature(client):
    from app import routes
    from app.utils import user_settings

    notifications = []

    def fake_emit_notification(app, **kwargs):
        notifications.append(kwargs)

    user_settings.save_settings(
        {
            "tool_resolution_notifications": True,
            "approval_level": "all",
        }
    )

    original_emit = routes.emit_notification
    routes.emit_notification = fake_emit_notification
    try:
        first = client.post(
            "/api/tools/propose",
            json={
                "name": "read_file",
                "args": {"path": "data/workspace/hello.txt"},
                "session_id": "sess",
                "message_id": "m1",
                "chain_id": "m1",
            },
        )
        assert first.status_code == 200
        first_id = first.json().get("id")
        assert first_id

        second = client.post(
            "/api/tools/propose",
            json={
                "name": "read_file",
                "args": {"path": "data/workspace/hello.txt"},
                "session_id": "sess",
                "message_id": "m1",
                "chain_id": "m1",
            },
        )
        assert second.status_code == 200
        assert second.json().get("id") == first_id
    finally:
        routes.emit_notification = original_emit

    assert len(notifications) == 1
    notice = notifications[0]
    assert notice["category"] == "tool_resolution"
    assert "review" in notice["body"].lower()
    assert notice["data"]["tool_ids"] == [first_id]

    from app.main import app

    registry = getattr(app.state, "pending_tools", {})
    assert isinstance(registry, dict)
    assert len(registry) == 1


def test_tool_propose_skips_notification_when_auto_approval(client):
    from app import routes
    from app.utils import user_settings

    notifications = []

    def fake_emit_notification(app, **kwargs):
        notifications.append(kwargs)

    user_settings.save_settings(
        {
            "tool_resolution_notifications": True,
            "approval_level": "auto",
        }
    )

    original_emit = routes.emit_notification
    routes.emit_notification = fake_emit_notification
    try:
        resp = client.post(
            "/api/tools/propose",
            json={
                "name": "read_file",
                "args": {"path": "data/workspace/hello.txt"},
                "session_id": "sess",
                "message_id": "m1",
                "chain_id": "m1",
            },
        )
        assert resp.status_code == 200
    finally:
        routes.emit_notification = original_emit

    assert notifications == []
