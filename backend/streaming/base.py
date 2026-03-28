from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict

from app.utils.blob_store import put_asset
from fastapi import WebSocket


def store_stream_capture(
    data: bytes,
    *,
    filename: str,
    capture_source: str = "stream",
) -> Dict[str, str]:
    """Persist a future live-mode capture under `data/files/screenshots`."""

    payload = put_asset(data, filename=filename, origin="screenshot")
    payload["capture_source"] = capture_source
    return payload


class StreamingService(ABC):
    """Abstract base class for streaming providers."""

    @abstractmethod
    async def negotiate(self, check_health: bool = False) -> Dict[str, str]:
        """Handle session negotiation."""

    @abstractmethod
    async def stream(self, websocket: WebSocket) -> None:
        """Handle WebSocket streaming."""

    @abstractmethod
    async def handle_image(self, data: bytes) -> Dict[str, int | str]:
        """Handle image uploads."""

    @abstractmethod
    async def handle_event(
        self, event: str, payload: Dict[str, str] | None = None
    ) -> Dict[str, str]:
        """Handle worker events (turn detection, screenshot triggers, etc.)."""
