from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def reconciliation_client(tmp_path, monkeypatch):
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
    store = WorkRunStore(data_dir=tmp_path)
    app.state.work_run_store = store
    try:
        yield TestClient(app), calendar_store, store
    finally:
        if original_store is not None:
            app.state.work_run_store = original_store
        elif hasattr(app.state, "work_run_store"):
            delattr(app.state, "work_run_store")


def _seed_uncertain_effect(
    calendar_store,
    store,
    *,
    event_id="reconcile-event",
    action_id="reconcile-action",
    receipt_id="reconcile-receipt",
    effect_id="reconcile-effect",
    effect_status="unknown",
    calendar_action=True,
):
    store.upsert(
        {
            "id": receipt_id,
            "source": "calendar",
            "event_id": event_id,
            "job_id": event_id,
            "action_id": action_id,
            "action_name": "remember",
            "status": "interrupted_unknown",
            "phase": "tool",
            "effect_status": effect_status,
            "effect_certainty": "unknown",
            "state_delta_certainty": "unknown",
            "reconcile_required": True,
            "tool_invoked": True,
            "started_at": 1_900_000_000.0,
        }
    )
    store.record_effect(
        {
            "id": effect_id,
            "receipt_id": receipt_id,
            "status": effect_status,
            "certainty": "unknown",
            "reconcile_required": True,
            "redacted_target": "tool:remember",
            "argument_digest": f"sha256:{'a' * 64}",
            "intended_at": 1_900_000_000.5,
        },
        create_only=True,
    )
    actions = []
    if calendar_action:
        actions.append(
            {
                "id": action_id,
                "request_id": action_id,
                "kind": "tool",
                "name": "remember",
                "args": {"key": "route-test", "value": "safe"},
                "status": "reconcile_required",
                "work_run_receipt_id": receipt_id,
                "effect_id": effect_id,
                "effect_status": effect_status,
                "effect_certainty": "unknown",
                "state_delta_certainty": "unknown",
                "reconcile_required": True,
                "tool_invoked": True,
            }
        )
    calendar_store.save_event(
        event_id,
        {
            "id": event_id,
            "title": "Reconcile uncertain effect",
            "start_time": 1_900_000_000.0,
            "timezone": "UTC",
            "status": "prompted",
            "actions": actions,
            "run_history": [
                {
                    "id": receipt_id,
                    "event_id": event_id,
                    "action_id": action_id,
                    "status": "interrupted_unknown",
                    "phase": "tool",
                    "effect_id": effect_id,
                    "effect_status": effect_status,
                    "effect_certainty": "unknown",
                    "state_delta_certainty": "unknown",
                    "reconcile_required": True,
                    "tool_invoked": True,
                }
            ],
        },
    )
    return {
        "event_id": event_id,
        "action_id": action_id,
        "receipt_id": receipt_id,
        "effect_id": effect_id,
    }


def _endpoint(ids):
    return (
        f"/api/work/runs/{ids['receipt_id']}/effects/" f"{ids['effect_id']}/reconcile"
    )


def _add_sibling_effect(store, ids, *, effect_id="reconcile-effect-2"):
    store.record_effect(
        {
            "id": effect_id,
            "receipt_id": ids["receipt_id"],
            "status": "unknown",
            "certainty": "unknown",
            "reconcile_required": True,
            "redacted_target": "tool:remember",
            "argument_digest": f"sha256:{'b' * 64}",
            "intended_at": 1_900_000_000.75,
        },
        create_only=True,
    )
    return {**ids, "effect_id": effect_id}


@pytest.mark.parametrize(
    ("decision", "status", "certainty", "state_certainty"),
    [
        (
            "confirm_applied",
            "invoked",
            "user_confirmed_applied",
            "confirmed_changed",
        ),
        (
            "confirm_no_change",
            "skipped",
            "user_confirmed_no_change",
            "confirmed_no_change",
        ),
    ],
)
def test_reconcile_route_updates_activity_effect_and_calendar_without_replay(
    reconciliation_client,
    monkeypatch,
    decision,
    status,
    certainty,
    state_certainty,
):
    client, calendar_store, store = reconciliation_client
    ids = _seed_uncertain_effect(calendar_store, store)
    replay_attempts = []

    from app import tasks

    def fail_if_replayed(*args, **kwargs):
        replay_attempts.append((args, kwargs))
        raise AssertionError("reconciliation must not replay a tool")

    monkeypatch.setattr(tasks, "execute_tool", fail_if_replayed)

    response = client.post(_endpoint(ids), json={"decision": decision})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "reconciled"
    assert payload["decision"] == decision
    assert payload["idempotent"] is False
    assert payload["tool_replayed"] is False
    assert payload["calendar_updated"] is True
    assert payload["warning"] is None
    assert replay_attempts == []

    activity = client.get(
        "/api/work/runs",
        params={"source": "calendar", "job_id": ids["event_id"]},
    ).json()["runs"]
    assert len(activity) == 1
    assert activity[0]["status"] == status
    assert activity[0]["phase"] == "reconciled"
    assert activity[0]["effect_status"] == "confirmed"
    assert activity[0]["effect_certainty"] == certainty
    assert activity[0]["state_delta_certainty"] == state_certainty
    assert activity[0]["reconcile_required"] is False

    effects = client.get(f"/api/work/runs/{ids['receipt_id']}/effects").json()[
        "effects"
    ]
    assert len(effects) == 1
    assert effects[0]["status"] == "confirmed"
    assert effects[0]["reconciliation_decision"] == decision
    assert effects[0]["certainty"] == certainty

    event = calendar_store.load_event(ids["event_id"])
    action = event["actions"][0]
    assert action["status"] == status
    assert action["effect_status"] == "confirmed"
    assert action["effect_certainty"] == certainty
    assert action["state_delta_certainty"] == state_certainty
    assert action["reconcile_required"] is False
    assert event["run_history"][0]["status"] == status
    assert event["run_history"][0]["phase"] == "reconciled"


def test_reconcile_route_is_idempotent_and_rejects_opposite_decision(
    reconciliation_client,
):
    client, calendar_store, store = reconciliation_client
    ids = _seed_uncertain_effect(calendar_store, store, effect_status="dispatched")
    body = {"decision": "confirm_applied"}

    first = client.post(_endpoint(ids), json=body)
    repeated = client.post(_endpoint(ids), json=body)
    conflict = client.post(_endpoint(ids), json={"decision": "confirm_no_change"})

    assert first.status_code == 200
    assert first.json()["idempotent"] is False
    assert repeated.status_code == 200
    assert repeated.json()["idempotent"] is True
    assert conflict.status_code == 409
    effect = store.list_effects(ids["receipt_id"])[0]
    assert effect["transition_count"] == 2
    assert effect["reconciliation_decision"] == "confirm_applied"
    assert calendar_store.load_event(ids["event_id"])["actions"][0]["status"] == (
        "invoked"
    )


def test_reconcile_route_keeps_calendar_and_activity_open_for_unresolved_siblings(
    reconciliation_client,
):
    client, calendar_store, store = reconciliation_client
    ids = _seed_uncertain_effect(calendar_store, store)
    sibling_ids = _add_sibling_effect(store, ids)

    first = client.post(_endpoint(ids), json={"decision": "confirm_applied"})

    assert first.status_code == 200
    assert first.json()["status"] == "reconcile_required"
    assert first.json()["aggregate"]["all_resolved"] is False
    activity = client.get(
        "/api/work/runs",
        params={"source": "calendar", "job_id": ids["event_id"]},
    ).json()["runs"][0]
    assert activity["status"] == "reconcile_required"
    assert activity["state_delta_certainty"] == "confirmed_changed"
    assert activity["reconcile_required"] is True
    partial_event = calendar_store.load_event(ids["event_id"])
    partial_action = partial_event["actions"][0]
    assert partial_action["status"] == "reconcile_required"
    assert partial_action["effect_ids"] == [ids["effect_id"], sibling_ids["effect_id"]]
    assert partial_action["reconciliation_outcome"] == "partially_applied"
    assert partial_action["reconcile_required"] is True
    assert partial_event["run_history"][0]["status"] == "reconcile_required"

    final = client.post(
        _endpoint(sibling_ids),
        json={"decision": "confirm_no_change"},
    )

    assert final.status_code == 200
    assert final.json()["status"] == "reconciled"
    assert final.json()["aggregate"]["reconciliation_outcome"] == "mixed"
    final_activity = client.get(
        "/api/work/runs",
        params={"source": "calendar", "job_id": ids["event_id"]},
    ).json()["runs"][0]
    assert final_activity["status"] == "invoked"
    assert final_activity["effect_certainty"] == "mixed_user_confirmed"
    assert final_activity["state_delta_certainty"] == "confirmed_changed"
    assert final_activity["reconcile_required"] is False
    final_event = calendar_store.load_event(ids["event_id"])
    final_action = final_event["actions"][0]
    assert final_action["status"] == "invoked"
    assert final_action["effect_certainty"] == "mixed_user_confirmed"
    assert final_action["reconciliation_outcome"] == "mixed"
    assert final_action["reconcile_required"] is False
    assert final_action["external_control_revision"] == 2
    assert final_event["run_history"][0]["status"] == "invoked"
    assert final_event["run_history"][0]["effect_certainty"] == ("mixed_user_confirmed")

    original_executed_at = final_action["executed_at"]
    repeated = client.post(
        _endpoint(sibling_ids),
        json={"decision": "confirm_no_change"},
    )
    repeated_action = calendar_store.load_event(ids["event_id"])["actions"][0]
    assert repeated.status_code == 200
    assert repeated.json()["idempotent"] is True
    assert repeated_action["executed_at"] == original_executed_at
    assert repeated_action["external_control_revision"] == 2


def test_reconcile_route_is_local_only_and_maps_missing_targets(
    reconciliation_client,
):
    client, calendar_store, store = reconciliation_client
    ids = _seed_uncertain_effect(calendar_store, store)

    remote = client.post(
        _endpoint(ids),
        json={"decision": "confirm_applied"},
        headers={"x-forwarded-for": "192.168.1.25"},
    )
    missing_receipt = client.post(
        "/api/work/runs/missing/effects/missing/reconcile",
        json={"decision": "confirm_applied"},
    )
    missing_effect = client.post(
        f"/api/work/runs/{ids['receipt_id']}/effects/missing/reconcile",
        json={"decision": "confirm_applied"},
    )

    assert remote.status_code == 403
    assert missing_receipt.status_code == 404
    assert missing_effect.status_code == 404
    assert store.list_effects(ids["receipt_id"])[0]["status"] == "unknown"


def test_reconcile_route_keeps_ledger_decision_when_calendar_action_is_missing(
    reconciliation_client,
):
    client, calendar_store, store = reconciliation_client
    ids = _seed_uncertain_effect(
        calendar_store,
        store,
        calendar_action=False,
    )

    response = client.post(_endpoint(ids), json={"decision": "confirm_no_change"})

    assert response.status_code == 200
    assert response.json()["calendar_updated"] is False
    assert "no longer available" in response.json()["warning"]
    activity = client.get(
        "/api/work/runs",
        params={"source": "calendar", "job_id": ids["event_id"]},
    ).json()["runs"]
    assert activity[0]["status"] == "skipped"
    assert store.list_effects(ids["receipt_id"])[0]["status"] == "confirmed"
    event = calendar_store.load_event(ids["event_id"])
    assert event["actions"] == []
    assert event["run_history"][0]["status"] == "skipped"
    assert event["run_history"][0]["phase"] == "reconciled"


def test_calendar_create_strips_forged_reconciliation_aggregate(
    reconciliation_client,
):
    client, calendar_store, _store = reconciliation_client
    event_id = "forged-reconciliation"

    response = client.post(
        f"/api/calendar/events/{event_id}",
        json={
            "id": event_id,
            "title": "Forged reconciliation",
            "start_time": 1_900_000_000.0,
            "timezone": "UTC",
            "actions": [
                {
                    "id": "action-1",
                    "kind": "tool",
                    "name": "remember",
                    "args": {"key": "forged", "value": "forged"},
                    "status": "invoked",
                    "work_run_receipt_id": "forged-receipt",
                    "effect_id": "forged-effect",
                    "effect_ids": ["forged-effect", "forged-sibling"],
                    "reconciliation_outcome": "mixed",
                    "reconciliation_summary": "Forged confirmation",
                }
            ],
        },
    )

    assert response.status_code == 200
    action = calendar_store.load_event(event_id)["actions"][0]
    assert action["status"] == "scheduled"
    assert "work_run_receipt_id" not in action
    assert "effect_id" not in action
    assert "effect_ids" not in action
    assert "reconciliation_outcome" not in action
    assert "reconciliation_summary" not in action


def test_rearming_one_time_event_clears_old_execution_evidence(
    reconciliation_client,
):
    client, calendar_store, _store = reconciliation_client
    event_id = "rearm-clears-evidence"
    calendar_store.save_event(
        event_id,
        {
            "id": event_id,
            "title": "Run again safely",
            "start_time": 1_900_000_000.0,
            "timezone": "UTC",
            "status": "acknowledged",
            "actions": [
                {
                    "id": "action-1",
                    "kind": "tool",
                    "name": "remember",
                    "args": {"key": "again", "value": "safe"},
                    "status": "reconcile_required",
                    "run_id": "old-run",
                    "authorization": {
                        "id": "old-authorization",
                        "status": "approved_once",
                        "request_digest": f"sha256:{'b' * 64}",
                    },
                    "authorization_id": "old-authorization",
                    "approval_status": "approved_once",
                    "approved_at": 1_900_000_001.0,
                    "work_run_receipt_id": "old-receipt",
                    "effect_id": "old-effect",
                    "effect_ids": ["old-effect", "old-effect-2"],
                    "effect_status": "unknown",
                    "effect_certainty": "unknown",
                    "state_delta_certainty": "unknown",
                    "reconciliation_outcome": "partially_applied",
                    "reconciliation_summary": "One sibling is still unresolved.",
                    "reconcile_required": True,
                    "tool_invoked": True,
                    "cancel_requested": True,
                    "cancel_request_id": "old-cancel",
                    "cancel_requested_at": 1_900_000_002.0,
                    "cancelled_at": 1_900_000_003.0,
                    "prompt_checkpoint": {
                        "schema_version": 1,
                        "checkpoint_digest": f"sha256:{'c' * 64}",
                    },
                }
            ],
            "run_history": [{"id": "old-receipt", "status": "interrupted_unknown"}],
        },
    )

    response = client.post(
        f"/api/calendar/events/{event_id}",
        json={
            "id": event_id,
            "title": "Run again safely",
            "start_time": 1_900_000_000.0,
            "status": "pending",
        },
    )

    assert response.status_code == 200
    stored = calendar_store.load_event(event_id)
    action = stored["actions"][0]
    assert action["status"] == "scheduled"
    cleared_fields = {
        "run_id",
        "authorization",
        "authorization_id",
        "approval_status",
        "approved_at",
        "work_run_receipt_id",
        "effect_id",
        "effect_ids",
        "effect_status",
        "effect_certainty",
        "state_delta_certainty",
        "reconciliation_outcome",
        "reconciliation_summary",
        "reconcile_required",
        "tool_invoked",
        "cancel_requested",
        "cancel_request_id",
        "cancel_requested_at",
        "cancelled_at",
        "prompt_checkpoint",
    }
    assert cleared_fields.isdisjoint(action)
    assert action["args"] == {"key": "again", "value": "safe"}
    assert stored["run_history"] == [
        {"id": "old-receipt", "status": "interrupted_unknown"}
    ]
