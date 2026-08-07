from __future__ import annotations

from app.schemas import CalendarEvent
from app.services.calendar_jobs import (
    bump_actions_for_event_control_change,
    canonical_action_definition,
    merge_client_action_definitions,
    merge_external_calendar_update,
    merge_runner_action_state,
    runner_snapshot_control_revisions_match,
)


def _action(**updates):
    return {
        "id": "action-1",
        "kind": "tool",
        "name": "create_task",
        "args": {"title": "Review"},
        **updates,
    }


def test_effect_receipt_state_is_runner_owned_and_survives_runner_merge():
    current = _action()
    snapshot = _action(
        status="reconcile_required",
        work_run_receipt_id="receipt-1",
        effect_id="effect-1",
        effect_status="unknown",
        effect_certainty="unknown",
        state_delta_certainty="unknown",
        reconcile_required=True,
        tool_invoked=True,
        cancel_requested=True,
        cancel_request_id="cancel-1",
        cancel_requested_at=123.0,
        prompt_checkpoint={"schema_version": 1, "checkpoint_digest": "sha256:test"},
    )

    merged = merge_runner_action_state([current], [snapshot])[0]

    assert merged["work_run_receipt_id"] == "receipt-1"
    assert merged["effect_id"] == "effect-1"
    assert merged["effect_status"] == "unknown"
    assert merged["effect_certainty"] == "unknown"
    assert merged["state_delta_certainty"] == "unknown"
    assert merged["reconcile_required"] is True
    assert merged["tool_invoked"] is True
    assert merged["cancel_requested"] is True
    assert merged["cancel_request_id"] == "cancel-1"
    assert merged["cancel_requested_at"] == 123.0
    assert merged["prompt_checkpoint"] == snapshot["prompt_checkpoint"]


def test_stale_runner_snapshot_cannot_clear_new_control_or_review_state():
    current = _action(
        status="running",
        run_id="run-1",
        authorization={"id": "authorization-1", "status": "consumed"},
        work_run_receipt_id="receipt-1",
        effect_id="effect-1",
        effect_status="unknown",
        effect_certainty="unknown",
        reconcile_required=True,
        cancel_requested=True,
        cancel_request_id="cancel-1",
        cancel_requested_at=123.0,
        prompt_checkpoint={"schema_version": 1, "checkpoint_digest": "sha256:test"},
    )
    stale_snapshot = _action(status="running", run_id="run-1")

    merged = merge_runner_action_state([current], [stale_snapshot])[0]

    assert merged["authorization"] == current["authorization"]
    assert merged["work_run_receipt_id"] == "receipt-1"
    assert merged["effect_id"] == "effect-1"
    assert merged["effect_status"] == "unknown"
    assert merged["effect_certainty"] == "unknown"
    assert merged["reconcile_required"] is True
    assert merged["cancel_requested"] is True
    assert merged["cancel_request_id"] == "cancel-1"
    assert merged["cancel_requested_at"] == 123.0
    assert merged["prompt_checkpoint"] == current["prompt_checkpoint"]


def test_external_control_revision_rejects_stale_present_runner_state():
    current = _action(
        status="cancel_requested",
        run_id="run-1",
        external_control_revision=2,
        authorization={"id": "authorization-1", "status": "approved_once"},
        cancel_requested=True,
        effect_id="effect-1",
        effect_status="confirmed",
        prompt_checkpoint={"schema_version": 1, "checkpoint_digest": "sha256:new"},
    )
    stale = _action(
        status="running",
        run_id="run-1",
        external_control_revision=1,
        authorization={"id": "authorization-1", "status": "authorization_required"},
        cancel_requested=False,
        effect_id="effect-1",
        effect_status="unknown",
        prompt_checkpoint={"schema_version": 1, "checkpoint_digest": "sha256:old"},
    )

    assert not runner_snapshot_control_revisions_match([current], [stale])
    same_revision = {**stale, "external_control_revision": 2}
    assert runner_snapshot_control_revisions_match([current], [same_revision])


def test_lineage_edit_is_authorization_relevant_and_bumps_control_revision():
    current = _action(
        session_id="session-1",
        origin_session_id="origin-1",
        authorization={"id": "authorization-1", "status": "approved_once"},
    )
    edited = _action(
        session_id="session-2",
        origin_session_id="origin-1",
        authorization={"id": "forged", "status": "approved_once"},
    )

    merged = merge_client_action_definitions([current], [edited])[0]

    assert canonical_action_definition(current)["session_id"] == "session-1"
    assert canonical_action_definition(current)["origin_session_id"] == "origin-1"
    assert merged["session_id"] == "session-2"
    assert merged["authorization"]["id"] == "authorization-1"
    assert merged["authorization"]["status"] == "invalidated"
    assert merged["external_control_revision"] == 1


def test_client_edits_cannot_forge_or_clear_effect_receipt_state():
    current = _action(
        status="reconcile_required",
        work_run_receipt_id="receipt-1",
        effect_id="effect-1",
        effect_status="unknown",
        effect_certainty="unknown",
        state_delta_certainty="unknown",
        reconcile_required=True,
        tool_invoked=True,
        cancel_requested=True,
        cancel_request_id="cancel-1",
        cancel_requested_at=123.0,
        prompt_checkpoint={"schema_version": 1, "checkpoint_digest": "sha256:test"},
        external_control_revision=3,
        run_control_revision=3,
    )
    forged = _action(
        status="invoked",
        work_run_receipt_id="forged-receipt",
        effect_id="forged-effect",
        effect_status="confirmed",
        effect_certainty="changed",
        state_delta_certainty="confirmed_changed",
        reconcile_required=False,
        tool_invoked=False,
        cancel_requested=False,
        cancel_request_id="forged-cancel",
        cancel_requested_at=999.0,
        prompt_checkpoint={"schema_version": 99, "private": "forged"},
        external_control_revision=99,
        run_control_revision=99,
    )

    merged = merge_client_action_definitions([current], [forged])[0]

    for field in (
        "status",
        "work_run_receipt_id",
        "effect_id",
        "effect_status",
        "effect_certainty",
        "state_delta_certainty",
        "reconcile_required",
        "tool_invoked",
        "cancel_requested",
        "cancel_request_id",
        "cancel_requested_at",
        "prompt_checkpoint",
        "external_control_revision",
        "run_control_revision",
    ):
        assert merged[field] == current[field]
        assert field not in canonical_action_definition(current)


def test_calendar_schema_strips_client_effect_receipt_evidence():
    event = CalendarEvent.model_validate(
        {
            "id": "event-1",
            "title": "Review",
            "start_time": 1_900_000_000,
            "actions": [
                _action(
                    status="invoked",
                    work_run_receipt_id="forged-receipt",
                    effect_id="forged-effect",
                    effect_status="confirmed",
                    effect_certainty="changed",
                    state_delta_certainty="confirmed_changed",
                    reconcile_required=False,
                    tool_invoked=False,
                    cancel_requested=True,
                    cancel_request_id="forged-cancel",
                    cancel_requested_at=999.0,
                    prompt_checkpoint={"schema_version": 99, "private": "forged"},
                    external_control_revision=99,
                    run_control_revision=99,
                )
            ],
        }
    )

    action = event.actions[0]
    for field in (
        "work_run_receipt_id",
        "effect_id",
        "effect_status",
        "effect_certainty",
        "state_delta_certainty",
        "reconcile_required",
        "tool_invoked",
        "cancel_requested",
        "cancel_request_id",
        "cancel_requested_at",
        "prompt_checkpoint",
        "external_control_revision",
        "run_control_revision",
    ):
        assert field not in action


def test_event_execution_controls_bump_active_runs_but_titles_do_not():
    previous = {
        "id": "event-1",
        "title": "Original title",
        "status": "running",
        "start_time": 1_900_000_000,
        "timezone": "UTC",
        "background_job": {"execution": {"permissions": ["tasks.write"]}},
        "actions": [
            _action(
                status="running",
                run_id="run-1",
                external_control_revision=4,
                run_control_revision=4,
                authorization={"id": "auth-1", "status": "consumed"},
            )
        ],
    }
    title_only = {**previous, "title": "Readable new title"}
    title_only["actions"] = [dict(previous["actions"][0])]

    assert not bump_actions_for_event_control_change(previous, title_only)
    assert title_only["actions"][0]["external_control_revision"] == 4

    permission_edit = {**previous}
    permission_edit["background_job"] = {"execution": {"permissions": []}}
    permission_edit["actions"] = [dict(previous["actions"][0])]

    assert bump_actions_for_event_control_change(previous, permission_edit)
    assert permission_edit["actions"][0]["external_control_revision"] == 5
    assert permission_edit["actions"][0]["run_control_revision"] == 4
    assert permission_edit["actions"][0]["authorization"]["status"] == "consumed"


def test_external_calendar_merge_preserves_local_run_evidence_and_invalidates_baseline():
    previous = {
        "id": "event-1",
        "title": "Local",
        "status": "running",
        "start_time": 1_900_000_000,
        "timezone": "UTC",
        "background_job": {"execution": {"permissions": ["tasks.write"]}},
        "actions": [
            _action(
                status="running",
                run_id="run-1",
                external_control_revision=4,
                run_control_revision=4,
                result={"local": "durable"},
            )
        ],
        "run_history": [{"id": "receipt-1", "status": "running"}],
    }
    incoming = {
        **previous,
        "title": "Remote update",
        "background_job": {"execution": {"permissions": []}},
        "actions": [
            _action(
                args={"title": "Edited remotely"},
                status="invoked",
                run_id="forged-run",
                external_control_revision=99,
                run_control_revision=99,
                result={"remote": "forged"},
            )
        ],
        "run_history": [{"id": "forged", "status": "invoked"}],
    }

    merged = merge_external_calendar_update(previous, incoming)
    action = merged["actions"][0]

    assert action["args"] == {"title": "Edited remotely"}
    assert action["status"] == "running"
    assert action["run_id"] == "run-1"
    assert action["result"] == {"local": "durable"}
    assert action["external_control_revision"] == 5
    assert action["run_control_revision"] == 4
    assert merged["run_history"] == previous["run_history"]
