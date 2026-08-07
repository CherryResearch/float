"""Durable cooperative cancellation requests for Calendar-backed actions."""

from __future__ import annotations

import hashlib
import json
import math
import time
from typing import Any, Dict, Mapping, Optional

from app.services.calendar_jobs import bump_external_control_revision
from app.utils import calendar_store

JsonDict = Dict[str, Any]

_RUNNING_STATUSES = frozenset({"followup_running", "running"})
_POST_TOOL_RESUMABLE_STATUSES = frozenset({"followup_pending"})
_ATTENTION_STATUSES = frozenset(
    {"interrupted_unknown", "orphaned", "reconcile_required", "unknown"}
)
_SAFE_EFFECT_STATUSES = frozenset({"acknowledged", "confirmed", "not_dispatched"})
_UNCERTAIN_EFFECT_STATUSES = frozenset({"dispatched", "interrupted_unknown", "unknown"})
_UNCERTAIN_CERTAINTIES = frozenset({"uncertain", "unconfirmed", "unknown"})
_FINISHED_STATUSES = frozenset(
    {
        "acknowledged",
        "authorization_denied",
        "cancelled",
        "complete",
        "error",
        "invoked",
        "prompted",
        "skipped",
    }
)


class ScheduledActionCancellationNotFound(LookupError):
    """The event or requested action does not exist."""


class ScheduledActionCancellationConflict(RuntimeError):
    """The cancellation request no longer identifies the active run."""


def _stable_id(*parts: Any) -> str:
    encoded = json.dumps(
        [str(part) for part in parts],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"cancel-sha256:{hashlib.sha256(encoded).hexdigest()}"


def _finite_time(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) and result > 0 else None


def _action_id(action: Mapping[str, Any], event_id: str, index: int) -> str:
    return str(
        action.get("request_id") or action.get("id") or f"{event_id}:tool:{index}"
    )


def _normalized(value: Any) -> str:
    return str(value or "").strip().lower()


def _dispatch_evidence(
    action: Mapping[str, Any],
    receipt: Mapping[str, Any],
    *,
    status: str,
) -> bool:
    """Return whether Calendar contains any evidence work may have dispatched."""

    return bool(
        status
        in _POST_TOOL_RESUMABLE_STATUSES
        | _ATTENTION_STATUSES
        | {"prompt_resume_pending"}
        or action.get("effect_id")
        or _normalized(action.get("effect_status"))
        or _normalized(action.get("effect_certainty"))
        or action.get("tool_invoked") is True
        or action.get("prompt_invoked") is True
        or action.get("reconcile_required") is True
        or action.get("followup_started_at") is not None
        or receipt.get("effect_id")
        or _normalized(receipt.get("effect_status"))
        or _normalized(receipt.get("effect_certainty"))
        or receipt.get("tool_invoked") is True
        or receipt.get("prompt_invoked") is True
        or receipt.get("reconcile_required") is True
    )


def _unresolved_dispatch_evidence(
    action: Mapping[str, Any], receipt: Mapping[str, Any], *, status: str
) -> bool:
    effect_statuses = {
        _normalized(action.get("effect_status")),
        _normalized(receipt.get("effect_status")),
    }
    certainties = {
        _normalized(action.get("effect_certainty")),
        _normalized(receipt.get("effect_certainty")),
        _normalized(receipt.get("state_delta_certainty")),
    }
    has_effect_id = bool(action.get("effect_id") or receipt.get("effect_id"))
    has_safe_effect_status = bool(effect_statuses & _SAFE_EFFECT_STATUSES)
    return bool(
        status in _ATTENTION_STATUSES | {"prompt_resume_pending"}
        or action.get("reconcile_required") is True
        or receipt.get("reconcile_required") is True
        or effect_statuses & _UNCERTAIN_EFFECT_STATUSES
        or certainties & _UNCERTAIN_CERTAINTIES
        or (has_effect_id and not has_safe_effect_status)
    )


def _occurrence_for_action(action: Mapping[str, Any]) -> Optional[float]:
    occurrence = _finite_time(action.get("running_occurrence_at"))
    if occurrence is not None:
        return occurrence
    authorization = action.get("authorization")
    if isinstance(authorization, Mapping):
        return _finite_time(authorization.get("occurrence_at"))
    return None


def cancellation_requested(
    action: Mapping[str, Any], *, expected_run_id: str = ""
) -> bool:
    """Return whether the current exact run has a durable cancellation request."""

    if action.get("cancel_requested") is not True:
        return False
    expected = str(expected_run_id or "").strip()
    actual = str(action.get("run_id") or "").strip()
    return not expected or expected == actual


def request_scheduled_action_cancellation(
    event_id: str,
    action_id: str,
    *,
    expected_run_id: str = "",
    requested_at: Optional[float] = None,
) -> JsonDict:
    """Persist one exact cancellation request without claiming termination."""

    safe_event_id = str(event_id or "").strip()
    safe_action_id = str(action_id or "").strip()
    safe_run_id = str(expected_run_id or "").strip()
    if not safe_event_id or not safe_action_id:
        raise ValueError("event_id and action_id are required")
    request_time = float(time.time() if requested_at is None else requested_at)
    if not math.isfinite(request_time) or request_time <= 0:
        raise ValueError("requested_at must be a finite positive timestamp")
    outcome: JsonDict = {}

    def request(latest: JsonDict) -> Optional[JsonDict]:
        nonlocal outcome
        actions = latest.get("actions")
        actions = list(actions) if isinstance(actions, list) else []
        for index, raw_action in enumerate(actions):
            if not isinstance(raw_action, dict):
                continue
            if _action_id(raw_action, safe_event_id, index) != safe_action_id:
                continue
            action = dict(raw_action)
            current_run_id = str(action.get("run_id") or "").strip()
            if safe_run_id and current_run_id != safe_run_id:
                raise ScheduledActionCancellationConflict(
                    "the requested run is no longer active"
                )
            status = str(action.get("status") or "").strip().lower()
            history = latest.get("run_history")
            history = list(history) if isinstance(history, list) else []
            receipt_id = str(action.get("work_run_receipt_id") or "").strip()
            receipt_index: Optional[int] = None
            receipt: JsonDict = {}
            for history_index, raw_receipt in enumerate(history):
                if not isinstance(raw_receipt, dict):
                    continue
                matches = bool(
                    (receipt_id and str(raw_receipt.get("id") or "") == receipt_id)
                    or (
                        current_run_id
                        and str(raw_receipt.get("run_id") or "") == current_run_id
                        and str(raw_receipt.get("action_id") or "") == safe_action_id
                    )
                )
                if matches:
                    receipt_index = history_index
                    receipt = dict(raw_receipt)
                    break
            has_dispatch_evidence = _dispatch_evidence(action, receipt, status=status)
            unresolved_dispatch = _unresolved_dispatch_evidence(
                action, receipt, status=status
            )
            if action.get("cancel_requested") is True:
                existing_request_id = str(action.get("cancel_request_id") or "")
                expected_request_id = _stable_id(
                    safe_event_id,
                    safe_action_id,
                    current_run_id,
                )
                if existing_request_id != expected_request_id:
                    raise ScheduledActionCancellationConflict(
                        "a different cancellation request is already recorded"
                    )
                reported_status = (
                    status
                    if status in _FINISHED_STATUSES | _ATTENTION_STATUSES
                    else "cancel_requested"
                )
                outcome = {
                    "status": reported_status,
                    "event_id": safe_event_id,
                    "action_id": safe_action_id,
                    "run_id": current_run_id or None,
                    "cancel_request_id": existing_request_id,
                    "idempotent": True,
                    "termination_confirmed": status == "cancelled",
                    "reconcile_required": bool(
                        status in _ATTENTION_STATUSES
                        or action.get("reconcile_required") is True
                    ),
                    "tool_invoked": (
                        action.get("tool_invoked")
                        if isinstance(action.get("tool_invoked"), bool)
                        else receipt.get("tool_invoked")
                    ),
                }
                return None
            if status in _FINISHED_STATUSES:
                raise ScheduledActionCancellationConflict(
                    f"scheduled action is already {status}"
                )

            cancel_request_id = _stable_id(
                safe_event_id,
                safe_action_id,
                current_run_id,
            )
            action.update(
                {
                    "cancel_requested": True,
                    "cancel_request_id": cancel_request_id,
                    "cancel_requested_at": request_time,
                }
            )
            before_dispatch = bool(
                status not in _RUNNING_STATUSES and not has_dispatch_evidence
            )
            inconsistent_dispatch_state = bool(
                has_dispatch_evidence
                and status not in _RUNNING_STATUSES | _POST_TOOL_RESUMABLE_STATUSES
            )
            reconcile_now = bool(
                status not in _RUNNING_STATUSES
                and (unresolved_dispatch or inconsistent_dispatch_state)
            )
            if before_dispatch:
                action["status"] = "cancelled"
                action["cancelled_at"] = request_time
                action["tool_invoked"] = False
                occurrence = _occurrence_for_action(action)
                if occurrence is not None:
                    action["last_occurrence_at"] = occurrence
                authorization = action.get("authorization")
                if isinstance(authorization, dict) and str(
                    authorization.get("status") or ""
                ).strip().lower() in {"authorization_required", "approved_once"}:
                    action["authorization"] = {
                        **authorization,
                        "status": "invalidated",
                        "invalidated_at": request_time,
                        "invalidation_reason": "user_cancelled",
                        "can_approve": False,
                    }
                latest["status"] = "scheduled" if latest.get("rrule") else "cancelled"
            elif reconcile_now:
                action["status"] = "reconcile_required"
                action["reconcile_required"] = True
                action["error"] = (
                    "Cancellation found prior dispatch or effect evidence that "
                    "does not match a safely pending action; reconciliation is "
                    "required."
                )
                action["tool_invoked"] = bool(
                    action.get("tool_invoked") is True
                    or receipt.get("tool_invoked") is True
                )
                occurrence = _occurrence_for_action(action)
                if occurrence is not None:
                    action["last_occurrence_at"] = occurrence
                latest["status"] = "scheduled" if latest.get("rrule") else "prompted"
            bump_external_control_revision(action)
            actions[index] = action
            latest["actions"] = actions

            if receipt_index is not None:
                receipt_status = (
                    "cancelled"
                    if before_dispatch
                    else "reconcile_required"
                    if reconcile_now
                    else "cancel_requested"
                )
                receipt.update(
                    {
                        "status": receipt_status,
                        "phase": "cancellation",
                        "summary": (
                            "Scheduled action cancelled before dispatch."
                            if before_dispatch
                            else "Cancellation found dispatch evidence; reconciliation is required."
                            if reconcile_now
                            else "Cancellation requested; waiting for the running action to acknowledge it."
                        ),
                        "tool_invoked": False
                        if before_dispatch
                        else action.get("tool_invoked")
                        if isinstance(action.get("tool_invoked"), bool)
                        else receipt.get("tool_invoked"),
                        "state_delta_certainty": (
                            "confirmed_no_change"
                            if before_dispatch
                            else "unknown"
                            if reconcile_now
                            else receipt.get("state_delta_certainty")
                        ),
                        "reconcile_required": True
                        if reconcile_now
                        else receipt.get("reconcile_required"),
                    }
                )
                if before_dispatch or reconcile_now:
                    receipt["finished_at"] = request_time
                else:
                    receipt.pop("finished_at", None)
                history[receipt_index] = receipt
            latest["run_history"] = history
            result_status = (
                "cancelled"
                if before_dispatch
                else "reconcile_required"
                if reconcile_now
                else "cancel_requested"
            )
            outcome = {
                "status": result_status,
                "event_id": safe_event_id,
                "action_id": safe_action_id,
                "run_id": current_run_id or None,
                "cancel_request_id": cancel_request_id,
                "idempotent": False,
                "termination_confirmed": before_dispatch,
                "tool_invoked": (
                    False
                    if before_dispatch
                    else action.get("tool_invoked")
                    if isinstance(action.get("tool_invoked"), bool)
                    else receipt.get("tool_invoked")
                ),
                "reconcile_required": reconcile_now,
            }
            return latest
        raise ScheduledActionCancellationNotFound("scheduled action not found")

    stored = calendar_store.update_event(safe_event_id, request)
    if not stored:
        raise ScheduledActionCancellationNotFound("calendar event not found")
    return outcome


__all__ = [
    "ScheduledActionCancellationConflict",
    "ScheduledActionCancellationNotFound",
    "cancellation_requested",
    "request_scheduled_action_cancellation",
]
