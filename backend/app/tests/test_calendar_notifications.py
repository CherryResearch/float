from datetime import datetime, timedelta, timezone

import pytest
from app.tasks import (
    dispatch_due_calendar_prompts,
    poll_calendar_events,
    send_event_prompt,
)
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
        {
            "event_id": "overnight",
            "occ_time": pytest.approx(start.timestamp(), rel=0.001),
        }
    ]
    stored = calendar_store.load_event("overnight")
    assert stored["status"] == "prompted"
    assert notifications[0]["event_id"] == "overnight"
    assert notifications[0]["description"] == "Check the overnight notes."


def test_recurring_prompt_dispatch_coalesces_to_latest_missed_occurrence(
    temp_calendar_dir, monkeypatch
):
    start = datetime.now(timezone.utc) - timedelta(minutes=10)
    calendar_store.save_event(
        "recurring-catchup",
        {
            "id": "recurring-catchup",
            "title": "Recurring catch-up",
            "start_time": start.timestamp(),
            "timezone": "UTC",
            "rrule": "FREQ=MINUTELY;INTERVAL=2;COUNT=10",
            "status": "scheduled",
        },
    )
    monkeypatch.setattr(
        user_settings,
        "load_settings",
        lambda: {"calendar_notify_minutes": 0},
    )
    calls = []
    monkeypatch.setattr(
        send_event_prompt,
        "delay",
        lambda event_id, occ_time: calls.append((event_id, occ_time)),
    )

    triggered = dispatch_due_calendar_prompts(enqueue=True)

    assert len(triggered) == 1
    assert calls == [("recurring-catchup", triggered[0]["occ_time"])]
    assert triggered[0]["occ_time"] > start.timestamp() + 8 * 60
    stored = calendar_store.load_event("recurring-catchup")
    assert stored["last_prompt_dispatched"] == triggered[0]["occ_time"]
    assert dispatch_due_calendar_prompts(enqueue=True) == []


def test_recurring_prompt_dispatch_keeps_next_day_lead_time(
    temp_calendar_dir, monkeypatch
):
    now = datetime.now(timezone.utc)
    first = now - timedelta(hours=23, minutes=55)
    next_occurrence = first + timedelta(days=1)
    calendar_store.save_event(
        "nightly-lead",
        {
            "id": "nightly-lead",
            "title": "Nightly lead",
            "start_time": first.timestamp(),
            "timezone": "UTC",
            "rrule": "FREQ=DAILY;COUNT=365",
            "status": "scheduled",
            "last_triggered": first.timestamp(),
            "last_prompt_dispatched": first.timestamp(),
        },
    )
    monkeypatch.setattr(
        user_settings,
        "load_settings",
        lambda: {"calendar_notify_minutes": 10},
    )
    calls = []
    monkeypatch.setattr(
        send_event_prompt,
        "delay",
        lambda event_id, occ_time: calls.append((event_id, occ_time)),
    )

    triggered = dispatch_due_calendar_prompts(enqueue=True)

    assert triggered == [
        {
            "event_id": "nightly-lead",
            "occ_time": pytest.approx(next_occurrence.timestamp(), abs=1),
        }
    ]
    assert calls[0][0] == "nightly-lead"


def test_malformed_rrule_does_not_abort_other_reminders(temp_calendar_dir, monkeypatch):
    now = datetime.now(timezone.utc)
    calendar_store.save_event(
        "malformed",
        {
            "id": "malformed",
            "title": "Malformed recurrence",
            "start_time": (now - timedelta(minutes=1)).timestamp(),
            "timezone": "UTC",
            "rrule": "FREQ=NOPE;BYDAY=???",
            "status": "scheduled",
        },
    )
    calendar_store.save_event(
        "valid-reminder",
        {
            "id": "valid-reminder",
            "title": "Valid reminder",
            "start_time": (now + timedelta(minutes=1)).timestamp(),
            "timezone": "UTC",
            "status": "pending",
        },
    )
    monkeypatch.setattr(
        user_settings,
        "load_settings",
        lambda: {"calendar_notify_minutes": 5},
    )
    calls = []
    monkeypatch.setattr(
        send_event_prompt,
        "delay",
        lambda event_id, occ_time: calls.append((event_id, occ_time)),
    )

    triggered = dispatch_due_calendar_prompts(enqueue=True)

    assert [item["event_id"] for item in triggered] == ["valid-reminder"]
    assert calls[0][0] == "valid-reminder"


def test_recurring_dispatch_releases_claim_when_enqueue_fails(
    temp_calendar_dir, monkeypatch
):
    start = datetime.now(timezone.utc) - timedelta(minutes=2)
    calendar_store.save_event(
        "retry-enqueue",
        {
            "id": "retry-enqueue",
            "title": "Retry enqueue",
            "start_time": start.timestamp(),
            "timezone": "UTC",
            "rrule": "FREQ=MINUTELY;COUNT=3",
            "status": "scheduled",
        },
    )
    monkeypatch.setattr(
        user_settings, "load_settings", lambda: {"calendar_notify_minutes": 0}
    )

    def fail_enqueue(*_args, **_kwargs):
        raise RuntimeError("broker down")

    monkeypatch.setattr(send_event_prompt, "delay", fail_enqueue)
    with pytest.raises(RuntimeError, match="broker down"):
        dispatch_due_calendar_prompts(enqueue=True)
    assert "last_prompt_dispatched" not in calendar_store.load_event("retry-enqueue")

    calls = []
    monkeypatch.setattr(
        send_event_prompt,
        "delay",
        lambda event_id, occ_time: calls.append((event_id, occ_time)),
    )
    triggered = dispatch_due_calendar_prompts(enqueue=True)
    assert calls == [("retry-enqueue", triggered[0]["occ_time"])]


def test_offline_prompt_worker_releases_recurring_dispatch_claim(
    temp_calendar_dir, monkeypatch
):
    start = datetime.now(timezone.utc) - timedelta(minutes=1)
    calendar_store.save_event(
        "retry-offline",
        {
            "id": "retry-offline",
            "title": "Retry offline",
            "start_time": start.timestamp(),
            "timezone": "UTC",
            "rrule": "FREQ=MINUTELY;COUNT=3",
            "status": "scheduled",
        },
    )
    monkeypatch.setattr(
        user_settings, "load_settings", lambda: {"calendar_notify_minutes": 0}
    )
    queued = []
    monkeypatch.setattr(
        send_event_prompt,
        "delay",
        lambda event_id, occ_time: queued.append((event_id, occ_time)),
    )
    first = dispatch_due_calendar_prompts(enqueue=True)
    occurrence = first[0]["occ_time"]
    assert queued == [("retry-offline", occurrence)]

    monkeypatch.setattr("app.tasks._float_online", lambda: False)
    assert send_event_prompt.run("retry-offline", occurrence) == "float offline"
    stored = calendar_store.load_event("retry-offline")
    assert "last_prompt_dispatched" not in stored

    queued.clear()
    retried = dispatch_due_calendar_prompts(enqueue=True)
    assert queued == [("retry-offline", retried[0]["occ_time"])]
