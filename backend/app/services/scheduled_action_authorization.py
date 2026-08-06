"""Digest-bound authorization for Calendar-backed scheduled tool actions.

This module owns only the durable decision contract.  The scheduled runner is
responsible for publishing pending receipts and atomically consuming an
``approved_once`` decision immediately before it claims a run.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from typing import Any, Callable, Dict, Literal, Mapping, MutableMapping, Optional

from app.services.calendar_jobs import (
    action_definition_digest,
    bump_external_control_revision,
    external_control_revision,
    normalize_permission_scopes,
)
from app.tool_catalog import scheduled_approval_policy_for_tool
from app.utils import calendar_store

JsonDict = Dict[str, Any]

AUTHORIZATION_SCHEMA_VERSION = 1
AUTHORIZATION_REQUIRED = "authorization_required"
AUTHORIZATION_APPROVED_ONCE = "approved_once"
AUTHORIZATION_DENIED = "authorization_denied"
AUTHORIZATION_CONSUMED = "consumed"
AUTHORIZATION_INVALIDATED = "invalidated"
AUTHORIZATION_EXPIRED = "expired"

_ACTIVE_AUTHORIZATION_STATUSES = frozenset(
    {AUTHORIZATION_REQUIRED, AUTHORIZATION_APPROVED_ONCE}
)
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class ScheduledActionAuthorizationError(RuntimeError):
    """Base error for a rejected scheduled-action authorization operation."""


class AuthorizationNotFoundError(ScheduledActionAuthorizationError):
    """The event, action, or current authorization request does not exist."""


class AuthorizationConflictError(ScheduledActionAuthorizationError):
    """The supplied decision no longer matches the server-owned request."""


class AuthorizationPermissionError(ScheduledActionAuthorizationError):
    """The job permission ceiling does not contain the required scopes."""


def _canonical_digest(value: Any) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("authorization input must be canonical JSON") from exc
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _stable_id(kind: str, *parts: Any) -> str:
    digest = _canonical_digest([kind, *parts]).removeprefix("sha256:")
    return f"{kind}-{digest}"


def _occurrence_millis(value: Any) -> int:
    try:
        occurrence = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("occurrence_at must be a finite positive timestamp") from exc
    if not math.isfinite(occurrence) or occurrence <= 0:
        raise ValueError("occurrence_at must be a finite positive timestamp")
    return int(round(occurrence * 1000.0))


def _action_id(action: Mapping[str, Any], fallback: str = "") -> str:
    return str(action.get("request_id") or action.get("id") or fallback).strip()


def _configured_permissions(event: Mapping[str, Any]) -> list[str]:
    background_job = event.get("background_job")
    background_job = background_job if isinstance(background_job, Mapping) else {}
    execution = background_job.get("execution")
    execution = execution if isinstance(execution, Mapping) else {}
    return normalize_permission_scopes(execution.get("permissions", []))


def _schedule_digest(event: Mapping[str, Any]) -> str:
    return _canonical_digest(
        {
            "start_time": event.get("start_time"),
            "start": event.get("start"),
            "timezone": event.get("timezone") or "UTC",
            "rrule": event.get("rrule"),
            "recurrence_exceptions": event.get("recurrence_exceptions") or [],
        }
    )


def build_authorization_request(
    event_id: str,
    event: Mapping[str, Any],
    action_id: str,
    action: Mapping[str, Any],
    occurrence_at: float,
) -> JsonDict:
    """Build a deterministic, content-free request for one exact occurrence."""

    resolved_event_id = str(event_id or event.get("id") or "").strip()
    resolved_action_id = str(action_id or _action_id(action)).strip()
    if not resolved_event_id or not resolved_action_id:
        raise ValueError("event_id and action_id are required")
    stored_action_id = _action_id(action, resolved_action_id)
    if stored_action_id != resolved_action_id:
        raise ValueError("action_id does not match the stored action")
    occurrence_ms = _occurrence_millis(occurrence_at)
    tool_name = str(action.get("name") or "").strip()
    if not tool_name:
        raise ValueError("scheduled tool name is required")

    policy = scheduled_approval_policy_for_tool(tool_name)
    required_scopes = tuple(
        normalize_permission_scopes(policy.get("permission_scopes", []))
    )
    if not required_scopes:
        # Policy construction is expected to be fail-closed already.  Retain a
        # final local guard so a malformed future catalog cannot grant access.
        required_scopes = ("custom.execute",)
    configured_scopes = tuple(_configured_permissions(event))
    missing_scopes = tuple(
        scope for scope in required_scopes if scope not in configured_scopes
    )
    definition_digest = action_definition_digest(action)
    schedule_digest = _schedule_digest(event)
    policy_digest = str(policy.get("policy_digest") or "")
    if not _SHA256_RE.fullmatch(policy_digest):
        raise ValueError("scheduled tool policy digest is invalid")
    request_payload = {
        "schema_version": AUTHORIZATION_SCHEMA_VERSION,
        "event_id": resolved_event_id,
        "action_id": resolved_action_id,
        "occurrence_at_ms": occurrence_ms,
        "action_definition_digest": definition_digest,
        "schedule_digest": schedule_digest,
        "configured_scopes": list(configured_scopes),
        "policy_digest": policy_digest,
    }
    request_digest = _canonical_digest(request_payload)
    authorization_id = _stable_id(
        "authorization",
        resolved_event_id,
        resolved_action_id,
        occurrence_ms,
        request_digest,
    )
    return {
        "schema_version": AUTHORIZATION_SCHEMA_VERSION,
        "id": authorization_id,
        "status": AUTHORIZATION_REQUIRED,
        "event_id": resolved_event_id,
        "action_id": resolved_action_id,
        "occurrence_at": occurrence_ms / 1000.0,
        "request_digest": request_digest,
        "action_definition_digest": definition_digest,
        "schedule_digest": schedule_digest,
        "policy_id": str(policy.get("policy_id") or "scheduled-tool-auth:v1"),
        "policy_digest": policy_digest,
        "required_scopes": list(required_scopes),
        "configured_scopes": list(configured_scopes),
        "missing_scopes": list(missing_scopes),
        "permission_status": "granted" if not missing_scopes else "missing",
        "approval_required": bool(policy.get("approval_required", True)),
        "can_approve": not missing_scopes,
    }


def mark_authorization_required(
    action: MutableMapping[str, Any],
    request: Mapping[str, Any],
    *,
    requested_at: Optional[float] = None,
) -> JsonDict:
    """Attach a server-owned pending request to an action."""

    state = dict(request)
    state["status"] = AUTHORIZATION_REQUIRED
    state["requested_at"] = float(requested_at or time.time())
    action["authorization"] = state
    action["status"] = AUTHORIZATION_REQUIRED
    return state


def authorization_allows_dispatch(
    event: Mapping[str, Any],
    action: Mapping[str, Any],
    request: Mapping[str, Any],
) -> bool:
    """Return whether the exact current action may cross the dispatch boundary."""

    try:
        current = build_authorization_request(
            str(event.get("id") or request.get("event_id") or ""),
            event,
            _action_id(action, str(request.get("action_id") or "")),
            action,
            float(request.get("occurrence_at")),
        )
    except (TypeError, ValueError):
        return False
    if current["id"] != request.get("id") or current["request_digest"] != request.get(
        "request_digest"
    ):
        return False
    if current["missing_scopes"]:
        return False
    if not current["approval_required"]:
        return True
    authorization = action.get("authorization")
    if not isinstance(authorization, Mapping):
        return False
    return bool(
        authorization.get("id") == current["id"]
        and authorization.get("request_digest") == current["request_digest"]
        and str(authorization.get("status") or "").strip().lower()
        in {AUTHORIZATION_APPROVED_ONCE, AUTHORIZATION_CONSUMED}
    )


def consume_authorization(
    action: MutableMapping[str, Any],
    request: Mapping[str, Any],
    *,
    consumed_at: Optional[float] = None,
) -> bool:
    """Consume one matching approval inside a caller-owned event-store CAS."""

    authorization = action.get("authorization")
    if not isinstance(authorization, dict):
        return False
    if (
        authorization.get("id") != request.get("id")
        or authorization.get("request_digest") != request.get("request_digest")
        or str(authorization.get("status") or "").strip().lower()
        != AUTHORIZATION_APPROVED_ONCE
    ):
        return False
    authorization = {
        **authorization,
        "status": AUTHORIZATION_CONSUMED,
        "consumed_at": float(consumed_at or time.time()),
        "can_approve": False,
    }
    action["authorization"] = authorization
    return True


def invalidate_authorization(
    action: MutableMapping[str, Any],
    *,
    reason: str,
    at: Optional[float] = None,
) -> bool:
    """Invalidate a pending/unconsumed decision without erasing its evidence."""

    authorization = action.get("authorization")
    if not isinstance(authorization, dict):
        return False
    status = str(authorization.get("status") or "").strip().lower()
    if status not in _ACTIVE_AUTHORIZATION_STATUSES:
        return False
    action["authorization"] = {
        **authorization,
        "status": AUTHORIZATION_INVALIDATED,
        "invalidated_at": float(at or time.time()),
        "invalidation_reason": str(reason or "authorization_input_changed"),
        "can_approve": False,
    }
    if str(action.get("status") or "").strip().lower() in {
        AUTHORIZATION_REQUIRED,
        "authorization_approved",
    }:
        action["status"] = "scheduled"
    return True


def expire_authorization_for_occurrence(
    action: MutableMapping[str, Any],
    current_occurrence_at: float,
    *,
    at: Optional[float] = None,
) -> bool:
    """Expire an unconsumed approval when a recurrence advances."""

    authorization = action.get("authorization")
    if not isinstance(authorization, dict):
        return False
    status = str(authorization.get("status") or "").strip().lower()
    if status not in _ACTIVE_AUTHORIZATION_STATUSES:
        return False
    try:
        stored_ms = _occurrence_millis(authorization.get("occurrence_at"))
        current_ms = _occurrence_millis(current_occurrence_at)
    except ValueError:
        return invalidate_authorization(
            action, reason="invalid_occurrence_binding", at=at
        )
    if stored_ms == current_ms:
        return False
    action["authorization"] = {
        **authorization,
        "status": AUTHORIZATION_EXPIRED,
        "invalidated_at": float(at or time.time()),
        "invalidation_reason": "occurrence_expired",
        "can_approve": False,
    }
    if str(action.get("status") or "").strip().lower() in {
        AUTHORIZATION_REQUIRED,
        "authorization_approved",
    }:
        action["status"] = "scheduled"
    return True


def invalidate_event_authorizations_for_edit(
    previous_event: Mapping[str, Any],
    updated_event: MutableMapping[str, Any],
    *,
    at: Optional[float] = None,
) -> list[JsonDict]:
    """Invalidate decisions affected by action, schedule, or policy edits."""

    previous_actions = previous_event.get("actions")
    previous_actions = previous_actions if isinstance(previous_actions, list) else []
    updated_actions = updated_event.get("actions")
    updated_actions = updated_actions if isinstance(updated_actions, list) else []
    previous_by_id = {
        _action_id(action): action
        for action in previous_actions
        if isinstance(action, Mapping) and _action_id(action)
    }
    invalidated: list[JsonDict] = []
    updated_ids = {
        _action_id(action)
        for action in updated_actions
        if isinstance(action, Mapping) and _action_id(action)
    }
    for action in updated_actions:
        if not isinstance(action, MutableMapping):
            continue
        action_id = _action_id(action)
        previous_action = previous_by_id.get(action_id)
        if not isinstance(previous_action, Mapping):
            continue
        previous_auth = previous_action.get("authorization")
        if not isinstance(previous_auth, Mapping):
            continue
        status = str(previous_auth.get("status") or "").strip().lower()
        if status not in _ACTIVE_AUTHORIZATION_STATUSES:
            continue
        action["authorization"] = dict(previous_auth)
        occurrence_at = previous_auth.get("occurrence_at")
        try:
            current = build_authorization_request(
                str(updated_event.get("id") or previous_event.get("id") or ""),
                updated_event,
                action_id,
                action,
                float(occurrence_at),
            )
        except (TypeError, ValueError):
            current = {}
        if current.get("id") == previous_auth.get("id") and current.get(
            "request_digest"
        ) == previous_auth.get("request_digest"):
            continue
        if invalidate_authorization(action, reason="event_edited", at=at):
            if external_control_revision(action) == external_control_revision(
                previous_action
            ):
                bump_external_control_revision(action)
            invalidated.append(dict(action["authorization"]))
    for action_id, previous_action in previous_by_id.items():
        if action_id in updated_ids:
            continue
        previous_auth = previous_action.get("authorization")
        if not isinstance(previous_auth, Mapping):
            continue
        detached_action = dict(previous_action)
        detached_action["authorization"] = dict(previous_auth)
        if invalidate_authorization(detached_action, reason="action_removed", at=at):
            bump_external_control_revision(detached_action)
            invalidated.append(dict(detached_action["authorization"]))
    return invalidated


def _find_action(event: Mapping[str, Any], action_id: str) -> Optional[JsonDict]:
    actions = event.get("actions")
    actions = actions if isinstance(actions, list) else []
    for action in actions:
        if isinstance(action, dict) and _action_id(action) == action_id:
            return action
    return None


def apply_authorization_decision(
    event_id: str,
    action_id: str,
    *,
    decision: Literal["approve_once", "deny"],
    authorization_id: str,
    request_digest: str,
    occurrence_at: float,
    decided_at: Optional[float] = None,
    event_mutator: Optional[Callable[[JsonDict, JsonDict], None]] = None,
) -> JsonDict:
    """CAS-apply an interactive decision to the exact server-owned request."""

    resolved_event_id = str(event_id or "").strip()
    resolved_action_id = str(action_id or "").strip()
    resolved_authorization_id = str(authorization_id or "").strip()
    resolved_digest = str(request_digest or "").strip()
    if decision not in {"approve_once", "deny"}:
        raise ValueError("decision must be approve_once or deny")
    if not resolved_event_id or not resolved_action_id or not resolved_authorization_id:
        raise ValueError("event_id, action_id, and authorization_id are required")
    if not _SHA256_RE.fullmatch(resolved_digest):
        raise ValueError("request_digest must be a sha256 digest")
    occurrence_ms = _occurrence_millis(occurrence_at)
    decision_time = float(decided_at or time.time())
    outcome: JsonDict = {}

    def decide(latest: JsonDict) -> Optional[JsonDict]:
        nonlocal outcome
        action = _find_action(latest, resolved_action_id)
        if action is None:
            raise AuthorizationNotFoundError("scheduled action not found")
        authorization = action.get("authorization")
        if not isinstance(authorization, dict):
            raise AuthorizationNotFoundError("authorization request not found")
        try:
            current = build_authorization_request(
                resolved_event_id,
                latest,
                resolved_action_id,
                action,
                occurrence_ms / 1000.0,
            )
        except ValueError as exc:
            raise AuthorizationConflictError(
                "authorization request can no longer be reconstructed"
            ) from exc
        supplied_matches = bool(
            current["id"] == resolved_authorization_id
            and current["request_digest"] == resolved_digest
            and _occurrence_millis(current["occurrence_at"]) == occurrence_ms
            and authorization.get("id") == resolved_authorization_id
            and authorization.get("request_digest") == resolved_digest
        )
        if not supplied_matches:
            raise AuthorizationConflictError(
                "authorization changed; review the current request"
            )
        existing_status = str(authorization.get("status") or "").strip().lower()
        target_status = (
            AUTHORIZATION_APPROVED_ONCE
            if decision == "approve_once"
            else AUTHORIZATION_DENIED
        )
        if existing_status == target_status:
            outcome = {
                "status": target_status,
                "event_id": resolved_event_id,
                "action_id": resolved_action_id,
                "authorization": dict(authorization),
                "idempotent": True,
            }
            if event_mutator is not None:
                event_mutator(latest, outcome)
                return latest
            return None
        if existing_status != AUTHORIZATION_REQUIRED:
            raise AuthorizationConflictError(
                f"authorization is already {existing_status or 'resolved'}"
            )
        if decision == "approve_once" and current["missing_scopes"]:
            raise AuthorizationPermissionError(
                "job permission ceiling is missing: "
                + ", ".join(current["missing_scopes"])
            )

        decision_id = _stable_id(
            "authorization-decision", resolved_authorization_id, decision
        )
        updated_authorization = {
            **authorization,
            **current,
            "status": target_status,
            "decision": decision,
            "decision_id": decision_id,
            "decided_at": decision_time,
            "actor_kind": "interactive_client",
            "can_approve": False,
        }
        action["authorization"] = updated_authorization
        recurring = bool(latest.get("rrule"))
        if decision == "approve_once":
            action["status"] = "authorization_approved"
            if str(latest.get("status") or "").strip().lower() == (
                AUTHORIZATION_REQUIRED
            ):
                latest["status"] = "scheduled"
        else:
            action["last_occurrence_at"] = occurrence_ms / 1000.0
            if recurring:
                action["status"] = "scheduled"
                latest["status"] = "scheduled"
            else:
                action["status"] = AUTHORIZATION_DENIED
                action["executed_at"] = decision_time
                latest["status"] = "prompted"
        bump_external_control_revision(action)
        outcome = {
            "status": target_status,
            "event_id": resolved_event_id,
            "action_id": resolved_action_id,
            "authorization": dict(updated_authorization),
            "idempotent": False,
        }
        if event_mutator is not None:
            event_mutator(latest, outcome)
        return latest

    stored = calendar_store.update_event(resolved_event_id, decide)
    if not stored:
        raise AuthorizationNotFoundError("calendar event not found")
    if not outcome:
        raise AuthorizationConflictError("authorization decision was not recorded")
    return outcome


__all__ = [
    "AUTHORIZATION_APPROVED_ONCE",
    "AUTHORIZATION_CONSUMED",
    "AUTHORIZATION_DENIED",
    "AUTHORIZATION_EXPIRED",
    "AUTHORIZATION_INVALIDATED",
    "AUTHORIZATION_REQUIRED",
    "AUTHORIZATION_SCHEMA_VERSION",
    "AuthorizationConflictError",
    "AuthorizationNotFoundError",
    "AuthorizationPermissionError",
    "ScheduledActionAuthorizationError",
    "action_definition_digest",
    "apply_authorization_decision",
    "authorization_allows_dispatch",
    "build_authorization_request",
    "consume_authorization",
    "expire_authorization_for_occurrence",
    "invalidate_authorization",
    "invalidate_event_authorizations_for_edit",
    "mark_authorization_required",
]
