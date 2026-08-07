from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def cancellation_client(tmp_path, monkeypatch):
    backend_dir = Path(__file__).resolve().parents[2]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    from app.main import app
    from app.services.work_run_store import WorkRunStore
    from app.utils import calendar_store

    monkeypatch.setattr(
        calendar_store, "EVENTS_DIR", tmp_path / "calendar", raising=False
    )
    calendar_store.EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    original_store = getattr(app.state, "work_run_store", None)
    app.state.work_run_store = WorkRunStore(data_dir=tmp_path)
    try:
        yield TestClient(app), calendar_store, app.state.work_run_store
    finally:
        if original_store is not None:
            app.state.work_run_store = original_store
        elif hasattr(app.state, "work_run_store"):
            delattr(app.state, "work_run_store")


def _seed_cancellable_action(
    calendar_store,
    *,
    event_id="cancel-event",
    action_id="cancel-action",
    run_id="cancel-run",
    status="running",
    recurring=False,
):
    occurrence_at = 1_900_000_000.0
    action = {
        "id": action_id,
        "request_id": action_id,
        "kind": "tool",
        "name": "remember",
        "args": {"key": "route-test", "value": "safe"},
        "status": status,
        "run_id": run_id,
        "running_occurrence_at": occurrence_at,
        "work_run_receipt_id": f"receipt-{event_id}",
    }
    receipt_status = status
    if status in {"authorization_required", "authorization_approved"}:
        authorization_status = (
            "approved_once"
            if status == "authorization_approved"
            else "authorization_required"
        )
        action["authorization"] = {
            "id": f"authorization-{event_id}",
            "status": authorization_status,
            "occurrence_at": occurrence_at,
            "request_digest": f"sha256:{'a' * 64}",
            "can_approve": authorization_status == "authorization_required",
        }
        receipt_status = status
    event = {
        "id": event_id,
        "title": "Cancel exact scheduled action",
        "start_time": occurrence_at,
        "timezone": "UTC",
        "status": "running" if status == "running" else "scheduled",
        "actions": [action],
        "run_history": [
            {
                "id": f"receipt-{event_id}",
                "run_id": run_id,
                "event_id": event_id,
                "action_id": action_id,
                "action_name": "remember",
                "occurrence_at": occurrence_at,
                "started_at": occurrence_at - 5,
                "status": receipt_status,
                "phase": (
                    "authorization" if status.startswith("authorization_") else "tool"
                ),
                "tool_invoked": status == "running",
                "state_delta_certainty": (
                    "unknown" if status == "running" else "confirmed_no_change"
                ),
            }
        ],
    }
    if recurring:
        event["rrule"] = "FREQ=DAILY;COUNT=3"
    calendar_store.save_event(event_id, event)
    return {
        "event_id": event_id,
        "action_id": action_id,
        "run_id": run_id,
        "receipt_id": f"receipt-{event_id}",
        "occurrence_at": occurrence_at,
    }


def _endpoint(ids):
    return (
        f"/api/calendar/events/{ids['event_id']}/actions/" f"{ids['action_id']}/cancel"
    )


def test_pending_action_cancels_before_dispatch_and_invalidates_authorization(
    cancellation_client,
    monkeypatch,
):
    client, calendar_store, store = cancellation_client
    ids = _seed_cancellable_action(
        calendar_store,
        status="authorization_approved",
    )
    replay_attempts = []

    from app import tasks

    def fail_if_replayed(*args, **kwargs):
        replay_attempts.append((args, kwargs))
        raise AssertionError("cancellation must not replay a tool")

    monkeypatch.setattr(tasks, "execute_tool", fail_if_replayed)

    response = client.post(_endpoint(ids), json={"run_id": ids["run_id"]})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "cancelled"
    assert payload["termination_confirmed"] is True
    assert payload["tool_invoked"] is False
    assert payload["tool_replayed"] is False
    assert payload["activity_projected"] is True
    assert payload["receipt_id"] == ids["receipt_id"]
    assert replay_attempts == []

    event = calendar_store.load_event(ids["event_id"])
    action = event["actions"][0]
    assert event["status"] == "cancelled"
    assert action["status"] == "cancelled"
    assert action["cancel_requested"] is True
    assert action["cancelled_at"] > 0
    assert action["authorization"]["status"] == "invalidated"
    assert action["authorization"]["invalidation_reason"] == "user_cancelled"
    receipt = event["run_history"][0]
    assert receipt["status"] == "cancelled"
    assert receipt["phase"] == "cancellation"
    assert receipt["tool_invoked"] is False
    assert receipt["state_delta_certainty"] == "confirmed_no_change"
    assert receipt["finished_at"] > 0
    assert store.get(ids["receipt_id"])["status"] == "cancelled"
    assert store.get(ids["receipt_id"])["recovery_state"] == "terminal"


def test_running_action_records_cancel_requested_until_runner_acknowledges(
    cancellation_client,
):
    client, calendar_store, store = cancellation_client
    ids = _seed_cancellable_action(calendar_store, status="running")

    first = client.post(_endpoint(ids), json={"run_id": ids["run_id"]})
    repeated = client.post(_endpoint(ids), json={"run_id": ids["run_id"]})

    assert first.status_code == 200
    assert first.json()["status"] == "cancel_requested"
    assert first.json()["termination_confirmed"] is False
    assert first.json()["idempotent"] is False
    assert repeated.status_code == 200
    assert repeated.json()["status"] == "cancel_requested"
    assert repeated.json()["termination_confirmed"] is False
    assert repeated.json()["idempotent"] is True

    event = calendar_store.load_event(ids["event_id"])
    action = event["actions"][0]
    assert event["status"] == "running"
    assert action["status"] == "running"
    assert action["cancel_requested"] is True
    assert "cancelled_at" not in action
    receipt = event["run_history"][0]
    assert receipt["status"] == "cancel_requested"
    assert receipt["phase"] == "cancellation"
    assert "finished_at" not in receipt
    projected = store.get(ids["receipt_id"])
    assert projected["status"] == "cancel_requested"
    assert projected["recovery_state"] != "terminal"


def test_stale_pending_status_with_dispatched_effect_projects_reconciliation(
    cancellation_client,
    monkeypatch,
):
    client, calendar_store, store = cancellation_client
    ids = _seed_cancellable_action(
        calendar_store,
        status="authorization_approved",
    )
    event = calendar_store.load_event(ids["event_id"])
    action = event["actions"][0]
    action.update(
        {
            "effect_id": "stale-dispatched-effect",
            "effect_status": "dispatched",
            "effect_certainty": "unknown",
            "tool_invoked": True,
            "reconcile_required": True,
        }
    )
    receipt = event["run_history"][0]
    receipt.update(
        {
            "effect_status": "dispatched",
            "effect_certainty": "unknown",
            "tool_invoked": True,
            "state_delta_certainty": "unknown",
            "reconcile_required": True,
        }
    )
    calendar_store.save_event(ids["event_id"], event)
    replay_attempts = []

    from app import tasks

    def fail_if_replayed(*args, **kwargs):
        replay_attempts.append((args, kwargs))
        raise AssertionError("stale cancellation must not replay a tool")

    monkeypatch.setattr(tasks, "execute_tool", fail_if_replayed)

    response = client.post(_endpoint(ids), json={"run_id": ids["run_id"]})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "reconcile_required"
    assert payload["termination_confirmed"] is False
    assert payload["reconcile_required"] is True
    assert payload["tool_invoked"] is True
    assert payload["tool_replayed"] is False
    assert payload["activity_projected"] is True
    assert replay_attempts == []
    stored = calendar_store.load_event(ids["event_id"])
    action = stored["actions"][0]
    assert stored["status"] == "prompted"
    assert action["status"] == "reconcile_required"
    assert action["cancel_requested"] is True
    assert action["effect_id"] == "stale-dispatched-effect"
    assert action["effect_status"] == "dispatched"
    assert action["effect_certainty"] == "unknown"
    assert "cancelled_at" not in action
    receipt = stored["run_history"][0]
    assert receipt["status"] == "reconcile_required"
    assert receipt["state_delta_certainty"] == "unknown"
    assert receipt["state_delta_certainty"] != "confirmed_no_change"
    projected = store.get(ids["receipt_id"])
    assert projected["status"] == "reconcile_required"
    assert projected["recovery_state"] == "attention"
    assert projected["state_delta_certainty"] == "unknown"


def test_recurring_pre_dispatch_cancel_advances_only_current_occurrence(
    cancellation_client,
):
    client, calendar_store, store = cancellation_client
    ids = _seed_cancellable_action(
        calendar_store,
        status="authorization_required",
        recurring=True,
    )

    response = client.post(_endpoint(ids), json={"run_id": ids["run_id"]})

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    event = calendar_store.load_event(ids["event_id"])
    action = event["actions"][0]
    assert event["status"] == "scheduled"
    assert action["status"] == "cancelled"
    assert action["last_occurrence_at"] == ids["occurrence_at"]
    assert action["authorization"]["status"] == "invalidated"
    assert store.get(ids["receipt_id"])["status"] == "cancelled"


def test_cancel_route_is_local_only_and_rejects_wrong_or_missing_run(
    cancellation_client,
):
    client, calendar_store, _store = cancellation_client
    ids = _seed_cancellable_action(calendar_store, status="running")

    remote = client.post(
        _endpoint(ids),
        json={"run_id": ids["run_id"]},
        headers={"x-forwarded-for": "192.168.1.25"},
    )
    wrong_run = client.post(_endpoint(ids), json={"run_id": "wrong-run"})
    missing_action = client.post(
        f"/api/calendar/events/{ids['event_id']}/actions/missing/cancel",
        json={"run_id": ids["run_id"]},
    )
    missing_event = client.post(
        "/api/calendar/events/missing/actions/missing/cancel",
        json={"run_id": ids["run_id"]},
    )

    assert remote.status_code == 403
    assert wrong_run.status_code == 409
    assert missing_action.status_code == 404
    assert missing_event.status_code == 404
    action = calendar_store.load_event(ids["event_id"])["actions"][0]
    assert "cancel_requested" not in action


def test_cancel_route_maps_calendar_storage_failure_to_service_unavailable(
    cancellation_client,
    monkeypatch,
):
    client, calendar_store, _store = cancellation_client
    ids = _seed_cancellable_action(calendar_store, status="running")

    def fail_update(*args, **kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(calendar_store, "update_event", fail_update)
    response = client.post(_endpoint(ids), json={"run_id": ids["run_id"]})

    assert response.status_code == 503
    assert "could not be recorded" in response.json()["detail"]
