from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

from .types import ComputerAction, ComputerSessionState


class ComputerRuntime(ABC):
    """Minimal runtime contract shared by browser and desktop adapters."""

    def __init__(self, *, screenshot_root: Path):
        self.screenshot_root = screenshot_root

    @property
    @abstractmethod
    def name(self) -> str:  # pragma: no cover - interface only
        raise NotImplementedError

    @abstractmethod
    def available(self) -> bool:  # pragma: no cover - interface only
        raise NotImplementedError

    @abstractmethod
    def start_session(
        self,
        *,
        session_id: str,
        width: int,
        height: int,
        start_url: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ComputerSessionState:  # pragma: no cover - interface only
        raise NotImplementedError

    @abstractmethod
    def stop_session(self, session: ComputerSessionState) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def observe(self, session: ComputerSessionState) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def navigate(self, session: ComputerSessionState, url: str) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def act(
        self, session: ComputerSessionState, actions: List[ComputerAction]
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def list_windows(self, session: ComputerSessionState) -> Dict[str, Any]:
        raise RuntimeError(f"{self.name} runtime does not support window listing")

    def focus_window(
        self, session: ComputerSessionState, window_title: str
    ) -> Dict[str, Any]:
        raise RuntimeError(f"{self.name} runtime does not support window focus")

    def launch_app(
        self,
        session: ComputerSessionState,
        app: str,
        args: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        raise RuntimeError(f"{self.name} runtime does not support app launch")
