from __future__ import annotations

import pytest
from app.services.work_run_reconciliation import (
    CalendarActionNotFound,
    WorkEffectNotFound,
    WorkEffectReconciliationConflict,
    apply_reconciliation_to_calendar_event,
    reconcile_work_effect,
)
from app.services.work_run_store import WorkRunStore


def _uncertain_effect(store: WorkRunStore, *, status: str = "unknown") -> None:
    store.upsert(
        {
            "id": "receipt-1",
            "source": "calendar",
            "event_id": "event-1",
            "job_id": "event-1",
            "action_id": "action-1",
            "status": "interrupted_unknown",
            "phase": "tool",
            "effect_status": status,
            "effect_certainty": "unknown",
            "state_delta_certainty": "unknown",
            "reconcile_required": True,
            "tool_invoked": True,
        }
    )
    store.record_effect(
        {
            "id": "effect-1",
            "receipt_id": "receipt-1",
            "status": status,
            "certainty": "unknown",
            "reconcile_required": True,
            "redacted_target": "tool:create_task",
            "argument_digest": "sha256:" + "a" * 64,
            "prompt": "private prompt",
            "arguments": {"secret": "private"},
        },
        create_only=True,
    )


def _add_uncertain_sibling(store: WorkRunStore, *, effect_id: str = "effect-2") -> None:
    store.record_effect(
        {
            "id": effect_id,
            "receipt_id": "receipt-1",
            "status": "unknown",
            "certainty": "unknown",
            "reconcile_required": True,
            "redacted_target": "tool:update_task",
            "argument_digest": "sha256:" + "b" * 64,
        },
        create_only=True,
    )


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
def test_reconcile_effect_closes_receipt_without_replay(
    tmp_path, decision, status, certainty, state_certainty
):
    store = WorkRunStore(data_dir=tmp_path)
    _uncertain_effect(store)

    result = reconcile_work_effect(
        store,
        receipt_id="receipt-1",
        effect_id="effect-1",
        decision=decision,
        now=123.0,
    )

    assert result["tool_replayed"] is False
    assert result["idempotent"] is False
    assert result["effect"]["status"] == "confirmed"
    assert result["effect"]["certainty"] == certainty
    assert result["effect"]["reconciliation_decision"] == decision
    assert result["effect"]["reconciled_by"] == "local_user"
    assert result["effect"]["reconciled_at"] == 123.0
    assert result["receipt"]["status"] == status
    assert result["receipt"]["phase"] == "reconciled"
    assert result["receipt"]["state_delta_certainty"] == state_certainty
    assert result["receipt"]["reconcile_required"] is False
    assert result["receipt"]["recovery_state"] == "terminal"
    stored_effect = store.list_effects("receipt-1")[0]
    assert "prompt" not in stored_effect
    assert "arguments" not in stored_effect


def test_reconcile_effect_is_idempotent_and_rejects_conflicting_decision(tmp_path):
    store = WorkRunStore(data_dir=tmp_path)
    _uncertain_effect(store, status="dispatched")

    first = reconcile_work_effect(
        store,
        receipt_id="receipt-1",
        effect_id="effect-1",
        decision="confirm_applied",
        now=123.0,
    )
    repeated = reconcile_work_effect(
        store,
        receipt_id="receipt-1",
        effect_id="effect-1",
        decision="confirm_applied",
        now=124.0,
    )

    assert first["effect"]["transition_count"] == 2
    assert repeated["idempotent"] is True
    assert repeated["effect"]["transition_count"] == 2
    assert repeated["effect"]["reconciled_at"] == 123.0
    assert repeated["receipt"]["finished_at"] == 123.0
    assert (
        repeated["receipt"]["storage"]["updated_at"]
        == first["receipt"]["storage"]["updated_at"]
    )
    with pytest.raises(WorkEffectReconciliationConflict):
        reconcile_work_effect(
            store,
            receipt_id="receipt-1",
            effect_id="effect-1",
            decision="confirm_no_change",
        )


def test_reconcile_effect_waits_for_siblings_and_preserves_mixed_outcome(tmp_path):
    store = WorkRunStore(data_dir=tmp_path)
    _uncertain_effect(store)
    _add_uncertain_sibling(store)

    first = reconcile_work_effect(
        store,
        receipt_id="receipt-1",
        effect_id="effect-1",
        decision="confirm_applied",
        now=123.0,
    )

    assert first["aggregate"]["all_resolved"] is False
    assert first["aggregate"]["resolved_effect_count"] == 1
    assert first["aggregate"]["unresolved_effect_ids"] == ["effect-2"]
    assert first["receipt"]["status"] == "reconcile_required"
    assert first["receipt"]["phase"] == "reconciliation"
    assert first["receipt"]["state_delta_certainty"] == "confirmed_changed"
    assert first["receipt"]["reconciliation_outcome"] == "partially_applied"
    assert first["receipt"]["reconcile_required"] is True
    assert store.get("receipt-1")["recovery_state"] == "attention"

    final = reconcile_work_effect(
        store,
        receipt_id="receipt-1",
        effect_id="effect-2",
        decision="confirm_no_change",
        now=124.0,
    )
    repeated = reconcile_work_effect(
        store,
        receipt_id="receipt-1",
        effect_id="effect-2",
        decision="confirm_no_change",
        now=125.0,
    )

    assert final["aggregate"]["all_resolved"] is True
    assert final["aggregate"]["reconciliation_outcome"] == "mixed"
    assert final["receipt"]["status"] == "invoked"
    assert final["receipt"]["effect_certainty"] == "mixed_user_confirmed"
    assert final["receipt"]["state_delta_certainty"] == "confirmed_changed"
    assert final["receipt"]["reconcile_required"] is False
    assert final["receipt"]["finished_at"] == 124.0
    assert store.get("receipt-1")["effect_certainty"] == "mixed_user_confirmed"
    assert repeated["idempotent"] is True
    assert repeated["effect"]["reconciled_at"] == 124.0
    assert repeated["receipt"]["finished_at"] == 124.0
    assert (
        repeated["receipt"]["storage"]["updated_at"]
        == final["receipt"]["storage"]["updated_at"]
    )


def test_reconcile_effect_rejects_missing_safe_or_invalid_targets(tmp_path):
    store = WorkRunStore(data_dir=tmp_path)
    _uncertain_effect(store, status="intent")

    with pytest.raises(WorkEffectNotFound):
        reconcile_work_effect(
            store,
            receipt_id="missing",
            effect_id="effect-1",
            decision="confirm_applied",
        )
    with pytest.raises(WorkEffectNotFound):
        reconcile_work_effect(
            store,
            receipt_id="receipt-1",
            effect_id="missing",
            decision="confirm_applied",
        )
    with pytest.raises(WorkEffectReconciliationConflict):
        reconcile_work_effect(
            store,
            receipt_id="receipt-1",
            effect_id="effect-1",
            decision="confirm_applied",
        )
    with pytest.raises(ValueError):
        reconcile_work_effect(
            store,
            receipt_id="receipt-1",
            effect_id="effect-1",
            decision="retry_tool",
        )


@pytest.mark.parametrize(
    ("decision", "action_status", "certainty"),
    [
        ("confirm_applied", "invoked", "user_confirmed_applied"),
        ("confirm_no_change", "skipped", "user_confirmed_no_change"),
    ],
)
def test_calendar_projection_updates_exact_action_and_receipt(
    decision, action_status, certainty
):
    event = {
        "id": "event-1",
        "title": "Review",
        "status": "prompted",
        "rrule": "FREQ=DAILY",
        "actions": [
            {
                "id": "action-1",
                "status": "reconcile_required",
                "run_id": "run-1",
                "work_run_receipt_id": "receipt-1",
                "effect_id": "effect-1",
                "effect_status": "unknown",
                "effect_certainty": "unknown",
                "reconcile_required": True,
                "error": "uncertain",
                "interrupted_at": 100,
                "args": {"title": "Review"},
            },
            {"id": "action-2", "status": "scheduled"},
        ],
        "run_history": [
            {
                "id": "receipt-1",
                "status": "interrupted_unknown",
                "phase": "tool",
                "reconcile_required": True,
            },
            {"id": "other-receipt", "status": "complete"},
        ],
    }

    updated = apply_reconciliation_to_calendar_event(
        event,
        event_id="event-1",
        action_id="action-1",
        receipt_id="receipt-1",
        effect_id="effect-1",
        decision=decision,
        now=123,
    )

    action = updated["actions"][0]
    assert updated["status"] == "scheduled"
    assert action["status"] == action_status
    assert action["effect_status"] == "confirmed"
    assert action["effect_certainty"] == certainty
    assert action["reconcile_required"] is False
    assert action["executed_at"] == 123
    assert action["external_control_revision"] == 1
    assert action["args"] == {"title": "Review"}
    assert "error" not in action
    assert "interrupted_at" not in action
    assert updated["actions"][1] == event["actions"][1]
    receipt = updated["run_history"][0]
    assert receipt["status"] == action_status
    assert receipt["phase"] == "reconciled"
    assert receipt["effect_certainty"] == certainty
    assert receipt["reconcile_required"] is False
    assert updated["run_history"][1] == event["run_history"][1]
    assert event["actions"][0]["status"] == "reconcile_required"

    repeated = apply_reconciliation_to_calendar_event(
        updated,
        event_id="event-1",
        action_id="action-1",
        receipt_id="receipt-1",
        effect_id="effect-1",
        decision=decision,
        now=124,
    )
    assert repeated["actions"][0]["executed_at"] == 123
    assert repeated["actions"][0]["external_control_revision"] == 1
    assert repeated["run_history"][0]["finished_at"] == 123


def test_calendar_projection_rejects_wrong_action_or_stale_run_binding():
    event = {
        "id": "event-1",
        "actions": [
            {
                "id": "action-1",
                "work_run_receipt_id": "receipt-1",
                "effect_id": "effect-1",
                "effect_status": "unknown",
                "reconcile_required": True,
            }
        ],
    }
    with pytest.raises(CalendarActionNotFound):
        apply_reconciliation_to_calendar_event(
            event,
            event_id="event-1",
            action_id="missing",
            receipt_id="receipt-1",
            effect_id="effect-1",
            decision="confirm_applied",
        )
    with pytest.raises(CalendarActionNotFound):
        apply_reconciliation_to_calendar_event(
            event,
            event_id="event-1",
            action_id="action-1",
            receipt_id="receipt-1",
            effect_id="different-effect",
            decision="confirm_applied",
        )

    rearmed = {
        "id": "event-1",
        "actions": [{"id": "action-1", "status": "scheduled"}],
    }
    with pytest.raises(CalendarActionNotFound):
        apply_reconciliation_to_calendar_event(
            rearmed,
            event_id="event-1",
            action_id="action-1",
            receipt_id="receipt-1",
            effect_id="effect-1",
            decision="confirm_applied",
        )

    newer_run = {
        "id": "event-1",
        "actions": [
            {
                "id": "action-1",
                "work_run_receipt_id": "receipt-2",
                "effect_status": "unknown",
                "reconcile_required": True,
            }
        ],
    }
    with pytest.raises(CalendarActionNotFound):
        apply_reconciliation_to_calendar_event(
            newer_run,
            event_id="event-1",
            action_id="action-1",
            receipt_id="receipt-1",
            effect_id="effect-1",
            decision="confirm_applied",
        )


def test_calendar_projection_repairs_stale_terminal_sibling_from_ledger_aggregate():
    event = {
        "id": "event-1",
        "status": "prompted",
        "actions": [
            {
                "id": "action-1",
                "status": "skipped",
                "work_run_receipt_id": "receipt-1",
                "effect_id": "effect-1",
                "effect_status": "confirmed",
                "effect_certainty": "user_confirmed_no_change",
                "state_delta_certainty": "confirmed_no_change",
                "reconcile_required": False,
                "external_control_revision": 2,
            }
        ],
        "run_history": [{"id": "receipt-1", "status": "skipped"}],
    }
    aggregate = {
        "status": "invoked",
        "phase": "reconciled",
        "effect_status": "confirmed",
        "effect_certainty": "mixed_user_confirmed",
        "state_delta_certainty": "confirmed_changed",
        "reconcile_required": False,
        "reconciliation_outcome": "mixed",
        "summary": "Reconciliation confirmed one applied and one unchanged effect.",
        "all_resolved": True,
        "effect_ids": ["effect-1", "effect-2"],
        "reconciled_at": 124.0,
    }

    updated = apply_reconciliation_to_calendar_event(
        event,
        event_id="event-1",
        action_id="action-1",
        receipt_id="receipt-1",
        effect_id="effect-2",
        decision="confirm_no_change",
        aggregate=aggregate,
        now=999.0,
    )

    action = updated["actions"][0]
    assert action["status"] == "invoked"
    assert action["effect_ids"] == ["effect-1", "effect-2"]
    assert action["effect_certainty"] == "mixed_user_confirmed"
    assert action["state_delta_certainty"] == "confirmed_changed"
    assert action["executed_at"] == 124.0
    assert action["external_control_revision"] == 3
    assert updated["run_history"][0]["status"] == "invoked"
