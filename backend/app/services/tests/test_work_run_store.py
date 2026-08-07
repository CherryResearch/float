import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from app.services.work_run_store import (
    WorkRunStore,
    WorkRunTransitionConflict,
    work_run_store_path,
)


def _receipt(receipt_id: str, *, status: str = "complete", started_at: float = 10):
    return {
        "id": receipt_id,
        "run_id": f"run-{receipt_id}",
        "job_id": "nightly-review",
        "event_id": "calendar-nightly-review",
        "event_title": "Nightly review",
        "action_id": "action-1",
        "action_kind": "prompt",
        "action_name": "prompt",
        "occurrence_at": started_at,
        "occurrence_id": f"calendar-nightly-review:{int(started_at)}",
        "started_at": started_at,
        "finished_at": started_at + 5 if status == "complete" else None,
        "status": status,
        "summary": "Reviewed the compact work queue.",
        "ownership": {
            "owner_kind": "conversation",
            "calendar_event_id": "calendar-nightly-review",
            "conversation_id": "session-123",
            "message_id": "message-456",
            "parent_job_id": "parent-job",
            "parent_agent_id": "parent-agent",
        },
        "patience": {
            "stop_condition": "until_useful",
            "max_attempts": 2,
            "max_runtime_seconds": 900,
            "max_provider_retries": 2,
            "satisfied_threshold": 0.8,
        },
        "execution": {
            "reasoning_effort": "high",
            "model": "inherit",
            "workflow": "continuous_review",
            "allow_subagents": True,
            "sandbox_processes": True,
            "permissions": ["calendar.read"],
        },
        "run_conversation_id": "run-session-789",
    }


def _digest(character: str) -> str:
    return f"sha256:{character * 64}"


def test_default_path_uses_configured_data_directory(tmp_path, monkeypatch):
    data_root = tmp_path / "device-data"
    monkeypatch.setenv("FLOAT_DATA_DIR", str(data_root))

    assert (
        work_run_store_path()
        == (data_root / "databases" / "work_runs.sqlite3").resolve()
    )


def test_upsert_is_idempotent_and_merges_running_receipt_into_final(tmp_path):
    clock = iter((100.0, 101.0, 102.0))
    store = WorkRunStore(data_dir=tmp_path, now_fn=lambda: next(clock))
    running = _receipt("receipt-1", status="running")
    running.pop("finished_at")

    stored = store.upsert_run(running, source="calendar")
    updated = store.upsert_run(
        {
            "id": "receipt-1",
            "status": "complete",
            "finished_at": 20,
            "summary": "Finished cleanly.",
        }
    )

    assert stored["status"] == "running"
    assert updated["status"] == "complete"
    assert updated["job_id"] == "nightly-review"
    assert updated["ownership"]["parent_agent_id"] == "parent-agent"
    assert updated["source"] == "calendar"
    assert updated["event_count"] == 2
    assert store.count_runs() == 1
    assert store.get_run("receipt-1") == updated

    # Retrying an identical final snapshot updates storage metadata but does not
    # manufacture another lifecycle transition.
    store.upsert_run(
        {
            "id": "receipt-1",
            "status": "complete",
            "finished_at": 20,
            "summary": "Finished cleanly.",
        }
    )
    assert store.count_events("receipt-1") == 2


def test_receipt_payload_is_allowlisted_and_bounded(tmp_path):
    store = WorkRunStore(data_dir=tmp_path)
    record = _receipt("receipt-private")
    record.update(
        {
            "summary": "x" * 2_000,
            "conversation_body": "private transcript",
            "prompt": "private prompt",
            "tool_arguments": {"secret": "value"},
            "ownership": {
                **record["ownership"],
                "conversation_body": "also private",
            },
            "patience": {
                **record["patience"],
                "private_notes": "do not store",
            },
            "execution": {
                **record["execution"],
                "raw_provider_result": "do not store",
                "permissions": [f"permission-{index}" for index in range(40)],
            },
        }
    )

    stored = store.upsert_run(record)

    assert len(stored["summary"]) == 1_200
    assert "conversation_body" not in stored
    assert "prompt" not in stored
    assert "tool_arguments" not in stored
    assert "conversation_body" not in stored["ownership"]
    assert "private_notes" not in stored["patience"]
    assert "raw_provider_result" not in stored["execution"]
    assert len(stored["execution"]["permissions"]) == 32


def test_authorization_receipt_keeps_review_metadata_without_private_content(tmp_path):
    store = WorkRunStore(data_dir=tmp_path)
    record = _receipt("receipt-authorization", status="authorization_required")
    record["authorization"] = {
        "schema_version": 1,
        "id": "authorization-1",
        "status": "authorization_required",
        "occurrence_at": 10,
        "request_digest": _digest("a"),
        "action_definition_digest": _digest("b"),
        "policy_id": "scheduled-tool-auth:v1",
        "policy_digest": _digest("c"),
        "required_scopes": ["memory.write"],
        "configured_scopes": [],
        "missing_scopes": ["memory.write"],
        "approval_required": True,
        "can_approve": False,
        "requested_at": 11,
        "prompt": "private prompt",
        "arguments": {"private": "value"},
        "unknown_future_field": "private",
    }

    stored = store.upsert_run(record)

    assert stored["recovery_state"] == "attention"
    assert stored["authorization"] == {
        "schema_version": 1,
        "id": "authorization-1",
        "status": "authorization_required",
        "occurrence_at": 10.0,
        "request_digest": _digest("a"),
        "action_definition_digest": _digest("b"),
        "policy_id": "scheduled-tool-auth:v1",
        "policy_digest": _digest("c"),
        "required_scopes": ["memory.write"],
        "configured_scopes": [],
        "missing_scopes": ["memory.write"],
        "approval_required": True,
        "can_approve": False,
        "requested_at": 11.0,
    }
    assert "private" not in str(stored["authorization"])


def test_run_history_survives_calendar_event_deletion(tmp_path):
    event_dir = tmp_path / "databases" / "calendar_events"
    event_dir.mkdir(parents=True)
    event_path = event_dir / "calendar-nightly-review.json"
    event_path.write_text('{"id":"calendar-nightly-review"}', encoding="utf-8")
    store = WorkRunStore(data_dir=tmp_path)
    store.upsert_run(_receipt("receipt-durable"))

    event_path.unlink()
    reopened = WorkRunStore(data_dir=tmp_path)

    assert not event_path.exists()
    assert reopened.get_run("receipt-durable")["event_id"] == (
        "calendar-nightly-review"
    )


def test_list_and_count_support_job_status_time_and_lineage_filters(tmp_path):
    store = WorkRunStore(data_dir=tmp_path)
    store.upsert_run(_receipt("older", started_at=10), source="calendar")
    running = _receipt("newer", status="running", started_at=30)
    running["job_id"] = "continuous-review"
    running["event_id"] = "calendar-continuous-review"
    running["ownership"] = {
        **running["ownership"],
        "conversation_id": "session-999",
        "parent_job_id": "other-parent",
    }
    store.upsert_run(running, source="calendar")
    reflection = _receipt("middle", started_at=20)
    reflection["job_id"] = "reflection-job"
    store.upsert_run(reflection, source="reflection")

    assert [item["id"] for item in store.list_runs()] == [
        "newer",
        "middle",
        "older",
    ]
    assert store.count_runs(source="calendar") == 2
    assert store.count_runs(status="running") == 1
    assert store.count_runs(conversation_id="session-123") == 2
    assert store.count_runs(parent_job_id="other-parent") == 1
    assert store.count_runs(started_after=15, started_before=25) == 1
    assert store.list_runs(job_id="continuous-review")[0]["id"] == "newer"
    assert store.list_runs(limit=1, offset=1)[0]["id"] == "middle"


def test_recovery_queries_separate_safe_candidates_from_uncertain_runs(tmp_path):
    store = WorkRunStore(data_dir=tmp_path, now_fn=lambda: 50)
    stale = _receipt("stale-running", status="running", started_at=10)
    stale["lease_expires_at"] = 15
    recent = _receipt("recent-running", status="running", started_at=30)
    recent["lease_expires_at"] = 40
    uncertain = _receipt("uncertain", status="interrupted_unknown", started_at=5)
    uncertain["lease_expires_at"] = 10
    cancellation = _receipt(
        "cancel-requested", status="cancel_requested", started_at=30
    )
    cancellation["lease_expires_at"] = 60
    prompt_resume = _receipt(
        "prompt-resume", status="prompt_resume_pending", started_at=10
    )
    prompt_resume["lease_expires_at"] = 15
    approved = _receipt(
        "authorization-approved", status="authorization_approved", started_at=10
    )
    approved["lease_expires_at"] = 15
    store.upsert_run(stale)
    store.upsert_run(recent)
    store.upsert_run(uncertain)
    store.upsert_run(cancellation)
    store.upsert_run(prompt_resume)
    store.upsert_run(approved)
    store.upsert_run(_receipt("finished", status="complete", started_at=1))

    assert [item["id"] for item in store.list_recovery_candidates(stale_before=20)] == [
        "authorization-approved",
        "prompt-resume",
        "stale-running",
    ]
    assert {
        item["id"]
        for item in store.list_recovery_candidates(
            stale_before=20, include_attention=True
        )
    } == {
        "authorization-approved",
        "prompt-resume",
        "stale-running",
        "uncertain",
    }
    assert store.recovery_state("stale-running") == "active"
    assert store.recovery_state("uncertain") == "attention"
    assert store.recovery_state("cancel-requested") == "active"
    assert store.recovery_state("prompt-resume") == "active"
    assert store.recovery_state("authorization-approved") == "active"
    assert store.recovery_state("finished") == "terminal"
    assert store.recovery_state("missing") is None


def test_unknown_effect_metadata_keeps_terminal_error_receipt_in_attention(tmp_path):
    store = WorkRunStore(data_dir=tmp_path)

    uncertain = store.upsert_run(
        {
            **_receipt("effect-needs-reconciliation", status="error"),
            "effect_status": "unknown",
            "effect_certainty": "unknown",
            "state_delta_certainty": "unknown",
            "reconcile_required": True,
            "patience": {
                "max_attempts": 1,
                "max_runtime_seconds": 900,
                "max_provider_retries": 2,
            },
        }
    )

    assert uncertain["recovery_state"] == "attention"
    assert uncertain["reconcile_required"] is True
    assert uncertain["effect_status"] == "unknown"
    assert uncertain["patience"]["max_provider_retries"] == 2
    assert store.recovery_state("effect-needs-reconciliation") == "attention"


def test_child_effect_state_overlays_parent_after_crash_and_safe_acknowledgement(
    tmp_path,
):
    store = WorkRunStore(data_dir=tmp_path, now_fn=lambda: 50)
    parent = _receipt("crash-effect-parent", status="running", started_at=10)
    parent["lease_expires_at"] = 15
    store.upsert_run(parent)
    store.record_effect(
        {
            "id": "crash-effect",
            "receipt_id": "crash-effect-parent",
            "status": "intent",
            "certainty": "pending",
            "reconcile_required": False,
        },
        create_only=True,
    )
    store.record_effect(
        {
            "id": "crash-effect",
            "receipt_id": "crash-effect-parent",
            "status": "dispatched",
            "certainty": "unknown",
            "reconcile_required": True,
        },
        expected_statuses={"intent"},
    )

    dispatched = store.get_run("crash-effect-parent")
    assert dispatched["effect_status"] == "dispatched"
    assert dispatched["effect_certainty"] == "unknown"
    assert dispatched["reconcile_required"] is True
    assert dispatched["recovery_state"] == "attention"
    assert store.list_runs(job_id="nightly-review")[0]["recovery_state"] == (
        "attention"
    )
    assert store.list_recovery_candidates(stale_before=20) == []
    assert [
        item["id"]
        for item in store.list_recovery_candidates(
            stale_before=20, include_attention=True
        )
    ] == ["crash-effect-parent"]

    store.record_effect(
        {
            "id": "crash-effect",
            "receipt_id": "crash-effect-parent",
            "status": "unknown",
            "certainty": "unknown",
            "reconcile_required": True,
        },
        expected_statuses={"dispatched"},
    )
    assert store.recovery_state("crash-effect-parent") == "attention"

    store.record_effect(
        {
            "id": "crash-effect",
            "receipt_id": "crash-effect-parent",
            "status": "acknowledged",
            "certainty": "reported_success",
            "reconcile_required": False,
        },
        expected_statuses={"unknown"},
    )

    acknowledged = store.get_run("crash-effect-parent")
    assert acknowledged["effect_status"] == "acknowledged"
    assert acknowledged["effect_status"] != "confirmed"
    assert acknowledged["effect_certainty"] == "reported_success"
    assert acknowledged["reconcile_required"] is False
    assert acknowledged["recovery_state"] == "active"
    assert [item["id"] for item in store.list_recovery_candidates(stale_before=20)] == [
        "crash-effect-parent"
    ]


def test_unknown_child_effect_promotes_terminal_parent_to_recovery_attention(tmp_path):
    store = WorkRunStore(data_dir=tmp_path)
    store.upsert_run(_receipt("terminal-parent", status="complete"))
    store.record_effect(
        {
            "id": "terminal-parent-effect",
            "receipt_id": "terminal-parent",
            "status": "unknown",
            "certainty": "unknown",
            "reconcile_required": True,
        },
        create_only=True,
    )

    assert store.get_run("terminal-parent")["recovery_state"] == "attention"
    assert store.list_recovery_candidates() == []
    assert [
        item["id"] for item in store.list_recovery_candidates(include_attention=True)
    ] == ["terminal-parent"]


def test_safe_child_effect_does_not_clear_independent_parent_uncertainty(tmp_path):
    store = WorkRunStore(data_dir=tmp_path)
    store.upsert_run(
        {
            **_receipt("independently-uncertain-parent", status="running"),
            "reconcile_required": True,
            "recovery_reason_code": "provider_output_checkpoint_missing",
        }
    )
    store.record_effect(
        {
            "id": "safe-child-effect",
            "receipt_id": "independently-uncertain-parent",
            "status": "acknowledged",
            "certainty": "reported_success",
            "reconcile_required": False,
        },
        create_only=True,
    )

    receipt = store.get_run("independently-uncertain-parent")
    assert receipt["effect_status"] == "acknowledged"
    assert receipt["effect_certainty"] == "reported_success"
    assert receipt["reconcile_required"] is True
    assert receipt["recovery_state"] == "attention"
    assert [
        item["id"] for item in store.list_recovery_candidates(include_attention=True)
    ] == ["independently-uncertain-parent"]


def test_recovery_query_skips_terminal_acknowledged_effect_before_projection(
    tmp_path, monkeypatch
):
    store = WorkRunStore(data_dir=tmp_path)
    store.upsert_run(_receipt("acknowledged-terminal", status="complete"))
    store.record_effect(
        {
            "id": "acknowledged-terminal-effect",
            "receipt_id": "acknowledged-terminal",
            "status": "acknowledged",
            "certainty": "reported_success",
            "reconcile_required": False,
        },
        create_only=True,
    )
    active = _receipt("active-without-effect", status="running")
    store.upsert_run(active)

    decoded_ids = []
    original_decode = store._decode_receipts_with_effects

    def track_decoded_receipts(connection, rows):
        decoded = original_decode(connection, rows)
        decoded_ids.extend(item["id"] for item in decoded)
        return decoded

    monkeypatch.setattr(store, "_decode_receipts_with_effects", track_decoded_receipts)

    assert [item["id"] for item in store.list_recovery_candidates(limit=1)] == [
        "active-without-effect"
    ]
    assert "acknowledged-terminal" not in decoded_ids


def test_recovery_effect_predicate_fails_closed_for_unrecognized_or_malformed_rows(
    tmp_path,
):
    store = WorkRunStore(data_dir=tmp_path)
    for receipt_id, effect_id, status in (
        ("unrecognized-terminal", "unrecognized-effect", "remote_maybe_done"),
        ("malformed-terminal", "malformed-effect", "acknowledged"),
    ):
        store.upsert_run(_receipt(receipt_id, status="complete"))
        store.record_effect(
            {
                "id": effect_id,
                "receipt_id": receipt_id,
                "status": status,
                "certainty": "reported_success",
                "reconcile_required": False,
            },
            create_only=True,
        )

    connection = sqlite3.connect(store.path)
    try:
        connection.execute(
            "UPDATE work_run_effects SET payload_json = ? WHERE effect_id = ?",
            ("{malformed-json", "malformed-effect"),
        )
        connection.commit()
    finally:
        connection.close()

    assert store.has_unresolved_effects(
        event_id="calendar-nightly-review", action_id="action-1"
    )
    assert {
        item["id"] for item in store.list_recovery_candidates(include_attention=True)
    } == {"unrecognized-terminal", "malformed-terminal"}


def test_followup_phase_and_lifecycle_events_are_compact_and_append_only(tmp_path):
    clock = iter((100.0, 101.0, 102.0, 103.0))
    store = WorkRunStore(data_dir=tmp_path, now_fn=lambda: next(clock))
    running = _receipt("receipt-followup", status="running")
    running.update(
        {
            "phase": "tool_running",
            "followup_status": "pending",
            "recovery_count": 0,
            "tool_invoked": False,
            "lease_expires_at": 150,
            "conversation_body": "must never be logged",
        }
    )
    first = store.upsert_run(running)
    second = store.upsert_run(
        {
            "id": "receipt-followup",
            "phase": "followup_pending",
            "followup_status": "running",
            "recovery_count": 1,
            "recovered_at": 101,
            "recovery_reason": "Recovered an unfinished follow-up after restart.",
            "recovery_reason_code": "startup_resume",
            "recovered_from_phase": "tool_running",
            "tool_invoked": True,
            "summary": "Tool completed; follow-up is pending.",
        }
    )
    third = store.upsert_run(
        {
            "id": "receipt-followup",
            "status": "complete",
            "phase": "complete",
            "followup_status": "complete",
            "finished_at": 103,
            "summary": "Follow-up completed.",
        }
    )

    events = store.list_events("receipt-followup")
    assert first["event_count"] == 1
    assert second["event_count"] == 2
    assert third["event_count"] == 3
    assert [event["phase"] for event in events] == [
        "tool_running",
        "followup_pending",
        "complete",
    ]
    assert events[1]["changed_fields"] == [
        "phase",
        "followup_status",
        "recovery_count",
    ]
    assert "conversation_body" not in events[0]
    assert all("summary" not in event for event in events)
    assert (
        store.list_events("receipt-followup", limit=1, offset=1)[0]["phase"]
        == "followup_pending"
    )
    assert store.get_run("receipt-followup")["event_count"] == 3
    assert store.get_run("receipt-followup")["recovery_count"] == 1
    assert store.get_run("receipt-followup")["recovery_state"] == "terminal"
    assert events[1]["recovery_reason_code"] == "startup_resume"
    assert third["storage"]["backend"] == "sqlite"
    assert third["storage"]["device_local"] is True


def test_reflection_generation_metadata_is_normalized_into_execution(tmp_path):
    store = WorkRunStore(data_dir=tmp_path)
    record = _receipt("reflection-receipt")
    record["execution"] = {
        "requested_model": "gpt-requested",
        "received_model": "gpt-received",
        "provider": "openai",
        "finish_reason": "stop",
        "usage": {"input_tokens": 20, "output_tokens": 5, "secret": 99},
    }

    stored = store.upsert_run(record, source="reflection")

    assert stored["execution"] == {
        "requested_model": "gpt-requested",
        "received_model": "gpt-received",
        "provider": "openai",
        "finish_reason": "stop",
        "usage": {"input_tokens": 20, "output_tokens": 5},
    }


def test_wal_busy_timeout_and_concurrent_writers(tmp_path):
    store = WorkRunStore(data_dir=tmp_path, busy_timeout_ms=7_500)

    with sqlite3.connect(store.path) as connection:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    with store._connect() as connection:
        busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]

    def write(index: int):
        return store.upsert_run(
            _receipt(f"concurrent-{index}", started_at=float(index))
        )["id"]

    with ThreadPoolExecutor(max_workers=8) as executor:
        written = list(executor.map(write, range(24)))

    assert journal_mode.lower() == "wal"
    assert busy_timeout == 7_500
    assert len(written) == 24
    assert store.count_runs() == 24


def test_attempt_journal_is_private_idempotent_pageable_and_counted(tmp_path):
    clock = iter(range(100, 120))
    store = WorkRunStore(data_dir=tmp_path, now_fn=lambda: float(next(clock)))
    store.upsert_run(_receipt("receipt-attempts"))

    first = store.record_attempt(
        {
            "id": "attempt-1",
            "receipt_id": "receipt-attempts",
            "run_id": "run-receipt-attempts",
            "step_id": "provider-followup",
            "attempt_number": 1,
            "status": "PROVIDER_RUNNING",
            "retry_reason_code": "provider_timeout",
            "effect_watermark_digest": _digest("a"),
            "state_delta_certainty": "confirmed_no_change",
            "retry": {
                "is_retry": True,
                "number": 1,
                "of_attempt_id": "attempt-0",
                "reason_code": "provider_timeout",
            },
            "checkpoint": {
                "id": "checkpoint-1",
                "status": "durable",
                "digest": _digest("b"),
                "content": "private checkpoint body",
            },
            "provider": {
                "name": "openai",
                "model": "gpt-test",
                "request_id": "provider-request-1",
                "raw_response": "private provider response",
            },
            "error": {
                "category": "transport",
                "code": "timeout",
                "retryable": True,
                "message": "private error body",
            },
            "state_delta": {
                "changed": False,
                "kind": "none_observed",
                "certainty": "confirmed_no_change",
                "before_digest": _digest("c"),
                "after_digest": _digest("d"),
                "raw_state": "private state",
            },
            "started_at": 101,
            "prompt": "private prompt",
            "tool_arguments": {"token": "private-token"},
            "raw_result": "private result",
        }
    )

    assert first["status"] == "provider_running"
    assert first["effect_watermark_digest"] == _digest("a")
    assert first["state_delta_certainty"] == "confirmed_no_change"
    assert first["provider_metadata"]["request_id"] == "provider-request-1"
    assert first["transition_count"] == 1
    assert "prompt" not in first
    assert "tool_arguments" not in first
    assert "raw_result" not in first
    assert "content" not in first["checkpoint"]
    assert "raw_response" not in first["provider_metadata"]
    assert "message" not in first["error"]
    assert "raw_state" not in first["state_delta"]

    duplicate = store.record_attempt(dict(first))
    updated = store.record_attempt(
        {
            "id": "attempt-1",
            "receipt_id": "receipt-attempts",
            "status": "error",
            "finished_at": 102,
            "error": {"category": "transport", "code": "timeout"},
        }
    )
    store.record_attempt(
        {
            "id": "attempt-2",
            "receipt_id": "receipt-attempts",
            "attempt_number": 2,
            "status": "complete",
        }
    )
    store.record_attempt(
        {
            "id": "attempt-3",
            "receipt_id": "receipt-attempts",
            "attempt_number": 3,
            "status": "complete",
        }
    )

    assert duplicate["transition_count"] == 1
    assert updated["transition_count"] == 2
    assert store.count_attempts("receipt-attempts") == 3
    assert [
        item["id"]
        for item in store.list_attempts("receipt-attempts", limit=1, offset=1)
    ] == ["attempt-2"]
    assert store.get_run("receipt-attempts")["attempt_count"] == 3
    assert store.list_runs(job_id="nightly-review")[0]["attempt_count"] == 3

    with sqlite3.connect(store.path) as connection:
        transition_rows = connection.execute(
            "SELECT snapshot_json FROM work_run_attempt_events "
            "WHERE attempt_id = ? ORDER BY sequence",
            ("attempt-1",),
        ).fetchall()
    assert len(transition_rows) == 2
    retained = " ".join(str(row[0]) for row in transition_rows)
    assert "private prompt" not in retained
    assert "private-token" not in retained
    assert "private provider response" not in retained
    assert "private error body" not in retained


def test_effect_journal_has_private_transitions_and_status_cas(tmp_path):
    clock = iter(range(200, 220))
    store = WorkRunStore(data_dir=tmp_path, now_fn=lambda: float(next(clock)))
    store.upsert_run(_receipt("receipt-effects"))
    intent = {
        "id": "effect-1",
        "receipt_id": "receipt-effects",
        "run_id": "run-receipt-effects",
        "step_id": "tool-step",
        "attempt_id": "attempt-1",
        "tool_name": "calendar_update",
        "tool_call_id": "call-1",
        "effect_scope": "external_calendar",
        "replay_policy": "reconcile_before_retry",
        "status": "INTENT",
        "certainty": "not_dispatched",
        "reconcile_required": False,
        "redacted_target": f"event:{_digest('e')}",
        "argument_digest": _digest("f"),
        "idempotency_key": _digest("0"),
        "approval_snapshot": {
            "required": True,
            "status": "approved",
            "id": "approval-1",
            "policy_id": "policy-1",
            "actor_kind": "user",
            "comment": "private approval comment",
        },
        "permission_snapshot": {
            "status": "allowed",
            "scope": "calendar.write",
            "scopes": ["calendar.read", "calendar.write"],
            "policy_id": "permission-policy-1",
            "grant_id": "grant-1",
            "credential": "private credential",
        },
        "remote_ids": {
            "operation_id": "operation-1",
            "resource_id": "resource-1",
            "private_account_id": "private-account",
        },
        "digests": {
            "arguments": _digest("f"),
            "before": _digest("c"),
            "raw": "private raw digest source",
        },
        "error": {
            "category": "none",
            "code": "none",
            "message": "private error details",
        },
        "intended_at": 201,
        "prompt": "private prompt",
        "arguments": {"secret": "private argument"},
        "raw_result": "private tool result",
        "target": "unredacted@example.test",
    }

    first = store.record_effect(intent, create_only=True)
    duplicate = store.record_effect(dict(intent), create_only=True)
    dispatched = store.record_effect(
        {
            "id": "effect-1",
            "receipt_id": "receipt-effects",
            "status": "dispatched",
            "certainty": "dispatch_confirmed",
            "dispatched_at": 202,
            "remote_ids": {"request_id": "remote-request-1"},
        },
        expected_statuses={"intent"},
    )
    confirmed = store.record_effect(
        {
            "id": "effect-1",
            "receipt_id": "receipt-effects",
            "status": "confirmed",
            "certainty": "changed",
            "confirmed_at": 203,
            "after_digest": _digest("d"),
        },
        expected_statuses=("dispatched", "acknowledged"),
    )

    assert first["status"] == "intent"
    assert first["reconcile_required"] is False
    assert duplicate["transition_count"] == 1
    assert dispatched["transition_count"] == 2
    assert confirmed["transition_count"] == 3
    assert confirmed["remote_ids"] == {
        "operation_id": "operation-1",
        "request_id": "remote-request-1",
        "resource_id": "resource-1",
    }
    assert confirmed["approval_snapshot"]["status"] == "approved"
    assert confirmed["permission_snapshot"]["scopes"] == [
        "calendar.read",
        "calendar.write",
    ]
    for forbidden in ("prompt", "arguments", "raw_result", "target"):
        assert forbidden not in confirmed

    with pytest.raises(WorkRunTransitionConflict) as conflict:
        store.record_effect(
            {
                "id": "effect-1",
                "receipt_id": "receipt-effects",
                "status": "unknown",
            },
            expected_statuses={"intent"},
        )
    assert conflict.value.actual_status == "confirmed"
    assert store.list_effects("receipt-effects")[0]["status"] == "confirmed"

    with pytest.raises(WorkRunTransitionConflict) as missing:
        store.record_effect(
            {
                "id": "missing-effect",
                "receipt_id": "receipt-effects",
                "status": "dispatched",
            },
            expected_statuses={"intent"},
        )
    assert missing.value.actual_status is None

    with sqlite3.connect(store.path) as connection:
        transition_rows = connection.execute(
            "SELECT snapshot_json FROM work_run_effect_events "
            "WHERE effect_id = ? ORDER BY sequence",
            ("effect-1",),
        ).fetchall()
    assert len(transition_rows) == 3
    retained = " ".join(str(row[0]) for row in transition_rows)
    for secret in (
        "private prompt",
        "private argument",
        "private tool result",
        "unredacted@example.test",
        "private approval comment",
        "private credential",
        "private-account",
        "private error details",
    ):
        assert secret not in retained


def test_effect_journal_validates_identity_and_supports_paging(tmp_path):
    store = WorkRunStore(data_dir=tmp_path)
    store.upsert_run(_receipt("receipt-effects"))

    for invalid in ({}, {"id": "effect"}, {"receipt_id": "receipt-effects"}):
        with pytest.raises(ValueError):
            store.record_effect(invalid)
    with pytest.raises(ValueError):
        store.record_attempt({"id": "attempt", "receipt_id": ""})
    with pytest.raises(ValueError, match="missing receipt"):
        store.record_attempt({"id": "orphan-attempt", "receipt_id": "missing-receipt"})
    with pytest.raises(ValueError, match="missing receipt"):
        store.record_effect({"id": "orphan-effect", "receipt_id": "missing-receipt"})
    with pytest.raises(ValueError):
        store.record_effect(
            {"id": "effect", "receipt_id": "receipt-effects"},
            expected_statuses=[],
        )

    for index in range(4):
        store.record_effect(
            {
                "id": f"effect-{index}",
                "receipt_id": "receipt-effects",
                "status": "intent",
            },
            create_only=True,
        )
    with pytest.raises(ValueError):
        store.record_effect(
            {
                "id": "effect-0",
                "receipt_id": "another-receipt",
                "status": "confirmed",
            }
        )

    assert store.count_effects("receipt-effects") == 4
    assert [
        item["id"] for item in store.list_effects("receipt-effects", limit=2, offset=1)
    ] == ["effect-1", "effect-2"]
    receipt = store.get_run("receipt-effects")
    assert receipt["effect_count"] == 4
    assert receipt["attempt_count"] == 0


def test_effect_status_cas_allows_one_concurrent_transition(tmp_path):
    store = WorkRunStore(data_dir=tmp_path)
    store.upsert_run(_receipt("receipt-cas"))
    store.record_effect(
        {
            "id": "effect-cas",
            "receipt_id": "receipt-cas",
            "status": "intent",
        },
        create_only=True,
    )
    barrier = Barrier(2)

    def transition(index: int):
        barrier.wait()
        try:
            stored = store.record_effect(
                {
                    "id": "effect-cas",
                    "receipt_id": "receipt-cas",
                    "status": f"dispatched-{index}",
                },
                expected_statuses={"intent"},
            )
            return "stored", stored["status"]
        except WorkRunTransitionConflict as exc:
            return "conflict", exc.actual_status

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(transition, range(2)))

    assert sorted(outcome[0] for outcome in outcomes) == ["conflict", "stored"]
    winning_status = next(value for kind, value in outcomes if kind == "stored")
    conflict_status = next(value for kind, value in outcomes if kind == "conflict")
    assert conflict_status == winning_status
    assert store.list_effects("receipt-cas")[0]["transition_count"] == 2


def test_effect_create_only_allows_exact_duplicate_and_rejects_regression(tmp_path):
    store = WorkRunStore(data_dir=tmp_path)
    store.upsert_run(_receipt("receipt-create-only"))
    intent = {
        "id": "effect-create-only",
        "receipt_id": "receipt-create-only",
        "status": "intent",
        "certainty": "pending",
        "effect_scope": "device_state",
        "redacted_target": "tool:calendar_update",
        "argument_digest": _digest("a"),
        "intended_at": 100.0,
    }

    first = store.record_effect(intent, create_only=True)
    duplicate = store.record_effect(dict(intent), create_only=True)

    assert first["transition_count"] == 1
    assert duplicate == first
    with pytest.raises(WorkRunTransitionConflict) as conflict:
        store.record_effect(
            {**intent, "status": "dispatched", "certainty": "unknown"},
            create_only=True,
        )
    assert conflict.value.expected_statuses == ("missing",)
    assert conflict.value.actual_status == "intent"
    assert store.list_effects("receipt-create-only")[0]["status"] == "intent"
    assert store.list_effects("receipt-create-only")[0]["transition_count"] == 1

    with pytest.raises(ValueError, match="mutually exclusive"):
        store.record_effect(
            intent,
            create_only=True,
            expected_statuses={"intent"},
        )


def test_unresolved_effect_guard_uses_current_child_state_and_action_scope(tmp_path):
    store = WorkRunStore(data_dir=tmp_path)
    store.upsert_run(_receipt("guard-receipt", status="running"))
    store.record_effect(
        {
            "id": "guard-effect",
            "receipt_id": "guard-receipt",
            "status": "intent",
            "certainty": "pending",
            "reconcile_required": False,
        },
        create_only=True,
    )
    assert not store.has_unresolved_effects(
        event_id="calendar-nightly-review", action_id="action-1"
    )

    store.record_effect(
        {
            "id": "guard-effect",
            "receipt_id": "guard-receipt",
            "status": "dispatched",
            "certainty": "unknown",
            "reconcile_required": True,
        },
        expected_statuses={"intent"},
    )
    assert store.has_unresolved_effects(
        event_id="calendar-nightly-review", action_id="action-1"
    )

    store.record_effect(
        {
            "id": "guard-effect",
            "receipt_id": "guard-receipt",
            "status": "unknown",
            "certainty": "unknown",
            "reconcile_required": True,
        },
        expected_statuses={"dispatched"},
    )
    assert store.has_unresolved_effects(
        event_id="calendar-nightly-review", action_id="action-1"
    )

    store.record_effect(
        {
            "id": "guard-effect",
            "receipt_id": "guard-receipt",
            "status": "acknowledged",
            "certainty": "reported_success",
            "reconcile_required": False,
        },
        expected_statuses={"unknown"},
    )
    assert not store.has_unresolved_effects(
        event_id="calendar-nightly-review", action_id="action-1"
    )

    other_parent = _receipt("other-action-receipt", status="running")
    other_parent["action_id"] = "action-2"
    store.upsert_run(other_parent)
    store.record_effect(
        {
            "id": "other-action-effect",
            "receipt_id": "other-action-receipt",
            "status": "unexpected_remote_state",
            "certainty": "reported_success",
            "reconcile_required": False,
        },
        create_only=True,
    )
    assert not store.has_unresolved_effects(
        event_id="calendar-nightly-review", action_id="action-1"
    )
    assert store.has_unresolved_effects(
        event_id="calendar-nightly-review", action_id="action-2"
    )


def test_unresolved_effect_guard_blocks_uncertain_acknowledgement_or_reconcile_flag(
    tmp_path,
):
    store = WorkRunStore(data_dir=tmp_path)
    store.upsert_run(_receipt("guard-uncertain-ack", status="running"))
    store.record_effect(
        {
            "id": "guard-uncertain-effect",
            "receipt_id": "guard-uncertain-ack",
            "status": "acknowledged",
            "certainty": "unknown",
            "reconcile_required": False,
        },
        create_only=True,
    )
    assert store.has_unresolved_effects(
        event_id="calendar-nightly-review", action_id="action-1"
    )

    store.record_effect(
        {
            "id": "guard-uncertain-effect",
            "receipt_id": "guard-uncertain-ack",
            "status": "acknowledged",
            "certainty": "reported_success",
            "reconcile_required": True,
        },
        expected_statuses={"acknowledged"},
    )
    assert store.has_unresolved_effects(
        event_id="calendar-nightly-review", action_id="action-1"
    )

    store.record_effect(
        {
            "id": "guard-uncertain-effect",
            "receipt_id": "guard-uncertain-ack",
            "status": "acknowledged",
            "certainty": "reported_success",
            "reconcile_required": False,
        },
        expected_statuses={"acknowledged"},
    )
    assert not store.has_unresolved_effects(
        event_id="calendar-nightly-review", action_id="action-1"
    )


def test_journal_identity_writes_reject_overlength_and_lookups_do_not_alias(
    tmp_path,
):
    store = WorkRunStore(data_dir=tmp_path)
    receipt_id = "r" * 512
    store.upsert_run(_receipt(receipt_id))

    assert store.get_run(receipt_id) is not None
    assert store.get_run(receipt_id + "x") is None
    assert store.list_attempts(receipt_id + "x") == []
    assert store.list_effects(receipt_id + "x") == []
    assert store.count_attempts(receipt_id + "x") == 0
    assert store.count_effects(receipt_id + "x") == 0
    assert store.list_events(receipt_id + "x") == []
    assert store.count_events(receipt_id + "x") == 0

    with pytest.raises(ValueError, match="at most 512"):
        store.upsert_run(_receipt("r" * 513))
    with pytest.raises(ValueError, match="at most 512"):
        store.record_attempt(
            {"id": "a" * 513, "receipt_id": receipt_id, "status": "running"}
        )
    with pytest.raises(ValueError, match="at most 512"):
        store.record_effect(
            {"id": "e" * 513, "receipt_id": receipt_id, "status": "intent"},
            create_only=True,
        )
    with pytest.raises(ValueError, match="at most 512"):
        store.record_effect(
            {
                "id": "effect-with-long-reference",
                "receipt_id": receipt_id,
                "attempt_id": "a" * 513,
                "status": "intent",
            },
            create_only=True,
        )


def test_journal_drops_structured_metadata_and_rejects_unsafe_security_fields(
    tmp_path,
):
    store = WorkRunStore(data_dir=tmp_path)
    store.upsert_run(_receipt("receipt-private-scalars"))
    attempt = store.record_attempt(
        {
            "id": "attempt-private-scalars",
            "receipt_id": "receipt-private-scalars",
            "status": "running",
            "provider": ["private", {"prompt": "secret"}],
            "model": {"raw_response": "secret"},
            "error_category": ["private error"],
            "provider_metadata": {
                "name": "openai",
                "model": {"raw_response": "secret"},
                "request_id": ["private request"],
            },
            "error": {"category": {"message": "secret"}, "code": ["secret"]},
        }
    )
    assert "provider" not in attempt
    assert "model" not in attempt
    assert "error_category" not in attempt
    assert attempt["provider_metadata"] == {"name": "openai"}
    assert "error" not in attempt

    effect = store.record_effect(
        {
            "id": "effect-private-scalars",
            "receipt_id": "receipt-private-scalars",
            "status": "intent",
            "tool_name": ["write_file", {"args": "secret"}],
            "effect_scope": {"raw": "secret"},
            "redacted_target": "tool:write_file",
            "argument_digest": _digest("b"),
            "idempotency_key": _digest("c"),
            "approval_snapshot": {
                "status": ["secret"],
                "actor_kind": "scheduled_job",
            },
            "permission_snapshot": {
                "scope": {"credential": "secret"},
                "scopes": ["calendar.write", {"credential": "secret"}],
            },
            "remote_ids": {
                "request_id": {"raw_result": "secret"},
                "operation_id": "operation-1",
            },
        },
        create_only=True,
    )
    assert "tool_name" not in effect
    assert "effect_scope" not in effect
    assert effect["approval_snapshot"] == {"actor_kind": "scheduled_job"}
    assert effect["permission_snapshot"] == {"scopes": ["calendar.write"]}
    assert effect["remote_ids"] == {"operation_id": "operation-1"}

    invalid_effect_fields = (
        {"argument_digest": "sha256:short"},
        {"idempotency_key": "raw-idempotency-key"},
        {"redacted_target": "unredacted@example.test"},
        {"digests": {"before": "not-a-digest"}},
    )
    for index, fields in enumerate(invalid_effect_fields):
        with pytest.raises(ValueError):
            store.record_effect(
                {
                    "id": f"effect-invalid-security-{index}",
                    "receipt_id": "receipt-private-scalars",
                    "status": "intent",
                    **fields,
                },
                create_only=True,
            )
    with pytest.raises(ValueError):
        store.record_attempt(
            {
                "id": "attempt-invalid-digest",
                "receipt_id": "receipt-private-scalars",
                "status": "running",
                "effect_watermark_digest": "sha256:short",
            }
        )


def test_journal_error_metadata_retains_only_machine_codes(tmp_path):
    store = WorkRunStore(data_dir=tmp_path)
    store.upsert_run(_receipt("receipt-error-codes"))

    safe_attempt = store.record_attempt(
        {
            "id": "attempt-safe-errors",
            "receipt_id": "receipt-error-codes",
            "status": "retryable_error",
            "error_category": "provider_timeout",
            "error_code": "http_503",
            "error": {
                "category": "worker_restart",
                "code": "provider_timeout",
                "retryable": True,
            },
        }
    )
    assert safe_attempt["error_category"] == "provider_timeout"
    assert safe_attempt["error_code"] == "http_503"
    assert safe_attempt["error"] == {
        "category": "worker_restart",
        "code": "provider_timeout",
        "retryable": True,
    }

    unsafe_attempt = store.record_attempt(
        {
            "id": "attempt-unsafe-errors",
            "receipt_id": "receipt-error-codes",
            "status": "error",
            "error_category": "Provider timeout with bearer SECRET-TOKEN",
            "error_code": "SECRET-REMOTE-ENDPOINT-WITH-TOKEN",
            "error": {
                "category": "provider failed at https://private.example.test",
                "code": "token=SECRET-123",
                "retryable": False,
            },
        }
    )
    assert "error_category" not in unsafe_attempt
    assert "error_code" not in unsafe_attempt
    assert unsafe_attempt["error"] == {"retryable": False}

    unsafe_effect = store.record_effect(
        {
            "id": "effect-unsafe-errors",
            "receipt_id": "receipt-error-codes",
            "status": "unknown",
            "certainty": "unknown",
            "reconcile_required": True,
            "error_category": "Tool failed for private user alice@example.test",
            "error_code": "SECRET_API_KEY_123",
            "error": {
                "category": "raw exception sentence",
                "code": "https://private.example.test/error",
            },
        },
        create_only=True,
    )
    assert "error_category" not in unsafe_effect
    assert "error_code" not in unsafe_effect
    assert "error" not in unsafe_effect
    retained = b"".join(
        path.read_bytes() for path in store.path.parent.glob(f"{store.path.name}*")
    )
    assert b"SECRET" not in retained
    assert b"alice@example.test" not in retained
    assert b"private.example.test" not in retained


def test_public_store_operations_release_database_handles(tmp_path):
    data_dir = tmp_path / "float-data"
    store = WorkRunStore(data_dir=data_dir)
    store.upsert_run(_receipt("receipt-closed-handles"))
    store.record_attempt(
        {
            "id": "attempt-closed-handles",
            "receipt_id": "receipt-closed-handles",
            "status": "complete",
        }
    )
    store.record_effect(
        {
            "id": "effect-closed-handles",
            "receipt_id": "receipt-closed-handles",
            "status": "acknowledged",
            "certainty": "reported_success",
            "reconcile_required": False,
        },
        create_only=True,
    )
    store.get_run("receipt-closed-handles")
    store.list_runs()
    store.list_attempts("receipt-closed-handles")
    store.list_effects("receipt-closed-handles")
    store.list_events("receipt-closed-handles")
    store.list_recovery_candidates(include_attention=True)
    store.count_runs()
    store.count_attempts("receipt-closed-handles")
    store.count_effects("receipt-closed-handles")
    store.count_events("receipt-closed-handles")

    renamed = tmp_path / "float-data-renamed"
    data_dir.rename(renamed)
    assert (renamed / "databases" / "work_runs.sqlite3").exists()
