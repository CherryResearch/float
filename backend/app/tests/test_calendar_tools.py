import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

backend_dir = Path(__file__).resolve().parents[2]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.utils import calendar_store, generate_signature  # noqa: E402


def test_create_task_tool_persists_calendar_event(tmp_path, monkeypatch):
    from app.tools.calendar import create_task

    monkeypatch.setattr(calendar_store, "EVENTS_DIR", tmp_path, raising=False)
    tmp_path.mkdir(parents=True, exist_ok=True)

    args = {
        "title": "Ship demo",
        "description": "Walk the team through the updated task flow.",
        "start_time": 1735732800,
        "duration_min": 45,
        "timezone": "UTC",
        "status": "proposed",
        "actions": [{"kind": "prompt", "prompt": "Prepare the release checklist."}],
    }
    signature = generate_signature("tester", "create_task", args)

    result = create_task(user="tester", signature=signature, **args)

    assert result["status"] == "saved"
    event = result["event"]
    assert event["id"] == "ship-demo-1735732800000"
    assert event["status"] == "scheduled"
    assert event["end_time"] == 1735735500
    assert event["actions"][0]["kind"] == "prompt"

    stored = calendar_store.load_event(event["id"])
    assert stored["title"] == "Ship demo"
    assert stored["status"] == "scheduled"
    assert stored["timezone"] == "UTC"


def test_create_task_accepts_iso_datetime_and_normalizes_legacy_actions(
    tmp_path, monkeypatch
):
    from app.tools.calendar import create_task

    monkeypatch.setattr(calendar_store, "EVENTS_DIR", tmp_path, raising=False)
    tmp_path.mkdir(parents=True, exist_ok=True)

    args = {
        "title": "Review image follow-up",
        "start_time": "2026-03-11T10:30:00Z",
        "duration_min": 15,
        "timezone": "UTC",
        "actions": [
            {
                "type": "continue_prompt",
                "prompt": "Use image recall and continue inline.",
                "conversation_mode": "current_chat",
                "session_id": "sess-123",
            }
        ],
    }
    signature = generate_signature("tester", "create_task", args)

    result = create_task(user="tester", signature=signature, **args)

    event = result["event"]
    assert event["start_time"] == 1773225000
    assert event["end_time"] == 1773225900
    assert event["actions"] == [
        {
            "kind": "prompt",
            "prompt": "Use image recall and continue inline.",
            "conversation_mode": "inline",
            "session_id": "sess-123",
        }
    ]


def test_create_task_accepts_natural_language_with_grounded_time_and_user_timezone(
    tmp_path, monkeypatch
):
    from app.tools.calendar import create_task
    from app.utils import time_resolution

    monkeypatch.setattr(calendar_store, "EVENTS_DIR", tmp_path, raising=False)
    tmp_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        time_resolution.user_settings,
        "load_settings",
        lambda: {"user_timezone": "America/New_York"},
    )

    grounded_at = datetime(2026, 3, 15, 14, 0, tzinfo=timezone.utc).timestamp()
    args = {
        "title": "Karate class",
        "start_time": "tomorrow at 6pm",
        "duration_min": 60,
        "grounded_at": grounded_at,
    }
    signature = generate_signature("tester", "create_task", args)

    result = create_task(user="tester", signature=signature, **args)

    event = result["event"]
    assert event["timezone"] == "America/New_York"
    assert event["grounded_at"] == grounded_at
    assert event["start_time"] == pytest.approx(
        datetime(2026, 3, 16, 22, 0, tzinfo=timezone.utc).timestamp()
    )
    assert event["end_time"] == pytest.approx(
        datetime(2026, 3, 16, 23, 0, tzinfo=timezone.utc).timestamp()
    )


def test_create_task_accepts_date_time_objects_and_duration_aliases(
    tmp_path, monkeypatch
):
    from app.tools.calendar import create_task

    monkeypatch.setattr(calendar_store, "EVENTS_DIR", tmp_path, raising=False)
    tmp_path.mkdir(parents=True, exist_ok=True)

    args = {
        "summary": "Breakfast sync",
        "start": {
            "date": "2026-03-22",
            "time": "10:00",
            "timezone": "America/Vancouver",
        },
        "duration": "90m",
        "status": "proposed",
    }
    signature = generate_signature("tester", "create_task", args)

    result = create_task(user="tester", signature=signature, **args)

    event = result["event"]
    assert event["title"] == "Breakfast sync"
    assert event["timezone"] == "America/Vancouver"
    assert event["status"] == "scheduled"
    assert event["end_time"] - event["start_time"] == pytest.approx(90 * 60)


def test_create_event_alias_persists_calendar_event(tmp_path, monkeypatch):
    from app.tools.calendar import create_event

    monkeypatch.setattr(calendar_store, "EVENTS_DIR", tmp_path, raising=False)
    tmp_path.mkdir(parents=True, exist_ok=True)

    args = {
        "title": "Night reminder",
        "start_time": {"date": "2026-03-22", "time": "10pm"},
        "timezone": "America/Vancouver",
        "duration_min": "30m",
    }
    signature = generate_signature("tester", "create_event", args)

    result = create_event(user="tester", signature=signature, **args)

    event = result["event"]
    assert event["title"] == "Night reminder"
    assert event["timezone"] == "America/Vancouver"
    assert event["end_time"] - event["start_time"] == pytest.approx(30 * 60)
