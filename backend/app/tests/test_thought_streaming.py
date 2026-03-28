import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    backend_dir = Path(__file__).resolve().parents[2]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    from app.main import app

    # Keep tests isolated from the repo's real data/ folder.
    mgr = app.state.memory_manager
    monkeypatch.setattr(mgr, "_store_path", tmp_path / "memory.json", raising=False)
    mgr.store = {}

    return TestClient(app)


@pytest.mark.asyncio
async def test_event_broker_broadcast_and_replay():
    from app.utils.event_broker import EventBroker

    broker = EventBroker(max_history=10, subscriber_queue_size=10)
    sub1, backlog1 = await broker.subscribe()
    sub2, backlog2 = await broker.subscribe()
    assert backlog1 == []
    assert backlog2 == []

    seq1 = await broker.publish({"type": "thought", "content": "hello"})
    item1 = await sub1.get()
    item2 = await sub2.get()
    assert item1.seq == seq1 == item2.seq
    assert item1.event["content"] == item2.event["content"] == "hello"

    await broker.unsubscribe(sub1)
    await broker.publish({"type": "thought", "content": "bye"})
    only_sub2 = await sub2.get()
    assert only_sub2.event["content"] == "bye"

    # Replay
    sub3, replay = await broker.subscribe(since=seq1)
    assert any(item.event.get("content") == "bye" for item in replay)
    await broker.unsubscribe(sub2)
    await broker.unsubscribe(sub3)


def _recv_until(ws, predicate, *, max_messages=20):
    for _ in range(max_messages):
        msg = ws.receive_json()
        if predicate(msg):
            return msg
    raise AssertionError("did not receive expected stream event")


def test_thoughts_websocket_broadcast_to_multiple_consumers(client):
    with client.websocket_connect("/api/ws/thoughts") as ws1:
        with client.websocket_connect("/api/ws/thoughts") as ws2:
            r = client.post("/api/memory/broadcast_test", json={"value": "hello"})
            assert r.status_code == 200

            def pred(message):
                return message.get("type") == "thought" and str(
                    message.get("content", "")
                ).startswith("memory upserted:")

            ev1 = _recv_until(ws1, pred)
            ev2 = _recv_until(ws2, pred)
            assert ev1["content"] == ev2["content"]


def test_thoughts_websocket_emits_keepalive(client):
    client.app.state.config["thought_ws_keepalive_seconds"] = 0.05
    with client.websocket_connect("/api/ws/thoughts") as ws:
        msg = _recv_until(ws, lambda m: m.get("type") == "keepalive", max_messages=50)
        assert msg["type"] == "keepalive"
