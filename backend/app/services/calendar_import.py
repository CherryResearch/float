from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, List
from uuid import uuid4

from app.schemas import CalendarEvent
from icalendar import Calendar


def _parse_google_time(info: Dict[str, Any]) -> float:
    dt_str = info.get("dateTime") or info.get("date")
    if not dt_str:
        return 0.0
    dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def parse_google_calendar(data: Dict[str, Any]) -> List[CalendarEvent]:
    events: List[CalendarEvent] = []
    for item in data.get("items", []):
        event_id = item.get("id") or str(uuid4())
        summary = item.get("summary") or "Untitled Event"
        start_ts = _parse_google_time(item.get("start", {}))
        end_ts = _parse_google_time(item.get("end", {}))
        recurrence = item.get("recurrence")
        recurrence_lines = recurrence if isinstance(recurrence, list) else []
        raw_rule = next(
            (
                line
                for line in recurrence_lines
                if str(line).upper().startswith("RRULE:")
            ),
            None,
        )
        recurrence_exceptions = [
            str(line) for line in recurrence_lines if line is not raw_rule
        ]
        rrule_value = (
            str(raw_rule).replace("RRULE:", "", 1)
            if raw_rule and str(raw_rule).upper().startswith("RRULE:")
            else (str(raw_rule) if raw_rule else None)
        )
        timezone_name = (
            item.get("start", {}).get("timeZone")
            or item.get("end", {}).get("timeZone")
            or "UTC"
        )
        events.append(
            CalendarEvent(
                id=event_id,
                title=summary,
                description=item.get("description"),
                location=item.get("location"),
                start_time=start_ts,
                end_time=end_ts,
                rrule=rrule_value,
                recurrence_exceptions=recurrence_exceptions,
                recurrence_import_warning=(
                    "Imported recurrence exceptions are preserved as metadata but "
                    "are not applied to occurrence expansion yet."
                    if recurrence_exceptions
                    else None
                ),
                timezone=timezone_name,
            )
        )
    return events


def _to_timestamp(value: Any) -> float:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.timestamp()
    if isinstance(value, date):
        dt = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
        return dt.timestamp()
    return float(value)


def _encoded_ics_properties(component, names: List[str]) -> List[str]:
    encoded: List[str] = []
    for name in names:
        raw = component.get(name)
        if raw is None:
            continue
        values = raw if isinstance(raw, list) else [raw]
        for value in values:
            payload = value.to_ical() if hasattr(value, "to_ical") else str(value)
            text = (
                payload.decode("utf-8") if isinstance(payload, bytes) else str(payload)
            )
            encoded.append(f"{name.upper()}:{text}")
    return encoded


def parse_ics(ics_bytes: bytes) -> List[CalendarEvent]:
    cal = Calendar.from_ical(ics_bytes)
    events: List[CalendarEvent] = []
    components = [component for component in cal.walk() if component.name == "VEVENT"]
    overrides_by_uid: Dict[str, List[str]] = {}
    for component in components:
        if component.get("recurrence-id") is None:
            continue
        override_uid = str(component.get("uid") or "")
        if override_uid:
            overrides_by_uid.setdefault(override_uid, []).extend(
                _encoded_ics_properties(component, ["recurrence-id"])
            )

    for component in components:
        # A RECURRENCE-ID VEVENT is an override of its master series, not a
        # second event with the same storage id. Preserve its marker on the
        # master until exception expansion is implemented.
        if component.get("recurrence-id") is not None:
            continue
        uid = str(component.get("uid") or uuid4())
        summary = str(component.get("summary", "Untitled Event"))
        start_property = component.get("dtstart")
        start = start_property.dt
        end = component.get("dtend")
        start_ts = _to_timestamp(start)
        end_ts = _to_timestamp(end.dt) if end else None
        recurrence = component.get("rrule")
        rrule_value = None
        if recurrence is not None:
            encoded = recurrence.to_ical()
            rrule_value = (
                encoded.decode("utf-8") if isinstance(encoded, bytes) else str(encoded)
            )
        recurrence_exceptions = _encoded_ics_properties(
            component, ["exdate", "rdate", "exrule", "recurrence-id"]
        )
        recurrence_exceptions.extend(overrides_by_uid.get(uid, []))
        timezone_params = getattr(start_property, "params", {})
        start_timezone = str(
            timezone_params.get("TZID")
            or getattr(getattr(start, "tzinfo", None), "key", None)
            or getattr(getattr(start, "tzinfo", None), "zone", None)
            or "UTC"
        )
        events.append(
            CalendarEvent(
                id=uid,
                title=summary,
                description=(
                    str(component.get("description"))
                    if component.get("description") is not None
                    else None
                ),
                location=(
                    str(component.get("location"))
                    if component.get("location") is not None
                    else None
                ),
                start_time=start_ts,
                end_time=end_ts,
                rrule=rrule_value,
                recurrence_exceptions=recurrence_exceptions,
                recurrence_import_warning=(
                    "Imported recurrence exceptions are preserved as metadata but "
                    "are not applied to occurrence expansion yet."
                    if recurrence_exceptions
                    else None
                ),
                timezone=start_timezone,
            )
        )
    return events
