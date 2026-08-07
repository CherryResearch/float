"""User-directed reconciliation for uncertain background-work effects.

Reconciliation records what the user observed.  It never invokes or retries the
tool whose effect became uncertain.
"""

from __future__ import annotations

import math
import time
from typing import Any, Dict, Iterable, Mapping, Optional

from app.services.calendar_jobs import bump_external_control_revision
from app.services.work_run_store import WorkRunStore, WorkRunTransitionConflict

JsonDict = Dict[str, Any]

CONFIRM_APPLIED = "confirm_applied"
CONFIRM_NO_CHANGE = "confirm_no_change"
_DECISIONS = frozenset({CONFIRM_APPLIED, CONFIRM_NO_CHANGE})
_UNRESOLVED_EFFECT_STATUSES = frozenset(
    {"dispatched", "interrupted_unknown", "unknown"}
)
_APPLIED_EFFECT_STATUSES = frozenset({"acknowledged"})
_NO_CHANGE_EFFECT_STATUSES = frozenset({"intent", "intended", "not_dispatched"})
_APPLIED_CERTAINTIES = frozenset(
    {"changed", "confirmed_changed", "reported_success", "user_confirmed_applied"}
)
_NO_CHANGE_CERTAINTIES = frozenset({"confirmed_no_change", "user_confirmed_no_change"})


class WorkEffectNotFound(LookupError):
    """Raised when a receipt or its requested child effect does not exist."""


class WorkEffectReconciliationConflict(RuntimeError):
    """Raised when an effect cannot accept the requested user decision."""

    def __init__(self, effect_id: str, *, status: str, decision: str) -> None:
        self.effect_id = effect_id
        self.status = status
        self.decision = decision
        super().__init__(
            f"effect {effect_id!r} cannot accept {decision!r} from status {status!r}"
        )


class CalendarActionNotFound(LookupError):
    """Raised when a reconciliation receipt no longer has its Calendar action."""


def _normalize_decision(value: Any) -> str:
    decision = str(value or "").strip().lower()
    if decision not in _DECISIONS:
        raise ValueError(
            "reconciliation decision must be confirm_applied or confirm_no_change"
        )
    return decision


def _find_effect(
    store: WorkRunStore, receipt_id: str, effect_id: str
) -> Optional[JsonDict]:
    for effect in store.list_effects(receipt_id, limit=500):
        if str(effect.get("id") or "") == effect_id:
            return effect
    return None


def _finite_timestamp(value: Any) -> Optional[float]:
    try:
        timestamp = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return timestamp if math.isfinite(timestamp) else None


def _effect_resolution(effect: Mapping[str, Any]) -> Optional[str]:
    """Classify one child only when its current evidence is safe to summarize."""

    status = str(effect.get("status") or "").strip().lower()
    certainty = str(effect.get("certainty") or "").strip().lower()
    decision = str(effect.get("reconciliation_decision") or "").strip().lower()
    if (
        effect.get("reconcile_required") is True
        or status in _UNRESOLVED_EFFECT_STATUSES
    ):
        return None
    if decision == CONFIRM_APPLIED or certainty in _APPLIED_CERTAINTIES:
        return "applied"
    if decision == CONFIRM_NO_CHANGE or certainty in _NO_CHANGE_CERTAINTIES:
        return "no_change"
    if status in _APPLIED_EFFECT_STATUSES:
        return "applied"
    if status in _NO_CHANGE_EFFECT_STATUSES:
        return "no_change"
    return None


def _aggregate_effects(effects: Iterable[Mapping[str, Any]]) -> JsonDict:
    """Summarize every sibling after the target effect CAS has committed."""

    current = [dict(effect) for effect in effects]
    effect_ids = [str(effect.get("id") or "").strip() for effect in current]
    effect_ids = [effect_id for effect_id in effect_ids if effect_id]
    applied_ids = []
    no_change_ids = []
    unresolved_ids = []
    resolution_times = []
    for effect in current:
        effect_id = str(effect.get("id") or "").strip()
        resolution = _effect_resolution(effect)
        if resolution == "applied":
            applied_ids.append(effect_id)
        elif resolution == "no_change":
            no_change_ids.append(effect_id)
        else:
            unresolved_ids.append(effect_id)
            continue
        for field in ("reconciled_at", "confirmed_at", "finished_at", "updated_at"):
            if (timestamp := _finite_timestamp(effect.get(field))) is not None:
                resolution_times.append(timestamp)
                break

    all_resolved = not unresolved_ids
    if not all_resolved:
        outcome = "partially_applied" if applied_ids else "reconcile_required"
        summary = f"{len(unresolved_ids)} of {len(current)} effects still need reconciliation."
        if applied_ids:
            applied_phrase = "effect was" if len(applied_ids) == 1 else "effects were"
            summary = (
                f"At least {len(applied_ids)} {applied_phrase} confirmed applied; "
                + summary
            )
        projection = {
            "status": "reconcile_required",
            "phase": "reconciliation",
            "effect_status": "unknown",
            "effect_certainty": "unknown",
            "state_delta_certainty": (
                "confirmed_changed" if applied_ids else "unknown"
            ),
            "reconcile_required": True,
            "reconciliation_outcome": outcome,
            "summary": summary,
        }
    elif applied_ids and no_change_ids:
        projection = {
            "status": "invoked",
            "phase": "reconciled",
            "effect_status": "confirmed",
            "effect_certainty": "mixed_user_confirmed",
            "state_delta_certainty": "confirmed_changed",
            "reconcile_required": False,
            "reconciliation_outcome": "mixed",
            "summary": (
                f"Reconciliation confirmed {len(applied_ids)} "
                f"{'effect was' if len(applied_ids) == 1 else 'effects were'} applied and "
                f"{len(no_change_ids)} made no change."
            ),
        }
    elif applied_ids:
        projection = {
            "status": "invoked",
            "phase": "reconciled",
            "effect_status": "confirmed",
            "effect_certainty": "user_confirmed_applied",
            "state_delta_certainty": "confirmed_changed",
            "reconcile_required": False,
            "reconciliation_outcome": "applied",
            "summary": "User confirmed the uncertain effect was applied.",
        }
    else:
        projection = {
            "status": "skipped",
            "phase": "reconciled",
            "effect_status": "confirmed",
            "effect_certainty": "user_confirmed_no_change",
            "state_delta_certainty": "confirmed_no_change",
            "reconcile_required": False,
            "reconciliation_outcome": "no_change",
            "summary": "User confirmed the uncertain effect made no change.",
        }

    return {
        **projection,
        "effect_ids": effect_ids,
        "applied_effect_ids": applied_ids,
        "no_change_effect_ids": no_change_ids,
        "unresolved_effect_ids": unresolved_ids,
        "effect_count": len(current),
        "resolved_effect_count": len(current) - len(unresolved_ids),
        "unresolved_effect_count": len(unresolved_ids),
        "all_resolved": all_resolved,
        "reconciled_at": max(resolution_times) if resolution_times else None,
    }


def reconcile_work_effect(
    store: WorkRunStore,
    *,
    receipt_id: str,
    effect_id: str,
    decision: Any,
    reconciled_by: str = "local_user",
    now: Optional[float] = None,
) -> JsonDict:
    """Confirm one uncertain effect and aggregate its siblings without replay."""

    safe_receipt_id = str(receipt_id or "").strip()
    safe_effect_id = str(effect_id or "").strip()
    if not safe_receipt_id or not safe_effect_id:
        raise WorkEffectNotFound("receipt and effect identifiers are required")
    normalized_decision = _normalize_decision(decision)
    receipt = store.get(safe_receipt_id)
    if receipt is None:
        raise WorkEffectNotFound(f"work run receipt {safe_receipt_id!r} was not found")
    effect = _find_effect(store, safe_receipt_id, safe_effect_id)
    if effect is None:
        raise WorkEffectNotFound(
            f"effect {safe_effect_id!r} was not found on receipt {safe_receipt_id!r}"
        )

    status = str(effect.get("status") or "").strip().lower()
    previous_decision = str(effect.get("reconciliation_decision") or "").strip().lower()
    idempotent = status == "confirmed" and previous_decision == normalized_decision
    if status == "confirmed" and not idempotent:
        raise WorkEffectReconciliationConflict(
            safe_effect_id,
            status=status,
            decision=normalized_decision,
        )
    if status not in _UNRESOLVED_EFFECT_STATUSES and not idempotent:
        raise WorkEffectReconciliationConflict(
            safe_effect_id,
            status=status or "missing",
            decision=normalized_decision,
        )

    decision_time = float(time.time() if now is None else now)
    certainty = (
        "user_confirmed_applied"
        if normalized_decision == CONFIRM_APPLIED
        else "user_confirmed_no_change"
    )
    if not idempotent:
        try:
            effect = store.record_effect(
                {
                    "id": safe_effect_id,
                    "receipt_id": safe_receipt_id,
                    "status": "confirmed",
                    "certainty": certainty,
                    "reconcile_required": False,
                    "reconciliation_decision": normalized_decision,
                    "reconciled_by": str(reconciled_by or "local_user"),
                    "confirmed_at": decision_time,
                    "reconciled_at": decision_time,
                    "finished_at": decision_time,
                },
                expected_statuses=_UNRESOLVED_EFFECT_STATUSES,
            )
        except WorkRunTransitionConflict as exc:
            latest = _find_effect(store, safe_receipt_id, safe_effect_id)
            latest_status = str((latest or {}).get("status") or "").strip().lower()
            latest_decision = (
                str((latest or {}).get("reconciliation_decision") or "").strip().lower()
            )
            if latest_status == "confirmed" and latest_decision == normalized_decision:
                effect = latest or effect
                idempotent = True
            else:
                raise WorkEffectReconciliationConflict(
                    safe_effect_id,
                    status=exc.actual_status or latest_status or "missing",
                    decision=normalized_decision,
                ) from exc

    effects = store.list_effects(safe_receipt_id, limit=500)
    aggregate = _aggregate_effects(effects)
    reconciled_at = (
        _finite_timestamp(aggregate.get("reconciled_at"))
        or _finite_timestamp(effect.get("reconciled_at"))
        or decision_time
    )
    aggregate["reconciled_at"] = reconciled_at
    receipt_update = {
        **receipt,
        "id": safe_receipt_id,
        "status": aggregate["status"],
        "phase": aggregate["phase"],
        "effect_status": aggregate["effect_status"],
        "effect_certainty": aggregate["effect_certainty"],
        "state_delta_certainty": aggregate["state_delta_certainty"],
        "reconcile_required": aggregate["reconcile_required"],
        "reconciliation_outcome": aggregate["reconciliation_outcome"],
        "tool_invoked": True,
        "summary": aggregate["summary"],
    }
    if aggregate["all_resolved"]:
        receipt_update["finished_at"] = reconciled_at
    receipt_projection_fields = (
        "status",
        "phase",
        "effect_status",
        "effect_certainty",
        "state_delta_certainty",
        "reconciliation_outcome",
        "summary",
    )
    parent_already_projected = bool(
        all(
            receipt.get(field) == receipt_update.get(field)
            for field in receipt_projection_fields
        )
        and (receipt.get("reconcile_required") is True)
        == bool(receipt_update["reconcile_required"])
        and receipt.get("tool_invoked") is True
        and (
            not aggregate["all_resolved"]
            or _finite_timestamp(receipt.get("finished_at")) == reconciled_at
        )
    )
    if not (idempotent and parent_already_projected):
        receipt = store.upsert(
            receipt_update,
            source=str(receipt.get("source") or "calendar"),
        )
    return {
        "receipt": receipt,
        "effect": effect,
        "aggregate": aggregate,
        "decision": normalized_decision,
        "reconciled_at": reconciled_at,
        "idempotent": idempotent,
        "tool_replayed": False,
    }


def apply_reconciliation_to_calendar_event(
    event: JsonDict,
    *,
    event_id: str,
    action_id: str,
    receipt_id: str,
    effect_id: str,
    decision: Any,
    aggregate: Optional[Mapping[str, Any]] = None,
    now: Optional[float] = None,
) -> JsonDict:
    """Project the ledger's aggregate sibling state without dispatching anything."""

    normalized_decision = _normalize_decision(decision)
    safe_action_id = str(action_id or "").strip()
    safe_effect_id = str(effect_id or "").strip()
    if not isinstance(event, dict) or not event or not safe_action_id:
        raise CalendarActionNotFound(
            f"calendar action {safe_action_id or '<missing>'!r} was not found"
        )
    fallback_certainty = (
        "user_confirmed_applied"
        if normalized_decision == CONFIRM_APPLIED
        else "user_confirmed_no_change"
    )
    projection: JsonDict = {
        "status": "invoked" if normalized_decision == CONFIRM_APPLIED else "skipped",
        "phase": "reconciled",
        "effect_status": "confirmed",
        "effect_certainty": fallback_certainty,
        "state_delta_certainty": (
            "confirmed_changed"
            if normalized_decision == CONFIRM_APPLIED
            else "confirmed_no_change"
        ),
        "reconcile_required": False,
        "reconciliation_outcome": (
            "applied" if normalized_decision == CONFIRM_APPLIED else "no_change"
        ),
        "summary": (
            "User confirmed the uncertain effect was applied."
            if normalized_decision == CONFIRM_APPLIED
            else "User confirmed the uncertain effect made no change."
        ),
        "all_resolved": True,
    }
    aggregate_effect_ids = []
    if isinstance(aggregate, Mapping):
        for field in (
            "status",
            "phase",
            "effect_status",
            "effect_certainty",
            "state_delta_certainty",
            "reconciliation_outcome",
            "summary",
        ):
            if aggregate.get(field) is not None:
                projection[field] = aggregate[field]
        projection["reconcile_required"] = aggregate.get("reconcile_required") is True
        projection["all_resolved"] = aggregate.get("all_resolved") is True
        raw_effect_ids = aggregate.get("effect_ids")
        if isinstance(raw_effect_ids, (list, tuple)):
            aggregate_effect_ids = [
                effect_id
                for item in raw_effect_ids
                if (effect_id := str(item or "").strip())
            ]
        if safe_effect_id not in aggregate_effect_ids:
            raise WorkEffectReconciliationConflict(
                safe_effect_id,
                status="aggregate_mismatch",
                decision=normalized_decision,
            )

    actions = event.get("actions")
    actions = list(actions) if isinstance(actions, list) else []
    updated_actions = list(actions)
    matched = False
    aggregate_reconciled_at = (
        _finite_timestamp(aggregate.get("reconciled_at"))
        if isinstance(aggregate, Mapping)
        else None
    )
    reconciled_at = (
        aggregate_reconciled_at
        if aggregate_reconciled_at is not None
        else float(time.time() if now is None else now)
    )
    for index, raw_action in enumerate(actions):
        if not isinstance(raw_action, dict):
            continue
        current_id = str(
            raw_action.get("request_id")
            or raw_action.get("id")
            or f"{event_id}:tool:{index}"
        )
        if current_id != safe_action_id:
            continue
        current_receipt_id = str(raw_action.get("work_run_receipt_id") or "").strip()
        current_effect_id = str(raw_action.get("effect_id") or "").strip()
        raw_bound_effect_ids = raw_action.get("effect_ids")
        bound_effect_ids = {
            str(item or "").strip()
            for item in (
                raw_bound_effect_ids
                if isinstance(raw_bound_effect_ids, (list, tuple))
                else []
            )
            if str(item or "").strip()
        }
        if current_effect_id:
            bound_effect_ids.add(current_effect_id)
        aggregate_ids = set(aggregate_effect_ids)
        aggregate_binding_matches = bool(
            aggregate_ids
            and safe_effect_id in aggregate_ids
            and bound_effect_ids
            and bound_effect_ids.issubset(aggregate_ids)
        )
        exact_binding_matches = current_effect_id == safe_effect_id
        if current_receipt_id != str(receipt_id) or not (
            exact_binding_matches or aggregate_binding_matches
        ):
            raise CalendarActionNotFound(
                "the Calendar action now belongs to a different work run"
            )
        current_effect_status = str(raw_action.get("effect_status") or "").lower()
        current_certainty = str(raw_action.get("effect_certainty") or "").lower()
        projected_ids_match = not aggregate_ids or bound_effect_ids == aggregate_ids
        already_projected = bool(
            str(raw_action.get("status") or "").strip().lower()
            == str(projection["status"]).strip().lower()
            and current_effect_status
            == str(projection["effect_status"]).strip().lower()
            and current_certainty == str(projection["effect_certainty"]).strip().lower()
            and str(raw_action.get("state_delta_certainty") or "").strip().lower()
            == str(projection["state_delta_certainty"]).strip().lower()
            and (raw_action.get("reconcile_required") is True)
            == bool(projection["reconcile_required"])
            and str(raw_action.get("reconciliation_outcome") or "").strip().lower()
            == str(projection["reconciliation_outcome"]).strip().lower()
            and str(raw_action.get("reconciliation_summary") or "")
            == str(projection["summary"])
            and projected_ids_match
        )
        if not already_projected and not bool(
            raw_action.get("reconcile_required")
            or current_effect_status in _UNRESOLVED_EFFECT_STATUSES
            or aggregate_binding_matches
        ):
            raise CalendarActionNotFound(
                "the Calendar action is no longer awaiting this reconciliation"
            )
        action = dict(raw_action)
        action.update(
            {
                "status": projection["status"],
                "work_run_receipt_id": str(receipt_id),
                "effect_id": current_effect_id or safe_effect_id,
                "effect_status": projection["effect_status"],
                "effect_certainty": projection["effect_certainty"],
                "state_delta_certainty": projection["state_delta_certainty"],
                "reconcile_required": projection["reconcile_required"],
                "reconciliation_outcome": projection["reconciliation_outcome"],
                "reconciliation_summary": projection["summary"],
                "tool_invoked": True,
            }
        )
        if aggregate_effect_ids:
            action["effect_ids"] = aggregate_effect_ids
        if projection["all_resolved"]:
            if already_projected:
                current_executed_at = _finite_timestamp(raw_action.get("executed_at"))
                if current_executed_at is not None:
                    reconciled_at = current_executed_at
            action["executed_at"] = reconciled_at
            action.pop("error", None)
            action.pop("interrupted_at", None)
        else:
            action["error"] = projection["summary"]
        if not already_projected:
            bump_external_control_revision(action)
        updated_actions[index] = action
        matched = True
        break
    if not matched:
        raise CalendarActionNotFound(
            f"calendar action {safe_action_id!r} was not found on event {event_id!r}"
        )

    updated_event = dict(event)
    updated_event["actions"] = updated_actions
    current_event_status = str(event.get("status") or "").strip().lower()
    if current_event_status not in {"acknowledged", "cancelled", "paused", "skipped"}:
        updated_event["status"] = "scheduled" if event.get("rrule") else "prompted"

    history = event.get("run_history")
    history = list(history) if isinstance(history, list) else []
    updated_history = []
    for raw_record in history:
        if not isinstance(raw_record, dict) or str(raw_record.get("id") or "") != str(
            receipt_id
        ):
            updated_history.append(raw_record)
            continue
        record = dict(raw_record)
        record.update(
            {
                "status": projection["status"],
                "phase": projection["phase"],
                "summary": projection["summary"],
                "effect_status": projection["effect_status"],
                "effect_certainty": projection["effect_certainty"],
                "state_delta_certainty": projection["state_delta_certainty"],
                "reconcile_required": projection["reconcile_required"],
                "reconciliation_outcome": projection["reconciliation_outcome"],
                "tool_invoked": True,
            }
        )
        if aggregate_effect_ids:
            record["effect_ids"] = aggregate_effect_ids
        if projection["all_resolved"]:
            record["finished_at"] = reconciled_at
        updated_history.append(record)
    updated_event["run_history"] = updated_history
    return updated_event


__all__ = [
    "CONFIRM_APPLIED",
    "CONFIRM_NO_CHANGE",
    "CalendarActionNotFound",
    "WorkEffectNotFound",
    "WorkEffectReconciliationConflict",
    "apply_reconciliation_to_calendar_event",
    "reconcile_work_effect",
]
