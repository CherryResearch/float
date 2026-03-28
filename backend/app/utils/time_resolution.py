from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any, Optional, TypedDict
from zoneinfo import ZoneInfo

from dateutil import parser as date_parser

from . import user_settings

_WEEKDAY_NAMES = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
_WEEKDAY_LOOKUP = {name: index for index, name in enumerate(_WEEKDAY_NAMES)}
_TIME_SUFFIX_PATTERN = (
    r"(?P<time_suffix>(?:\s+at)?(?:\s+(?:\d{1,2}(?::\d{2})?\s*(?:am|pm)?"
    r"|noon|midnight))?)"
)
_RELATIVE_DAY_PATTERN = re.compile(
    rf"\b(?P<token>today|tomorrow|yesterday)\b{_TIME_SUFFIX_PATTERN}",
    re.IGNORECASE,
)
_RELATIVE_WEEKDAY_PATTERN = re.compile(
    rf"\b(?P<modifier>next|this|last)\s+"
    rf"(?P<weekday>{'|'.join(_WEEKDAY_NAMES)})\b{_TIME_SUFFIX_PATTERN}",
    re.IGNORECASE,
)
_TIME_COMPONENT_PATTERN = re.compile(
    r"\b\d{1,2}:\d{2}\b|\b\d{1,2}\s*(?:am|pm)\b|\bnoon\b|\bmidnight\b",
    re.IGNORECASE,
)
_ISO_DATE_ONLY_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ResolvedTemporalValue(TypedDict, total=False):
    timestamp: Optional[float]
    timezone: str
    grounded_at: float
    normalized_text: str
    date_only: bool
    used_relative: bool


def _coerce_epoch_seconds(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        result = float(value)
        return result / 1000.0 if result > 1.0e12 else result
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            result = float(raw)
        except ValueError:
            return None
        return result / 1000.0 if result > 1.0e12 else result
    return None


def _current_grounded_at(grounded_at: Any = None) -> float:
    resolved = _coerce_epoch_seconds(grounded_at)
    return resolved if resolved is not None else time.time()


def _default_timezone_name() -> str:
    try:
        local_tz = datetime.now().astimezone().tzinfo
    except Exception:
        return "UTC"
    zone_key = getattr(local_tz, "key", None)
    if isinstance(zone_key, str) and zone_key.strip():
        return zone_key
    return "UTC"


def normalize_timezone_name(value: Any) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        ZoneInfo(raw)
    except Exception:
        return None
    return raw


def resolve_timezone_name(
    timezone_name: Any = None,
    *,
    fallback_to_user: bool = True,
) -> str:
    normalized = normalize_timezone_name(timezone_name)
    if normalized:
        return normalized
    if fallback_to_user:
        try:
            settings = user_settings.load_settings()
        except Exception:
            settings = {}
        normalized = normalize_timezone_name(settings.get("user_timezone"))
        if normalized:
            return normalized
    return _default_timezone_name()


def _weekday_target_date(
    modifier: str,
    weekday_name: str,
    *,
    grounded_at: float,
    timezone_name: str,
) -> datetime.date:
    tz = ZoneInfo(timezone_name)
    base_dt = datetime.fromtimestamp(grounded_at, tz=timezone.utc).astimezone(tz)
    base_date = base_dt.date()
    base_weekday = base_date.weekday()
    target_weekday = _WEEKDAY_LOOKUP[weekday_name.lower()]
    delta = target_weekday - base_weekday
    if modifier == "next":
        if delta <= 0:
            delta += 7
    elif modifier == "last":
        if delta >= 0:
            delta -= 7
    elif delta < 0:
        delta += 7
    return base_date.fromordinal(base_date.toordinal() + delta)


def _contains_time_component(value: str) -> bool:
    return bool(_TIME_COMPONENT_PATTERN.search(str(value or "")))


def _normalize_relative_text_match(
    match: re.Match[str],
    *,
    grounded_at: float,
    timezone_name: str,
) -> tuple[str, bool]:
    groups = match.groupdict()
    token = str(groups.get("token") or "").strip().lower()
    modifier = str(groups.get("modifier") or "").strip().lower()
    weekday_name = str(groups.get("weekday") or "").strip().lower()
    time_suffix = str(match.group("time_suffix") or "")
    tz = ZoneInfo(timezone_name)
    base_dt = datetime.fromtimestamp(grounded_at, tz=timezone.utc).astimezone(tz)
    if token:
        offset = {"yesterday": -1, "today": 0, "tomorrow": 1}[token]
        target_date = base_dt.date().fromordinal(base_dt.date().toordinal() + offset)
    else:
        target_date = _weekday_target_date(
            modifier,
            weekday_name,
            grounded_at=grounded_at,
            timezone_name=timezone_name,
        )
    normalized = f"{target_date.isoformat()}{time_suffix}"
    return normalized, not _contains_time_component(time_suffix)


def normalize_temporal_references(
    text: str,
    *,
    grounded_at: Any = None,
    timezone_name: Any = None,
) -> tuple[str, Optional[float], Optional[float], str]:
    source = str(text or "")
    if not source.strip():
        resolved_timezone = resolve_timezone_name(timezone_name)
        return source, None, None, resolved_timezone
    resolved_timezone = resolve_timezone_name(timezone_name)
    grounded_ts = _current_grounded_at(grounded_at)

    for pattern in (_RELATIVE_WEEKDAY_PATTERN, _RELATIVE_DAY_PATTERN):
        match = pattern.search(source)
        if match is None:
            continue
        normalized_expr, date_only = _normalize_relative_text_match(
            match,
            grounded_at=grounded_ts,
            timezone_name=resolved_timezone,
        )
        normalized_text = (
            source[: match.start()] + normalized_expr + source[match.end() :]
        )
        occurs = resolve_temporal_value(
            normalized_expr,
            timezone_name=resolved_timezone,
            grounded_at=grounded_ts,
            end_of_day=False,
        ).get("timestamp")
        review = resolve_temporal_value(
            normalized_expr,
            timezone_name=resolved_timezone,
            grounded_at=grounded_ts,
            end_of_day=date_only,
        ).get("timestamp")
        return normalized_text, occurs, review, resolved_timezone
    return source, None, None, resolved_timezone


def _looks_like_date_only(value: Any, *, original_text: str = "") -> bool:
    if isinstance(value, dict) and value.get("date") and not value.get("dateTime"):
        return True
    if not isinstance(value, str):
        return False
    text = value.strip()
    if _ISO_DATE_ONLY_PATTERN.match(text):
        return True
    if original_text and not _contains_time_component(original_text):
        return _ISO_DATE_ONLY_PATTERN.search(text) is not None
    return False


def resolve_temporal_value(
    value: Any,
    *,
    timezone_name: Any = None,
    grounded_at: Any = None,
    end_of_day: bool = False,
) -> ResolvedTemporalValue:
    resolved_timezone = resolve_timezone_name(timezone_name)
    grounded_ts = _current_grounded_at(grounded_at)
    base: ResolvedTemporalValue = {
        "timestamp": None,
        "timezone": resolved_timezone,
        "grounded_at": grounded_ts,
        "normalized_text": str(value or "").strip() if isinstance(value, str) else "",
        "date_only": False,
        "used_relative": False,
    }

    numeric = _coerce_epoch_seconds(value)
    if numeric is not None:
        base["timestamp"] = numeric
        return base

    if isinstance(value, dict):
        if value.get("dateTime"):
            return resolve_temporal_value(
                value.get("dateTime"),
                timezone_name=resolved_timezone,
                grounded_at=grounded_ts,
                end_of_day=end_of_day,
            )
        if value.get("date"):
            return resolve_temporal_value(
                str(value.get("date")),
                timezone_name=resolved_timezone,
                grounded_at=grounded_ts,
                end_of_day=end_of_day,
            )

    if value is None:
        return base

    if not isinstance(value, str):
        raise ValueError(
            "time values must be numeric unix timestamps, ISO datetimes, or natural-language dates"
        )

    text = value.strip()
    if not text:
        return base

    (
        normalized_text,
        occurs_at,
        review_at,
        resolved_timezone,
    ) = normalize_temporal_references(
        text,
        grounded_at=grounded_ts,
        timezone_name=resolved_timezone,
    )
    base["normalized_text"] = normalized_text
    base["timezone"] = resolved_timezone
    if occurs_at is not None or review_at is not None:
        base["timestamp"] = review_at if end_of_day else occurs_at
        base["date_only"] = not _contains_time_component(normalized_text)
        base["used_relative"] = normalized_text != text
        return base

    tz = ZoneInfo(resolved_timezone)
    default_dt = datetime.fromtimestamp(grounded_ts, tz=timezone.utc).astimezone(tz)
    default_dt = default_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        parsed = date_parser.parse(normalized_text, default=default_dt)
    except (ValueError, OverflowError) as exc:
        raise ValueError(
            "time values must be numeric unix timestamps, ISO datetimes, or natural-language dates"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    date_only = _looks_like_date_only(normalized_text, original_text=text)
    if date_only:
        if end_of_day:
            parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=0)
        else:
            parsed = parsed.replace(hour=0, minute=0, second=0, microsecond=0)
    base["timestamp"] = parsed.timestamp()
    base["date_only"] = date_only
    return base
