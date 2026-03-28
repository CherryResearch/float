from datetime import datetime, timedelta, timezone

import pytest

from app.tasks import dispatch_due_calendar_prompts, poll_calendar_events, send_event_prompt
from app.utils import calendar_store, user_settings


@pytest.fixture
def temp_calendar_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(calendar_store, "EVENTS_DIR", tmp_path)
    return tmp_path


def test_poll_calendar_events_honors_user_lead_time(temp_calendar_dir, monkeypatch):
    start = datetime.now(timezone.utc) + timedelta(minutes=10)
    calendar_store.save_event(
        "evt",
        {
            "id": "evt",
            "title": "Planning session",
            "start_time": start.timestamp(),
            "timezone": "UTC",
            "status": "pending",
        },
    )
    monkeypatch.setattr(
        user_settings,
        "load_settings",
        lambda: {"calendar_notify_minutes": 15},
    )

    called = {}

    def fake_delay(event_id, occ_time=None):
        called["event_id"] = event_id
        called["occ_time"] = occ_time

    monkeypatch.setattr(send_event_prompt, "delay", fake_delay)

    poll_calendar_events()

    assert called["event_id"] == "evt"
    assert called["occ_time"] == pytest.approx(start.timestamp(), rel=0.01)


def test_poll_calendar_events_skips_non_pending(temp_calendar_dir, monkeypatch):
    start = datetime.now(timezone.utc) + timedelta(minutes=5)
    calendar_store.save_event(
        "done",
        {
            "id": "done",
            "title": "Finished task",
            "start_time": start.timestamp(),
            "timezone": "UTC",
            "status": "prompted",
        },
    )
    monkeypatch.setattr(
        user_settings,
        "load_settings",
        lambda: {"calendar_notify_minutes": 20},
    )

    called = []

    def fake_delay(*args, **kwargs):
        called.append(args)

    monkeypatch.setattr(send_event_prompt, "delay", fake_delay)

    poll_calendar_events()

    assert called == []


def test_send_event_prompt_updates_store_and_push(temp_calendar_dir, monkeypatch):
    start = datetime.now(timezone.utc) + timedelta(minutes=2)
    calendar_store.save_event(
        "followup",
        {
            "id": "followup",
            "title": "Follow up",
            "start_time": start.timestamp(),
            "timezone": "UTC",
            "status": "pending",
        },
    )
    monkeypatch.setattr(
        user_settings,
        "load_settings",
        lambda: {
            "push_subscription": {"endpoint": "https://example.test"},
            "push_enabled": True,
        },
    )
    monkeypatch.setattr("app.tasks.can_send_push", lambda: True)

    push_calls = {}

    def fake_push(subscription, payload):
        push_calls["subscription"] = subscription
        push_calls["payload"] = payload
        return None

    monkeypatch.setattr("app.tasks.send_web_push", fake_push)

    message = send_event_prompt.run("followup", start.timestamp())

    stored = calendar_store.load_event("followup")
    assert stored["status"] == "prompted"
    assert stored["prompt_message"] == message
    assert stored["last_triggered"] == pytest.approx(start.timestamp(), rel=0.001)
    assert push_calls["subscription"]["endpoint"] == "https://example.test"
    assert push_calls["payload"]["title"] == "Follow up"
    assert push_calls["payload"]["data"]["event_id"] == "followup"
    assert "Upcoming event" in message


def test_dispatch_due_calendar_prompts_flushes_overdue_reminders(
    temp_calendar_dir, monkeypatch
):
    start = datetime.now(timezone.utc) - timedelta(hours=7)
    calendar_store.save_event(
        "overnight",
        {
            "id": "overnight",
            "title": "Overnight reminder",
            "description": "Check the overnight notes.",
            "start_time": start.timestamp(),
            "timezone": "UTC",
            "status": "pending",
        },
    )
    monkeypatch.setattr(
        user_settings,
        "load_settings",
        lambda: {"calendar_notify_minutes": 0},
    )
    monkeypatch.setattr("app.tasks.can_send_push", lambda: False)

    notifications = []

    def fake_emit(**payload):
        notifications.append(payload)

    monkeypatch.setattr("app.tasks._emit_calendar_notification", fake_emit)

    triggered = dispatch_due_calendar_prompts(enqueue=False)

    assert triggered == [
        {"event_id": "overnight", "occ_time": pytest.approx(start.timestamp(), rel=0.001)}
    ]
    stored = calendar_store.load_event("overnight")
    assert stored["status"] == "prompted"
    assert notifications[0]["event_id"] == "overnight"
    assert notifications[0]["description"] == "Check the overnight notes."
