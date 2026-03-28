from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, List, Optional, Set, Tuple


@dataclass(frozen=True, slots=True)
class BrokerEvent:
    seq: int
    event: Dict[str, Any]


class EventBroker:
    """In-memory broadcast broker with replay support.

    Broadcasts events to all subscribers (no "stealing" like a shared Queue)
    and keeps a bounded history so SSE clients can resume via Last-Event-ID.
    """

    def __init__(self, *, max_history: int = 500, subscriber_queue_size: int = 250):
        self._lock = asyncio.Lock()
        self._next_seq = 0
        self._history: Deque[BrokerEvent] = deque(maxlen=max(1, int(max_history)))
        self._subscribers: Set[asyncio.Queue[BrokerEvent]] = set()
        self._subscriber_queue_size = max(1, int(subscriber_queue_size))

    async def subscribe(
        self, *, since: Optional[int] = None
    ) -> Tuple[asyncio.Queue[BrokerEvent], List[BrokerEvent]]:
        """Subscribe and return (queue, backlog).

        Backlog includes all events with seq > since, if since is provided.
        """

        subscriber: asyncio.Queue[BrokerEvent] = asyncio.Queue(
            maxsize=self._subscriber_queue_size
        )
        backlog: List[BrokerEvent] = []
        since_id: Optional[int] = None
        if since is not None:
            try:
                since_id = int(since)
            except Exception:
                since_id = None

        async with self._lock:
            self._subscribers.add(subscriber)
            if since_id is not None:
                backlog = [item for item in self._history if item.seq > since_id]
        return subscriber, backlog

    async def unsubscribe(self, subscriber: asyncio.Queue[BrokerEvent]) -> None:
        async with self._lock:
            self._subscribers.discard(subscriber)

    async def publish(self, event: Dict[str, Any]) -> int:
        """Publish an event to history and all subscribers."""

        async with self._lock:
            self._next_seq += 1
            broker_event = BrokerEvent(self._next_seq, event)
            self._history.append(broker_event)
            subscribers = list(self._subscribers)

        for subscriber in subscribers:
            try:
                subscriber.put_nowait(broker_event)
            except asyncio.QueueFull:
                # Best-effort: make room by dropping one older event.
                try:
                    subscriber.get_nowait()
                except Exception:
                    pass
                try:
                    subscriber.put_nowait(broker_event)
                except Exception:
                    pass

        return broker_event.seq
