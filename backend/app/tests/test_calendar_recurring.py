from datetime import datetime, timedelta, timezone

from app.tasks import poll_calendar_events, send_event_prompt
from app.utils import calendar_store


def test_poll_calendar_events_recurring(monkeypatch):
    event_id = "recurring"
    start = datetime.now(timezone.utc) + timedelta(seconds=100)
    calendar_store.save_event(
        event_id,
        {
            "id": event_id,
            "title": "Daily standup",
            "start_time": start.timestamp(),
            "rrule": "FREQ=DAILY;COUNT=2",
            "timezone": "UTC",
        },
    )

    called = {}

    def fake_delay(eid, occ_time=None):
        called["args"] = (eid, occ_time)

    monkeypatch.setattr(send_event_prompt, "delay", fake_delay)
    monkeypatch.setattr("app.tasks.EVENT_PROMPT_LEAD_TIME", 200)

    poll_calendar_events()

    assert called["args"][0] == event_id
    assert abs(called["args"][1] - start.timestamp()) < 1
