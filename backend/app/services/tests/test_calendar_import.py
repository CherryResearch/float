from datetime import datetime, timezone

from app.services.calendar_import import parse_google_calendar, parse_ics


def test_parse_google_calendar():
    data = {
        "items": [
            {
                "id": "evt1",
                "summary": "Meeting",
                "start": {"dateTime": "2024-01-01T09:00:00Z"},
                "end": {"dateTime": "2024-01-01T10:00:00Z"},
            }
        ]
    }
    events = parse_google_calendar(data)
    assert len(events) == 1
    event = events[0]
    assert event.id == "evt1"
    assert event.title == "Meeting"
    expected_start = datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc)
    expected_start = expected_start.timestamp()
    assert event.start_time == expected_start


def test_parse_ics():
    ics = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:abc123
SUMMARY:Party
DTSTART:20240101T090000Z
DTEND:20240101T100000Z
END:VEVENT
END:VCALENDAR"""
    events = parse_ics(ics.encode())
    assert len(events) == 1
    event = events[0]
    assert event.id == "abc123"
    assert event.title == "Party"
    expected_start = datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc)
    expected_start = expected_start.timestamp()
    assert event.start_time == expected_start
