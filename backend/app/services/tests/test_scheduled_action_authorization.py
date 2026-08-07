from copy import deepcopy

import pytest


def _event(*, permissions=None, recurring=False):
    event = {
        "id": "scheduled-auth",
        "title": "Authorized background work",
        "start_time": 1_900_000_000.0,
        "timezone": "UTC",
        "status": "scheduled",
        "background_job": {
            "execution": {"permissions": list(permissions or [])},
        },
        "actions": [
            {
                "id": "remember-once",
                "kind": "tool",
                "name": "remember",
                "args": {"key": "authorization", "value": "bounded"},
                "prompt": "Summarize the stored memory.",
                "status": "scheduled",
            }
        ],
    }
    if recurring:
        event["rrule"] = "FREQ=DAILY;COUNT=3"
    return event


@pytest.fixture
def isolated_calendar(tmp_path, monkeypatch):
    from app.utils import calendar_store

    root = tmp_path / "calendar"
    root.mkdir()
    monkeypatch.setattr(calendar_store, "EVENTS_DIR", root)
    return calendar_store


def test_permission_scopes_and_scheduled_policy_fail_closed(monkeypatch):
    from app import tool_catalog

    assert tool_catalog.permission_scopes_for_tool("remember") == ("memory.write",)
    assert tool_catalog.permission_scopes_for_tool("read_file") == ("files.read",)
    assert tool_catalog.permission_scopes_for_tool("mcp.call") == (
        "integration.execute",
    )
    assert tool_catalog.permission_scopes_for_tool("unknown.custom") == (
        "custom.execute",
    )
    assert (
        tool_catalog.scheduled_approval_policy_for_tool("help")["approval_required"]
        is False
    )
    remember_policy = tool_catalog.scheduled_approval_policy_for_tool("remember")
    assert remember_policy["approval_required"] is True
    assert remember_policy["permission_scopes"] == ("memory.write",)
    assert remember_policy["policy_digest"].startswith("sha256:")

    def broken_catalog(_name):
        raise RuntimeError("catalog unavailable")

    monkeypatch.setattr(tool_catalog, "get_tool_catalog_entry", broken_catalog)
    assert tool_catalog.permission_scopes_for_tool("remember") == ("custom.execute",)
    fallback = tool_catalog.scheduled_approval_policy_for_tool("remember")
    assert fallback["approval_required"] is True
    assert fallback["permission_scopes"] == ("custom.execute",)


def test_request_digest_is_deterministic_and_binds_execution_inputs():
    from app.services.scheduled_action_authorization import build_authorization_request

    event = _event(permissions=["memory.write"])
    action = event["actions"][0]
    first = build_authorization_request(
        event["id"], event, action["id"], action, event["start_time"]
    )
    reordered = deepcopy(event)
    reordered["actions"][0]["args"] = {
        "value": "bounded",
        "key": "authorization",
    }
    second = build_authorization_request(
        reordered["id"],
        reordered,
        action["id"],
        reordered["actions"][0],
        reordered["start_time"],
    )
    assert second["id"] == first["id"]
    assert second["request_digest"] == first["request_digest"]
    assert first["can_approve"] is True
    assert first["missing_scopes"] == []

    changes = []
    changed_args = deepcopy(event)
    changed_args["actions"][0]["args"]["value"] = "changed"
    changes.append(changed_args)
    changed_prompt = deepcopy(event)
    changed_prompt["actions"][0]["prompt"] = "Use different output guidance."
    changes.append(changed_prompt)
    changed_permissions = deepcopy(event)
    changed_permissions["background_job"]["execution"]["permissions"] = []
    changes.append(changed_permissions)
    changed_schedule = deepcopy(event)
    changed_schedule["timezone"] = "America/Vancouver"
    changes.append(changed_schedule)
    changed_lineage = deepcopy(event)
    changed_lineage["actions"][0]["session_id"] = "different-session"
    changes.append(changed_lineage)
    changed_origin = deepcopy(event)
    changed_origin["actions"][0]["origin_session_id"] = "different-origin"
    changes.append(changed_origin)

    for changed in changes:
        request = build_authorization_request(
            changed["id"],
            changed,
            action["id"],
            changed["actions"][0],
            changed["start_time"],
        )
        assert request["request_digest"] != first["request_digest"]
        assert request["id"] != first["id"]

    next_occurrence = build_authorization_request(
        event["id"], event, action["id"], action, event["start_time"] + 86400
    )
    assert next_occurrence["id"] != first["id"]


def test_missing_permission_is_visible_and_cannot_be_approved(isolated_calendar):
    from app.services.scheduled_action_authorization import (
        AuthorizationPermissionError,
        apply_authorization_decision,
        build_authorization_request,
        mark_authorization_required,
    )

    event = _event()
    action = event["actions"][0]
    request = build_authorization_request(
        event["id"], event, action["id"], action, event["start_time"]
    )
    assert request["missing_scopes"] == ["memory.write"]
    assert request["can_approve"] is False
    mark_authorization_required(action, request, requested_at=100.0)
    isolated_calendar.save_event(event["id"], event)

    with pytest.raises(AuthorizationPermissionError, match="memory.write"):
        apply_authorization_decision(
            event["id"],
            action["id"],
            decision="approve_once",
            authorization_id=request["id"],
            request_digest=request["request_digest"],
            occurrence_at=request["occurrence_at"],
            decided_at=101.0,
        )
    stored = isolated_calendar.load_event(event["id"])
    assert stored["actions"][0]["status"] == "authorization_required"


def test_approve_once_is_atomic_digest_bound_and_idempotent(isolated_calendar):
    from app.services.scheduled_action_authorization import (
        AuthorizationConflictError,
        apply_authorization_decision,
        authorization_allows_dispatch,
        build_authorization_request,
        consume_authorization,
        mark_authorization_required,
    )

    event = _event(permissions=["memory.write"])
    action = event["actions"][0]
    request = build_authorization_request(
        event["id"], event, action["id"], action, event["start_time"]
    )
    mark_authorization_required(action, request, requested_at=100.0)
    isolated_calendar.save_event(event["id"], event)

    approved = apply_authorization_decision(
        event["id"],
        action["id"],
        decision="approve_once",
        authorization_id=request["id"],
        request_digest=request["request_digest"],
        occurrence_at=request["occurrence_at"],
        decided_at=101.0,
    )
    assert approved["status"] == "approved_once"
    assert approved["idempotent"] is False
    repeated = apply_authorization_decision(
        event["id"],
        action["id"],
        decision="approve_once",
        authorization_id=request["id"],
        request_digest=request["request_digest"],
        occurrence_at=request["occurrence_at"],
        decided_at=102.0,
    )
    assert repeated["idempotent"] is True
    stored = isolated_calendar.load_event(event["id"])
    stored_action = stored["actions"][0]
    assert stored_action["external_control_revision"] == 1
    assert authorization_allows_dispatch(stored, stored_action, request) is True
    assert consume_authorization(stored_action, request, consumed_at=103.0) is True
    assert consume_authorization(stored_action, request, consumed_at=104.0) is False

    with pytest.raises(AuthorizationConflictError, match="already"):
        apply_authorization_decision(
            event["id"],
            action["id"],
            decision="deny",
            authorization_id=request["id"],
            request_digest=request["request_digest"],
            occurrence_at=request["occurrence_at"],
            decided_at=105.0,
        )
    with pytest.raises(AuthorizationConflictError, match="changed"):
        apply_authorization_decision(
            event["id"],
            action["id"],
            decision="approve_once",
            authorization_id=request["id"],
            request_digest="sha256:" + ("0" * 64),
            occurrence_at=request["occurrence_at"],
            decided_at=105.0,
        )


def test_deny_is_per_occurrence_and_next_recurrence_gets_new_request(
    isolated_calendar,
):
    from app.services.scheduled_action_authorization import (
        apply_authorization_decision,
        build_authorization_request,
        mark_authorization_required,
    )

    event = _event(permissions=["memory.write"], recurring=True)
    action = event["actions"][0]
    first = build_authorization_request(
        event["id"], event, action["id"], action, event["start_time"]
    )
    mark_authorization_required(action, first, requested_at=100.0)
    isolated_calendar.save_event(event["id"], event)
    denied = apply_authorization_decision(
        event["id"],
        action["id"],
        decision="deny",
        authorization_id=first["id"],
        request_digest=first["request_digest"],
        occurrence_at=first["occurrence_at"],
        decided_at=101.0,
    )
    assert denied["status"] == "authorization_denied"
    stored = isolated_calendar.load_event(event["id"])
    assert stored["status"] == "scheduled"
    assert stored["actions"][0]["status"] == "scheduled"
    assert stored["actions"][0]["last_occurrence_at"] == first["occurrence_at"]

    second = build_authorization_request(
        event["id"],
        stored,
        action["id"],
        stored["actions"][0],
        event["start_time"] + 86400,
    )
    assert second["id"] != first["id"]
    assert second["request_digest"] != first["request_digest"]


def test_client_forgery_is_stripped_and_edits_invalidate_authorization():
    from app.schemas import CalendarEvent
    from app.services.calendar_jobs import merge_client_action_definitions
    from app.services.scheduled_action_authorization import (
        build_authorization_request,
        invalidate_event_authorizations_for_edit,
        mark_authorization_required,
    )

    event = _event(permissions=["memory.write"])
    action = event["actions"][0]
    request = build_authorization_request(
        event["id"], event, action["id"], action, event["start_time"]
    )
    mark_authorization_required(action, request, requested_at=100.0)
    forged = deepcopy(action)
    forged["authorization"] = {"status": "approved_once", "id": "forged"}
    forged["approval_status"] = "approved_once"
    forged["approval_id"] = "forged"
    forged["args"]["value"] = "edited"
    merged = merge_client_action_definitions([action], [forged])
    assert merged[0]["authorization"]["status"] == "invalidated"
    assert merged[0]["authorization"]["id"] == request["id"]
    assert "approval_status" not in merged[0]
    assert "approval_id" not in merged[0]

    parsed = CalendarEvent.model_validate(
        {
            **_event(permissions=["memory.write"]),
            "actions": [forged],
        }
    )
    parsed_action = parsed.actions[0]
    assert "authorization" not in parsed_action
    assert "approval_status" not in parsed_action
    assert parsed.background_job is not None
    assert parsed.background_job.execution.permissions == ["memory.write"]

    previous = _event(permissions=["memory.write"])
    previous_request = build_authorization_request(
        previous["id"],
        previous,
        action["id"],
        previous["actions"][0],
        previous["start_time"],
    )
    mark_authorization_required(
        previous["actions"][0], previous_request, requested_at=100.0
    )
    title_only = deepcopy(previous)
    title_only["title"] = "Renamed without changing execution"
    assert invalidate_event_authorizations_for_edit(previous, title_only) == []
    assert title_only["actions"][0]["authorization"]["status"] == (
        "authorization_required"
    )

    permission_edit = deepcopy(previous)
    permission_edit["background_job"]["execution"]["permissions"] = []
    invalidated = invalidate_event_authorizations_for_edit(previous, permission_edit)
    assert len(invalidated) == 1
    assert permission_edit["actions"][0]["authorization"]["status"] == "invalidated"
    assert permission_edit["actions"][0]["external_control_revision"] == 1

    removed = deepcopy(previous)
    removed["actions"] = []
    removed_authorizations = invalidate_event_authorizations_for_edit(previous, removed)
    assert len(removed_authorizations) == 1
    assert removed_authorizations[0]["status"] == "invalidated"
    assert removed_authorizations[0]["invalidation_reason"] == "action_removed"
    assert removed_authorizations[0]["action_id"] == "remember-once"


def test_invalid_permission_ceiling_is_rejected():
    from app.schemas import CalendarEvent
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="category.read"):
        CalendarEvent.model_validate(_event(permissions=["broad host access"]))
