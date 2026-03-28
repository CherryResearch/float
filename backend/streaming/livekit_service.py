from __future__ import annotations

from typing import Dict

from config import ModelCatalog
from fastapi import WebSocket

from .base import StreamingService


class LiveKitService(StreamingService):
    """Stub implementation of a LiveKit-based streaming service."""

    def __init__(self, catalog: ModelCatalog):
        self.catalog = catalog

    async def negotiate(self, check_health: bool = False) -> Dict[str, str]:
        return {"provider": "livekit"}

    async def stream(self, websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_text("livekit stream")
        await websocket.close()

    async def handle_image(self, data: bytes) -> Dict[str, int | str]:
        return {"provider": "livekit", "size": len(data)}

    async def handle_event(
        self, event: str, payload: Dict[str, str] | None = None
    ) -> Dict[str, str]:
        return {"provider": "livekit", "event": event}
