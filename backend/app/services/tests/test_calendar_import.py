from datetime import datetime, timezone

from app.services.calendar_import import parse_google_calendar, parse_ics


def test_parse_google_calendar():
    data = {
        "items": [
            {
                "id": "evt1",
                "summary": "Meeting",
                "description": "Nightly review",
                "location": "Float",
                "start": {
                    "dateTime": "2024-01-01T09:00:00Z",
                    "timeZone": "America/Vancouver",
                },
                "end": {"dateTime": "2024-01-01T10:00:00Z"},
                "recurrence": [
                    "RRULE:FREQ=DAILY;COUNT=365",
                    "EXDATE:20240102T090000Z",
                ],
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
    assert event.rrule == "FREQ=DAILY;COUNT=365"
    assert event.timezone == "America/Vancouver"
    assert event.description == "Nightly review"
    assert event.location == "Float"
    assert event.recurrence_exceptions == ["EXDATE:20240102T090000Z"]
    assert "not applied" in event.recurrence_import_warning


def test_parse_ics():
    ics = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:abc123
SUMMARY:Party
DTSTART:20240101T090000Z
DTEND:20240101T100000Z
RRULE:FREQ=MINUTELY;INTERVAL=2;COUNT=30
EXDATE:20240101T090200Z
DESCRIPTION:Continuous review
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
    assert event.rrule == "FREQ=MINUTELY;COUNT=30;INTERVAL=2"
    assert event.description == "Continuous review"
    assert event.recurrence_exceptions == ["EXDATE:20240101T090200Z"]
    assert "not applied" in event.recurrence_import_warning


def test_parse_ics_keeps_tzid_and_folds_override_into_master_metadata():
    ics = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:nightly-vancouver
SUMMARY:Nightly review
DTSTART;TZID=America/Vancouver:20270313T023000
DTEND;TZID=America/Vancouver:20270313T033000
RRULE:FREQ=DAILY;COUNT=3
END:VEVENT
BEGIN:VEVENT
UID:nightly-vancouver
RECURRENCE-ID;TZID=America/Vancouver:20270314T023000
SUMMARY:Nightly review adjusted
DTSTART;TZID=America/Vancouver:20270314T040000
DTEND;TZID=America/Vancouver:20270314T050000
END:VEVENT
END:VCALENDAR"""

    events = parse_ics(ics.encode())

    assert len(events) == 1
    event = events[0]
    assert event.id == "nightly-vancouver"
    assert event.timezone == "America/Vancouver"
    assert event.rrule == "FREQ=DAILY;COUNT=3"
    assert event.recurrence_exceptions == ["RECURRENCE-ID:20270314T023000"]
    assert "not applied" in event.recurrence_import_warning
