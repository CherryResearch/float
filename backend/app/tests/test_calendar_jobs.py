from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from app.services.calendar_jobs import (
    due_occurrence_time,
    expand_events,
    normalize_background_job,
    occurrence_times,
    recurrence_error,
)
from app.utils.calendar_recurrence import validate_rrule_text


def test_expands_dense_and_nightly_calendar_jobs() -> None:
    timezone = ZoneInfo("America/Vancouver")
    start = datetime(2026, 7, 25, 20, 0, tzinfo=timezone).timestamp()
    dense = {
        "id": "dense",
        "title": "Continuous review",
        "start_time": start,
        "end_time": start + 60,
        "timezone": "America/Vancouver",
        "rrule": "FREQ=MINUTELY;INTERVAL=2;COUNT=30",
    }
    nightly = {
        "id": "nightly",
        "title": "Nightly review",
        "start_time": start,
        "timezone": "America/Vancouver",
        "rrule": "FREQ=DAILY;COUNT=365",
    }

    dense_times = occurrence_times(dense, range_start=start, range_end=start + 3600)
    nightly_times = occurrence_times(
        nightly, range_start=start, range_end=start + 366 * 86400
    )
    expanded = expand_events([dense], range_start=start, range_end=start + 3600)

    assert len(dense_times) == 30
    assert dense_times[-1] == start + 58 * 60
    assert len(nightly_times) == 365
    assert all(
        datetime.fromtimestamp(item, timezone).hour == 20 for item in nightly_times
    )
    assert expanded[1]["occurrence_id"] == f"dense:{int(start + 120)}"
    assert expanded[1]["end_time"] == start + 180


def test_due_occurrence_and_policy_normalization() -> None:
    start = 1_900_000_000.0
    event = {
        "id": "bounded",
        "start_time": start,
        "timezone": "UTC",
        "rrule": "FREQ=MINUTELY;COUNT=3",
        "background_job": {"ownership": {"conversation_id": "chat-1"}},
    }

    assert due_occurrence_time(event, now=start + 119) == start + 60
    policy = normalize_background_job("bounded", event)
    assert policy is not None
    assert policy["patience"]["stop_condition"] == "one_pass"
    assert policy["execution"]["allow_subagents"] is True
    assert policy["ownership"] == {
        "conversation_id": "chat-1",
        "calendar_event_id": "bounded",
        "owner_kind": "calendar_event",
    }


def test_zero_interval_rrule_is_rejected_before_dateutil_iteration() -> None:
    event = {
        "id": "unsafe",
        "start_time": 1_900_000_000.0,
        "timezone": "UTC",
        "rrule": "FREQ=DAILY;INTERVAL=0",
    }

    with pytest.raises(ValueError, match="positive integer"):
        validate_rrule_text(event["rrule"])
    assert "positive integer" in str(recurrence_error(event))
    assert (
        occurrence_times(
            event,
            range_start=event["start_time"],
            range_end=event["start_time"] + 86400,
        )
        == []
    )
    assert due_occurrence_time(event, now=event["start_time"] + 86400) is None
