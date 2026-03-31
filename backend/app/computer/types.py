from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


DEFAULT_DISPLAY_WIDTH = 1280
DEFAULT_DISPLAY_HEIGHT = 720


@dataclass
class ComputerDisplay:
    width: int = DEFAULT_DISPLAY_WIDTH
    height: int = DEFAULT_DISPLAY_HEIGHT


@dataclass
class ComputerAction:
    type: str
    x: Optional[int] = None
    y: Optional[int] = None
    button: Optional[str] = None
    text: Optional[str] = None
    keys: Optional[str | List[str]] = None
    delta_x: Optional[int] = None
    delta_y: Optional[int] = None
    ms: Optional[int] = None
    url: Optional[str] = None
    app: Optional[str] = None
    args: List[str] = field(default_factory=list)
    window_title: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ComputerSessionState:
    id: str
    runtime: str
    status: str
    width: int
    height: int
    created_at: float
    updated_at: float
    current_url: Optional[str] = None
    active_window: Optional[str] = None
    last_screenshot_path: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        screenshot = payload.get("last_screenshot_path")
        if isinstance(screenshot, Path):
            payload["last_screenshot_path"] = screenshot.as_posix()
        return payload


@dataclass
class ComputerObservation:
    session: Dict[str, Any]
    summary: str
    attachment: Optional[Dict[str, Any]] = None
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "session": dict(self.session),
            "summary": self.summary,
            "data": dict(self.data),
        }
        if self.attachment:
            payload["attachment"] = dict(self.attachment)
            payload["attachments"] = [dict(self.attachment)]
            payload["image_attachments"] = [dict(self.attachment)]
        return payload
