"""Calendar/task tools exposed to the assistant."""

from __future__ import annotations

import math
import re
from typing import Any, Dict, Optional

from app.schemas import CalendarEvent
from app.services.rag_provider import ingest_calendar_event
from app.utils import calendar_store, verify_signature
from app.utils.time_resolution import resolve_temporal_value, resolve_timezone_name

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_ACTION_KIND_ALIASES = {
    "continue_prompt": "prompt",
    "followup_prompt": "prompt",
    "follow_up_prompt": "prompt",
}
_CONVERSATION_MODE_ALIASES = {
    "current_chat": "inline",
    "current_thread": "inline",
    "inline": "inline",
    "inline_chat": "inline",
    "same_chat": "inline",
    "same_thread": "inline",
    "new": "new_chat",
    "new_chat": "new_chat",
    "new_thread": "new_chat",
    "separate_chat": "new_chat",
    "separate_thread": "new_chat",
    "task_chat": "new_chat",
}
_STATUS_ALIASES = {
    "pending": "pending",
    "proposed": "scheduled",
    "scheduled": "scheduled",
    "prompted": "prompted",
    "acknowledge": "acknowledged",
    "acknowledged": "acknowledged",
    "complete": "acknowledged",
    "completed": "acknowledged",
    "done": "acknowledged",
    "skip": "skipped",
    "skipped": "skipped",
}


def _slugify(value: str) -> str:
    return _SLUG_RE.sub("-", value.lower()).strip("-")


def _normalize_optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_status(value: Any) -> str:
    raw = _normalize_optional_str(value)
    if not raw:
        return "pending"
    return _STATUS_ALIASES.get(raw.lower(), raw.lower())


def _normalize_conversation_mode(value: Any) -> Optional[str]:
    raw = _normalize_optional_str(value)
    if not raw:
        return None
    key = raw.lower().replace("-", "_").replace(" ", "_")
    return _CONVERSATION_MODE_ALIASES.get(key)


def _coerce_timestamp(
    value: Any,
    *,
    timezone_name: Optional[str] = None,
    grounded_at: Any = None,
) -> Optional[float]:
    if isinstance(value, dict) and not isinstance(value, list):
        direct_value = (
            value.get("value")
            or value.get("datetime")
            or value.get("dateTime")
            or value.get("timestamp")
            or value.get("unix")
            or value.get("iso")
        )
        if direct_value not in (None, ""):
            resolved_timezone = resolve_timezone_name(
                value.get("timezone") or timezone_name
            )
            return _coerce_timestamp(
                direct_value,
                timezone_name=resolved_timezone,
                grounded_at=grounded_at,
            )
        date_value = value.get("date")
        time_value = value.get("time")
        if date_value not in (None, "") and time_value not in (None, ""):
            resolved_timezone = resolve_timezone_name(
                value.get("timezone") or timezone_name
            )
            combined = f"{date_value} {time_value}"
            return _coerce_timestamp(
                combined,
                timezone_name=resolved_timezone,
                grounded_at=grounded_at,
            )
    resolved = resolve_temporal_value(
        value,
        timezone_name=timezone_name,
        grounded_at=grounded_at,
        end_of_day=False,
    )
    return resolved.get("timestamp")


_DURATION_MINUTES_RE = re.compile(
    r"^\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>m|min|mins|minute|minutes|h|hr|hrs|hour|hours)?\s*$",
    re.IGNORECASE,
)


def _coerce_duration_minutes(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError("duration_min must be an integer number of minutes")
    numeric_value: Optional[float] = None
    if isinstance(value, (int, float)):
        numeric_value = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        match = _DURATION_MINUTES_RE.match(text)
        if match is None:
            raise ValueError("duration_min must be an integer number of minutes")
        numeric_value = float(match.group("value"))
        unit = (match.group("unit") or "m").lower()
        if unit.startswith("h"):
            numeric_value *= 60.0
    else:
        raise ValueError("duration_min must be an integer number of minutes")
    if numeric_value is None or not math.isfinite(numeric_value):
        raise ValueError("duration_min must be an integer number of minutes")
    return max(5, min(24 * 60, int(round(numeric_value))))


def _extract_time_payload_timezone(*values: Any) -> Optional[str]:
    for value in values:
        if not isinstance(value, dict) or isinstance(value, list):
            continue
        raw_timezone = (
            value.get("timezone") or value.get("tz") or value.get("time_zone")
        )
        normalized = resolve_timezone_name(raw_timezone, fallback_to_user=False)
        if normalized:
            return normalized
    return None


def _normalize_action(item: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict) or isinstance(item, list):
        return None
    normalized = dict(item)
    name = _normalize_optional_str(normalized.get("name") or normalized.get("tool"))
    prompt = _normalize_optional_str(
        normalized.get("prompt") or normalized.get("text") or normalized.get("message")
    )
    raw_kind = _normalize_optional_str(
        normalized.get("kind") or normalized.get("type") or normalized.get("action")
    )
    kind = ""
    if raw_kind:
        kind = raw_kind.lower().replace("-", "_").replace(" ", "_")
        kind = _ACTION_KIND_ALIASES.get(kind, kind)
    if not kind:
        if prompt and not name:
            kind = "prompt"
        elif name:
            kind = "tool"
    if kind not in {"tool", "prompt"}:
        return None
    normalized["kind"] = kind
    normalized.pop("type", None)
    if name:
        normalized["name"] = name
    if kind == "tool":
        args = normalized.get("args")
        normalized["args"] = (
            args if isinstance(args, dict) and not isinstance(args, list) else {}
        )
        if prompt:
            normalized["prompt"] = prompt
        else:
            normalized.pop("prompt", None)
    else:
        if prompt is not None:
            normalized["prompt"] = prompt
        normalized.pop("args", None)

    conversation_mode = _normalize_conversation_mode(
        normalized.get("conversation_mode")
        or normalized.get("run_target")
        or normalized.get("target")
    )
    if not conversation_mode:
        conversation_mode = "inline" if normalized.get("session_id") else "new_chat"
    normalized["conversation_mode"] = conversation_mode
    if conversation_mode != "inline":
        normalized.pop("session_id", None)
        normalized.pop("message_id", None)
        normalized.pop("chain_id", None)
    return normalized


def _normalize_actions(value: Any) -> list[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[Dict[str, Any]] = []
    for item in value:
        action = _normalize_action(item)
        if action is not None:
            normalized.append(action)
    return normalized


def _save_task_event(args: Dict[str, Any]) -> Dict[str, Any]:
    title = _normalize_optional_str(
        args.get("title") or args.get("summary") or args.get("name")
    )
    if not title:
        raise ValueError("create_task requires a non-empty 'title'")

    grounded_at = args.get("grounded_at")
    timezone_name = resolve_timezone_name(
        args.get("timezone")
        or args.get("tz")
        or args.get("time_zone")
        or _extract_time_payload_timezone(
            args.get("start_time"),
            args.get("start"),
            args.get("starts_at"),
            args.get("start_at"),
            args.get("when"),
            args.get("end_time"),
            args.get("end"),
            args.get("ends_at"),
            args.get("end_at"),
        )
    )
    start_time = _coerce_timestamp(
        args.get("start_time")
        or args.get("start")
        or args.get("starts_at")
        or args.get("start_at")
        or args.get("when"),
        timezone_name=timezone_name,
        grounded_at=grounded_at,
    )
    if start_time is None:
        raise ValueError("create_task requires a 'start_time'")

    end_time = _coerce_timestamp(
        args.get("end_time") or args.get("end") or args.get("ends_at") or args.get("end_at"),
        timezone_name=timezone_name,
        grounded_at=grounded_at,
    )
    duration_value = args.get("duration_min", args.get("durationMin"))
    if duration_value is None:
        duration_value = (
            args.get("duration")
            or args.get("duration_minutes")
            or args.get("durationMinutes")
        )
    if end_time is None and duration_value is not None:
        safe_duration = _coerce_duration_minutes(duration_value)
        end_time = start_time + safe_duration * 60
    if end_time is not None and end_time < start_time:
        raise ValueError("end_time must be greater than or equal to start_time")

    event_id = _normalize_optional_str(args.get("id"))
    if not event_id:
        event_id = f"{_slugify(title) or 'task'}-{int(start_time * 1000)}"

    event_payload = CalendarEvent(
        id=event_id,
        title=title,
        description=_normalize_optional_str(
            args.get("description") or args.get("notes")
        ),
        location=_normalize_optional_str(args.get("location")),
        start_time=start_time,
        end_time=end_time,
        rrule=_normalize_optional_str(args.get("rrule")),
        timezone=timezone_name,
        grounded_at=_coerce_timestamp(grounded_at),
        actions=_normalize_actions(args.get("actions")),
        status=_normalize_status(args.get("status")),
    ).model_dump(exclude_none=True)

    calendar_store.save_event(event_id, event_payload)
    try:
        ingest_calendar_event(event_id, event_payload)
    except Exception:
        pass
    return {"status": "saved", "event": event_payload}


def create_task(*, user: str, signature: str, **payload: Any) -> Dict[str, Any]:
    """Create or update a calendar task/event."""

    args = dict(payload)
    verify_signature(signature, user, "create_task", args)
    return _save_task_event(args)


def create_event(*, user: str, signature: str, **payload: Any) -> Dict[str, Any]:
    """Compatibility alias for create_task."""

    args = dict(payload)
    verify_signature(signature, user, "create_event", args)
    return _save_task_event(args)
