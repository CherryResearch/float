from __future__ import annotations

from app import hooks
import app.hooks_observers as observers


class DummyMetric:
    def __init__(self) -> None:
        self.labels_calls = []
        self.inc_calls = []
        self.observe_calls = []

    def labels(self, *labels):
        self.labels_calls.append(labels)
        return self

    def inc(self, amount: int = 1):
        self.inc_calls.append(amount)
        return self

    def observe(self, value: float):
        self.observe_calls.append(value)
        return self


def test_ingestion_observer_updates_metrics(monkeypatch):
    counter = DummyMetric()
    events = []
    monkeypatch.setattr(observers, "rag_ingestion_total", counter)
    monkeypatch.setattr(observers, "log_event", lambda name, data=None: events.append((name, data)))

    event = hooks.IngestionEvent(
        kind="document",
        source="file.txt",
        metadata={"tags": ["alpha"]},
        preview="hello world",
        size=42,
    )
    hooks.emit(hooks.INGESTION_EVENT, event)

    assert counter.labels_calls == [("document",)]
    assert counter.inc_calls == [1]
    assert events and events[0][0] == "ingestion"


def test_retrieval_observer_counts_matches(monkeypatch):
    counter = DummyMetric()
    hist = DummyMetric()
    events = []
    monkeypatch.setattr(observers, "retrieval_events_total", counter)
    monkeypatch.setattr(observers, "retrieval_matches_histogram", hist)
    monkeypatch.setattr(observers, "log_event", lambda name, data=None: events.append((name, data)))

    result = hooks.RetrievalResult(
        session_id="session",
        query="where is it",
        matches=[{"id": 1}, {"id": 2}, {"id": 3}],
        metadata={"channel": "chat"},
    )
    hooks.emit(hooks.AFTER_RETRIEVAL_EVENT, result)

    assert counter.labels_calls == [("chat",)]
    assert counter.inc_calls == [1]
    assert hist.observe_calls == [3]
    assert events and events[0][0] == "retrieval"


def test_memory_write_observer_logs(monkeypatch):
    counter = DummyMetric()
    events = []
    monkeypatch.setattr(observers, "memory_writes_total", counter)
    monkeypatch.setattr(observers, "log_event", lambda name, data=None: events.append((name, data)))

    event = hooks.MemoryWriteEvent(
        key="remember-this",
        source="tool.remember",
        payload={"importance": 0.8, "sensitivity": "personal"},
    )
    hooks.emit(hooks.MEMORY_WRITE_EVENT, event)

    assert counter.labels_calls == [("tool.remember",)]
    assert counter.inc_calls == [1]
    assert events and events[0][0] == "memory_write"


def test_tool_observer_counts_status(monkeypatch):
    counter = DummyMetric()
    monkeypatch.setattr(observers, "tool_events_total", counter)

    event = hooks.ToolInvocationEvent(
        name="remember",
        status="invoked",
        args={"key": "note"},
        result="ok",
    )
    hooks.emit(hooks.TOOL_EVENT, event)

    assert counter.labels_calls == [("remember", "invoked")]
    assert counter.inc_calls == [1]


def test_error_observer_logs_and_counts(monkeypatch):
    counter = DummyMetric()
    events = []
    monkeypatch.setattr(observers, "error_events_total", counter)
    monkeypatch.setattr(observers, "log_event", lambda name, data=None: events.append((name, data)))

    event = hooks.ErrorEvent(
        location="test",
        exception_type="ValueError",
        detail="boom",
        context={"path": "/"},
    )
    hooks.emit(hooks.ERROR_EVENT, event)

    assert counter.labels_calls == [("test",)]
    assert counter.inc_calls == [1]
    assert events and events[0][0] == "hook_error"
