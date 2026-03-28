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
        events.append(
            CalendarEvent(
                id=event_id,
                title=summary,
                start_time=start_ts,
                end_time=end_ts,
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


def parse_ics(ics_bytes: bytes) -> List[CalendarEvent]:
    cal = Calendar.from_ical(ics_bytes)
    events: List[CalendarEvent] = []
    for component in cal.walk():
        if component.name != "VEVENT":
            continue
        uid = str(component.get("uid") or uuid4())
        summary = str(component.get("summary", "Untitled Event"))
        start = component.get("dtstart").dt
        end = component.get("dtend")
        start_ts = _to_timestamp(start)
        end_ts = _to_timestamp(end.dt) if end else None
        events.append(
            CalendarEvent(
                id=uid,
                title=summary,
                start_time=start_ts,
                end_time=end_ts,
            )
        )
    return events
