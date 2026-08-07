from __future__ import annotations

import pytest
from app.services.scheduled_action_cancellation import (
    ScheduledActionCancellationConflict,
    ScheduledActionCancellationNotFound,
    cancellation_requested,
    request_scheduled_action_cancellation,
)


@pytest.fixture
def isolated_calendar(tmp_path, monkeypatch):
    from app.utils import calendar_store

    root = tmp_path / "calendar"
    root.mkdir()
    monkeypatch.setattr(calendar_store, "EVENTS_DIR", root)
    return calendar_store


def _event(*, status="running", recurring=False):
    event = {
        "id": "event-1",
        "title": "Background review",
        "status": "running" if status == "running" else "scheduled",
        "actions": [
            {
                "id": "action-1",
                "status": status,
                "run_id": "run-1",
                "running_occurrence_at": 100.0,
                "work_run_receipt_id": "receipt-1",
            }
        ],
        "run_history": [
            {
                "id": "receipt-1",
                "run_id": "run-1",
                "action_id": "action-1",
                "status": status,
                "phase": "tool",
            }
        ],
    }
    if recurring:
        event["rrule"] = "FREQ=DAILY"
    return event


def test_running_action_records_request_without_claiming_termination(isolated_calendar):
    isolated_calendar.save_event("event-1", _event())

    result = request_scheduled_action_cancellation(
        "event-1",
        "action-1",
        expected_run_id="run-1",
        requested_at=123,
    )

    assert result["status"] == "cancel_requested"
    assert result["termination_confirmed"] is False
    stored = isolated_calendar.load_event("event-1")
    action = stored["actions"][0]
    assert action["status"] == "running"
    assert action["cancel_requested"] is True
    assert cancellation_requested(action, expected_run_id="run-1") is True
    assert cancellation_requested(action, expected_run_id="another-run") is False
    receipt = stored["run_history"][0]
    assert receipt["status"] == "cancel_requested"
    assert receipt["phase"] == "cancellation"
    assert "finished_at" not in receipt

    repeated = request_scheduled_action_cancellation(
        "event-1",
        "action-1",
        expected_run_id="run-1",
        requested_at=124,
    )
    assert repeated["idempotent"] is True
    assert repeated["termination_confirmed"] is False


def test_pending_action_cancels_before_dispatch_and_advances_only_occurrence(
    isolated_calendar,
):
    event = _event(status="authorization_required", recurring=True)
    event["actions"][0]["authorization"] = {
        "id": "auth-1",
        "status": "authorization_required",
        "occurrence_at": 100,
        "can_approve": True,
    }
    isolated_calendar.save_event("event-1", event)

    result = request_scheduled_action_cancellation(
        "event-1", "action-1", expected_run_id="run-1", requested_at=123
    )

    assert result["status"] == "cancelled"
    assert result["termination_confirmed"] is True
    assert result["tool_invoked"] is False
    stored = isolated_calendar.load_event("event-1")
    action = stored["actions"][0]
    assert stored["status"] == "scheduled"
    assert action["status"] == "cancelled"
    assert action["last_occurrence_at"] == 100
    assert action["authorization"]["status"] == "invalidated"
    assert action["authorization"]["invalidation_reason"] == "user_cancelled"
    receipt = stored["run_history"][0]
    assert receipt["status"] == "cancelled"
    assert receipt["tool_invoked"] is False
    assert receipt["state_delta_certainty"] == "confirmed_no_change"
    assert receipt["finished_at"] == 123


@pytest.mark.parametrize(
    ("action_evidence", "receipt_evidence"),
    [
        ({"effect_status": "dispatched"}, {}),
        ({"effect_status": "unknown"}, {}),
        ({"effect_id": "effect-1"}, {}),
        ({"tool_invoked": True}, {}),
        ({"reconcile_required": True}, {}),
        ({}, {"tool_invoked": True}),
    ],
)
def test_stale_predispatch_status_with_dispatch_evidence_requires_reconciliation(
    isolated_calendar,
    action_evidence,
    receipt_evidence,
):
    event = _event(status="authorization_required")
    event["actions"][0].update(action_evidence)
    event["run_history"][0].update(receipt_evidence)
    isolated_calendar.save_event("event-1", event)

    result = request_scheduled_action_cancellation(
        "event-1", "action-1", expected_run_id="run-1", requested_at=123
    )

    assert result["status"] == "reconcile_required"
    assert result["termination_confirmed"] is False
    assert result["reconcile_required"] is True
    stored = isolated_calendar.load_event("event-1")
    action = stored["actions"][0]
    assert stored["status"] == "prompted"
    assert action["status"] == "reconcile_required"
    assert action["cancel_requested"] is True
    assert action["reconcile_required"] is True
    assert action["last_occurrence_at"] == 100
    assert "cancelled_at" not in action
    for key, value in action_evidence.items():
        if key != "reconcile_required":
            assert action[key] == value
    receipt = stored["run_history"][0]
    assert receipt["status"] == "reconcile_required"
    assert receipt["phase"] == "cancellation"
    assert receipt["state_delta_certainty"] == "unknown"
    assert receipt["state_delta_certainty"] != "confirmed_no_change"
    assert receipt["reconcile_required"] is True
    assert receipt["finished_at"] == 123

    repeated = request_scheduled_action_cancellation(
        "event-1", "action-1", expected_run_id="run-1", requested_at=124
    )
    assert repeated["status"] == "reconcile_required"
    assert repeated["termination_confirmed"] is False
    assert repeated["reconcile_required"] is True
    assert repeated["idempotent"] is True


def test_safe_completed_tool_waiting_for_followup_records_request_not_no_change(
    isolated_calendar,
):
    event = _event(status="followup_pending")
    action = event["actions"][0]
    action.update(
        {
            "effect_id": "effect-safe",
            "effect_status": "acknowledged",
            "effect_certainty": "reported_success",
            "tool_invoked": True,
        }
    )
    receipt = event["run_history"][0]
    receipt.update(
        {
            "effect_status": "acknowledged",
            "effect_certainty": "reported_success",
            "tool_invoked": True,
            "state_delta_certainty": "reported_success",
        }
    )
    isolated_calendar.save_event("event-1", event)

    result = request_scheduled_action_cancellation(
        "event-1", "action-1", expected_run_id="run-1", requested_at=123
    )

    assert result["status"] == "cancel_requested"
    assert result["termination_confirmed"] is False
    assert result["tool_invoked"] is True
    stored = isolated_calendar.load_event("event-1")
    action = stored["actions"][0]
    assert action["status"] == "followup_pending"
    assert action["cancel_requested"] is True
    assert action["effect_id"] == "effect-safe"
    assert action["effect_status"] == "acknowledged"
    assert action["effect_certainty"] == "reported_success"
    assert "cancelled_at" not in action
    receipt = stored["run_history"][0]
    assert receipt["status"] == "cancel_requested"
    assert receipt["tool_invoked"] is True
    assert receipt["effect_status"] == "acknowledged"
    assert receipt["effect_certainty"] == "reported_success"
    assert receipt["state_delta_certainty"] == "reported_success"
    assert "finished_at" not in receipt


def test_cancellation_rejects_wrong_run_finished_or_missing_target(isolated_calendar):
    isolated_calendar.save_event("event-1", _event())
    with pytest.raises(ScheduledActionCancellationConflict, match="no longer active"):
        request_scheduled_action_cancellation(
            "event-1", "action-1", expected_run_id="wrong-run"
        )

    finished = _event(status="invoked")
    isolated_calendar.save_event("event-1", finished)
    with pytest.raises(ScheduledActionCancellationConflict, match="already invoked"):
        request_scheduled_action_cancellation("event-1", "action-1")
    with pytest.raises(ScheduledActionCancellationNotFound):
        request_scheduled_action_cancellation("event-1", "missing")
    with pytest.raises(ScheduledActionCancellationNotFound):
        request_scheduled_action_cancellation("missing", "action-1")
