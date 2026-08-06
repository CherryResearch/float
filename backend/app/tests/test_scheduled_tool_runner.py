import asyncio
import json
import sys
import time
from pathlib import Path

import pytest


def test_run_summary_keeps_structured_tool_receipts_compact():
    from workers.scheduled_tool_runner import _run_summary

    summary = _run_summary(
        {
            "status": "invoked",
            "result": {
                "name": "modules",
                "summary": "Runtime workflow catalog.",
                "raw_payload": {"large": ["content"] * 100},
            },
        }
    )

    assert summary == "Scheduled action returned structured output (3 fields)."
    assert "Runtime workflow catalog" not in summary
    assert "raw_payload" not in summary


@pytest.fixture
def app_with_temp_stores(tmp_path, monkeypatch):
    backend_dir = Path(__file__).resolve().parents[2]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    from app.main import app
    from app.services import scheduled_action_authorization as authorization
    from app.services.work_run_store import WorkRunStore
    from app.utils import calendar_store, conversation_store

    monkeypatch.setattr(
        conversation_store, "CONV_DIR", tmp_path / "conversations", raising=False
    )
    conversation_store.CONV_DIR.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        calendar_store, "EVENTS_DIR", tmp_path / "calendar", raising=False
    )
    calendar_store.EVENTS_DIR.mkdir(parents=True, exist_ok=True)

    # Most existing runner tests predate scheduled authorization and focus on
    # later execution/recovery phases. Keep them on a catalog-auto test policy
    # with a bounded scope ceiling; focused tests below restore the real policy.
    real_policy = authorization.scheduled_approval_policy_for_tool
    real_configured_permissions = authorization._configured_permissions

    def catalog_auto_policy(tool_name):
        return {**real_policy(tool_name), "approval_required": False}

    def configured_test_permissions(event):
        return sorted(
            set(real_configured_permissions(event)) | {"custom.execute", "memory.write"}
        )

    monkeypatch.setattr(
        authorization, "scheduled_approval_policy_for_tool", catalog_auto_policy
    )
    monkeypatch.setattr(
        authorization, "_configured_permissions", configured_test_permissions
    )

    # Keep tests isolated from existing console state.
    app.state.agent_console_state = {"agents": {}}

    class DummyManager:
        def __init__(self):
            self.calls = []

        def invoke_tool(self, name, *, user=None, signature=None, **args):
            self.calls.append({"name": name, "user": user, "args": args})
            return "ok"

    original_manager = getattr(app.state, "memory_manager", None)
    original_work_run_store = getattr(app.state, "work_run_store", None)
    dummy = DummyManager()
    app.state.memory_manager = dummy
    app.state.work_run_store = WorkRunStore(data_dir=tmp_path)
    try:
        yield app, dummy
    finally:
        if original_manager is not None:
            app.state.memory_manager = original_manager
        if original_work_run_store is not None:
            app.state.work_run_store = original_work_run_store


def _restore_real_scheduled_authorization(monkeypatch):
    from app import tool_catalog
    from app.services import scheduled_action_authorization as authorization
    from app.services.calendar_jobs import normalize_permission_scopes

    def configured_permissions(event):
        background_job = event.get("background_job")
        background_job = background_job if isinstance(background_job, dict) else {}
        execution = background_job.get("execution")
        execution = execution if isinstance(execution, dict) else {}
        return normalize_permission_scopes(execution.get("permissions", []))

    monkeypatch.setattr(
        authorization,
        "scheduled_approval_policy_for_tool",
        tool_catalog.scheduled_approval_policy_for_tool,
    )
    monkeypatch.setattr(
        authorization, "_configured_permissions", configured_permissions
    )


@pytest.mark.anyio
async def test_persist_event_rejects_stale_external_control_state_and_history(
    app_with_temp_stores,
):
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    app, _dummy = app_with_temp_stores
    event_id = "stale-external-control"
    current = {
        "id": event_id,
        "title": "Current control state",
        "start_time": time.time() - 5,
        "timezone": "UTC",
        "status": "running",
        "actions": [
            {
                "id": "action-1",
                "kind": "tool",
                "name": "remember",
                "args": {"key": "bounded", "value": "current"},
                "status": "cancel_requested",
                "run_id": "run-1",
                "external_control_revision": 2,
                "authorization": {"id": "auth-1", "status": "approved_once"},
                "cancel_requested": True,
                "effect_id": "effect-1",
                "effect_status": "confirmed",
                "effect_certainty": "user_confirmed_applied",
            }
        ],
        "run_history": [
            {"id": "receipt-1", "status": "cancel_requested", "run_id": "run-1"}
        ],
    }
    calendar_store.save_event(event_id, current)
    stale = json.loads(json.dumps(current))
    stale_action = stale["actions"][0]
    stale_action.update(
        {
            "status": "running",
            "external_control_revision": 1,
            "authorization": {"id": "auth-1", "status": "authorization_required"},
            "cancel_requested": False,
            "effect_status": "unknown",
            "effect_certainty": "unknown",
        }
    )
    stale["run_history"][0]["status"] = "running"

    assert not await runner._persist_event(app, event_id, stale)

    stored = calendar_store.load_event(event_id)
    assert stored["actions"][0] == current["actions"][0]
    assert stored["run_history"][0]["status"] == "cancel_requested"


@pytest.mark.anyio
async def test_targeted_persist_does_not_regress_an_unrelated_action_or_receipt(
    app_with_temp_stores,
):
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    app, _dummy = app_with_temp_stores
    event_id = "targeted-persist"
    current = {
        "id": event_id,
        "title": "Independent actions",
        "start_time": time.time() - 5,
        "timezone": "UTC",
        "status": "running",
        "actions": [
            {
                "id": "action-a",
                "kind": "tool",
                "name": "list_tasks",
                "args": {},
                "status": "running",
                "run_id": "run-a",
                "external_control_revision": 1,
                "run_control_revision": 1,
            },
            {
                "id": "action-b",
                "kind": "tool",
                "name": "list_tasks",
                "args": {"limit": 2},
                "status": "cancel_requested",
                "run_id": "run-b",
                "external_control_revision": 3,
                "run_control_revision": 2,
                "cancel_requested": True,
            },
        ],
        "run_history": [
            {
                "id": "receipt-a",
                "event_id": event_id,
                "action_id": "action-a",
                "run_id": "run-a",
                "status": "running",
            },
            {
                "id": "receipt-b",
                "event_id": event_id,
                "action_id": "action-b",
                "run_id": "run-b",
                "status": "cancel_requested",
            },
        ],
    }
    calendar_store.save_event(event_id, current)
    payload = json.loads(json.dumps(current))
    payload["actions"][0]["status"] = "invoked"
    payload["actions"][1].update({"status": "running", "external_control_revision": 2})
    payload["run_history"][0]["status"] = "invoked"
    payload["run_history"][1]["status"] = "running"

    assert await runner._persist_event(
        app,
        event_id,
        payload,
        expected_action_id="action-a",
        expected_run_id="run-a",
    )

    stored = calendar_store.load_event(event_id)
    assert stored["actions"][0]["status"] == "invoked"
    assert stored["actions"][1] == current["actions"][1]
    receipts = {item["id"]: item for item in stored["run_history"]}
    assert receipts["receipt-a"]["status"] == "invoked"
    assert receipts["receipt-b"]["status"] == "cancel_requested"


def _save_authorized_tool_event(
    calendar_store,
    event_id,
    *,
    tool_name="remember",
    permissions=None,
    start_time=None,
    rrule=None,
    secret="private scheduled value",
):
    event = {
        "id": event_id,
        "title": "Authorization boundary",
        "start_time": start_time or time.time() - 5,
        "timezone": "UTC",
        "status": "scheduled",
        "background_job": {
            "execution": {"permissions": list(permissions or [])},
        },
        "actions": [
            {
                "id": f"{event_id}-action",
                "kind": "tool",
                "name": tool_name,
                "args": (
                    {"key": "authorization", "value": secret}
                    if tool_name == "remember"
                    else {}
                ),
                "status": "scheduled",
            }
        ],
    }
    if rrule:
        event["rrule"] = rrule
    calendar_store.save_event(event_id, event)


@pytest.mark.anyio
async def test_scheduled_tool_runner_executes_due_action(app_with_temp_stores):
    from app.utils import calendar_store, conversation_store
    from workers.scheduled_tool_runner import run_scheduled_tools_for_event

    app, dummy = app_with_temp_stores
    event_id = "ev-1"
    request_id = "rid-1"
    session_id = "s1"
    message_id = "m1"

    conversation_store.save_conversation(
        session_id,
        [
            {
                "id": message_id,
                "role": "ai",
                "text": "tool scheduled",
                "tools": [
                    {
                        "id": request_id,
                        "name": "remember",
                        "args": {"key": "k", "value": "v"},
                        "status": "scheduled",
                        "result": {"scheduled_event_id": event_id},
                    }
                ],
            }
        ],
    )

    calendar_store.save_event(
        event_id,
        {
            "id": event_id,
            "title": "Schedule tool: remember",
            "start_time": time.time() - 5,
            "timezone": "UTC",
            "status": "scheduled",
            "actions": [
                {
                    "id": request_id,
                    "request_id": request_id,
                    "kind": "tool",
                    "name": "remember",
                    "args": {"key": "k", "value": "v"},
                    "status": "scheduled",
                    "session_id": session_id,
                    "message_id": message_id,
                    "chain_id": message_id,
                }
            ],
        },
    )

    res = await run_scheduled_tools_for_event(app, event_id)
    assert res["status"] == "invoked"
    assert dummy.calls
    assert dummy.calls[-1]["name"] == "remember"

    stored_event = calendar_store.load_event(event_id)
    assert stored_event.get("status") == "prompted"
    action = stored_event.get("actions", [])[0]
    assert action.get("status") == "invoked"
    assert action.get("result") == "ok"

    receipt = app.state.work_run_store.list_runs(event_id=event_id)[0]
    assert receipt["status"] == "invoked"
    assert receipt["phase"] == "complete"
    assert receipt["event_count"] == 2
    assert [
        transition["phase"]
        for transition in app.state.work_run_store.list_events(receipt["id"])
    ] == ["tool", "complete"]

    stored_conv = conversation_store.load_conversation(session_id)
    tool = stored_conv[0]["tools"][0]
    assert tool["status"] == "invoked"
    assert tool["result"] == "ok"

    agents = (app.state.agent_console_state or {}).get("agents") or {}
    assert message_id in agents
    events = agents[message_id].get("events") or []
    tool_events = [
        e for e in events if e.get("type") == "tool" and e.get("id") == request_id
    ]
    assert tool_events
    assert tool_events[-1].get("status") == "invoked"


@pytest.mark.anyio
async def test_runner_does_not_invoke_tool_when_initial_ledger_write_fails(
    app_with_temp_stores,
):
    from app.utils import calendar_store
    from workers.scheduled_tool_runner import run_scheduled_tools_for_event

    app, dummy = app_with_temp_stores
    healthy_store = app.state.work_run_store

    class FailingWorkRunStore:
        def has_unresolved_effects(self, *args, **kwargs):
            return False

        def upsert(self, *args, **kwargs):
            raise OSError("ledger unavailable")

    app.state.work_run_store = FailingWorkRunStore()
    calendar_store.save_event(
        "ledger-gate",
        {
            "id": "ledger-gate",
            "title": "Ledger gate",
            "start_time": time.time() - 5,
            "timezone": "UTC",
            "status": "scheduled",
            "actions": [
                {
                    "id": "action-1",
                    "kind": "tool",
                    "name": "remember",
                    "args": {"key": "k", "value": "v"},
                    "status": "scheduled",
                }
            ],
        },
    )

    result = await run_scheduled_tools_for_event(app, "ledger-gate")

    assert result["status"] == "error"
    assert result["results"][0]["tool_invoked"] is False
    assert result["results"][0]["retryable"] is True
    assert result["results"][0]["state_delta_certainty"] == "confirmed_no_change"
    assert "was not invoked" in result["results"][0]["error"]
    assert dummy.calls == []

    released = calendar_store.load_event("ledger-gate")
    assert released["status"] == "scheduled"
    released_action = released["actions"][0]
    assert released_action["status"] == "scheduled"
    assert "run_id" not in released_action
    assert "running_occurrence_at" not in released_action
    assert "started_at" not in released_action
    assert "executed_at" not in released_action
    assert "last_occurrence_at" not in released_action
    assert released["run_history"][-1]["status"] == "error"
    assert released["run_history"][-1]["phase"] == "receipt_gate"
    assert released["run_history"][-1]["tool_invoked"] is False
    assert released["run_history"][-1]["state_delta_certainty"] == "confirmed_no_change"

    app.state.work_run_store = healthy_store
    retried = await run_scheduled_tools_for_event(app, "ledger-gate")

    assert retried["status"] == "invoked"
    assert len(dummy.calls) == 1
    assert dummy.calls[0]["name"] == "remember"


def test_unprojected_claim_release_preserves_edits_and_requires_exact_run_token(
    app_with_temp_stores,
):
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    _app, _dummy = app_with_temp_stores
    event_id = "ledger-release-cas"
    action_id = "ledger-release-action"
    occurrence_time = time.time() - 5
    calendar_store.save_event(
        event_id,
        {
            "id": event_id,
            "title": "Ledger release CAS",
            "start_time": occurrence_time,
            "timezone": "UTC",
            "status": "running",
            "actions": [
                {
                    "id": action_id,
                    "kind": "tool",
                    "name": "remember",
                    "args": {"key": "original", "value": "original"},
                    "status": "running",
                    "run_id": "run-expected",
                    "started_at": occurrence_time,
                    "running_occurrence_at": occurrence_time,
                }
            ],
        },
    )
    stale_snapshot = calendar_store.load_event(event_id)

    def edit_and_pause(latest):
        latest["status"] = "paused"
        latest["actions"][0]["args"] = {
            "key": "edited",
            "value": "preserve me",
        }
        latest["actions"][0]["prompt"] = "Edited while the ledger was unavailable."
        return latest

    calendar_store.update_event(event_id, edit_and_pause)

    assert runner._release_unprojected_claim(
        stale_snapshot,
        event_id=event_id,
        action_id=action_id,
        run_id="run-expected",
        occurrence_time=occurrence_time,
    )
    released = calendar_store.load_event(event_id)
    assert released["status"] == "paused"
    assert released["actions"][0]["status"] == "scheduled"
    assert released["actions"][0]["args"] == {
        "key": "edited",
        "value": "preserve me",
    }
    assert (
        released["actions"][0]["prompt"] == "Edited while the ledger was unavailable."
    )
    assert "last_occurrence_at" not in released["actions"][0]

    def replace_owner(latest):
        latest["status"] = "running"
        latest["actions"][0]["status"] = "running"
        latest["actions"][0]["run_id"] = "run-replacement"
        latest["actions"][0]["started_at"] = time.time()
        return latest

    calendar_store.update_event(event_id, replace_owner)

    assert not runner._release_unprojected_claim(
        stale_snapshot,
        event_id=event_id,
        action_id=action_id,
        run_id="run-expected",
        occurrence_time=occurrence_time,
    )
    replacement = calendar_store.load_event(event_id)
    assert replacement["status"] == "running"
    assert replacement["actions"][0]["status"] == "running"
    assert replacement["actions"][0]["run_id"] == "run-replacement"
    assert replacement["actions"][0]["args"]["key"] == "edited"


@pytest.mark.anyio
async def test_confirm_policy_blocks_then_consumes_exact_approval_and_reuses_receipt(
    app_with_temp_stores, monkeypatch
):
    from app.services.scheduled_action_authorization import apply_authorization_decision
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    _restore_real_scheduled_authorization(monkeypatch)
    app, dummy = app_with_temp_stores
    event_id = "authorization-confirm"
    secret = "SECRET-SCHEDULED-AUTH-CONTENT"
    _save_authorized_tool_event(
        calendar_store,
        event_id,
        permissions=["memory.write"],
        secret=secret,
    )

    blocked = await runner.run_scheduled_tools_for_event(app, event_id)
    repeated = await runner.run_scheduled_tools_for_event(app, event_id)

    assert blocked["status"] == "authorization_required"
    assert repeated["status"] == "authorization_required"
    assert blocked["results"][0]["tool_invoked"] is False
    assert dummy.calls == []
    pending = calendar_store.load_event(event_id)
    pending_action = pending["actions"][0]
    authorization = pending_action["authorization"]
    assert pending_action["status"] == "authorization_required"
    assert authorization["status"] == "authorization_required"
    assert authorization["missing_scopes"] == []
    assert len(pending["run_history"]) == 1
    pending_receipt = pending["run_history"][0]
    assert pending_receipt["status"] == "authorization_required"
    assert pending_receipt["phase"] == "authorization"
    assert pending_receipt["tool_invoked"] is False
    assert pending_receipt["state_delta_certainty"] == "confirmed_no_change"
    assert app.state.work_run_store.count_effects(pending_receipt["id"]) == 0
    assert secret not in json.dumps(pending_receipt)
    activity_receipt = app.state.work_run_store.get(pending_receipt["id"])
    assert activity_receipt["status"] == "authorization_required"
    assert activity_receipt["recovery_state"] == "attention"
    assert activity_receipt["authorization"]["id"] == authorization["id"]
    assert secret not in json.dumps(activity_receipt)

    apply_authorization_decision(
        event_id,
        pending_action["id"],
        decision="approve_once",
        authorization_id=authorization["id"],
        request_digest=authorization["request_digest"],
        occurrence_at=authorization["occurrence_at"],
    )
    invoked = await runner.run_scheduled_tools_for_event(app, event_id)

    assert invoked["status"] == "invoked"
    assert len(dummy.calls) == 1
    stored = calendar_store.load_event(event_id)
    stored_action = stored["actions"][0]
    assert stored_action["authorization"]["status"] == "consumed"
    assert len(stored["run_history"]) == 1
    final_receipt = stored["run_history"][0]
    assert final_receipt["id"] == pending_receipt["id"]
    assert final_receipt["run_id"] == pending_receipt["run_id"]
    assert final_receipt["status"] == "invoked"
    effect = app.state.work_run_store.list_effects(final_receipt["id"])[0]
    assert effect["approval_snapshot"]["status"] == "approved_once"
    assert effect["approval_snapshot"]["method"] == "approve_once"
    assert effect["permission_snapshot"]["status"] == "granted"
    assert effect["permission_snapshot"]["scopes"] == ["memory.write"]
    assert effect["permission_snapshot"]["grant_id"] == authorization["id"]
    assert secret not in json.dumps(effect)

    forced = await runner.run_scheduled_tools_for_event(app, event_id, force=True)
    assert forced["status"] == "authorization_required"
    assert forced["results"][0]["tool_invoked"] is False
    assert len(dummy.calls) == 1


@pytest.mark.anyio
async def test_projection_failure_restores_unconsumed_approval_and_stable_run(
    app_with_temp_stores, monkeypatch
):
    from app.services.scheduled_action_authorization import apply_authorization_decision
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    _restore_real_scheduled_authorization(monkeypatch)
    app, dummy = app_with_temp_stores
    healthy_store = app.state.work_run_store
    event_id = "authorization-ledger-retry"
    _save_authorized_tool_event(calendar_store, event_id, permissions=["memory.write"])
    blocked = await runner.run_scheduled_tools_for_event(app, event_id)
    pending = calendar_store.load_event(event_id)
    pending_action = pending["actions"][0]
    pending_authorization = pending_action["authorization"]
    pending_receipt = pending["run_history"][0]
    assert blocked["status"] == "authorization_required"

    apply_authorization_decision(
        event_id,
        pending_action["id"],
        decision="approve_once",
        authorization_id=pending_authorization["id"],
        request_digest=pending_authorization["request_digest"],
        occurrence_at=pending_authorization["occurrence_at"],
    )

    class FailingWorkRunStore:
        def has_unresolved_effects(self, *args, **kwargs):
            return False

        def upsert(self, *args, **kwargs):
            raise OSError("ledger unavailable")

    app.state.work_run_store = FailingWorkRunStore()
    failed = await runner.run_scheduled_tools_for_event(app, event_id)

    assert failed["status"] == "error"
    assert failed["results"][0]["tool_invoked"] is False
    assert dummy.calls == []
    released = calendar_store.load_event(event_id)
    released_action = released["actions"][0]
    assert released_action["status"] == "authorization_approved"
    assert released_action["authorization"]["status"] == "approved_once"
    assert released_action["run_id"] == pending_receipt["run_id"]
    assert released["run_history"][0]["id"] == pending_receipt["id"]
    assert released["run_history"][0]["status"] == "error"
    assert released["run_history"][0]["tool_invoked"] is False

    app.state.work_run_store = healthy_store
    retried = await runner.run_scheduled_tools_for_event(app, event_id)

    assert retried["status"] == "invoked"
    assert len(dummy.calls) == 1
    stored = calendar_store.load_event(event_id)
    assert len(stored["run_history"]) == 1
    assert stored["run_history"][0]["id"] == pending_receipt["id"]
    assert stored["run_history"][0]["run_id"] == pending_receipt["run_id"]
    assert stored["actions"][0]["authorization"]["status"] == "consumed"


@pytest.mark.anyio
async def test_pending_authorization_projection_retries_on_later_healthy_tick(
    app_with_temp_stores, monkeypatch
):
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    _restore_real_scheduled_authorization(monkeypatch)
    app, dummy = app_with_temp_stores
    healthy_store = app.state.work_run_store
    event_id = "authorization-card-retry"
    _save_authorized_tool_event(calendar_store, event_id, permissions=["memory.write"])

    class FailingWorkRunStore:
        def has_unresolved_effects(self, *args, **kwargs):
            return False

        def upsert(self, *args, **kwargs):
            raise OSError("ledger unavailable")

    app.state.work_run_store = FailingWorkRunStore()
    failed = await runner.run_scheduled_tools_for_event(app, event_id)

    assert failed["status"] == "authorization_required"
    assert failed["results"][0]["receipt_durable"] is False
    assert failed["results"][0]["retryable"] is True
    assert dummy.calls == []
    released = calendar_store.load_event(event_id)
    released_action = released["actions"][0]
    pending_receipt = released["run_history"][0]
    assert released["status"] == "scheduled"
    assert released_action["status"] == "scheduled"
    assert released_action["authorization"]["status"] == "authorization_required"
    assert runner._event_has_due_action(released, now=time.time()) is True
    assert healthy_store.get(pending_receipt["id"]) is None

    app.state.work_run_store = healthy_store
    ran = await runner.run_due_scheduled_tools_once(app)

    assert ran == 0
    assert dummy.calls == []
    stored = calendar_store.load_event(event_id)
    assert stored["actions"][0]["status"] == "authorization_required"
    assert len(stored["run_history"]) == 1
    assert stored["run_history"][0]["id"] == pending_receipt["id"]
    assert stored["run_history"][0]["run_id"] == pending_receipt["run_id"]
    activity = healthy_store.get(pending_receipt["id"])
    assert activity["status"] == "authorization_required"
    assert activity["recovery_state"] == "attention"
    assert healthy_store.count_effects(pending_receipt["id"]) == 0


@pytest.mark.anyio
async def test_missing_scope_and_force_both_fail_closed(
    app_with_temp_stores, monkeypatch
):
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    _restore_real_scheduled_authorization(monkeypatch)
    app, dummy = app_with_temp_stores
    event_id = "authorization-missing-scope"
    _save_authorized_tool_event(calendar_store, event_id, permissions=[])

    blocked = await runner.run_scheduled_tools_for_event(app, event_id)
    forced = await runner.run_scheduled_tools_for_event(app, event_id, force=True)

    assert blocked["status"] == "authorization_required"
    assert forced["status"] == "authorization_required"
    assert blocked["results"][0]["tool_invoked"] is False
    assert forced["results"][0]["tool_invoked"] is False
    assert dummy.calls == []
    stored = calendar_store.load_event(event_id)
    assert stored["actions"][0]["authorization"]["missing_scopes"] == ["memory.write"]
    assert all(receipt["tool_invoked"] is False for receipt in stored["run_history"])
    assert all(
        app.state.work_run_store.count_effects(receipt["id"]) == 0
        for receipt in stored["run_history"]
    )


@pytest.mark.anyio
async def test_catalog_auto_tool_still_requires_its_configured_scope(
    app_with_temp_stores, monkeypatch
):
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    _restore_real_scheduled_authorization(monkeypatch)
    app, dummy = app_with_temp_stores
    _save_authorized_tool_event(
        calendar_store,
        "authorization-auto",
        tool_name="help",
        permissions=["help.read"],
    )
    _save_authorized_tool_event(
        calendar_store,
        "authorization-auto-missing",
        tool_name="help",
        permissions=[],
    )

    allowed = await runner.run_scheduled_tools_for_event(app, "authorization-auto")
    blocked = await runner.run_scheduled_tools_for_event(
        app, "authorization-auto-missing"
    )

    assert allowed["status"] == "invoked"
    assert blocked["status"] == "authorization_required"
    assert [call["name"] for call in dummy.calls] == ["help"]
    allowed_action = calendar_store.load_event("authorization-auto")["actions"][0]
    assert allowed_action["authorization"]["status"] == "catalog_auto"
    blocked_action = calendar_store.load_event("authorization-auto-missing")["actions"][
        0
    ]
    assert blocked_action["authorization"]["missing_scopes"] == ["help.read"]


@pytest.mark.anyio
async def test_pending_recurrence_expires_before_later_occurrence_request(
    app_with_temp_stores, monkeypatch
):
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    _restore_real_scheduled_authorization(monkeypatch)
    app, dummy = app_with_temp_stores
    current = [1_900_000_000.0]
    start = current[0] - 60
    monkeypatch.setattr(runner.time, "time", lambda: current[0])
    _save_authorized_tool_event(
        calendar_store,
        "authorization-recurrence",
        permissions=["memory.write"],
        start_time=start,
        rrule="FREQ=MINUTELY;INTERVAL=1;COUNT=4",
    )

    first = await runner.run_scheduled_tools_for_event(app, "authorization-recurrence")
    first_stored = calendar_store.load_event("authorization-recurrence")
    first_receipt = dict(first_stored["run_history"][0])
    assert first["status"] == "authorization_required"
    assert runner._event_has_due_action(first_stored, now=current[0]) is False

    current[0] += 60
    assert runner._event_has_due_action(first_stored, now=current[0]) is True
    second = await runner.run_scheduled_tools_for_event(app, "authorization-recurrence")

    assert second["status"] == "authorization_required"
    assert dummy.calls == []
    stored = calendar_store.load_event("authorization-recurrence")
    assert len(stored["run_history"]) == 2
    expired, pending = stored["run_history"]
    assert expired["id"] == first_receipt["id"]
    assert expired["status"] == "authorization_expired"
    assert expired["authorization"]["status"] == "expired"
    assert pending["status"] == "authorization_required"
    assert pending["id"] != expired["id"]
    assert pending["occurrence_at"] > expired["occurrence_at"]


@pytest.mark.anyio
async def test_scheduled_tool_runner_runs_prompt_followup(
    app_with_temp_stores, monkeypatch
):
    from app import routes as routes_module
    from app.utils import calendar_store, conversation_store
    from workers.scheduled_tool_runner import run_scheduled_tools_for_event

    app, _dummy = app_with_temp_stores
    event_id = "ev-prompt"
    request_id = "rid-prompt"
    session_id = "s-prompt"
    message_id = "m-prompt"

    def fake_generate(*_args, **_kwargs):
        return {"text": "Follow-up response", "thought": ""}

    monkeypatch.setattr(routes_module.llm_service, "generate", fake_generate)

    conversation_store.save_conversation(
        session_id,
        [
            {
                "id": message_id,
                "role": "ai",
                "text": "tool scheduled",
                "tools": [
                    {
                        "id": request_id,
                        "name": "remember",
                        "args": {"key": "k", "value": "v"},
                        "status": "scheduled",
                        "result": {"scheduled_event_id": event_id},
                    }
                ],
            }
        ],
    )

    calendar_store.save_event(
        event_id,
        {
            "id": event_id,
            "title": "Schedule tool: remember",
            "start_time": time.time() - 5,
            "timezone": "UTC",
            "status": "scheduled",
            "actions": [
                {
                    "id": request_id,
                    "request_id": request_id,
                    "kind": "tool",
                    "name": "remember",
                    "args": {"key": "k", "value": "v"},
                    "status": "scheduled",
                    "session_id": session_id,
                    "message_id": message_id,
                    "chain_id": message_id,
                    "prompt": "Say something about the result.",
                }
            ],
        },
    )

    res = await run_scheduled_tools_for_event(app, event_id)
    assert res["status"] == "invoked"

    stored_conv = conversation_store.load_conversation(session_id)
    assert any(
        entry.get("role") == "user"
        and entry.get("text") == "Say something about the result."
        for entry in stored_conv
        if isinstance(entry, dict)
    )
    assert any(
        entry.get("role") == "ai" and entry.get("text") == "Follow-up response"
        for entry in stored_conv
        if isinstance(entry, dict)
    )

    agents = (app.state.agent_console_state or {}).get("agents") or {}
    assert message_id in agents
    events = agents[message_id].get("events") or []
    content_events = [e for e in events if e.get("type") == "content"]
    assert len(content_events) == 2
    assert content_events[0].get("content") == "Scheduled prompt running."
    assert content_events[-1].get("content") == "Follow-up response"
    assert [event["metadata"]["run_status"] for event in content_events] == [
        "active",
        "complete",
    ]
    assert (
        content_events[0]["metadata"]["run_id"]
        == content_events[-1]["metadata"]["run_id"]
    )
    assert content_events[0].get("agent_status") == "active"
    assert content_events[-1].get("agent_status") == "complete"


@pytest.mark.anyio
async def test_scheduled_tool_runner_routes_new_chat_followup_to_task_conversation(
    app_with_temp_stores, monkeypatch
):
    from app import routes as routes_module
    from app.utils import calendar_store, conversation_store
    from workers.scheduled_tool_runner import run_scheduled_tools_for_event

    app, _dummy = app_with_temp_stores
    event_id = "ev-new-chat"
    request_id = "rid-new-chat"
    session_id = "s-origin"
    message_id = "m-origin"

    def fake_generate(*_args, **_kwargs):
        return {"text": "New chat follow-up response", "thought": ""}

    monkeypatch.setattr(routes_module.llm_service, "generate", fake_generate)

    conversation_store.save_conversation(
        session_id,
        [
            {
                "id": message_id,
                "role": "ai",
                "text": "tool scheduled",
                "tools": [
                    {
                        "id": request_id,
                        "name": "remember",
                        "args": {"key": "k", "value": "v"},
                        "status": "scheduled",
                        "result": {"scheduled_event_id": event_id},
                    }
                ],
            }
        ],
    )

    calendar_store.save_event(
        event_id,
        {
            "id": event_id,
            "title": "Schedule tool: remember",
            "start_time": time.time() - 5,
            "timezone": "UTC",
            "status": "scheduled",
            "actions": [
                {
                    "id": request_id,
                    "request_id": request_id,
                    "kind": "tool",
                    "name": "remember",
                    "args": {"key": "k", "value": "v"},
                    "status": "scheduled",
                    "prompt": "Write the follow-up in a new task chat.",
                    "conversation_mode": "new_chat",
                    "session_id": session_id,
                    "message_id": message_id,
                    "chain_id": message_id,
                }
            ],
        },
    )

    res = await run_scheduled_tools_for_event(app, event_id)
    assert res["status"] == "invoked"

    stored_event = calendar_store.load_event(event_id)
    stored_action = stored_event.get("actions", [])[0]
    generated_session = stored_action.get("session_id")
    assert isinstance(generated_session, str)
    assert generated_session.startswith("task-")
    assert generated_session != session_id
    assert stored_action.get("conversation_mode") == "new_chat"
    assert stored_action.get("origin_session_id") == session_id
    assert stored_action.get("origin_message_id") == message_id
    receipt = stored_event["run_history"][-1]
    assert receipt["ownership"]["conversation_id"] == session_id
    assert receipt["ownership"]["message_id"] == message_id

    original_conv = conversation_store.load_conversation(session_id)
    assert not any(
        entry.get("role") == "user"
        and entry.get("text") == "Write the follow-up in a new task chat."
        for entry in original_conv
        if isinstance(entry, dict)
    )
    generated_conv = conversation_store.load_conversation(generated_session)
    generated_meta = conversation_store.get_metadata(generated_session)
    assert generated_meta["provenance"]["kind"] == "subchat"
    assert generated_meta["provenance"]["parent_session_id"] == session_id
    assert generated_meta["provenance"]["parent_message_id"] == message_id
    assert generated_meta["handoff"]["summary"] == "Schedule tool: remember"
    assert any(
        entry.get("role") == "user"
        and entry.get("text") == "Write the follow-up in a new task chat."
        for entry in generated_conv
        if isinstance(entry, dict)
    )
    assert any(
        entry.get("role") == "ai" and entry.get("text") == "New chat follow-up response"
        for entry in generated_conv
        if isinstance(entry, dict)
    )


@pytest.mark.anyio
async def test_scheduled_tool_runner_runs_prompt_action(
    app_with_temp_stores, monkeypatch
):
    from app import routes as routes_module
    from app.utils import calendar_store, conversation_store
    from workers.scheduled_tool_runner import run_scheduled_tools_for_event

    app, _dummy = app_with_temp_stores
    event_id = "ev-prompt-only"
    action_id = "act-prompt-only"
    session_id = "s-prompt-only"
    message_id = "m-prompt-only"
    generated_contexts = []

    def fake_generate(*_args, **_kwargs):
        generated_contexts.append(_kwargs["context"])
        return {"text": "Prompt-only response", "thought": ""}

    monkeypatch.setattr(routes_module.llm_service, "generate", fake_generate)

    conversation_store.save_conversation(
        session_id,
        [
            {
                "id": message_id,
                "role": "ai",
                "text": "ready",
            }
        ],
    )

    calendar_store.save_event(
        event_id,
        {
            "id": event_id,
            "title": "Prompt-only task",
            "start_time": time.time() - 5,
            "timezone": "UTC",
            "status": "scheduled",
            "actions": [
                {
                    "id": action_id,
                    "kind": "prompt",
                    "prompt": "Write a summary.",
                    "status": "scheduled",
                    "session_id": session_id,
                    "message_id": message_id,
                    "chain_id": message_id,
                }
            ],
        },
    )

    res = await run_scheduled_tools_for_event(app, event_id)
    assert res["status"] == "invoked"
    assert len(generated_contexts) == 1
    prompt_messages = [
        message
        for message in generated_contexts[0].messages
        if message.get("role") == "user"
        and message.get("content") == "Write a summary."
    ]
    assert len(prompt_messages) == 1

    stored_event = calendar_store.load_event(event_id)
    assert stored_event.get("status") == "prompted"
    stored_action = stored_event.get("actions", [])[0]
    assert stored_action.get("status") == "prompted"
    assert stored_action.get("result") == "Prompt-only response"

    stored_conv = conversation_store.load_conversation(session_id)
    assert any(
        entry.get("role") == "user" and entry.get("text") == "Write a summary."
        for entry in stored_conv
        if isinstance(entry, dict)
    )
    assert any(
        entry.get("role") == "ai" and entry.get("text") == "Prompt-only response"
        for entry in stored_conv
        if isinstance(entry, dict)
    )

    agents = (app.state.agent_console_state or {}).get("agents") or {}
    assert message_id in agents
    events = agents[message_id].get("events") or []
    content_events = [e for e in events if e.get("type") == "content"]
    assert len(content_events) == 2
    assert content_events[0].get("content") == "Scheduled prompt running."
    assert content_events[-1].get("content") == "Prompt-only response"
    assert [event["metadata"]["run_status"] for event in content_events] == [
        "active",
        "complete",
    ]
    assert (
        content_events[0]["metadata"]["run_id"]
        == content_events[-1]["metadata"]["run_id"]
    )
    assert agents[message_id]["status"] == "complete"
    assert agents[message_id]["provenance"]["kind"] == "scheduled_prompt"
    assert agents[message_id]["provenance"]["source_event_id"] == event_id


@pytest.mark.anyio
async def test_scheduled_prompt_error_closes_active_console_run(
    app_with_temp_stores, monkeypatch
):
    from app import routes as routes_module
    from app.utils import conversation_store
    from workers.scheduled_tool_runner import _run_prompt_action

    app, _dummy = app_with_temp_stores
    event_id = "ev-prompt-error"
    action_id = "act-prompt-error"
    session_id = "s-prompt-error"
    message_id = "m-prompt-error"

    def fail_generate(*_args, **_kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(routes_module.llm_service, "generate", fail_generate)
    monkeypatch.setattr(
        app.state.work_run_store,
        "record_attempt",
        lambda record: record,
    )
    conversation_store.save_conversation(
        session_id,
        [{"id": message_id, "role": "ai", "text": "ready"}],
    )

    with pytest.raises(RuntimeError, match="provider prompt failed"):
        await _run_prompt_action(
            app,
            event={"id": event_id, "title": "Prompt failure"},
            session_id=session_id,
            chain_id=message_id,
            prompt="Write a summary.",
            event_id=event_id,
            action_id=action_id,
        )

    agents = (app.state.agent_console_state or {}).get("agents") or {}
    events = agents[message_id].get("events") or []
    content_events = [event for event in events if event.get("type") == "content"]
    assert len(content_events) == 2
    assert [event["metadata"]["run_status"] for event in content_events] == [
        "active",
        "error",
    ]
    assert (
        content_events[0]["metadata"]["run_id"]
        == content_events[-1]["metadata"]["run_id"]
    )
    assert (
        content_events[-1]
        .get("content", "")
        .startswith("(scheduled prompt failed) provider prompt failed: provider_error")
    )
    assert content_events[-1].get("agent_status") == "error"
    assert agents[message_id]["status"] == "error"


@pytest.mark.anyio
async def test_prompt_generation_failure_records_error_receipt(
    app_with_temp_stores, monkeypatch
):
    from app import routes as routes_module
    from app.utils import calendar_store
    from workers.scheduled_tool_runner import run_scheduled_tools_for_event

    app, _dummy = app_with_temp_stores

    def fail_generate(*_args, **_kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(routes_module.llm_service, "generate", fail_generate)
    calendar_store.save_event(
        "failed-prompt",
        {
            "id": "failed-prompt",
            "title": "Failed prompt",
            "start_time": time.time() - 5,
            "timezone": "UTC",
            "status": "scheduled",
            "actions": [
                {
                    "id": "failed-prompt-action",
                    "kind": "prompt",
                    "prompt": "Run the check.",
                    "status": "scheduled",
                }
            ],
        },
    )

    result = await run_scheduled_tools_for_event(app, "failed-prompt")

    assert result["status"] == "error"
    stored = calendar_store.load_event("failed-prompt")
    assert stored["actions"][0]["status"] == "error"
    assert stored["run_history"][-1]["status"] == "error"
    assert stored["run_history"][-1]["summary"] == "prompt ended with status error."
    assert "provider unavailable" not in stored["run_history"][-1]["summary"]


@pytest.mark.anyio
async def test_scheduled_tool_runner_normalizes_legacy_continue_prompt_action(
    app_with_temp_stores, monkeypatch
):
    from app import routes as routes_module
    from app.utils import calendar_store, conversation_store
    from workers.scheduled_tool_runner import run_scheduled_tools_for_event

    app, _dummy = app_with_temp_stores
    event_id = "ev-legacy-prompt"

    def fake_generate(*_args, **_kwargs):
        return {"text": "Legacy prompt response", "thought": ""}

    monkeypatch.setattr(routes_module.llm_service, "generate", fake_generate)

    calendar_store.save_event(
        event_id,
        {
            "id": event_id,
            "title": "Legacy prompt task",
            "start_time": time.time() - 5,
            "timezone": "UTC",
            "status": "scheduled",
            "actions": [
                {
                    "id": "legacy-1",
                    "type": "continue_prompt",
                    "prompt": "Continue from the stored task.",
                }
            ],
        },
    )

    res = await run_scheduled_tools_for_event(app, event_id)
    assert res["status"] == "invoked"

    stored_event = calendar_store.load_event(event_id)
    stored_action = stored_event["actions"][0]
    assert stored_action.get("status") == "prompted"
    assert stored_action.get("result") == "Legacy prompt response"

    generated_session = stored_action.get("session_id")
    assert isinstance(generated_session, str) and generated_session.startswith("task-")
    stored_conv = conversation_store.load_conversation(generated_session)
    assert any(
        entry.get("role") == "ai" and entry.get("text") == "Legacy prompt response"
        for entry in stored_conv
        if isinstance(entry, dict)
    )


@pytest.mark.anyio
async def test_scheduled_tool_runner_executes_each_recurring_occurrence_once(
    app_with_temp_stores, monkeypatch
):
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    app, dummy = app_with_temp_stores
    event_id = "ev-recurring"
    action_id = "repeat-remember"
    current = [1_900_000_000.0]
    start = current[0] - 120
    monkeypatch.setattr(runner.time, "time", lambda: current[0])

    calendar_store.save_event(
        event_id,
        {
            "id": event_id,
            "title": "Recurring review",
            "start_time": start,
            "timezone": "UTC",
            "rrule": "FREQ=MINUTELY;INTERVAL=1;COUNT=4",
            "status": "scheduled",
            "background_job": {
                "patience": {"stop_condition": "until_useful", "max_attempts": 3},
                "ownership": {"conversation_id": "owner-thread"},
            },
            "actions": [
                {
                    "id": action_id,
                    "kind": "tool",
                    "name": "remember",
                    "args": {"key": "recurring", "value": "ok"},
                    "status": "scheduled",
                }
            ],
        },
    )

    first = await runner.run_scheduled_tools_for_event(app, event_id)
    duplicate = await runner.run_scheduled_tools_for_event(app, event_id)
    current[0] += 60
    second = await runner.run_scheduled_tools_for_event(app, event_id)

    assert first["status"] == "invoked"
    assert duplicate["results"][0]["status"] == "already_executed"
    assert second["status"] == "invoked"
    assert len(dummy.calls) == 2

    stored_event = calendar_store.load_event(event_id)
    assert stored_event["status"] == "scheduled"
    history = stored_event["run_history"]
    assert len(history) == 2
    assert history[0]["occurrence_at"] != history[1]["occurrence_at"]
    assert history[1]["ownership"]["calendar_event_id"] == event_id
    assert history[1]["ownership"]["conversation_id"] == "owner-thread"
    assert history[1]["patience"]["stop_condition"] == "until_useful"


@pytest.mark.anyio
async def test_long_job_does_not_block_another_calendar_event(
    app_with_temp_stores, monkeypatch
):
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    app, _dummy = app_with_temp_stores
    slow_started = asyncio.Event()
    release_slow = asyncio.Event()

    async def fake_prompt_action(_app, *, event, **_kwargs):
        if event.get("id") == "slow-job":
            slow_started.set()
            await release_slow.wait()
        return f"completed {event.get('id')}"

    monkeypatch.setattr(runner, "_run_prompt_action", fake_prompt_action)
    for event_id in ("slow-job", "fast-job"):
        calendar_store.save_event(
            event_id,
            {
                "id": event_id,
                "title": event_id,
                "start_time": time.time() - 5,
                "timezone": "UTC",
                "status": "scheduled",
                "actions": [
                    {
                        "id": f"{event_id}-prompt",
                        "kind": "prompt",
                        "prompt": "Run the check.",
                        "status": "scheduled",
                    }
                ],
            },
        )

    slow_task = asyncio.create_task(
        runner.run_scheduled_tools_for_event(app, "slow-job")
    )
    await asyncio.wait_for(slow_started.wait(), timeout=1)
    fast_result = await asyncio.wait_for(
        runner.run_scheduled_tools_for_event(app, "fast-job"), timeout=1
    )
    release_slow.set()
    slow_result = await asyncio.wait_for(slow_task, timeout=1)

    assert fast_result["status"] == "invoked"
    assert slow_result["status"] == "invoked"


@pytest.mark.anyio
async def test_dispatcher_starts_later_event_while_earlier_job_is_running(
    app_with_temp_stores, monkeypatch
):
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    app, _dummy = app_with_temp_stores
    slow_started = asyncio.Event()
    fast_finished = asyncio.Event()
    release_slow = asyncio.Event()
    runner._ACTIVE_EVENT_TASKS.clear()

    async def fake_run(_app, event_id, **_kwargs):
        if event_id == "slow-overnight":
            slow_started.set()
            await release_slow.wait()
        else:
            fast_finished.set()
        return {
            "status": "invoked",
            "event_id": event_id,
            "results": [{"status": "prompted"}],
        }

    monkeypatch.setattr(runner, "run_scheduled_tools_for_event", fake_run)

    def save_due_event(event_id):
        calendar_store.save_event(
            event_id,
            {
                "id": event_id,
                "title": event_id,
                "start_time": time.time() - 1,
                "timezone": "UTC",
                "status": "scheduled",
                "actions": [
                    {
                        "id": f"{event_id}-prompt",
                        "kind": "prompt",
                        "prompt": "Run the check.",
                        "status": "scheduled",
                    }
                ],
            },
        )

    try:
        save_due_event("slow-overnight")
        assert await runner.dispatch_due_scheduled_tools(app) == 1
        await asyncio.wait_for(slow_started.wait(), timeout=1)

        save_due_event("later-fast-check")
        assert await runner.dispatch_due_scheduled_tools(app) == 1
        await asyncio.wait_for(fast_finished.wait(), timeout=1)
        assert not runner._ACTIVE_EVENT_TASKS["slow-overnight"].done()
    finally:
        release_slow.set()
        await asyncio.gather(
            *list(runner._ACTIVE_EVENT_TASKS.values()), return_exceptions=True
        )
        await asyncio.sleep(0)
        runner._ACTIVE_EVENT_TASKS.clear()


@pytest.mark.anyio
async def test_stale_running_action_becomes_a_terminal_interruption_receipt(
    app_with_temp_stores,
):
    from app.utils import calendar_store
    from workers.scheduled_tool_runner import run_scheduled_tools_for_event

    app, dummy = app_with_temp_stores
    now = time.time()
    calendar_store.save_event(
        "stale-running",
        {
            "id": "stale-running",
            "title": "Stale running action",
            "start_time": now - 10,
            "timezone": "UTC",
            "status": "running",
            "background_job": {"patience": {"max_runtime_seconds": 30}},
            "actions": [
                {
                    "id": "stale-action",
                    "kind": "tool",
                    "name": "remember",
                    "args": {"key": "stale", "value": "no retry"},
                    "status": "running",
                    "started_at": now - 120,
                }
            ],
        },
    )

    result = await run_scheduled_tools_for_event(app, "stale-running")

    assert result["status"] == "error"
    assert dummy.calls == []
    stored = calendar_store.load_event("stale-running")
    assert stored["actions"][0]["status"] == "interrupted_unknown"
    assert stored["run_history"][-1]["status"] == "interrupted_unknown"
    assert stored["run_history"][-1]["summary"] == (
        "remember ended with status interrupted_unknown."
    )


@pytest.mark.anyio
async def test_cancelled_active_action_is_not_stranded_running(
    app_with_temp_stores, monkeypatch
):
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    app, _dummy = app_with_temp_stores
    invoked = asyncio.Event()

    async def wait_forever(*_args, **_kwargs):
        invoked.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(runner, "_invoke_tool", wait_forever)
    calendar_store.save_event(
        "cancelled-active",
        {
            "id": "cancelled-active",
            "title": "Cancelled active action",
            "start_time": time.time() - 5,
            "timezone": "UTC",
            "status": "scheduled",
            "actions": [
                {
                    "id": "cancelled-action",
                    "kind": "tool",
                    "name": "remember",
                    "args": {"key": "cancelled", "value": "true"},
                    "status": "scheduled",
                }
            ],
        },
    )
    task = asyncio.create_task(
        runner.run_scheduled_tools_for_event(app, "cancelled-active")
    )
    await asyncio.wait_for(invoked.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    stored = calendar_store.load_event("cancelled-active")
    assert stored["actions"][0]["status"] == "interrupted_unknown"
    assert stored["run_history"][-1]["status"] == "interrupted_unknown"
    assert stored["run_history"][-1]["summary"] == (
        "remember ended with status interrupted_unknown."
    )


@pytest.mark.anyio
async def test_user_pause_is_not_overwritten_by_active_run(
    app_with_temp_stores, monkeypatch
):
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    app, _dummy = app_with_temp_stores
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_prompt(*_args, **_kwargs):
        started.set()
        await release.wait()
        return "finished"

    monkeypatch.setattr(runner, "_run_prompt_action", slow_prompt)
    event_id = "controlled-pause"
    calendar_store.save_event(
        event_id,
        {
            "id": event_id,
            "title": "Controlled active action",
            "start_time": time.time() - 5,
            "timezone": "UTC",
            "status": "scheduled",
            "actions": [
                {
                    "id": "controlled-action",
                    "kind": "prompt",
                    "prompt": "Run the check.",
                    "status": "scheduled",
                }
            ],
        },
    )
    task = asyncio.create_task(runner.run_scheduled_tools_for_event(app, event_id))
    await asyncio.wait_for(started.wait(), timeout=1)
    latest = calendar_store.load_event(event_id)
    latest["status"] = "paused"
    latest["actions"][0]["prompt"] = "Edited while running."
    calendar_store.save_event(event_id, latest)
    release.set()
    await asyncio.wait_for(task, timeout=1)

    stored = calendar_store.load_event(event_id)
    assert stored["status"] == "paused"
    assert stored["actions"][0]["prompt"] == "Edited while running."


@pytest.mark.anyio
async def test_active_run_blocks_low_level_event_deletion(
    app_with_temp_stores, monkeypatch
):
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    app, _dummy = app_with_temp_stores
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_prompt(*_args, **_kwargs):
        started.set()
        await release.wait()
        return "finished"

    async def no_reindex(*_args, **_kwargs):
        return None

    monkeypatch.setattr(runner, "_run_prompt_action", slow_prompt)
    monkeypatch.setattr(runner, "_reindex_calendar_event", no_reindex)
    event_id = "guarded-delete"
    calendar_store.save_event(
        event_id,
        {
            "id": event_id,
            "title": "Guarded active action",
            "start_time": time.time() - 5,
            "timezone": "UTC",
            "status": "scheduled",
            "actions": [
                {
                    "id": "guarded-action",
                    "kind": "prompt",
                    "prompt": "Run the check.",
                    "status": "scheduled",
                }
            ],
        },
    )
    task = asyncio.create_task(runner.run_scheduled_tools_for_event(app, event_id))
    await asyncio.wait_for(started.wait(), timeout=1)

    with pytest.raises(calendar_store.CalendarEventActiveRunError):
        calendar_store.delete_event(event_id)
    assert calendar_store.load_event(event_id)["actions"][0]["status"] == "running"

    release.set()
    await asyncio.wait_for(task, timeout=1)
    assert calendar_store.load_event(event_id)["actions"][0]["status"] == "prompted"


def test_authorization_approved_blocks_low_level_event_deletion(
    app_with_temp_stores,
):
    from app.utils import calendar_store

    event_id = "approved-delete-guard"
    calendar_store.save_event(
        event_id,
        {
            "id": event_id,
            "title": "Approved action",
            "status": "authorization_approved",
            "actions": [
                {
                    "id": "approved-action",
                    "kind": "prompt",
                    "status": "authorization_approved",
                }
            ],
        },
    )

    with pytest.raises(calendar_store.CalendarEventActiveRunError):
        calendar_store.delete_event(event_id)
    assert calendar_store.load_event(event_id)["id"] == event_id


@pytest.mark.anyio
async def test_failed_running_claim_prevents_tool_side_effect(
    app_with_temp_stores, monkeypatch
):
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    app, dummy = app_with_temp_stores
    calendar_store.save_event(
        "claim-failure",
        {
            "id": "claim-failure",
            "title": "Claim failure",
            "start_time": time.time() - 5,
            "timezone": "UTC",
            "status": "scheduled",
            "actions": [
                {
                    "id": "claim-action",
                    "kind": "tool",
                    "name": "remember",
                    "args": {"key": "unsafe", "value": "must not run"},
                    "status": "scheduled",
                }
            ],
        },
    )

    def fail_update(*_args, **_kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(calendar_store, "update_event", fail_update)

    with pytest.raises(OSError, match="disk unavailable"):
        await runner.run_scheduled_tools_for_event(app, "claim-failure")
    assert dummy.calls == []


@pytest.mark.anyio
async def test_tool_followup_failure_replaces_receipt_with_partial_error(
    app_with_temp_stores, monkeypatch
):
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    app, dummy = app_with_temp_stores

    async def failed_followup(*_args, **_kwargs):
        return {"status": "error", "error": "provider unavailable"}

    monkeypatch.setattr(runner, "_run_prompt_followup", failed_followup)
    calendar_store.save_event(
        "followup-failure",
        {
            "id": "followup-failure",
            "title": "Follow-up failure",
            "start_time": time.time() - 5,
            "timezone": "UTC",
            "status": "scheduled",
            "actions": [
                {
                    "id": "followup-action",
                    "kind": "tool",
                    "name": "remember",
                    "args": {"key": "ran", "value": "true"},
                    "prompt": "Summarize the result.",
                    "status": "scheduled",
                }
            ],
        },
    )

    result = await runner.run_scheduled_tools_for_event(app, "followup-failure")

    assert result["status"] == "error"
    assert result["results"][0]["tool_invoked"] is True
    assert dummy.calls
    stored = calendar_store.load_event("followup-failure")
    assert len(stored["run_history"]) == 1
    assert stored["run_history"][0]["status"] == "error"
    assert stored["run_history"][0]["summary"] == "remember ended with status error."


@pytest.mark.anyio
async def test_restart_resumes_durable_followup_without_reinvoking_tool(
    app_with_temp_stores, monkeypatch
):
    from app import routes as routes_module
    from app.utils import calendar_store, conversation_store
    from workers import scheduled_tool_runner as runner

    app, dummy = app_with_temp_stores
    event_id = "recover-followup"
    action_id = "recover-action"
    session_id = "recover-session"
    message_id = "recover-message"
    start = time.time() - 5

    conversation_store.save_conversation(
        session_id,
        [{"id": message_id, "role": "ai", "text": "Scheduled work"}],
    )
    calendar_store.save_event(
        event_id,
        {
            "id": event_id,
            "title": "Recover follow-up",
            "start_time": start,
            "timezone": "UTC",
            "status": "scheduled",
            "actions": [
                {
                    "id": action_id,
                    "kind": "tool",
                    "name": "remember",
                    "args": {"key": "once", "value": "only once"},
                    "prompt": "Summarize the durable result.",
                    "status": "scheduled",
                    "session_id": session_id,
                    "message_id": message_id,
                    "chain_id": message_id,
                }
            ],
        },
    )

    original_resume = runner._resume_pending_tool_followup

    async def simulate_exit_before_followup(*_args, **_kwargs):
        raise RuntimeError("simulated worker exit")

    monkeypatch.setattr(
        runner, "_resume_pending_tool_followup", simulate_exit_before_followup
    )
    with pytest.raises(RuntimeError, match="simulated worker exit"):
        await runner.run_scheduled_tools_for_event(app, event_id)

    pending = calendar_store.load_event(event_id)
    pending_action = pending["actions"][0]
    pending_receipt = pending["run_history"][0]
    original_run_id = pending_action["run_id"]
    original_receipt_id = pending_receipt["id"]
    assert len(dummy.calls) == 1
    assert pending_action["status"] == "followup_pending"
    assert pending_action["result"] == "ok"
    assert pending_action["followup_status"] == "pending"
    assert pending_action["followup_message_id"] == runner._stable_composite_id(
        "scheduled-message",
        event_id,
        action_id,
        original_run_id,
        "tool-followup",
    )
    assert pending_receipt["status"] == "followup_pending"
    assert pending_receipt["phase"] == "awaiting_followup"
    assert pending_receipt["finished_at"] is None
    assert pending_receipt["recovery_count"] == 0

    app.state.work_run_store.record_attempt(
        {
            "id": f"{original_receipt_id}:provider-followup:1",
            "receipt_id": original_receipt_id,
            "run_id": original_run_id,
            "step_id": f"{original_run_id}:provider-followup",
            "attempt_number": 1,
            "status": "running",
            "started_at": time.time() - 120,
        }
    )

    def fake_generate(*_args, **_kwargs):
        return {"text": "Recovered follow-up", "thought": ""}

    monkeypatch.setattr(routes_module.llm_service, "generate", fake_generate)
    monkeypatch.setattr(runner, "_resume_pending_tool_followup", original_resume)
    runner._EVENT_RUN_LOCKS.clear()

    result = await runner.run_scheduled_tools_for_event(app, event_id)

    assert result["status"] == "invoked"
    assert len(dummy.calls) == 1
    stored = calendar_store.load_event(event_id)
    action = stored["actions"][0]
    assert action["run_id"] == original_run_id
    assert action["status"] == "invoked"
    assert action["followup_status"] == "complete"
    assert action["recovery_count"] == 1
    assert action["recovered_at"] >= pending_action["executed_at"]
    assert len(stored["run_history"]) == 1
    receipt = stored["run_history"][0]
    assert receipt["id"] == original_receipt_id
    assert receipt["run_id"] == original_run_id
    assert receipt["status"] == "invoked"
    assert receipt["phase"] == "followup"
    assert receipt["followup_status"] == "complete"
    assert receipt["recovery_count"] == 1
    assert receipt["recovered_at"] == action["recovered_at"]
    assert receipt["finished_at"] >= receipt["started_at"]
    durable_receipt = app.state.work_run_store.get(original_receipt_id)
    assert durable_receipt["recovery_state"] == "terminal"
    assert durable_receipt["recovery_count"] == 1
    assert durable_receipt["tool_invoked"] is True
    assert [
        transition["phase"]
        for transition in app.state.work_run_store.list_events(original_receipt_id)
    ] == ["tool", "awaiting_followup", "followup", "followup"]

    conversation = conversation_store.load_conversation(session_id)
    followup_id = action["followup_message_id"]
    assert sum(item.get("id") == f"{followup_id}:user" for item in conversation) == 1
    assert sum(item.get("id") == followup_id for item in conversation) == 1
    assert (
        next(item for item in conversation if item.get("id") == followup_id)["text"]
        == "Recovered follow-up"
    )
    attempts = app.state.work_run_store.list_attempts(original_receipt_id)
    assert [item["status"] for item in attempts] == [
        "interrupted_unknown",
        "complete",
    ]
    assert attempts[0]["retry_reason_code"] == "worker_restart"
    assert attempts[1]["retry_of_attempt_id"] == attempts[0]["id"]


@pytest.mark.anyio
async def test_completed_provider_attempt_retries_missing_output_checkpoint(
    app_with_temp_stores, monkeypatch
):
    from app import routes as routes_module
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    app, dummy = app_with_temp_stores
    event_id = "recover-complete-provider"
    action_id = "recover-complete-action"
    calendar_store.save_event(
        event_id,
        {
            "id": event_id,
            "title": "Recover completed provider generation",
            "start_time": time.time() - 5,
            "timezone": "UTC",
            "status": "scheduled",
            "background_job": {"patience": {"max_provider_retries": 2}},
            "actions": [
                {
                    "id": action_id,
                    "kind": "tool",
                    "name": "remember",
                    "args": {"key": "once", "value": "only once"},
                    "prompt": "Summarize the durable result.",
                    "status": "scheduled",
                }
            ],
        },
    )
    original_resume = runner._resume_pending_tool_followup

    async def stop_before_provider(*_args, **_kwargs):
        raise RuntimeError("simulated exit before provider")

    monkeypatch.setattr(runner, "_resume_pending_tool_followup", stop_before_provider)
    with pytest.raises(RuntimeError, match="simulated exit"):
        await runner.run_scheduled_tools_for_event(app, event_id)

    pending = calendar_store.load_event(event_id)
    pending_action = pending["actions"][0]
    receipt_id = pending["run_history"][0]["id"]
    run_id = pending_action["run_id"]
    first_attempt_id = runner._stable_composite_id(
        "attempt", receipt_id, "provider-followup", 1
    )
    app.state.work_run_store.record_attempt(
        {
            "id": first_attempt_id,
            "receipt_id": receipt_id,
            "run_id": run_id,
            "step_id": runner._stable_composite_id("step", run_id, "provider-followup"),
            "attempt_number": 1,
            "status": "complete",
            "started_at": time.time() - 2,
            "finished_at": time.time() - 1,
        }
    )
    contexts = []

    def replacement_generate(*_args, **kwargs):
        contexts.append(kwargs["context"])
        return {"text": "Replacement canonical output", "thought": ""}

    monkeypatch.setattr(routes_module.llm_service, "generate", replacement_generate)
    monkeypatch.setattr(runner, "_resume_pending_tool_followup", original_resume)
    runner._EVENT_RUN_LOCKS.clear()

    result = await runner.run_scheduled_tools_for_event(app, event_id)

    assert result["status"] == "invoked"
    assert len(dummy.calls) == 1
    assert len(contexts) == 1
    attempts = app.state.work_run_store.list_attempts(receipt_id)
    assert [item["status"] for item in attempts] == ["complete", "complete"]
    assert attempts[1]["retry_of_attempt_id"] == first_attempt_id
    assert attempts[1]["retry_reason_code"] == ("provider_output_checkpoint_missing")
    envelope = "\n".join(
        str(message.get("content") or "") for message in contexts[0].messages
    )
    assert "Prior generation completed" in envelope
    assert "worker_restart_after_generation" in envelope
    assert pending_action["followup_message_id"] in envelope
    assert "will be updated in place" in envelope
    assert "do not invoke the tool" in envelope


@pytest.mark.anyio
async def test_complete_canonical_followup_repairs_running_attempt_without_regeneration(
    app_with_temp_stores, monkeypatch
):
    from app import routes as routes_module
    from app.utils import calendar_store, conversation_store
    from workers import scheduled_tool_runner as runner

    app, dummy = app_with_temp_stores
    event_id = "recover-canonical-followup"
    action_id = "recover-canonical-action"
    _save_journaled_tool_event(calendar_store, event_id)
    pending_event = calendar_store.load_event(event_id)
    pending_event["actions"][0]["id"] = action_id
    calendar_store.save_event(event_id, pending_event)

    original_resume = runner._resume_pending_tool_followup

    async def stop_before_provider(*_args, **_kwargs):
        raise RuntimeError("simulated exit before provider")

    monkeypatch.setattr(runner, "_resume_pending_tool_followup", stop_before_provider)
    with pytest.raises(RuntimeError, match="simulated exit"):
        await runner.run_scheduled_tools_for_event(app, event_id)

    pending = calendar_store.load_event(event_id)
    pending_action = pending["actions"][0]
    receipt_id = pending["run_history"][0]["id"]
    followup_id = pending_action["followup_message_id"]
    attempt_id = runner._stable_composite_id(
        "attempt", receipt_id, "provider-followup", 1
    )
    app.state.work_run_store.record_attempt(
        {
            "id": attempt_id,
            "receipt_id": receipt_id,
            "run_id": pending_action["run_id"],
            "step_id": runner._stable_composite_id(
                "step", pending_action["run_id"], "provider-followup"
            ),
            "attempt_number": 1,
            "status": "running",
            "started_at": time.time() - 1,
        }
    )
    conversation = conversation_store.load_conversation(pending_action["session_id"])
    conversation.append(
        {
            "id": followup_id,
            "role": "ai",
            "text": "Canonical output survived",
            "thought": "",
            "metadata": {"status": "complete", "scheduled": True},
            "timestamp": time.time(),
        }
    )
    conversation_store.save_conversation(pending_action["session_id"], conversation)

    provider_calls = 0

    def must_not_generate(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("canonical output must prevent regeneration")

    monkeypatch.setattr(routes_module.llm_service, "generate", must_not_generate)
    monkeypatch.setattr(runner, "_resume_pending_tool_followup", original_resume)
    runner._EVENT_RUN_LOCKS.clear()

    result = await runner.run_scheduled_tools_for_event(app, event_id)

    assert result["status"] == "invoked"
    assert len(dummy.calls) == 1
    assert provider_calls == 0
    attempts = app.state.work_run_store.list_attempts(receipt_id)
    assert len(attempts) == 1
    assert attempts[0]["id"] == attempt_id
    assert attempts[0]["status"] == "complete"
    assert attempts[0]["retry_reason_code"] == "canonical_output_recovered"
    assert attempts[0]["checkpoint_status"] == "canonical_output_durable"
    stored_conversation = conversation_store.load_conversation(
        pending_action["session_id"]
    )
    canonical_entries = [
        item for item in stored_conversation if item.get("id") == followup_id
    ]
    assert len(canonical_entries) == 1
    assert canonical_entries[0]["text"] == "Canonical output survived"


@pytest.mark.anyio
async def test_pending_followup_claim_is_atomic_across_worker_snapshots(
    app_with_temp_stores, monkeypatch
):
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    app, dummy = app_with_temp_stores
    started = asyncio.Event()
    release = asyncio.Event()
    occurrence = time.time() - 5

    async def blocked_followup(*_args, **_kwargs):
        started.set()
        await release.wait()
        return {"status": "complete", "result": "done"}

    monkeypatch.setattr(runner, "_run_prompt_followup", blocked_followup)
    calendar_store.save_event(
        "atomic-followup",
        {
            "id": "atomic-followup",
            "title": "Atomic follow-up",
            "start_time": occurrence,
            "timezone": "UTC",
            "status": "running",
            "actions": [
                {
                    "id": "atomic-followup-action",
                    "kind": "tool",
                    "name": "remember",
                    "args": {"key": "already", "value": "done"},
                    "prompt": "Summarize it.",
                    "status": "followup_pending",
                    "run_id": "run-stable-followup",
                    "external_control_revision": 0,
                    "run_control_revision": 0,
                    "started_at": occurrence,
                    "executed_at": occurrence + 1,
                    "running_occurrence_at": occurrence,
                    "result": "durable tool result",
                    "followup_status": "pending",
                    "followup_prompt": "Summarize it.",
                    "followup_tool_name": "remember",
                    "followup_tool_args": {"key": "already", "value": "done"},
                    "followup_message_id": "stable-followup-message",
                }
            ],
        },
    )
    first_snapshot = calendar_store.load_event("atomic-followup")
    second_snapshot = calendar_store.load_event("atomic-followup")

    first_task = asyncio.create_task(
        runner._run_tool_action(
            app,
            event_id="atomic-followup",
            event=first_snapshot,
            action=runner._iter_actions(first_snapshot)[0],
            action_id="atomic-followup-action",
            occurrence_time=occurrence,
            force=False,
        )
    )
    await asyncio.wait_for(started.wait(), timeout=20)
    second_result = await runner._run_tool_action(
        app,
        event_id="atomic-followup",
        event=second_snapshot,
        action=runner._iter_actions(second_snapshot)[0],
        action_id="atomic-followup-action",
        occurrence_time=occurrence,
        force=False,
    )
    release.set()
    first_result = await asyncio.wait_for(first_task, timeout=10)

    assert second_result["status"] == "already_claimed"
    assert second_result["tool_invoked"] is True
    assert first_result["status"] == "invoked"
    assert dummy.calls == []
    stored = calendar_store.load_event("atomic-followup")
    assert stored["actions"][0]["run_id"] == "run-stable-followup"
    assert stored["actions"][0]["followup_status"] == "complete"
    assert len(stored["run_history"]) == 1


@pytest.mark.anyio
async def test_runtime_limit_cancels_and_records_hung_action(
    app_with_temp_stores, monkeypatch
):
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    app, _dummy = app_with_temp_stores

    async def hang(*_args, **_kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(runner, "_invoke_tool", hang)
    monkeypatch.setattr(runner, "_event_runtime_limit_seconds", lambda _event: 0.02)
    calendar_store.save_event(
        "runtime-limit",
        {
            "id": "runtime-limit",
            "title": "Runtime limit",
            "start_time": time.time() - 5,
            "timezone": "UTC",
            "status": "scheduled",
            "background_job": {"patience": {"max_runtime_seconds": 30}},
            "actions": [
                {
                    "id": "runtime-action",
                    "kind": "tool",
                    "name": "remember",
                    "args": {"key": "hung", "value": "true"},
                    "status": "scheduled",
                }
            ],
        },
    )

    result = await runner.run_scheduled_tools_for_event(app, "runtime-limit")

    assert result["status"] == "error"
    stored = calendar_store.load_event("runtime-limit")
    assert stored["actions"][0]["status"] == "interrupted_unknown"
    assert stored["run_history"][-1]["status"] == "interrupted_unknown"
    assert stored["run_history"][-1]["summary"] == (
        "remember ended with status interrupted_unknown."
    )


@pytest.mark.anyio
async def test_atomic_claim_blocks_independent_worker_snapshot(
    app_with_temp_stores, monkeypatch
):
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    app, _dummy = app_with_temp_stores
    started = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def blocked_invoke(_app, *, name, args, **_kwargs):
        calls.append((name, args))
        started.set()
        await release.wait()
        return "complete"

    monkeypatch.setattr(runner, "_invoke_tool", blocked_invoke)
    start = time.time() - 5
    calendar_store.save_event(
        "cross-worker-claim",
        {
            "id": "cross-worker-claim",
            "title": "Cross worker claim",
            "start_time": start,
            "timezone": "UTC",
            "status": "scheduled",
            "actions": [
                {
                    "id": "claimed-action",
                    "kind": "tool",
                    "name": "remember",
                    "args": {"key": "one", "value": "only"},
                    "status": "scheduled",
                }
            ],
        },
    )
    first_snapshot = calendar_store.load_event("cross-worker-claim")
    second_snapshot = calendar_store.load_event("cross-worker-claim")
    first_action = runner._iter_actions(first_snapshot)[0]
    second_action = runner._iter_actions(second_snapshot)[0]

    first_task = asyncio.create_task(
        runner._run_tool_action(
            app,
            event_id="cross-worker-claim",
            event=first_snapshot,
            action=first_action,
            action_id="claimed-action",
            occurrence_time=start,
            force=False,
        )
    )
    # A cold shared RAG/index service can make the durable claim projection
    # take several seconds before the test reaches the blocked invocation.
    await asyncio.wait_for(started.wait(), timeout=20)
    second_result = await runner._run_tool_action(
        app,
        event_id="cross-worker-claim",
        event=second_snapshot,
        action=second_action,
        action_id="claimed-action",
        occurrence_time=start,
        force=False,
    )
    release.set()
    first_result = await asyncio.wait_for(first_task, timeout=1)

    assert first_result["status"] == "invoked"
    assert second_result["status"] == "already_claimed"
    assert calls == [("remember", {"key": "one", "value": "only"})]
    assert len(calendar_store.load_event("cross-worker-claim")["run_history"]) == 1


@pytest.mark.anyio
async def test_claim_refreshes_edited_tool_definition(
    app_with_temp_stores, monkeypatch
):
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    app, _dummy = app_with_temp_stores
    invoked = []

    async def capture_invoke(_app, *, name, args, **_kwargs):
        invoked.append((name, args))
        return "new result"

    monkeypatch.setattr(runner, "_invoke_tool", capture_invoke)
    start = time.time() - 5
    original = {
        "id": "edited-definition",
        "title": "Edited definition",
        "start_time": start,
        "timezone": "UTC",
        "status": "scheduled",
        "actions": [
            {
                "id": "edited-action",
                "kind": "tool",
                "name": "old-tool",
                "args": {"value": "old"},
                "status": "scheduled",
            }
        ],
    }
    calendar_store.save_event("edited-definition", original)
    stale = calendar_store.load_event("edited-definition")
    latest = calendar_store.load_event("edited-definition")
    latest["actions"][0]["name"] = "new-tool"
    latest["actions"][0]["args"] = {"value": "new"}
    calendar_store.save_event("edited-definition", latest)

    result = await runner._run_tool_action(
        app,
        event_id="edited-definition",
        event=stale,
        action=runner._iter_actions(stale)[0],
        action_id="edited-action",
        occurrence_time=start,
        force=False,
    )

    assert result["status"] == "invoked"
    assert invoked == [("new-tool", {"value": "new"})]
    receipt = calendar_store.load_event("edited-definition")["run_history"][0]
    assert receipt["action_name"] == "new-tool"


@pytest.mark.anyio
async def test_force_runs_keep_distinct_receipts(app_with_temp_stores):
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    app, _dummy = app_with_temp_stores
    calendar_store.save_event(
        "force-receipts",
        {
            "id": "force-receipts",
            "title": "Force receipts",
            "start_time": time.time() - 5,
            "timezone": "UTC",
            "status": "scheduled",
            "actions": [
                {
                    "id": "force-action",
                    "kind": "tool",
                    "name": "remember",
                    "args": {"key": "forced", "value": "twice"},
                    "status": "scheduled",
                }
            ],
        },
    )

    first = await runner.run_scheduled_tools_for_event(
        app, "force-receipts", force=True
    )
    second = await runner.run_scheduled_tools_for_event(
        app, "force-receipts", force=True
    )

    assert first["status"] == "invoked"
    assert second["status"] == "invoked"
    receipts = calendar_store.load_event("force-receipts")["run_history"]
    assert len(receipts) == 2
    assert len({receipt["id"] for receipt in receipts}) == 2
    assert len({receipt["run_id"] for receipt in receipts}) == 2


@pytest.mark.anyio
async def test_cancel_during_running_publication_records_receipt(
    app_with_temp_stores, monkeypatch
):
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    app, dummy = app_with_temp_stores
    publishing = asyncio.Event()

    async def blocked_publish(*_args, **_kwargs):
        publishing.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(runner, "_publish_tool_status", blocked_publish)
    calendar_store.save_event(
        "cancel-publish",
        {
            "id": "cancel-publish",
            "title": "Cancel publish",
            "start_time": time.time() - 5,
            "timezone": "UTC",
            "status": "scheduled",
            "actions": [
                {
                    "id": "cancel-publish-action",
                    "kind": "tool",
                    "name": "remember",
                    "args": {"key": "never", "value": "invoked"},
                    "status": "scheduled",
                }
            ],
        },
    )
    task = asyncio.create_task(
        runner.run_scheduled_tools_for_event(app, "cancel-publish")
    )
    await asyncio.wait_for(publishing.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    stored = calendar_store.load_event("cancel-publish")
    assert stored["actions"][0]["status"] == "interrupted_unknown"
    assert stored["run_history"][-1]["status"] == "interrupted_unknown"
    assert dummy.calls == []


def _save_journaled_tool_event(
    calendar_store,
    event_id,
    *,
    prompt="Summarize the result.",
    patience=None,
    args=None,
):
    event = {
        "id": event_id,
        "title": f"Journaled {event_id}",
        "start_time": time.time() - 5,
        "timezone": "UTC",
        "status": "scheduled",
        "actions": [
            {
                "id": f"{event_id}-action",
                "kind": "tool",
                "name": "remember",
                "args": args or {"key": "journal", "value": "once"},
                "prompt": prompt,
                "status": "scheduled",
            }
        ],
    }
    if patience is not None:
        event["background_job"] = {"patience": patience}
    calendar_store.save_event(event_id, event)


def test_background_job_patience_validates_provider_retry_budget():
    from app.schemas import BackgroundJobPatience
    from pydantic import ValidationError

    assert BackgroundJobPatience().max_provider_retries == 2
    assert BackgroundJobPatience(max_provider_retries=0).max_provider_retries == 0
    with pytest.raises(ValidationError):
        BackgroundJobPatience(max_provider_retries=11)


def test_returned_provider_error_does_not_persist_untrusted_category():
    from workers.scheduled_tool_runner import _provider_response_error

    classified = _provider_response_error(
        {
            "text": "fallback",
            "metadata": {
                "error": "private failure",
                "category": "SECRET-TENANT-AND-ENDPOINT",
            },
        }
    )

    assert classified == ("provider_error", "provider_error_return", False)


def test_composite_ids_hash_oversized_calendar_identity():
    from workers.scheduled_tool_runner import _stable_composite_id

    short = _stable_composite_id("receipt", "event", "action", "run")
    assert short.startswith("receipt:sha256:")
    oversized = _stable_composite_id(
        "receipt", "event-" + "e" * 400, "action-" + "a" * 400, "run"
    )
    assert oversized.startswith("receipt:sha256:")
    assert len(oversized) < 512
    assert oversized == _stable_composite_id(
        "receipt", "event-" + "e" * 400, "action-" + "a" * 400, "run"
    )
    assert _stable_composite_id("receipt", "a:b", "c") != _stable_composite_id(
        "receipt", "a", "b:c"
    )


def test_run_record_reuses_legacy_receipt_id_for_same_run():
    from workers.scheduled_tool_runner import _append_run_record

    action = {
        "id": "action",
        "kind": "tool",
        "name": "remember",
        "run_id": "run-existing",
        "started_at": 10.0,
    }
    event = {
        "id": "event",
        "title": "Legacy receipt",
        "run_history": [
            {
                "id": "legacy-readable-receipt",
                "run_id": "run-existing",
                "action_id": "action",
                "started_at": 10.0,
            }
        ],
    }

    record = _append_run_record(
        event,
        event_id="event",
        action=action,
        action_id="action",
        occurrence_time=5.0,
        result={"status": "running"},
    )

    assert record["id"] == "legacy-readable-receipt"
    assert action["work_run_receipt_id"] == "legacy-readable-receipt"
    assert len(event["run_history"]) == 1


@pytest.mark.anyio
async def test_transient_followup_retry_reuses_tool_and_adds_recovery_envelope(
    app_with_temp_stores, monkeypatch
):
    from app import routes as routes_module
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    app, dummy = app_with_temp_stores
    contexts = []

    def flaky_generate(*_args, **kwargs):
        contexts.append(kwargs["context"])
        if len(contexts) == 1:
            raise TimeoutError("private provider timeout detail")
        return {"text": "Recovered safely", "thought": ""}

    monkeypatch.setattr(routes_module.llm_service, "generate", flaky_generate)
    _save_journaled_tool_event(
        calendar_store,
        "provider-retry",
        patience={"max_provider_retries": 2},
    )

    result = await runner.run_scheduled_tools_for_event(app, "provider-retry")

    assert result["status"] == "invoked"
    assert len(dummy.calls) == 1
    receipt_id = calendar_store.load_event("provider-retry")["run_history"][0]["id"]
    attempts = app.state.work_run_store.list_attempts(receipt_id)
    assert [item["status"] for item in attempts] == ["retry_scheduled", "complete"]
    assert attempts[1]["retry_of_attempt_id"] == attempts[0]["id"]
    assert attempts[1]["effect_watermark_digest"]
    assert "private provider timeout detail" not in json.dumps(attempts)
    assert app.state.work_run_store.count_effects(receipt_id) == 1
    retry_context = "\n".join(
        str(message.get("content") or "") for message in contexts[1].messages
    )
    assert "Retry 2 of 3 provider attempt(s)" in retry_context
    assert "Prior provider error: provider_timeout" in retry_context
    assert "tool reported success" in retry_context
    assert "not independent verification" in retry_context
    assert "No other durable state changes were recorded" in retry_context
    assert "do not invoke the tool" in retry_context
    followup_message_id = calendar_store.load_event("provider-retry")["actions"][0][
        "followup_message_id"
    ]
    assert followup_message_id in retry_context
    assert "pending assistant" in retry_context
    assert "updated in place" in retry_context


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("event_id", "error_factory", "max_retries", "provider_calls"),
    [
        ("provider-nonretryable", lambda: RuntimeError("invalid request"), 2, 1),
        ("provider-exhausted", lambda: TimeoutError("still down"), 1, 2),
    ],
)
async def test_followup_retry_stops_for_nonretryable_or_exhausted_errors(
    app_with_temp_stores,
    monkeypatch,
    event_id,
    error_factory,
    max_retries,
    provider_calls,
):
    from app import routes as routes_module
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    app, dummy = app_with_temp_stores
    calls = 0

    def failed_generate(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise error_factory()

    monkeypatch.setattr(routes_module.llm_service, "generate", failed_generate)
    _save_journaled_tool_event(
        calendar_store,
        event_id,
        patience={"max_provider_retries": max_retries},
    )

    result = await runner.run_scheduled_tools_for_event(app, event_id)

    assert result["status"] == "error"
    assert len(dummy.calls) == 1
    assert calls == provider_calls
    receipt_id = calendar_store.load_event(event_id)["run_history"][0]["id"]
    attempts = app.state.work_run_store.list_attempts(receipt_id)
    assert len(attempts) == provider_calls
    assert attempts[-1]["status"] == "error"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("event_id", "first_metadata", "should_retry"),
    [
        (
            "provider-return-timeout",
            {"error": "private timeout", "category": "timeout", "status_code": 504},
            True,
        ),
        (
            "provider-return-auth",
            {
                "error": "private auth detail",
                "category": "unauthorized",
                "status_code": 401,
            },
            False,
        ),
    ],
)
async def test_followup_classifies_returned_provider_errors(
    app_with_temp_stores,
    monkeypatch,
    event_id,
    first_metadata,
    should_retry,
):
    from app import routes as routes_module
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    app, dummy = app_with_temp_stores
    calls = 0

    def returned_error_then_success(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"text": "Provider error body", "metadata": first_metadata}
        return {"text": "Recovered from returned error", "thought": ""}

    monkeypatch.setattr(
        routes_module.llm_service, "generate", returned_error_then_success
    )
    _save_journaled_tool_event(
        calendar_store,
        event_id,
        patience={"max_provider_retries": 1},
    )

    result = await runner.run_scheduled_tools_for_event(app, event_id)

    assert len(dummy.calls) == 1
    assert calls == (2 if should_retry else 1)
    assert result["status"] == ("invoked" if should_retry else "error")
    receipt_id = calendar_store.load_event(event_id)["run_history"][0]["id"]
    attempts = app.state.work_run_store.list_attempts(receipt_id)
    assert [item["status"] for item in attempts] == (
        ["retry_scheduled", "complete"] if should_retry else ["error"]
    )
    assert "private" not in json.dumps(attempts)


@pytest.mark.anyio
async def test_prompt_only_provider_retry_is_journaled_without_effect(
    app_with_temp_stores, monkeypatch
):
    from app import routes as routes_module
    from app.utils import calendar_store, conversation_store
    from workers import scheduled_tool_runner as runner

    app, dummy = app_with_temp_stores
    contexts = []

    def flaky_generate(*_args, **kwargs):
        contexts.append(kwargs["context"])
        if len(contexts) == 1:
            raise TimeoutError("prompt transport timeout")
        return {"text": "Prompt recovered", "thought": ""}

    monkeypatch.setattr(routes_module.llm_service, "generate", flaky_generate)
    calendar_store.save_event(
        "prompt-provider-retry",
        {
            "id": "prompt-provider-retry",
            "title": "Prompt retry",
            "start_time": time.time() - 5,
            "timezone": "UTC",
            "status": "scheduled",
            "background_job": {"patience": {"max_provider_retries": 1}},
            "actions": [
                {
                    "id": "prompt-provider-action",
                    "kind": "prompt",
                    "prompt": "Write the report.",
                    "status": "scheduled",
                }
            ],
        },
    )

    result = await runner.run_scheduled_tools_for_event(app, "prompt-provider-retry")

    assert result["status"] == "invoked"
    assert dummy.calls == []
    receipt_id = calendar_store.load_event("prompt-provider-retry")["run_history"][0][
        "id"
    ]
    assert app.state.work_run_store.count_attempts(receipt_id) == 2
    assert app.state.work_run_store.count_effects(receipt_id) == 0
    stored_action = calendar_store.load_event("prompt-provider-retry")["actions"][0]
    expected_message_id = runner._stable_composite_id(
        "scheduled-message",
        "prompt-provider-retry",
        "prompt-provider-action",
        stored_action["run_id"],
        "prompt",
    )
    conversation = conversation_store.load_conversation(stored_action["session_id"])
    assert sum(item.get("id") == expected_message_id for item in conversation) == 1
    retry_context = "\n".join(
        str(message.get("content") or "") for message in contexts[1].messages
    )
    assert "dispatched no tool or external effect" in retry_context
    assert "No other durable state changes were recorded" in retry_context
    assert expected_message_id in retry_context
    assert "pending assistant" in retry_context
    assert "updated in place" in retry_context


def _save_restartable_prompt_event(calendar_store, event_id, *, prompt):
    calendar_store.save_event(
        event_id,
        {
            "id": event_id,
            "title": f"Restartable prompt {event_id}",
            "start_time": time.time() - 5,
            "timezone": "UTC",
            "status": "scheduled",
            "background_job": {"patience": {"max_provider_retries": 1}},
            "actions": [
                {
                    "id": f"{event_id}-action",
                    "kind": "prompt",
                    "prompt": prompt,
                    "status": "scheduled",
                    "session_id": f"{event_id}-session",
                    "message_id": f"{event_id}-message",
                    "chain_id": f"{event_id}-message",
                }
            ],
        },
    )


def _age_running_prompt(calendar_store, event_id):
    def age_latest(latest):
        latest["actions"][0]["started_at"] = time.time() - 10_000
        return latest

    assert calendar_store.update_event(event_id, age_latest)


@pytest.mark.anyio
async def test_prompt_restart_reclaims_same_checkpoint_after_provider_process_loss(
    app_with_temp_stores, monkeypatch
):
    from app import routes as routes_module
    from app.utils import calendar_store, conversation_store
    from workers import scheduled_tool_runner as runner

    class SimulatedProcessLoss(BaseException):
        pass

    app, dummy = app_with_temp_stores
    event_id = "prompt-process-loss"
    prompt = "Private prompt body that must stay out of the checkpoint."
    contexts = []

    def lose_process_during_generate(*_args, **kwargs):
        contexts.append(kwargs["context"])
        raise SimulatedProcessLoss()

    monkeypatch.setattr(
        routes_module.llm_service, "generate", lose_process_during_generate
    )
    _save_restartable_prompt_event(calendar_store, event_id, prompt=prompt)

    with pytest.raises(SimulatedProcessLoss):
        await runner.run_scheduled_tools_for_event(app, event_id)

    crashed = calendar_store.load_event(event_id)
    crashed_action = crashed["actions"][0]
    checkpoint = dict(crashed_action["prompt_checkpoint"])
    run_id = crashed_action["run_id"]
    receipt_id = checkpoint["receipt_id"]
    assert crashed_action["status"] == "running"
    assert set(checkpoint) == runner._PROMPT_CHECKPOINT_KEYS
    assert checkpoint["schema_version"] == 1
    assert prompt not in json.dumps(checkpoint)
    assert all(len(str(value)) <= 512 for value in checkpoint.values())
    assert crashed["run_history"][0]["id"] == receipt_id
    attempts = app.state.work_run_store.list_attempts(receipt_id)
    assert [item["status"] for item in attempts] == ["running"]
    assert attempts[0]["checkpoint_id"] == checkpoint["checkpoint_id"]
    conversation = conversation_store.load_conversation(checkpoint["session_id"])
    assert (
        sum(item.get("id") == checkpoint["user_message_id"] for item in conversation)
        == 1
    )
    assert (
        sum(item.get("id") == checkpoint["output_message_id"] for item in conversation)
        == 1
    )
    assert dummy.calls == []
    assert app.state.work_run_store.count_effects(receipt_id) == 0

    _age_running_prompt(calendar_store, event_id)

    def recover_generate(*_args, **kwargs):
        contexts.append(kwargs["context"])
        return {"text": "Recovered after restart", "thought": ""}

    monkeypatch.setattr(routes_module.llm_service, "generate", recover_generate)
    runner._EVENT_RUN_LOCKS.clear()
    result = await runner.run_scheduled_tools_for_event(app, event_id)

    assert result["status"] == "invoked"
    assert len(contexts) == 2
    stored = calendar_store.load_event(event_id)
    action = stored["actions"][0]
    assert action["status"] == "prompted"
    assert action["result"] == "Recovered after restart"
    assert action["run_id"] == run_id
    assert action["prompt_checkpoint"] == checkpoint
    assert stored["run_history"][0]["id"] == receipt_id
    attempts = app.state.work_run_store.list_attempts(receipt_id)
    assert [item["status"] for item in attempts] == [
        "interrupted_unknown",
        "complete",
    ]
    assert attempts[1]["retry_of_attempt_id"] == attempts[0]["id"]
    assert attempts[1]["checkpoint_id"] == checkpoint["checkpoint_id"]
    recovery_context = "\n".join(
        str(message.get("content") or "") for message in contexts[1].messages
    )
    assert "Retry 2 of 2 provider attempt(s)" in recovery_context
    assert "dispatched no tool or external effect" in recovery_context
    conversation = conversation_store.load_conversation(checkpoint["session_id"])
    assert (
        sum(item.get("id") == checkpoint["user_message_id"] for item in conversation)
        == 1
    )
    assert (
        sum(item.get("id") == checkpoint["output_message_id"] for item in conversation)
        == 1
    )
    assert dummy.calls == []
    assert app.state.work_run_store.count_effects(receipt_id) == 0


@pytest.mark.anyio
async def test_prompt_restart_uses_canonical_output_without_regeneration(
    app_with_temp_stores, monkeypatch
):
    from app import routes as routes_module
    from app.utils import calendar_store, conversation_store
    from workers import scheduled_tool_runner as runner

    class SimulatedProcessLoss(BaseException):
        pass

    app, dummy = app_with_temp_stores
    event_id = "prompt-canonical-process-loss"
    provider_calls = []

    def successful_generate(*_args, **_kwargs):
        provider_calls.append("generated")
        return {"text": "Canonical output survived", "thought": ""}

    monkeypatch.setattr(routes_module.llm_service, "generate", successful_generate)
    _save_restartable_prompt_event(
        calendar_store, event_id, prompt="Write a durable report."
    )
    original_prompt_action = runner._run_prompt_action

    async def lose_process_after_canonical_output(*args, **kwargs):
        await original_prompt_action(*args, **kwargs)
        raise SimulatedProcessLoss()

    monkeypatch.setattr(
        runner, "_run_prompt_action", lose_process_after_canonical_output
    )
    with pytest.raises(SimulatedProcessLoss):
        await runner.run_scheduled_tools_for_event(app, event_id)

    crashed = calendar_store.load_event(event_id)
    crashed_action = crashed["actions"][0]
    checkpoint = dict(crashed_action["prompt_checkpoint"])
    run_id = crashed_action["run_id"]
    receipt_id = checkpoint["receipt_id"]
    assert crashed_action["status"] == "running"
    output = next(
        item
        for item in conversation_store.load_conversation(checkpoint["session_id"])
        if item.get("id") == checkpoint["output_message_id"]
    )
    assert output["text"] == "Canonical output survived"
    assert output["metadata"]["status"] == "complete"
    assert [
        item["status"] for item in app.state.work_run_store.list_attempts(receipt_id)
    ] == ["complete"]

    _age_running_prompt(calendar_store, event_id)

    def must_not_generate(*_args, **_kwargs):
        raise AssertionError("canonical output recovery called the provider")

    monkeypatch.setattr(routes_module.llm_service, "generate", must_not_generate)
    monkeypatch.setattr(runner, "_run_prompt_action", original_prompt_action)
    runner._EVENT_RUN_LOCKS.clear()
    result = await runner.run_scheduled_tools_for_event(app, event_id)

    assert result["status"] == "invoked"
    assert provider_calls == ["generated"]
    stored = calendar_store.load_event(event_id)
    action = stored["actions"][0]
    assert action["status"] == "prompted"
    assert action["result"] == "Canonical output survived"
    assert action["run_id"] == run_id
    assert action["prompt_checkpoint"] == checkpoint
    assert stored["run_history"][0]["id"] == receipt_id
    assert [
        item["status"] for item in app.state.work_run_store.list_attempts(receipt_id)
    ] == ["complete"]
    conversation = conversation_store.load_conversation(checkpoint["session_id"])
    assert (
        sum(item.get("id") == checkpoint["user_message_id"] for item in conversation)
        == 1
    )
    assert (
        sum(item.get("id") == checkpoint["output_message_id"] for item in conversation)
        == 1
    )
    assert dummy.calls == []
    assert app.state.work_run_store.count_effects(receipt_id) == 0


@pytest.mark.anyio
async def test_changed_prompt_checkpoint_fails_closed_after_process_loss(
    app_with_temp_stores, monkeypatch
):
    from app import routes as routes_module
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    class SimulatedProcessLoss(BaseException):
        pass

    app, dummy = app_with_temp_stores
    event_id = "prompt-changed-checkpoint"
    provider_calls = []

    def lose_process_during_generate(*_args, **_kwargs):
        provider_calls.append("initial")
        raise SimulatedProcessLoss()

    monkeypatch.setattr(
        routes_module.llm_service, "generate", lose_process_during_generate
    )
    _save_restartable_prompt_event(
        calendar_store, event_id, prompt="Original durable prompt."
    )
    with pytest.raises(SimulatedProcessLoss):
        await runner.run_scheduled_tools_for_event(app, event_id)

    crashed = calendar_store.load_event(event_id)
    receipt_id = crashed["actions"][0]["prompt_checkpoint"]["receipt_id"]

    def change_prompt_and_age(latest):
        latest["actions"][0]["prompt"] = "Changed after the provider started."
        latest["actions"][0]["started_at"] = time.time() - 10_000
        return latest

    assert calendar_store.update_event(event_id, change_prompt_and_age)

    def must_not_generate(*_args, **_kwargs):
        provider_calls.append("unexpected")
        raise AssertionError("changed checkpoint dispatched the provider")

    monkeypatch.setattr(routes_module.llm_service, "generate", must_not_generate)
    runner._EVENT_RUN_LOCKS.clear()
    result = await runner.run_scheduled_tools_for_event(app, event_id)

    assert result["status"] == "reconcile_required"
    assert provider_calls == ["initial"]
    stored = calendar_store.load_event(event_id)
    assert stored["actions"][0]["status"] == "reconcile_required"
    assert stored["run_history"][0]["id"] == receipt_id
    assert stored["run_history"][0]["status"] == "reconcile_required"
    assert dummy.calls == []
    assert app.state.work_run_store.count_effects(receipt_id) == 0


@pytest.mark.anyio
async def test_cancellation_checkpoint_rejects_stale_run_token(
    app_with_temp_stores,
):
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    app, dummy = app_with_temp_stores
    event_id = "cancel-stale-run"
    action_id = "cancel-stale-action"
    occurrence = time.time() - 5
    calendar_store.save_event(
        event_id,
        {
            "id": event_id,
            "title": "Replacement run owns cancellation",
            "start_time": occurrence,
            "timezone": "UTC",
            "status": "running",
            "actions": [
                {
                    "id": action_id,
                    "kind": "prompt",
                    "prompt": "Replacement work",
                    "status": "running",
                    "run_id": "replacement-run",
                    "started_at": time.time(),
                    "running_occurrence_at": occurrence,
                    "cancel_requested": True,
                    "cancel_request_id": "replacement-cancel-request",
                    "cancel_requested_at": time.time(),
                }
            ],
        },
    )
    stale_action = {
        "id": action_id,
        "kind": "prompt",
        "status": "running",
        "run_id": "stale-run",
        "running_occurrence_at": occurrence,
    }
    stale_event = {
        "id": event_id,
        "status": "running",
        "actions": [stale_action],
    }

    result = await runner._checkpoint_cooperative_cancellation(
        app,
        event_id=event_id,
        event=stale_event,
        action=stale_action,
        action_id=action_id,
        occurrence_time=occurrence,
        phase="prompt",
        tool_invoked=False,
    )

    assert result["status"] == "already_claimed"
    stored = calendar_store.load_event(event_id)
    action = stored["actions"][0]
    assert action["status"] == "running"
    assert action["run_id"] == "replacement-run"
    assert action["cancel_requested"] is True
    assert "cancelled_at" not in action
    assert stored.get("run_history") in (None, [])
    assert dummy.calls == []


@pytest.mark.anyio
async def test_predispatch_checkpoint_retires_changed_definition_without_running_it(
    app_with_temp_stores,
):
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    app, dummy = app_with_temp_stores
    event_id = "changed-after-claim"
    action_id = "changed-action"
    occurrence = time.time() - 5
    current_action = {
        "id": action_id,
        "kind": "prompt",
        "prompt": "Latest definition",
        "status": "running",
        "run_id": "run-1",
        "started_at": time.time(),
        "running_occurrence_at": occurrence,
        "external_control_revision": 2,
        "run_control_revision": 1,
        "authorization": {
            "id": "authorization-1",
            "status": "consumed",
            "claim_run_id": "run-1",
        },
    }
    calendar_store.save_event(
        event_id,
        {
            "id": event_id,
            "title": "Changed after claim",
            "start_time": occurrence,
            "timezone": "UTC",
            "status": "running",
            "actions": [current_action],
        },
    )
    stale_action = {
        **current_action,
        "prompt": "Stale definition must not run",
        "external_control_revision": 1,
        "run_control_revision": 1,
    }
    stale_event = {
        "id": event_id,
        "title": "Changed after claim",
        "start_time": occurrence,
        "timezone": "UTC",
        "status": "running",
        "actions": [stale_action],
    }

    result = await runner._checkpoint_cooperative_cancellation(
        app,
        event_id=event_id,
        event=stale_event,
        action=stale_action,
        action_id=action_id,
        occurrence_time=occurrence,
        phase="prompt",
        tool_invoked=False,
    )

    assert result["status"] == "skipped"
    assert result["phase"] == "control_changed"
    assert result["state_delta_certainty"] == "confirmed_no_change"
    stored = calendar_store.load_event(event_id)
    action = stored["actions"][0]
    assert stored["status"] == "scheduled"
    assert action["prompt"] == "Latest definition"
    assert action["status"] == "scheduled"
    assert action["external_control_revision"] == 2
    assert action["authorization"]["status"] == "invalidated"
    assert "run_id" not in action
    assert "run_control_revision" not in action
    assert "last_occurrence_at" not in action
    assert stored["run_history"][0]["status"] == "skipped"
    assert stored["run_history"][0]["phase"] == "control_changed"
    assert dummy.calls == []


def test_prompt_claim_captures_control_revision_and_checkpoint_rejects_lineage_edit(
    app_with_temp_stores,
):
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    event_id = "prompt-control-baseline"
    action_id = "prompt-action"
    occurrence = time.time() - 5
    calendar_store.save_event(
        event_id,
        {
            "id": event_id,
            "title": "Prompt baseline",
            "start_time": occurrence,
            "timezone": "UTC",
            "status": "scheduled",
            "actions": [
                {
                    "id": action_id,
                    "kind": "prompt",
                    "prompt": "Do the bounded review.",
                    "conversation_mode": "inline",
                    "session_id": "session-old",
                    "chain_id": "chain-old",
                    "status": "scheduled",
                    "external_control_revision": 7,
                }
            ],
        },
    )
    claimed_event = calendar_store.load_event(event_id)
    claimed_action = runner._iter_actions(claimed_event)[0]

    assert runner._claim_action_run(
        event_id,
        claimed_event,
        claimed_action,
        action_id=action_id,
        occurrence_time=occurrence,
        force=False,
    )
    assert claimed_action["run_control_revision"] == 7

    def edit_lineage(current):
        current["actions"][0]["session_id"] = "session-new"
        current["actions"][0]["chain_id"] = "chain-new"
        current["actions"][0]["external_control_revision"] = 8
        return current

    calendar_store.update_event(event_id, edit_lineage)
    checkpoint = runner._persist_prompt_checkpoint_claim(
        event_id,
        claimed_event,
        claimed_action,
        action_id=action_id,
        occurrence_time=occurrence,
        prompt="Do the bounded review.",
        session_id="session-old",
        chain_id="chain-old",
    )

    assert checkpoint is None
    stored = calendar_store.load_event(event_id)
    stored_action = stored["actions"][0]
    assert stored_action["session_id"] == "session-new"
    assert stored_action["chain_id"] == "chain-new"
    assert stored_action["external_control_revision"] == 8
    assert stored_action["run_control_revision"] == 7
    assert "prompt_checkpoint" not in stored_action


def test_prompt_resume_refuses_a_control_changed_run_before_output_recovery(
    app_with_temp_stores,
):
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    event_id = "prompt-resume-control-change"
    action_id = "prompt-resume-action"
    occurrence = time.time() - 90
    calendar_store.save_event(
        event_id,
        {
            "id": event_id,
            "title": "Changed prompt recovery",
            "start_time": occurrence,
            "timezone": "UTC",
            "status": "running",
            "actions": [
                {
                    "id": action_id,
                    "kind": "prompt",
                    "prompt": "The latest definition",
                    "status": "prompt_resume_pending",
                    "run_id": "run-old",
                    "run_control_revision": 3,
                    "external_control_revision": 4,
                    "prompt_checkpoint": {"occurrence_at": occurrence},
                }
            ],
        },
    )
    event = calendar_store.load_event(event_id)
    action = runner._iter_actions(event)[0]

    outcome = runner._claim_pending_prompt_resume(
        event_id,
        event,
        action,
        action_id=action_id,
    )

    assert outcome["status"] == "control_changed"
    assert outcome["result"]["status"] == "reconcile_required"
    stored = calendar_store.load_event(event_id)
    assert stored["actions"][0]["prompt"] == "The latest definition"
    assert stored["actions"][0]["status"] == "reconcile_required"
    assert stored["actions"][0]["external_control_revision"] == 4
    assert stored["actions"][0]["run_control_revision"] == 3


@pytest.mark.anyio
async def test_prompt_cancellation_before_provider_dispatch_is_confirmed_no_change(
    app_with_temp_stores, monkeypatch
):
    from app import routes as routes_module
    from app.services.scheduled_action_cancellation import (
        request_scheduled_action_cancellation,
    )
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    app, dummy = app_with_temp_stores
    event_id = "cancel-prompt-before-provider"
    original_checkpoint = runner._persist_prompt_checkpoint_claim

    def checkpoint_then_cancel(*args, **kwargs):
        checkpoint = original_checkpoint(*args, **kwargs)
        action = args[2]
        request_scheduled_action_cancellation(
            event_id,
            action["id"],
            expected_run_id=action["run_id"],
        )
        return checkpoint

    def must_not_generate(*_args, **_kwargs):
        raise AssertionError("cancelled prompt reached provider dispatch")

    monkeypatch.setattr(
        runner, "_persist_prompt_checkpoint_claim", checkpoint_then_cancel
    )
    monkeypatch.setattr(routes_module.llm_service, "generate", must_not_generate)
    _save_restartable_prompt_event(
        calendar_store, event_id, prompt="Do not send this prompt."
    )

    result = await runner.run_scheduled_tools_for_event(app, event_id)

    assert result["status"] == "cancelled"
    stored = calendar_store.load_event(event_id)
    action = stored["actions"][0]
    receipt = stored["run_history"][0]
    assert stored["status"] == "cancelled"
    assert action["status"] == "cancelled"
    assert action["cancel_requested"] is True
    assert action["prompt_invoked"] is False
    assert action["tool_invoked"] is False
    assert action["last_occurrence_at"] == receipt["occurrence_at"]
    assert receipt["status"] == "cancelled"
    assert receipt["tool_invoked"] is False
    assert receipt["state_delta_certainty"] == "confirmed_no_change"
    assert receipt["summary"] == "Scheduled action cancelled before dispatch."
    activity = app.state.work_run_store.get(receipt["id"])
    assert activity["status"] == "cancelled"
    assert activity["state_delta_certainty"] == "confirmed_no_change"
    assert app.state.work_run_store.count_attempts(receipt["id"]) == 0
    assert app.state.work_run_store.count_effects(receipt["id"]) == 0
    assert dummy.calls == []


@pytest.mark.anyio
async def test_tool_cancellation_before_effect_intent_blocks_side_effect(
    app_with_temp_stores, monkeypatch
):
    from app.services.scheduled_action_cancellation import (
        request_scheduled_action_cancellation,
    )
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    app, dummy = app_with_temp_stores
    event_id = "cancel-tool-before-effect"
    original_effect_policy = runner._scheduled_tool_effect_policy
    cancellation_requested = False

    def policy_then_cancel(name, args):
        nonlocal cancellation_requested
        policy = original_effect_policy(name, args)
        if not cancellation_requested:
            latest = calendar_store.load_event(event_id)
            action = latest["actions"][0]
            request_scheduled_action_cancellation(
                event_id,
                action["id"],
                expected_run_id=action["run_id"],
            )
            cancellation_requested = True
        return policy

    monkeypatch.setattr(runner, "_scheduled_tool_effect_policy", policy_then_cancel)
    _save_journaled_tool_event(calendar_store, event_id, prompt=None)

    result = await runner.run_scheduled_tools_for_event(app, event_id)

    assert result["status"] == "cancelled"
    stored = calendar_store.load_event(event_id)
    action = stored["actions"][0]
    receipt = stored["run_history"][0]
    assert action["status"] == "cancelled"
    assert action["cancel_requested"] is True
    assert action["tool_invoked"] is False
    assert receipt["status"] == "cancelled"
    assert receipt["tool_invoked"] is False
    assert receipt["state_delta_certainty"] == "confirmed_no_change"
    assert app.state.work_run_store.count_effects(receipt["id"]) == 0
    assert app.state.work_run_store.get(receipt["id"])["status"] == "cancelled"
    assert dummy.calls == []


@pytest.mark.anyio
async def test_followup_cancellation_preserves_completed_tool_effect(
    app_with_temp_stores, monkeypatch
):
    from app import routes as routes_module
    from app.services.scheduled_action_cancellation import (
        request_scheduled_action_cancellation,
    )
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    app, dummy = app_with_temp_stores
    event_id = "cancel-before-followup-provider"
    original_followup = runner._run_prompt_followup
    acknowledged_effect = {}

    async def cancel_then_enter_followup(*args, **kwargs):
        action = kwargs["action"]
        acknowledged_effect.update(
            {
                "effect_id": action.get("effect_id"),
                "effect_status": action.get("effect_status"),
                "effect_certainty": action.get("effect_certainty"),
            }
        )
        request_scheduled_action_cancellation(
            event_id,
            kwargs["action_id"],
            expected_run_id=action["run_id"],
        )
        return await original_followup(*args, **kwargs)

    def must_not_generate(*_args, **_kwargs):
        raise AssertionError("cancelled follow-up reached provider dispatch")

    monkeypatch.setattr(runner, "_run_prompt_followup", cancel_then_enter_followup)
    monkeypatch.setattr(routes_module.llm_service, "generate", must_not_generate)
    _save_journaled_tool_event(calendar_store, event_id)

    result = await runner.run_scheduled_tools_for_event(app, event_id)

    assert result["status"] == "cancelled"
    assert len(dummy.calls) == 1
    stored = calendar_store.load_event(event_id)
    action = stored["actions"][0]
    receipt = stored["run_history"][0]
    assert action["status"] == "cancelled"
    assert action["cancel_requested"] is True
    assert action["tool_invoked"] is True
    assert action["followup_status"] == "cancelled"
    assert {
        "effect_id": action.get("effect_id"),
        "effect_status": action.get("effect_status"),
        "effect_certainty": action.get("effect_certainty"),
    } == acknowledged_effect
    assert acknowledged_effect["effect_status"] == "acknowledged"
    assert acknowledged_effect["effect_certainty"] == "reported_success"
    assert receipt["status"] == "cancelled"
    assert receipt["tool_invoked"] is True
    assert receipt["followup_status"] == "cancelled"
    assert receipt["effect_status"] == "acknowledged"
    assert receipt["effect_certainty"] == "reported_success"
    assert receipt["state_delta_certainty"] == "reported_success"
    assert receipt["summary"] == (
        "Tool completed; the remaining provider follow-up was cancelled."
    )
    attempts = app.state.work_run_store.list_attempts(receipt["id"])
    effects = app.state.work_run_store.list_effects(receipt["id"])
    assert attempts == []
    assert len(effects) == 1
    assert effects[0]["status"] == "acknowledged"


@pytest.mark.anyio
async def test_cancellation_after_effect_dispatch_preserves_uncertainty(
    app_with_temp_stores, monkeypatch
):
    from app.services.scheduled_action_cancellation import (
        request_scheduled_action_cancellation,
    )
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    app, dummy = app_with_temp_stores
    event_id = "cancel-after-effect-dispatch"
    tool_started = asyncio.Event()
    release_tool = asyncio.Event()
    tool_calls = []

    async def fail_after_dispatch(*_args, **_kwargs):
        tool_calls.append("dispatched")
        tool_started.set()
        await release_tool.wait()
        raise RuntimeError("simulated uncertain tool failure")

    monkeypatch.setattr(runner, "_invoke_tool", fail_after_dispatch)
    _save_journaled_tool_event(calendar_store, event_id, prompt=None)
    task = asyncio.create_task(runner.run_scheduled_tools_for_event(app, event_id))
    await asyncio.wait_for(tool_started.wait(), timeout=1)

    running = calendar_store.load_event(event_id)
    running_action = running["actions"][0]
    cancellation = request_scheduled_action_cancellation(
        event_id,
        running_action["id"],
        expected_run_id=running_action["run_id"],
    )

    assert cancellation["status"] == "cancel_requested"
    while_running = calendar_store.load_event(event_id)
    assert while_running["actions"][0]["status"] == "running"
    assert while_running["actions"][0]["cancel_requested"] is True
    receipt_id = while_running["run_history"][0]["id"]
    dispatched_effect = app.state.work_run_store.list_effects(receipt_id)[0]
    assert dispatched_effect["status"] == "dispatched"
    assert dispatched_effect["certainty"] == "unknown"

    release_tool.set()
    result = await asyncio.wait_for(task, timeout=1)

    assert result["status"] == "reconcile_required"
    assert tool_calls == ["dispatched"]
    assert dummy.calls == []
    stored = calendar_store.load_event(event_id)
    action = stored["actions"][0]
    receipt = stored["run_history"][0]
    assert action["status"] == "reconcile_required"
    assert action["status"] != "cancelled"
    assert action["cancel_requested"] is True
    assert action["reconcile_required"] is True
    assert action["effect_status"] == "unknown"
    assert action["effect_certainty"] == "unknown"
    assert receipt["status"] == "reconcile_required"
    assert receipt["tool_invoked"] is True
    assert receipt["state_delta_certainty"] == "unknown"
    assert receipt["state_delta_certainty"] != "confirmed_no_change"
    effect = app.state.work_run_store.list_effects(receipt_id)[0]
    assert effect["status"] == "unknown"
    assert effect["certainty"] == "unknown"
    assert effect["reconcile_required"] is True


@pytest.mark.anyio
async def test_followup_output_save_failure_records_attention_without_success_publish(
    app_with_temp_stores, monkeypatch
):
    from app import routes as routes_module
    from app.utils import calendar_store, conversation_store
    from workers import scheduled_tool_runner as runner

    app, dummy = app_with_temp_stores
    event_id = "followup-output-save-failure"
    published = []

    def generated_output(*_args, **_kwargs):
        return {"text": "Generated but not canonical", "thought": ""}

    async def capture_publish(*_args, **kwargs):
        published.append(kwargs)

    monkeypatch.setattr(routes_module.llm_service, "generate", generated_output)
    monkeypatch.setattr(runner, "_update_conversation_entry", lambda **_kwargs: False)
    monkeypatch.setattr(runner, "_publish_content", capture_publish)
    _save_journaled_tool_event(
        calendar_store,
        event_id,
        patience={"max_provider_retries": 0},
    )

    result = await runner.run_scheduled_tools_for_event(app, event_id)

    assert result["status"] == "error"
    assert result["results"][0]["tool_invoked"] is True
    assert result["results"][0]["reconcile_required"] is True
    assert len(dummy.calls) == 1
    assert [item["metadata"]["run_status"] for item in published] == [
        "active",
        "error",
    ]
    assert not any(
        item.get("content") == "Generated but not canonical" for item in published
    )
    stored = calendar_store.load_event(event_id)
    receipt_id = stored["run_history"][0]["id"]
    attempts = app.state.work_run_store.list_attempts(receipt_id)
    assert [item["status"] for item in attempts] == ["output_checkpoint_missing"]
    assert attempts[0]["error_category"] == "provider_output_checkpoint_missing"
    assert attempts[0]["error_code"] == "canonical_conversation_write_failed"
    receipt = app.state.work_run_store.get(receipt_id)
    assert receipt["reconcile_required"] is True
    assert receipt["recovery_state"] == "attention"
    action = stored["actions"][0]
    conversation = conversation_store.load_conversation(action["session_id"])
    output = next(
        item for item in conversation if item.get("id") == action["followup_message_id"]
    )
    assert output["text"] == ""
    assert output["metadata"]["status"] == "pending"


@pytest.mark.anyio
async def test_effect_ledger_failure_prevents_mutating_tool(
    app_with_temp_stores, monkeypatch
):
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    app, dummy = app_with_temp_stores

    def fail_effect(*_args, **_kwargs):
        raise OSError("effect ledger unavailable")

    monkeypatch.setattr(app.state.work_run_store, "record_effect", fail_effect)
    _save_journaled_tool_event(calendar_store, "effect-ledger-failure", prompt=None)

    result = await runner.run_scheduled_tools_for_event(app, "effect-ledger-failure")

    assert result["status"] == "error"
    assert result["results"][0]["tool_invoked"] is False
    assert result["results"][0]["state_delta_certainty"] == "confirmed_no_change"
    assert dummy.calls == []


@pytest.mark.anyio
async def test_effect_journal_inspection_failure_blocks_without_claim_and_retries(
    app_with_temp_stores, monkeypatch
):
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    app, dummy = app_with_temp_stores
    store = app.state.work_run_store
    original_guard = store.has_unresolved_effects

    def unavailable_guard(*_args, **_kwargs):
        raise OSError("effect journal unavailable")

    monkeypatch.setattr(store, "has_unresolved_effects", unavailable_guard)
    _save_journaled_tool_event(
        calendar_store, "effect-inspection-unavailable", prompt=None
    )

    blocked = await runner.run_scheduled_tools_for_event(
        app, "effect-inspection-unavailable"
    )

    assert blocked["status"] == "effect_journal_unavailable"
    assert blocked["results"][0]["status"] == "effect_journal_unavailable"
    assert blocked["results"][0]["tool_invoked"] is False
    assert blocked["results"][0]["retryable"] is True
    assert blocked["results"][0]["state_delta_certainty"] == "confirmed_no_change"
    assert blocked["results"][0].get("reconcile_required") is not True
    assert dummy.calls == []
    assert (
        app.state.work_run_store.list_runs(
            event_id="effect-inspection-unavailable", limit=500
        )
        == []
    )
    stored = calendar_store.load_event("effect-inspection-unavailable")
    assert stored["actions"][0]["status"] == "effect_journal_unavailable"
    assert stored.get("run_history") in (None, [])

    monkeypatch.setattr(store, "has_unresolved_effects", original_guard)
    retried = await runner.run_scheduled_tools_for_event(
        app, "effect-inspection-unavailable"
    )

    assert retried["status"] == "invoked"
    assert len(dummy.calls) == 1


@pytest.mark.anyio
async def test_post_dispatch_exception_marks_effect_unknown_and_attention(
    app_with_temp_stores, monkeypatch
):
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    app, dummy = app_with_temp_stores
    private_error = "SECRET-REMOTE-ENDPOINT-WITH-TOKEN"

    def failed_tool(name, *, user=None, signature=None, **args):
        dummy.calls.append({"name": name, "args": args})
        raise RuntimeError(private_error)

    monkeypatch.setattr(dummy, "invoke_tool", failed_tool)
    _save_journaled_tool_event(calendar_store, "effect-unknown", prompt=None)

    result = await runner.run_scheduled_tools_for_event(app, "effect-unknown")

    assert result["status"] == "error"
    assert len(dummy.calls) == 1
    stored = calendar_store.load_event("effect-unknown")
    receipt_id = stored["run_history"][0]["id"]
    effect = app.state.work_run_store.list_effects(receipt_id)[0]
    assert effect["status"] == "unknown"
    assert effect["certainty"] == "unknown"
    assert effect["replay_policy"] == "never_auto_replay"
    assert effect["reconcile_required"] is True
    receipt = app.state.work_run_store.get(receipt_id)
    assert receipt["reconcile_required"] is True
    assert receipt["recovery_state"] == "attention"
    assert private_error not in json.dumps(receipt)
    assert all(
        private_error.encode() not in path.read_bytes()
        for path in app.state.work_run_store.path.parent.glob(
            f"{app.state.work_run_store.path.name}*"
        )
    )

    # Simulate import/edit of a clean Calendar compatibility record. The
    # authoritative SQLite effect journal must still prevent a replay.
    calendar_store.save_event(
        "effect-unknown",
        {
            "id": "effect-unknown",
            "title": "Reimported effect",
            "start_time": time.time() - 5,
            "timezone": "UTC",
            "status": "scheduled",
            "actions": [
                {
                    "id": "effect-unknown-action",
                    "kind": "tool",
                    "name": "remember",
                    "args": {"key": "journal", "value": "once"},
                    "status": "scheduled",
                }
            ],
        },
    )
    receipt_count = len(
        app.state.work_run_store.list_runs(
            event_id="effect-unknown", action_id="effect-unknown-action", limit=500
        )
    )
    effect_count = app.state.work_run_store.count_effects(receipt_id)

    forced = await runner.run_scheduled_tools_for_event(
        app, "effect-unknown", force=True
    )
    repeated = await runner.run_scheduled_tools_for_event(
        app, "effect-unknown", force=True
    )

    assert forced["status"] == "reconcile_required"
    assert forced["results"][0]["status"] == "reconcile_required"
    assert forced["results"][0]["tool_invoked"] is False
    assert repeated["status"] == "reconcile_required"
    assert repeated["results"][0]["tool_invoked"] is False
    assert len(dummy.calls) == 1
    blocked = calendar_store.load_event("effect-unknown")
    assert blocked["actions"][0]["status"] == "reconcile_required"
    assert (
        len(
            app.state.work_run_store.list_runs(
                event_id="effect-unknown", action_id="effect-unknown-action", limit=500
            )
        )
        == receipt_count
    )
    assert app.state.work_run_store.count_effects(receipt_id) == effect_count


@pytest.mark.anyio
async def test_error_result_after_dispatch_is_not_confirmed(
    app_with_temp_stores, monkeypatch
):
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    app, dummy = app_with_temp_stores

    def error_result(name, *, user=None, signature=None, **args):
        dummy.calls.append({"name": name, "args": args})
        return {"ok": False, "error": "remote may have partially applied"}

    monkeypatch.setattr(dummy, "invoke_tool", error_result)
    _save_journaled_tool_event(calendar_store, "effect-error-result", prompt=None)

    result = await runner.run_scheduled_tools_for_event(app, "effect-error-result")

    assert result["status"] == "error"
    receipt_id = calendar_store.load_event("effect-error-result")["run_history"][0][
        "id"
    ]
    effect = app.state.work_run_store.list_effects(receipt_id)[0]
    assert effect["status"] == "unknown"
    assert effect["certainty"] == "unknown"
    assert effect["reconcile_required"] is True


@pytest.mark.anyio
async def test_effect_journal_contains_digests_not_raw_args_or_result(
    app_with_temp_stores, monkeypatch
):
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    app, dummy = app_with_temp_stores
    raw_arg_secret = "SECRET-ARGUMENT-VALUE"
    raw_result_secret = "SECRET-RESULT-VALUE"

    def secret_result(name, *, user=None, signature=None, **args):
        dummy.calls.append({"name": name, "args": args})
        return {"resource_id": "resource-1", "payload": raw_result_secret}

    monkeypatch.setattr(dummy, "invoke_tool", secret_result)
    _save_journaled_tool_event(
        calendar_store,
        "effect-redaction",
        prompt=None,
        args={"key": "private", "value": raw_arg_secret},
    )
    event = calendar_store.load_event("effect-redaction")
    event["background_job"] = {"execution": {"permissions": ["memory.write"]}}
    calendar_store.save_event("effect-redaction", event)

    result = await runner.run_scheduled_tools_for_event(app, "effect-redaction")

    assert result["status"] == "invoked"
    receipt_id = calendar_store.load_event("effect-redaction")["run_history"][0]["id"]
    effect = app.state.work_run_store.list_effects(receipt_id)[0]
    serialized = json.dumps(effect)
    assert raw_arg_secret not in serialized
    assert raw_result_secret not in serialized
    assert effect["argument_digest"].startswith("sha256:")
    assert effect["result_digest"].startswith("sha256:")
    assert effect["status"] == "acknowledged"
    assert effect["certainty"] == "reported_success"
    assert effect["remote_ids"] == {"resource_id": "resource-1"}
    assert effect["approval_snapshot"]["policy_id"] == "scheduled-tool-auth:v1"
    assert effect["approval_snapshot"]["status"] == "catalog_auto"
    assert effect["permission_snapshot"]["status"] == "granted"
    assert effect["permission_snapshot"]["scopes"] == ["memory.write"]
    assert effect["permission_snapshot"]["checked_at"] > 0
    receipt = app.state.work_run_store.get(receipt_id)
    assert receipt["summary"] == "remember returned structured output (2 fields)."
    assert receipt["effect_status"] == "acknowledged"
    assert receipt["effect_certainty"] == "reported_success"
    assert raw_arg_secret not in json.dumps(receipt)
    assert raw_result_secret not in json.dumps(receipt)


@pytest.mark.anyio
async def test_mutating_string_result_is_not_copied_into_activity_receipt(
    app_with_temp_stores, monkeypatch
):
    from app.utils import calendar_store
    from workers import scheduled_tool_runner as runner

    app, dummy = app_with_temp_stores
    private_result = "SECRET-STRING-TOOL-RESULT"

    def secret_string_result(name, *, user=None, signature=None, **args):
        dummy.calls.append({"name": name, "args": args})
        return private_result

    monkeypatch.setattr(dummy, "invoke_tool", secret_string_result)
    _save_journaled_tool_event(
        calendar_store,
        "effect-string-redaction",
        prompt=None,
    )

    result = await runner.run_scheduled_tools_for_event(app, "effect-string-redaction")

    assert result["status"] == "invoked"
    receipt_id = calendar_store.load_event("effect-string-redaction")["run_history"][0][
        "id"
    ]
    receipt = app.state.work_run_store.get(receipt_id)
    assert receipt["summary"] == (
        f"remember returned text output ({len(private_result)} characters)."
    )
    assert private_result not in json.dumps(receipt)
    assert all(
        private_result.encode() not in path.read_bytes()
        for path in app.state.work_run_store.path.parent.glob(
            f"{app.state.work_run_store.path.name}*"
        )
    )
