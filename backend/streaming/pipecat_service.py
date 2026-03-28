from __future__ import annotations

from typing import Dict

from config import ModelCatalog
from fastapi import WebSocket

from .base import StreamingService


class PipecatService(StreamingService):
    """Stub implementation of a Pipecat-based streaming service."""

    def __init__(self, catalog: ModelCatalog):
        self.catalog = catalog

    async def negotiate(self, check_health: bool = False) -> Dict[str, str]:
        # Surface readiness for defined workflows and default modes.
        names: list[str] = []
        if self.catalog.workflows:
            names.extend(self.catalog.workflows.keys())
        if self.catalog.defaults:
            # include modes (e.g., chat/voice) to infer requirements from defaults
            names.extend(self.catalog.defaults.keys())
        # de-duplicate while preserving order
        seen = set()
        ordered = [n for n in names if not (n in seen or seen.add(n))]
        readiness = {
            name: self.catalog.readiness(name, check_health=check_health)
            for name in ordered
        }

        return {"provider": "pipecat", "readiness": readiness}

    async def stream(self, websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_text("pipecat stream")
        await websocket.close()

    async def handle_image(self, data: bytes) -> Dict[str, int | str]:
        return {"provider": "pipecat", "size": len(data)}

    async def handle_event(
        self, event: str, payload: Dict[str, str] | None = None
    ) -> Dict[str, str]:
        return {"provider": "pipecat", "event": event}
