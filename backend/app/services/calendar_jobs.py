"""Shared schedule helpers for calendar-backed background jobs.

Calendar events remain the source of truth for *when* work runs.  This module
keeps recurrence expansion and runner due checks on the same implementation so
the Calendar UI cannot describe occurrences that the worker interprets
differently.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from app.utils.calendar_recurrence import validate_rrule_text
from app.utils.time_resolution import resolve_timezone_name
from dateutil import rrule

JsonDict = Dict[str, Any]

_PERMISSION_SCOPE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}\.(?:read|write|execute)$")
_AUTHORIZATION_STATE_FIELDS = {
    "authorization",
    "authorization_id",
    "authorization_status",
    "authorization_request_digest",
    "authorization_occurrence_at",
    # Older experimental fields are runner-owned too; accepting them from a
    # Calendar save would let a client forge the evidence consumed by effects.
    "approval_id",
    "approval_status",
    "approved_at",
}
_EFFECT_STATE_FIELDS = {
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
}
_CANCELLATION_STATE_FIELDS = {
    "cancel_requested",
    "cancel_request_id",
    "cancel_requested_at",
    "cancelled_at",
}
_PROMPT_CHECKPOINT_STATE_FIELDS = {
    "prompt_checkpoint",
}
_EXTERNAL_CONTROL_STATE_FIELDS = {
    "external_control_revision",
}
_RUN_CONTROL_STATE_FIELDS = {
    "run_control_revision",
}

_ACTION_STATE_FIELDS = (
    {
        "status",
        "run_id",
        "running_occurrence_at",
        "started_at",
        "executed_at",
        "interrupted_at",
        "last_occurrence_at",
        "result",
        "error",
        "followup_status",
        "followup_error",
        "followup_prompt",
        "followup_tool_name",
        "followup_tool_args",
        "followup_message_id",
        "followup_started_at",
        "followup_completed_at",
        "recovery_count",
        "recovered_at",
    }
    | _AUTHORIZATION_STATE_FIELDS
    | _EFFECT_STATE_FIELDS
    | _CANCELLATION_STATE_FIELDS
    | _PROMPT_CHECKPOINT_STATE_FIELDS
    | _EXTERNAL_CONTROL_STATE_FIELDS
    | _RUN_CONTROL_STATE_FIELDS
)
_RUNNER_ACTION_FIELDS = _ACTION_STATE_FIELDS | {
    "session_id",
    "message_id",
    "chain_id",
    "origin_session_id",
    "origin_message_id",
}
_PRESERVE_IF_ABSENT_RUNNER_FIELDS = (
    _AUTHORIZATION_STATE_FIELDS
    | _EFFECT_STATE_FIELDS
    | _CANCELLATION_STATE_FIELDS
    | _PROMPT_CHECKPOINT_STATE_FIELDS
    | _EXTERNAL_CONTROL_STATE_FIELDS
    | _RUN_CONTROL_STATE_FIELDS
)


def external_control_revision(action: Mapping[str, Any]) -> int:
    """Return the monotonic revision for user/control-plane action changes."""

    value = action.get("external_control_revision")
    if isinstance(value, bool):
        return 0
    try:
        revision = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return revision if revision >= 0 else 0


def bump_external_control_revision(action: JsonDict) -> int:
    """Invalidate runner snapshots loaded before an external control change."""

    revision = external_control_revision(action) + 1
    action["external_control_revision"] = revision
    return revision


def run_control_revision(action: Mapping[str, Any]) -> Optional[int]:
    """Return the immutable control revision captured for the active run."""

    value = action.get("run_control_revision")
    if isinstance(value, bool):
        return None
    try:
        revision = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return revision if revision >= 0 else None


def normalize_permission_scopes(value: Any) -> List[str]:
    """Return a bounded, canonical scheduled-job permission ceiling."""

    if not isinstance(value, (list, tuple, set, frozenset)):
        return []
    scopes: set[str] = set()
    for item in value:
        scope = str(item or "").strip().lower()
        if _PERMISSION_SCOPE_RE.fullmatch(scope):
            scopes.add(scope)
        if len(scopes) >= 32:
            break
    return sorted(scopes)


def canonical_action_definition(action: Mapping[str, Any]) -> JsonDict:
    """Remove execution state while retaining authorization-relevant lineage."""

    return {
        str(key): value
        for key, value in action.items()
        if str(key) not in _ACTION_STATE_FIELDS
    }


def action_definition_digest(action: Mapping[str, Any]) -> str:
    """Hash the complete client-owned action definition deterministically."""

    try:
        encoded = json.dumps(
            canonical_action_definition(action),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("calendar action definition must be canonical JSON") from exc
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def event_execution_control_digest(event: Mapping[str, Any]) -> str:
    """Hash event-level fields that can change scheduled execution semantics."""

    actions = event.get("actions")
    has_explicit_actions = bool(
        isinstance(actions, list) and any(isinstance(item, Mapping) for item in actions)
    )
    payload = {
        "status": str(event.get("status") or "pending").strip().lower(),
        "start_time": event.get("start_time"),
        "start": event.get("start"),
        "timezone": event.get("timezone") or "UTC",
        "rrule": event.get("rrule"),
        "recurrence_exceptions": event.get("recurrence_exceptions") or [],
        "background_job": event.get("background_job"),
        # Legacy schedules may encode their only tool action in description.
        "legacy_description_action": (
            None if has_explicit_actions else event.get("description")
        ),
    }
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("calendar event controls must be canonical JSON") from exc
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def bump_actions_for_event_control_change(
    previous_event: Mapping[str, Any], updated_event: JsonDict
) -> bool:
    """Invalidate active runner snapshots once when event controls change."""

    if event_execution_control_digest(previous_event) == event_execution_control_digest(
        updated_event
    ):
        return False
    previous_actions = previous_event.get("actions")
    previous_actions = previous_actions if isinstance(previous_actions, list) else []
    previous_by_key = {
        _action_key(action, index): action
        for index, action in enumerate(previous_actions)
        if isinstance(action, dict)
    }
    updated_actions = updated_event.get("actions")
    updated_actions = updated_actions if isinstance(updated_actions, list) else []
    changed = False
    for index, action in enumerate(updated_actions):
        if not isinstance(action, dict):
            continue
        previous = previous_by_key.get(_action_key(action, index))
        if not isinstance(previous, dict):
            continue
        if external_control_revision(action) == external_control_revision(previous):
            bump_external_control_revision(action)
            changed = True
    return changed


def _invalidate_edited_authorization(
    action: JsonDict, *, reason: str = "action_edited"
) -> None:
    authorization = action.get("authorization")
    if not isinstance(authorization, dict):
        return
    status = str(authorization.get("status") or "").strip().lower()
    if status not in {"authorization_required", "approved_once"}:
        return
    action["authorization"] = {
        **authorization,
        "status": "invalidated",
        "invalidated_at": time.time(),
        "invalidation_reason": reason,
        "can_approve": False,
    }
    if str(action.get("status") or "").strip().lower() in {
        "authorization_required",
        "authorization_approved",
    }:
        action["status"] = "scheduled"


def _action_key(action: JsonDict, index: int) -> str:
    explicit = action.get("request_id") or action.get("id")
    stable_id = explicit if explicit is not None else f"action-{index + 1}"
    return f"id:{stable_id}"


def runner_snapshot_control_revisions_match(
    current: Sequence[JsonDict], runner_snapshot: Sequence[JsonDict]
) -> bool:
    """Reject a runner write if a matched action changed via the control plane."""

    current_by_key = {
        _action_key(action, index): action
        for index, action in enumerate(current)
        if isinstance(action, dict)
    }
    for index, action in enumerate(runner_snapshot):
        if not isinstance(action, dict):
            continue
        latest = current_by_key.get(_action_key(action, index))
        if not isinstance(latest, dict):
            return False
        if external_control_revision(latest) != external_control_revision(action):
            return False
    return True


def merge_runner_action_state(
    current: Sequence[JsonDict], runner_snapshot: Sequence[JsonDict]
) -> List[JsonDict]:
    """Merge only runner-owned fields into the latest user action definitions."""

    snapshot_by_key = {
        _action_key(action, index): action
        for index, action in enumerate(runner_snapshot)
        if isinstance(action, dict)
    }
    merged: List[JsonDict] = []
    for index, current_action in enumerate(current):
        if not isinstance(current_action, dict):
            continue
        item = dict(current_action)
        runner_action = snapshot_by_key.get(_action_key(current_action, index))
        if isinstance(runner_action, dict):
            for field in _RUNNER_ACTION_FIELDS:
                if field in runner_action:
                    item[field] = runner_action[field]
                elif (
                    field in _ACTION_STATE_FIELDS
                    and field not in _PRESERVE_IF_ABSENT_RUNNER_FIELDS
                ):
                    item.pop(field, None)
        merged.append(item)
    return merged


def merge_client_action_definitions(
    current: Sequence[JsonDict], incoming: Sequence[JsonDict]
) -> List[JsonDict]:
    """Apply user definitions without accepting forged/stale execution state."""

    current_by_key = {
        _action_key(action, index): action
        for index, action in enumerate(current)
        if isinstance(action, dict)
    }
    merged: List[JsonDict] = []
    for index, incoming_action in enumerate(incoming):
        if not isinstance(incoming_action, dict):
            continue
        key = _action_key(incoming_action, index)
        existing = current_by_key.get(key)
        item = dict(existing) if isinstance(existing, dict) else {}
        for field, field_value in incoming_action.items():
            if field not in _ACTION_STATE_FIELDS:
                item[field] = field_value
        if not isinstance(existing, dict):
            for field in _AUTHORIZATION_STATE_FIELDS:
                item.pop(field, None)
            item["status"] = "scheduled"
        elif action_definition_digest(existing) != action_definition_digest(item):
            bump_external_control_revision(item)
            _invalidate_edited_authorization(item)
        merged.append(item)
    return merged


def merge_external_calendar_update(
    previous_event: Mapping[str, Any], incoming_event: Mapping[str, Any]
) -> JsonDict:
    """Apply an imported/synced event without replacing local run evidence."""

    if not previous_event:
        return dict(incoming_event)
    merged = dict(previous_event)
    for field, value in incoming_event.items():
        if field not in {"actions", "run_history"}:
            merged[str(field)] = value
    incoming_actions = incoming_event.get("actions")
    if isinstance(incoming_actions, list):
        previous_actions = previous_event.get("actions")
        previous_actions = (
            previous_actions if isinstance(previous_actions, list) else []
        )
        merged["actions"] = merge_client_action_definitions(
            previous_actions, incoming_actions
        )
    previous_history = previous_event.get("run_history")
    if isinstance(previous_history, list):
        merged["run_history"] = list(previous_history)
    bump_actions_for_event_control_change(previous_event, merged)
    return merged


def coerce_epoch_seconds(value: Any) -> Optional[float]:
    """Return an epoch timestamp from Float's supported calendar shapes."""

    if value is None:
        return None
    if isinstance(value, (int, float)) and float(value) > 0:
        result = float(value)
        return result / 1000.0 if result > 1.0e12 else result
    if isinstance(value, str) and value.strip():
        raw = value.strip()
        try:
            result = float(raw)
            return result / 1000.0 if result > 1.0e12 else result
        except ValueError:
            pass
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    if isinstance(value, dict):
        if value.get("dateTime"):
            return coerce_epoch_seconds(value.get("dateTime"))
        if value.get("date"):
            return coerce_epoch_seconds(f"{value.get('date')}T00:00:00+00:00")
    return None


def event_start_time(event: JsonDict) -> Optional[float]:
    """Resolve the first occurrence timestamp for a stored calendar event."""

    return (
        coerce_epoch_seconds(event.get("start_time"))
        or coerce_epoch_seconds(event.get("start"))
        or coerce_epoch_seconds((event.get("start") or {}).get("dateTime"))
        or coerce_epoch_seconds((event.get("start") or {}).get("date"))
    )


def _event_timezone(event: JsonDict) -> ZoneInfo:
    return ZoneInfo(resolve_timezone_name(event.get("timezone")))


def _event_rule(event: JsonDict):
    raw_rule = event.get("rrule")
    start = event_start_time(event)
    if not raw_rule or start is None:
        return None
    normalized_rule = validate_rrule_text(raw_rule)
    timezone = _event_timezone(event)
    start_dt = datetime.fromtimestamp(start, timezone)
    return rrule.rrulestr(normalized_rule, dtstart=start_dt)


def recurrence_error(event: JsonDict) -> Optional[str]:
    """Return a compact parse error for an invalid RRULE, if present."""

    if not event.get("rrule"):
        return None
    try:
        return (
            None if _event_rule(event) is not None else "recurrence has no start time"
        )
    except (TypeError, ValueError, OverflowError) as exc:
        return str(exc) or "invalid recurrence rule"


def occurrence_times(
    event: JsonDict,
    *,
    range_start: float,
    range_end: float,
    limit: int = 512,
) -> List[float]:
    """Expand an event into occurrence timestamps inside an inclusive range."""

    start = event_start_time(event)
    if start is None or range_end < range_start:
        return []
    # API callers apply their own display cap and may request one extra item to
    # prove truncation, so keep this internal guard slightly roomier.
    safe_limit = max(1, min(int(limit), 10_000))
    if not event.get("rrule"):
        return [start] if range_start <= start <= range_end else []
    try:
        timezone = _event_timezone(event)
        rule = _event_rule(event)
        if rule is None:
            return []
        lower = datetime.fromtimestamp(range_start, timezone)
        upper = datetime.fromtimestamp(range_end, timezone)
        occurrences: List[float] = []
        for occurrence in rule.xafter(lower, count=safe_limit, inc=True):
            if occurrence > upper:
                break
            occurrences.append(occurrence.timestamp())
        return occurrences
    except (TypeError, ValueError, OverflowError):
        # A malformed imported RRULE should not break the full calendar view.
        return []


def due_occurrence_time(
    event: JsonDict,
    *,
    now: float,
) -> Optional[float]:
    """Return the latest occurrence due at ``now`` for runner de-duplication."""

    start = event_start_time(event)
    if start is None or start > now:
        return None
    if not event.get("rrule"):
        return start
    try:
        timezone = _event_timezone(event)
        rule = _event_rule(event)
        if rule is None:
            return None
        latest = rule.before(datetime.fromtimestamp(now, timezone), inc=True)
        return latest.timestamp() if latest is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def expand_events(
    events: Iterable[JsonDict],
    *,
    range_start: float,
    range_end: float,
    per_event_limit: int = 512,
) -> List[JsonDict]:
    """Return occurrence records suitable for Calendar rendering."""

    out: List[JsonDict] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        event_id = str(event.get("id") or "").strip()
        if not event_id:
            continue
        start = event_start_time(event)
        if start is None:
            continue
        raw_end = coerce_epoch_seconds(event.get("end_time"))
        duration = max(0.0, (raw_end or start) - start)
        for occurrence in occurrence_times(
            event,
            range_start=range_start,
            range_end=range_end,
            limit=per_event_limit,
        ):
            item = dict(event)
            item["source_event_id"] = event_id
            item["occurrence_time"] = occurrence
            item["start_time"] = occurrence
            if raw_end is not None:
                item["end_time"] = occurrence + duration
            item["occurrence_id"] = f"{event_id}:{int(occurrence)}"
            out.append(item)
    out.sort(key=lambda item: float(item.get("occurrence_time") or 0))
    return out


def normalize_background_job(event_id: str, event: JsonDict) -> Optional[JsonDict]:
    """Normalize optional job policy while keeping unknown future fields intact."""

    raw = event.get("background_job")
    if not isinstance(raw, dict):
        return None
    policy = dict(raw)
    policy.setdefault("schema_version", 1)

    patience = policy.get("patience")
    if not isinstance(patience, dict):
        patience = {}
    patience = dict(patience)
    patience.setdefault("stop_condition", "one_pass")
    patience.setdefault("max_attempts", 1)
    patience.setdefault("max_runtime_seconds", 900)
    patience.setdefault("satisfied_threshold", 0.8)
    policy["patience"] = patience

    execution = policy.get("execution")
    if not isinstance(execution, dict):
        execution = {}
    execution = dict(execution)
    execution.setdefault("reasoning_effort", "inherit")
    execution.setdefault("model", "inherit")
    execution.setdefault("workflow", "inherit")
    execution.setdefault("allow_subagents", True)
    execution.setdefault("sandbox_processes", True)
    execution["permissions"] = normalize_permission_scopes(
        execution.get("permissions", [])
    )
    execution["permission_semantics"] = "allowed_scopes"
    policy["execution"] = execution

    ownership = policy.get("ownership")
    if not isinstance(ownership, dict):
        ownership = {}
    ownership = dict(ownership)
    ownership["calendar_event_id"] = event_id
    ownership.setdefault("owner_kind", "calendar_event")
    policy["ownership"] = ownership
    return policy
