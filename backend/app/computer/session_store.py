from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from typing import Dict, Optional

from .types import ComputerSessionState


class ComputerSessionStore:
    """Process-local session registry for computer-use adapters."""

    def __init__(self, screenshot_root: Path):
        self.screenshot_root = screenshot_root
        self.screenshot_root.mkdir(parents=True, exist_ok=True)
        self._sessions: Dict[str, ComputerSessionState] = {}

    def get(self, session_id: str) -> Optional[ComputerSessionState]:
        return self._sessions.get(str(session_id or "").strip())

    def put(self, session: ComputerSessionState) -> ComputerSessionState:
        self._sessions[session.id] = session
        return session

    def delete(self, session_id: str) -> Optional[ComputerSessionState]:
        return self._sessions.pop(str(session_id or "").strip(), None)

    def touch(self, session_id: str, **updates) -> Optional[ComputerSessionState]:
        session = self.get(session_id)
        if session is None:
            return None
        updates.setdefault("updated_at", time.time())
        updated = replace(session, **updates)
        self._sessions[session.id] = updated
        return updated

    def all(self) -> Dict[str, ComputerSessionState]:
        return dict(self._sessions)
