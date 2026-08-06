from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def calendar_job_client(tmp_path, monkeypatch):
    backend_dir = Path(__file__).resolve().parents[2]
    import sys

    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    from app.main import app
    from app.services.work_run_store import WorkRunStore
    from app.utils import calendar_store

    monkeypatch.setattr(
        calendar_store, "EVENTS_DIR", tmp_path / "calendar", raising=False
    )
    calendar_store.EVENTS_DIR.mkdir(parents=True, exist_ok=True)

    class FakeReflectionService:
        def list_runs(self, task_id="", *, limit=50):
            if task_id and task_id != "reflection-task-1":
                return []
            return [
                {
                    "id": "reflection-run-1",
                    "task_id": "reflection-task-1",
                    "created_at": 1_900_000_050.0,
                    "compact_note": "Reflection found a useful follow-up.",
                    "generation": {"model": "test-model"},
                }
            ][:limit]

        def get_task(self, task_id):
            assert task_id == "reflection-task-1"
            return {
                "id": task_id,
                "title": "Review the week",
                "source_thread_id": "chat-owner",
                "patience_budget": {"max_reasoning_turns": 2},
            }

    original_service = getattr(app.state, "reflection_service", None)
    original_work_run_store = getattr(app.state, "work_run_store", None)
    app.state.reflection_service = FakeReflectionService()
    app.state.work_run_store = WorkRunStore(data_dir=tmp_path)
    try:
        yield TestClient(app), calendar_store
    finally:
        if original_service is not None:
            app.state.reflection_service = original_service
        elif hasattr(app.state, "reflection_service"):
            delattr(app.state, "reflection_service")
        if original_work_run_store is not None:
            app.state.work_run_store = original_work_run_store


def _store_pending_authorization(
    calendar_store,
    *,
    event_id,
    action_id="action-1",
    occurrence_at=1_900_000_000.0,
    permissions=("memory.write",),
    include_receipt=True,
):
    from app.services.scheduled_action_authorization import (
        build_authorization_request,
        mark_authorization_required,
    )

    action = {
        "id": action_id,
        "request_id": action_id,
        "kind": "tool",
        "name": "remember",
        "args": {"key": "route-test", "value": "safe"},
    }
    event = {
        "id": event_id,
        "title": "Authorization route test",
        "start_time": occurrence_at,
        "timezone": "UTC",
        "status": "authorization_required",
        "actions": [action],
        "background_job": {
            "execution": {"permissions": list(permissions)},
            "ownership": {"calendar_event_id": event_id},
        },
    }
    authorization = build_authorization_request(
        event_id,
        event,
        action_id,
        action,
        occurrence_at,
    )
    mark_authorization_required(action, authorization, requested_at=occurrence_at - 5)
    if include_receipt:
        event["run_history"] = [
            {
                "id": f"receipt-{event_id}",
                "run_id": f"run-{event_id}",
                "event_id": event_id,
                "action_id": action_id,
                "action_name": "remember",
                "occurrence_at": occurrence_at,
                "started_at": occurrence_at - 5,
                "status": "authorization_required",
                "phase": "authorization",
                "tool_invoked": False,
                "state_delta_certainty": "confirmed_no_change",
                "authorization": dict(action["authorization"]),
            }
        ]
    calendar_store.save_event(event_id, event)
    return authorization


def _authorization_body(authorization, decision="approve_once"):
    return {
        "decision": decision,
        "authorization_id": authorization["id"],
        "request_digest": authorization["request_digest"],
        "occurrence_at": authorization["occurrence_at"],
    }


def test_calendar_occurrences_and_activity_runs(calendar_job_client):
    client, calendar_store = calendar_job_client
    start = 1_900_000_000.0
    calendar_store.save_event(
        "recurring-job",
        {
            "id": "recurring-job",
            "title": "Recurring job",
            "start_time": start,
            "end_time": start + 60,
            "timezone": "UTC",
            "rrule": "FREQ=MINUTELY;INTERVAL=2;COUNT=3",
            "status": "scheduled",
            "run_history": [
                {
                    "id": "calendar-run-1",
                    "event_id": "recurring-job",
                    "finished_at": start + 1,
                    "status": "prompted",
                    "summary": "Calendar prompt completed.",
                }
            ],
        },
    )
    calendar_store.save_event(
        "stopped-job",
        {
            "id": "stopped-job",
            "title": "Stopped job",
            "start_time": start,
            "timezone": "UTC",
            "rrule": "FREQ=MINUTELY;COUNT=3",
            "status": "acknowledged",
        },
    )
    calendar_store.save_event(
        "malformed-job",
        {
            "id": "malformed-job",
            "title": "Malformed job",
            "start_time": start + 30,
            "timezone": "UTC",
            "rrule": "FREQ=NOPE;BYDAY=???",
            "status": "scheduled",
        },
    )

    occurrences = client.get(
        "/api/calendar/occurrences",
        params={"range_start": start, "range_end": start + 600},
    )
    assert occurrences.status_code == 200
    payload = occurrences.json()
    assert payload["count"] == 4
    assert payload["errors"][0]["event_id"] == "malformed-job"
    assert payload["occurrences"][2]["occurrence_id"] == (
        f"recurring-job:{int(start + 120)}"
    )
    inactive = client.get(
        "/api/calendar/occurrences",
        params={
            "range_start": start,
            "range_end": start + 600,
            "include_inactive": True,
        },
    ).json()["occurrences"]
    assert {item["source_event_id"] for item in inactive} == {
        "recurring-job",
        "stopped-job",
        "malformed-job",
    }

    activity = client.get("/api/work/runs")
    assert activity.status_code == 200
    runs = activity.json()["runs"]
    assert [run["source"] for run in runs] == ["reflection", "calendar"]
    assert runs[0]["ownership"]["conversation_id"] == "chat-owner"
    assert runs[1]["summary"] == "Calendar prompt completed."

    filtered = client.get(
        "/api/work/runs", params={"source": "reflection", "job_id": "reflection-task-1"}
    )
    assert [item["id"] for item in filtered.json()["runs"]] == ["reflection-run-1"]
    assert activity.json()["storage"]["backend"] == "sqlite"
    assert activity.json()["storage"]["device_local"] is True


def test_calendar_delete_preserves_ledger_receipt_and_lifecycle(
    calendar_job_client,
):
    client, calendar_store = calendar_job_client
    event_id = "delete-after-run"
    calendar_store.save_event(
        event_id,
        {
            "id": event_id,
            "title": "Delete after run",
            "start_time": 1_900_000_000,
            "timezone": "UTC",
            "status": "acknowledged",
            "run_history": [
                {
                    "id": "durable-calendar-run",
                    "run_id": "run-token",
                    "event_id": event_id,
                    "started_at": 1_900_000_000,
                    "finished_at": 1_900_000_010,
                    "status": "prompted",
                    "phase": "complete",
                    "summary": "A bounded local receipt summary.",
                    "prompt": "must not enter the ledger",
                }
            ],
        },
    )

    first_page = client.get(
        "/api/work/runs",
        params={"source": "calendar", "job_id": event_id, "limit": 1, "offset": 0},
    )
    assert first_page.status_code == 200
    assert first_page.json()["count"] == 1
    assert first_page.json()["has_more"] is False
    assert first_page.json()["runs"][0]["summary"] == (
        "A bounded local receipt summary."
    )
    assert "prompt" not in first_page.json()["runs"][0]

    detail = client.get("/api/work/runs/durable-calendar-run/events")
    assert detail.status_code == 200
    assert detail.json()["count"] == 1
    assert detail.json()["events"][0]["phase"] == "complete"
    assert "summary" not in detail.json()["events"][0]

    deleted = client.delete(f"/api/calendar/events/{event_id}")
    assert deleted.status_code == 200
    assert deleted.json()["preserved_run_count"] == 1
    assert calendar_store.load_event(event_id) == {}

    retained = client.get(
        "/api/work/runs", params={"source": "calendar", "job_id": event_id}
    )
    assert [item["id"] for item in retained.json()["runs"]] == ["durable-calendar-run"]
    calendar_subset = client.get(
        "/api/calendar/runs", params={"event_id": event_id}
    ).json()
    assert [item["id"] for item in calendar_subset["runs"]] == ["durable-calendar-run"]


def test_calendar_delete_rejects_active_run_and_preserves_event(calendar_job_client):
    client, calendar_store = calendar_job_client
    event_id = "delete-active-run"
    calendar_store.save_event(
        event_id,
        {
            "id": event_id,
            "title": "Keep active run",
            "start_time": 1_900_000_000,
            "timezone": "UTC",
            "status": "acknowledged",
            "actions": [
                {
                    "id": "action-1",
                    "kind": "prompt",
                    "status": "running",
                    "run_id": "run-active",
                }
            ],
            "run_history": [
                {
                    "id": "receipt-active",
                    "run_id": "run-active",
                    "event_id": event_id,
                    "action_id": "action-1",
                    "started_at": 1_900_000_000,
                    "status": "running",
                    "phase": "provider",
                }
            ],
        },
    )

    deleted = client.delete(f"/api/calendar/events/{event_id}")

    assert deleted.status_code == 409
    assert "Request current run stop in Activity" in deleted.json()["detail"]
    assert calendar_store.load_event(event_id)["actions"][0]["run_id"] == "run-active"


def test_calendar_delete_rejects_ledger_active_run_when_event_state_is_stale(
    calendar_job_client,
):
    client, calendar_store = calendar_job_client
    from app.main import app

    event_id = "delete-ledger-active"
    calendar_store.save_event(
        event_id,
        {
            "id": event_id,
            "title": "Keep ledger-owned run",
            "start_time": 1_900_000_000,
            "timezone": "UTC",
            "status": "acknowledged",
        },
    )
    app.state.work_run_store.upsert_run(
        {
            "id": "receipt-ledger-active",
            "run_id": "run-ledger-active",
            "event_id": event_id,
            "action_id": "action-1",
            "started_at": 1_900_000_000,
            "status": "running",
            "phase": "provider",
        }
    )

    deleted = client.delete(f"/api/calendar/events/{event_id}")

    assert deleted.status_code == 409
    assert calendar_store.load_event(event_id)["id"] == event_id


def test_activity_attempt_and_effect_routes_return_only_safe_metadata(
    calendar_job_client,
):
    client, _calendar_store = calendar_job_client
    from app.main import app

    store = app.state.work_run_store
    store.upsert_run(
        {
            "id": "evidence-run",
            "source": "calendar",
            "job_id": "evidence-job",
            "event_id": "evidence-job",
            "status": "error",
            "phase": "followup",
            "started_at": 1_900_000_000.0,
            "finished_at": 1_900_000_001.0,
        }
    )
    store.record_attempt(
        {
            "id": "provider-attempt-1",
            "receipt_id": "evidence-run",
            "attempt_number": 1,
            "status": "retryable_error",
            "provider": "openai_compatible",
            "error_category": "provider_timeout",
            "retry_reason_code": "transient_provider_error",
            "state_delta_certainty": "confirmed_no_change",
            "effect_watermark_digest": f"sha256:{'a' * 64}",
            "started_at": 1_900_000_000.0,
            "finished_at": 1_900_000_000.5,
            "prompt": "must not enter Activity",
            "raw_response": {"secret": "must not enter Activity"},
        }
    )
    store.record_effect(
        {
            "id": "effect-intent-1",
            "receipt_id": "evidence-run",
            "attempt_id": "provider-attempt-1",
            "tool_name": "write_file",
            "effect_scope": "workspace",
            "status": "intended",
            "certainty": "confirmed_no_change",
            "replay_policy": "reconcile_before_retry",
            "argument_digest": f"sha256:{'b' * 64}",
            "intended_at": 1_900_000_000.25,
            "args": {"content": "must not enter Activity"},
            "result": "must not enter Activity",
        }
    )

    run = client.get(
        "/api/work/runs", params={"source": "calendar", "job_id": "evidence-job"}
    )
    assert run.status_code == 200
    assert run.json()["runs"][0]["attempt_count"] == 1
    assert run.json()["runs"][0]["effect_count"] == 1

    attempts = client.get(
        "/api/work/runs/evidence-run/attempts", params={"limit": 1, "offset": 0}
    )
    assert attempts.status_code == 200
    assert attempts.json()["count"] == 1
    assert attempts.json()["has_more"] is False
    attempt = attempts.json()["attempts"][0]
    assert attempt["id"] == "provider-attempt-1"
    assert attempt["state_delta_certainty"] == "confirmed_no_change"
    assert "prompt" not in attempt
    assert "raw_response" not in attempt

    effects = client.get(
        "/api/work/runs/evidence-run/effects", params={"limit": 1, "offset": 0}
    )
    assert effects.status_code == 200
    assert effects.json()["count"] == 1
    effect = effects.json()["effects"][0]
    assert effect["id"] == "effect-intent-1"
    assert effect["effect_scope"] == "workspace"
    assert "args" not in effect
    assert "result" not in effect

    assert client.get("/api/work/runs/missing/attempts").status_code == 404
    assert client.get("/api/work/runs/missing/effects").status_code == 404


def test_reflection_backfill_pages_beyond_legacy_two_hundred_run_cap(
    calendar_job_client,
):
    client, _calendar_store = calendar_job_client
    from app.main import app

    runs = [
        {
            "id": f"reflection-run-{index:03d}",
            "task_id": "deep-review",
            "created_at": 1_900_000_000.0 + index,
            "compact_note": f"Compact receipt {index}.",
        }
        for index in range(205)
    ]

    class PagedReflectionService:
        def list_runs(self, task_id="", *, limit=50, offset=0):
            assert task_id in {"", "deep-review"}
            ordered = list(reversed(runs))
            return ordered[offset : offset + limit]

        def get_task(self, task_id):
            assert task_id == "deep-review"
            return {"id": task_id, "title": "Deep review"}

    app.state.reflection_service = PagedReflectionService()

    first = client.get(
        "/api/work/runs",
        params={"source": "reflection", "job_id": "deep-review", "limit": 50},
    ).json()
    tail = client.get(
        "/api/work/runs",
        params={
            "source": "reflection",
            "job_id": "deep-review",
            "limit": 50,
            "offset": 200,
        },
    ).json()

    assert first["count"] == 205
    assert first["has_more"] is True
    assert first["next_offset"] == 50
    assert len(first["runs"]) == 50
    assert len(tail["runs"]) == 5
    assert tail["has_more"] is False


def test_calendar_event_rejects_unknown_timezone(calendar_job_client):
    client, _calendar_store = calendar_job_client
    response = client.post(
        "/api/calendar/events/bad-timezone",
        json={
            "id": "bad-timezone",
            "title": "Bad timezone",
            "start_time": 1_900_000_000,
            "timezone": "Mars/Olympus_Mons",
        },
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "rule, expected",
    [
        ("FREQ=DAILY;INTERVAL=0", "positive integer"),
        ("FREQ=DAILY;BYDAY=???", "invalid RRULE"),
    ],
)
def test_calendar_event_rejects_unsafe_rrule(calendar_job_client, rule, expected):
    client, _calendar_store = calendar_job_client
    response = client.post(
        "/api/calendar/events/unsafe-rule",
        json={
            "id": "unsafe-rule",
            "title": "Unsafe recurrence",
            "start_time": 1_900_000_000,
            "timezone": "UTC",
            "rrule": rule,
        },
    )

    assert response.status_code == 422
    assert expected in response.text


def test_calendar_update_preserves_server_receipts_and_future_policy_fields(
    calendar_job_client,
):
    client, calendar_store = calendar_job_client
    calendar_store.save_event(
        "owned-job",
        {
            "id": "owned-job",
            "title": "Owned job",
            "start_time": 1_900_000_000,
            "timezone": "UTC",
            "status": "scheduled",
            "actions": [
                {
                    "request_id": "action-1",
                    "kind": "prompt",
                    "prompt": "Old prompt",
                    "status": "prompted",
                    "executed_at": 1_900_000_010,
                    "result": "real result",
                }
            ],
            "run_history": [
                {
                    "id": "real-run",
                    "finished_at": 1_900_000_010,
                    "status": "prompted",
                    "summary": "real receipt",
                }
            ],
        },
    )

    response = client.post(
        "/api/calendar/events/owned-job",
        json={
            "id": "owned-job",
            "title": "Owned job updated",
            "start_time": 1_900_000_000,
            "timezone": "UTC",
            "actions": [
                {
                    "request_id": "action-1",
                    "kind": "prompt",
                    "prompt": "New prompt",
                    "status": "scheduled",
                    "result": "forged result",
                }
            ],
            "run_history": [
                {
                    "id": "forged-run",
                    "finished_at": "not-a-time",
                    "status": "complete",
                }
            ],
            "background_job": {
                "execution": {"model": "future-model", "workflow": "review"},
                "ownership": {"conversation_id": "chat-1"},
                "future_policy": {"version": 2},
            },
        },
    )

    assert response.status_code == 200
    stored = calendar_store.load_event("owned-job")
    assert [item["id"] for item in stored["run_history"]] == ["real-run"]
    assert stored["actions"][0]["prompt"] == "New prompt"
    assert stored["actions"][0]["status"] == "prompted"
    assert stored["actions"][0]["result"] == "real result"
    assert stored["background_job"]["execution"]["model"] == "future-model"
    assert stored["background_job"]["future_policy"] == {"version": 2}


def test_calendar_store_keeps_external_ids_contained(calendar_job_client):
    _client, calendar_store = calendar_job_client
    escaped = calendar_store.EVENTS_DIR.parent / "escaped.json"

    with pytest.raises(ValueError, match="safe filename"):
        calendar_store.save_event("../escaped", {"id": "../escaped"})

    assert not escaped.exists()
    calendar_store.save_event("urn:uuid:external", {"id": "urn:uuid:external"})
    assert calendar_store.load_event("urn:uuid:external")["id"] == "urn:uuid:external"
    assert "urn:uuid:external" in calendar_store.list_events()


def test_calendar_event_rejects_duplicate_action_ids(calendar_job_client):
    client, _calendar_store = calendar_job_client
    response = client.post(
        "/api/calendar/events/duplicate-actions",
        json={
            "id": "duplicate-actions",
            "title": "Duplicate actions",
            "start_time": 1_900_000_000,
            "timezone": "UTC",
            "actions": [
                {"id": "same", "kind": "prompt", "prompt": "First"},
                {"id": "same", "kind": "prompt", "prompt": "Second"},
            ],
        },
    )

    assert response.status_code == 422
    assert "duplicate calendar action id" in response.text


def test_reopening_one_time_event_rearms_actions_and_keeps_history(
    calendar_job_client,
):
    client, calendar_store = calendar_job_client
    calendar_store.save_event(
        "reopen-once",
        {
            "id": "reopen-once",
            "title": "Reopen once",
            "start_time": 1_900_000_000,
            "timezone": "UTC",
            "status": "acknowledged",
            "actions": [
                {
                    "id": "once-action",
                    "kind": "prompt",
                    "prompt": "Run again",
                    "status": "prompted",
                    "executed_at": 1_900_000_010,
                    "result": "first result",
                }
            ],
            "run_history": [{"id": "first-run", "status": "prompted"}],
        },
    )

    response = client.post(
        "/api/calendar/events/reopen-once",
        json={
            "id": "reopen-once",
            "title": "Reopen once",
            "start_time": 1_900_000_000,
            "timezone": "UTC",
            "status": "pending",
        },
    )

    assert response.status_code == 200
    stored = calendar_store.load_event("reopen-once")
    assert stored["actions"][0]["status"] == "scheduled"
    assert stored["actions"][0]["external_control_revision"] == 1
    assert "executed_at" not in stored["actions"][0]
    assert "result" not in stored["actions"][0]
    assert [item["id"] for item in stored["run_history"]] == ["first-run"]


def test_occurrence_route_reports_dense_series_truncation(calendar_job_client):
    client, calendar_store = calendar_job_client
    start = 1_900_000_000.0
    calendar_store.save_event(
        "dense-series",
        {
            "id": "dense-series",
            "title": "Dense series",
            "start_time": start,
            "timezone": "UTC",
            "rrule": "FREQ=MINUTELY;COUNT=10",
            "status": "scheduled",
        },
    )

    payload = client.get(
        "/api/calendar/occurrences",
        params={
            "range_start": start - 1,
            "range_end": start + 900,
            "limit_per_event": 3,
        },
    ).json()

    assert payload["count"] == 3
    assert payload["truncated"] == [
        {"event_id": "dense-series", "title": "Dense series", "limit": 3}
    ]


def test_calendar_reimport_preserves_local_job_state(calendar_job_client):
    client, calendar_store = calendar_job_client
    event_id = "urn:uuid:imported-job"
    calendar_store.save_event(
        event_id,
        {
            "id": event_id,
            "title": "Old imported title",
            "start_time": 1_900_000_000,
            "timezone": "UTC",
            "status": "paused",
            "actions": [{"id": "local-action", "status": "scheduled"}],
            "background_job": {"ownership": {"conversation_id": "chat-owner"}},
            "run_history": [{"id": "local-receipt", "status": "prompted"}],
        },
    )

    response = client.post(
        "/api/calendar/import/google",
        json={
            "items": [
                {
                    "id": event_id,
                    "summary": "Updated imported title",
                    "start": {"dateTime": "2030-03-17T10:00:00Z"},
                    "end": {"dateTime": "2030-03-17T11:00:00Z"},
                }
            ]
        },
    )

    assert response.status_code == 200
    stored = calendar_store.load_event(event_id)
    assert stored["title"] == "Updated imported title"
    assert stored["status"] == "paused"
    assert stored["actions"][0]["id"] == "local-action"
    assert stored["background_job"]["ownership"]["conversation_id"] == "chat-owner"
    assert stored["run_history"][0]["id"] == "local-receipt"


def test_calendar_authorization_approve_once_is_local_projected_and_idempotent(
    calendar_job_client,
):
    client, calendar_store = calendar_job_client
    event_id = "approve-once-route"
    action_id = "remember-action"
    authorization = _store_pending_authorization(
        calendar_store, event_id=event_id, action_id=action_id
    )
    endpoint = f"/api/calendar/events/{event_id}/actions/{action_id}/authorization"
    body = _authorization_body(authorization)

    first = client.post(endpoint, json=body)

    assert first.status_code == 200
    assert first.json()["status"] == "approved_once"
    assert first.json()["idempotent"] is False
    assert first.json()["execution_started"] is False
    assert first.json()["receipt_id"] == f"receipt-{event_id}"
    stored = calendar_store.load_event(event_id)
    assert stored["actions"][0]["status"] == "authorization_approved"
    assert stored["actions"][0]["authorization"]["status"] == "approved_once"
    assert len(stored["run_history"]) == 1
    receipt = stored["run_history"][0]
    assert receipt["status"] == "authorization_approved"
    assert receipt["phase"] == "authorization"
    assert receipt["authorization"]["status"] == "approved_once"
    assert receipt["tool_invoked"] is False

    projected = client.get(
        "/api/work/runs", params={"source": "calendar", "job_id": event_id}
    ).json()["runs"]
    assert len(projected) == 1
    assert projected[0]["id"] == f"receipt-{event_id}"
    assert projected[0]["status"] == "authorization_approved"
    assert projected[0]["authorization"]["status"] == "approved_once"

    repeated = client.post(endpoint, json=body)

    assert repeated.status_code == 200
    assert repeated.json()["idempotent"] is True
    assert len(calendar_store.load_event(event_id)["run_history"]) == 1


def test_calendar_authorization_deny_updates_same_terminal_receipt(
    calendar_job_client,
):
    client, calendar_store = calendar_job_client
    event_id = "deny-route"
    action_id = "remember-action"
    authorization = _store_pending_authorization(
        calendar_store, event_id=event_id, action_id=action_id
    )

    response = client.post(
        f"/api/calendar/events/{event_id}/actions/{action_id}/authorization",
        json=_authorization_body(authorization, "deny"),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "authorization_denied"
    assert response.json()["execution_started"] is False
    stored = calendar_store.load_event(event_id)
    assert stored["status"] == "prompted"
    assert stored["actions"][0]["status"] == "authorization_denied"
    assert stored["actions"][0]["authorization"]["status"] == ("authorization_denied")
    assert len(stored["run_history"]) == 1
    receipt = stored["run_history"][0]
    assert receipt["status"] == "authorization_denied"
    assert receipt["phase"] == "authorization"
    assert receipt["authorization"]["status"] == "authorization_denied"
    assert receipt["tool_invoked"] is False


def test_calendar_authorization_is_local_only_and_rejects_stale_or_missing(
    calendar_job_client,
):
    client, calendar_store = calendar_job_client
    event_id = "guarded-route"
    action_id = "remember-action"
    authorization = _store_pending_authorization(
        calendar_store, event_id=event_id, action_id=action_id
    )
    endpoint = f"/api/calendar/events/{event_id}/actions/{action_id}/authorization"
    body = _authorization_body(authorization)

    remote = client.post(
        endpoint,
        json=body,
        headers={"x-forwarded-for": "192.168.1.25"},
    )
    assert remote.status_code == 403
    assert (
        calendar_store.load_event(event_id)["actions"][0]["authorization"]["status"]
        == "authorization_required"
    )

    stale = client.post(
        endpoint,
        json={**body, "request_digest": f"sha256:{'f' * 64}"},
    )
    assert stale.status_code == 409
    assert "changed" in stale.json()["detail"]

    missing = client.post(
        f"/api/calendar/events/missing-event/actions/{action_id}/authorization",
        json=body,
    )
    assert missing.status_code == 404


def test_calendar_authorization_missing_scope_cannot_be_approved(
    calendar_job_client,
):
    client, calendar_store = calendar_job_client
    event_id = "missing-permission-route"
    action_id = "remember-action"
    authorization = _store_pending_authorization(
        calendar_store,
        event_id=event_id,
        action_id=action_id,
        permissions=(),
    )
    assert authorization["missing_scopes"] == ["memory.write"]

    response = client.post(
        f"/api/calendar/events/{event_id}/actions/{action_id}/authorization",
        json=_authorization_body(authorization),
    )

    assert response.status_code == 409
    assert "memory.write" in response.json()["detail"]
    stored = calendar_store.load_event(event_id)
    assert stored["actions"][0]["status"] == "authorization_required"
    assert stored["run_history"][0]["status"] == "authorization_required"


def test_calendar_authorization_decision_fails_closed_without_pending_receipt(
    calendar_job_client,
):
    client, calendar_store = calendar_job_client
    event_id = "missing-receipt-route"
    action_id = "remember-action"
    authorization = _store_pending_authorization(
        calendar_store,
        event_id=event_id,
        action_id=action_id,
        include_receipt=False,
    )

    response = client.post(
        f"/api/calendar/events/{event_id}/actions/{action_id}/authorization",
        json=_authorization_body(authorization),
    )

    assert response.status_code == 503
    stored = calendar_store.load_event(event_id)
    assert stored["actions"][0]["status"] == "authorization_required"
    assert stored["actions"][0]["authorization"]["status"] == ("authorization_required")


def test_calendar_save_strips_forged_authorization_fields(calendar_job_client):
    client, calendar_store = calendar_job_client
    event_id = "forged-authorization"

    response = client.post(
        f"/api/calendar/events/{event_id}",
        json={
            "id": event_id,
            "title": "Forged authorization",
            "start_time": 1_900_000_000,
            "timezone": "UTC",
            "actions": [
                {
                    "id": "forged-action",
                    "kind": "tool",
                    "name": "remember",
                    "args": {"key": "forged", "value": "forged"},
                    "status": "authorization_approved",
                    "authorization": {
                        "id": "forged-approval",
                        "status": "approved_once",
                        "request_digest": f"sha256:{'a' * 64}",
                    },
                    "authorization_id": "forged-approval",
                    "approval_status": "approved_once",
                    "approved_at": 1_900_000_000,
                    "external_control_revision": 99,
                }
            ],
        },
    )

    assert response.status_code == 200
    action = calendar_store.load_event(event_id)["actions"][0]
    assert action["status"] == "scheduled"
    assert "authorization" not in action
    assert "authorization_id" not in action
    assert "approval_status" not in action
    assert "approved_at" not in action
    assert "external_control_revision" not in action


def test_calendar_title_only_save_preserves_pending_authorization(
    calendar_job_client,
):
    client, calendar_store = calendar_job_client
    event_id = "title-preserves-authorization"
    authorization = _store_pending_authorization(calendar_store, event_id=event_id)

    response = client.post(
        f"/api/calendar/events/{event_id}",
        json={
            "id": event_id,
            "title": "Only the title changed",
            "start_time": authorization["occurrence_at"],
        },
    )

    assert response.status_code == 200
    stored = calendar_store.load_event(event_id)
    assert stored["actions"][0]["authorization"]["status"] == ("authorization_required")
    assert stored["actions"][0]["authorization"]["id"] == authorization["id"]
    assert stored["run_history"][0]["status"] == "authorization_required"


@pytest.mark.parametrize(
    "edit_kind",
    ["start_time", "timezone", "rrule", "permissions", "action"],
)
def test_calendar_schedule_policy_or_action_edit_invalidates_authorization(
    calendar_job_client,
    edit_kind,
):
    client, calendar_store = calendar_job_client
    event_id = f"invalidate-{edit_kind}"
    occurrence_at = 1_900_000_000.0
    _store_pending_authorization(
        calendar_store, event_id=event_id, occurrence_at=occurrence_at
    )
    payload = {
        "id": event_id,
        "title": "Authorization input changed",
        "start_time": occurrence_at,
    }
    if edit_kind == "start_time":
        payload["start_time"] = occurrence_at + 60
    elif edit_kind == "timezone":
        payload["timezone"] = "America/Vancouver"
    elif edit_kind == "rrule":
        payload["rrule"] = "FREQ=DAILY;COUNT=2"
    elif edit_kind == "permissions":
        payload["background_job"] = {
            "execution": {"permissions": ["memory.write", "files.read"]}
        }
    else:
        payload["actions"] = [
            {
                "id": "action-1",
                "request_id": "action-1",
                "kind": "tool",
                "name": "remember",
                "args": {"key": "route-test", "value": "changed"},
            }
        ]

    response = client.post(f"/api/calendar/events/{event_id}", json=payload)

    assert response.status_code == 200
    stored = calendar_store.load_event(event_id)
    authorization = stored["actions"][0]["authorization"]
    assert authorization["status"] == "invalidated"
    assert authorization["invalidation_reason"] == "event_edited"
    receipt = stored["run_history"][0]
    assert receipt["status"] == "authorization_invalidated"
    assert receipt["phase"] == "authorization"
    assert receipt["authorization"]["status"] == "invalidated"


def test_calendar_action_removal_terminalizes_its_activity_authorization(
    calendar_job_client,
):
    client, calendar_store = calendar_job_client
    event_id = "remove-pending-authorization"
    _store_pending_authorization(calendar_store, event_id=event_id)

    response = client.post(
        f"/api/calendar/events/{event_id}",
        json={
            "id": event_id,
            "title": "Removed scheduled action",
            "start_time": 1_900_000_000.0,
            "actions": [],
        },
    )

    assert response.status_code == 200
    stored = calendar_store.load_event(event_id)
    assert stored["actions"] == []
    assert stored["run_history"][0]["status"] == "authorization_invalidated"
    assert stored["run_history"][0]["authorization"]["invalidation_reason"] == (
        "action_removed"
    )
    runs = client.get(
        "/api/work/runs", params={"source": "calendar", "job_id": event_id}
    ).json()["runs"]
    assert len(runs) == 1
    assert runs[0]["status"] == "authorization_invalidated"
    assert runs[0]["recovery_state"] == "terminal"
