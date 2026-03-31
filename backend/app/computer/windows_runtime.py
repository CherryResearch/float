from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

from .runtime_base import ComputerRuntime
from .types import ComputerAction, ComputerSessionState

try:  # pragma: no cover - optional dependency
    from PIL import ImageGrab

    PIL_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - optional dependency
    ImageGrab = None  # type: ignore[assignment]
    PIL_IMPORT_ERROR = exc

try:  # pragma: no cover - optional dependency
    from pywinauto import Desktop, keyboard, mouse

    PYWINAUTO_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - optional dependency
    Desktop = None  # type: ignore[assignment]
    keyboard = None  # type: ignore[assignment]
    mouse = None  # type: ignore[assignment]
    PYWINAUTO_IMPORT_ERROR = exc


class WindowsComputerRuntime(ComputerRuntime):
    @property
    def name(self) -> str:
        return "windows"

    def available(self) -> bool:
        return (
            sys.platform.startswith("win")
            and ImageGrab is not None
            and Desktop is not None
            and mouse is not None
            and keyboard is not None
        )

    def _save_screenshot(self, session_id: str) -> Path:
        if ImageGrab is None:
            raise RuntimeError("Pillow ImageGrab support is unavailable")
        target = self.screenshot_root / f"{session_id}-{int(time.time() * 1000)}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        ImageGrab.grab().save(target)
        return target

    def _active_window_title(self) -> Optional[str]:
        if Desktop is None:
            return None
        try:
            return Desktop(backend="uia").get_active().window_text()
        except Exception:
            return None

    def _attachment_for(self, screenshot_path: Path) -> Dict[str, str]:
        return {
            "name": screenshot_path.name,
            "type": "image/png",
            "url": screenshot_path.as_posix(),
            "origin": "computer_use",
            "relative_path": screenshot_path.name,
            "capture_source": "windows",
        }

    def start_session(
        self,
        *,
        session_id: str,
        width: int,
        height: int,
        start_url: Optional[str] = None,
        metadata: Optional[Dict[str, object]] = None,
    ) -> ComputerSessionState:
        if not self.available():
            raise RuntimeError(
                "Windows desktop control is unavailable. Install Pillow and pywinauto on Windows."
            )
        now = time.time()
        return ComputerSessionState(
            id=session_id,
            runtime=self.name,
            status="active",
            width=int(width),
            height=int(height),
            created_at=now,
            updated_at=now,
            active_window=self._active_window_title(),
            metadata=dict(metadata or {}),
        )

    def stop_session(self, session: ComputerSessionState) -> Dict[str, str]:
        return {"status": "stopped", "session_id": session.id}

    def observe(self, session: ComputerSessionState) -> Dict[str, object]:
        screenshot = self._save_screenshot(session.id)
        active_window = self._active_window_title()
        return {
            "summary": "Captured Windows desktop state",
            "active_window": active_window,
            "last_screenshot_path": screenshot.as_posix(),
            "attachment": self._attachment_for(screenshot),
        }

    def navigate(self, session: ComputerSessionState, url: str) -> Dict[str, object]:
        raise RuntimeError("Windows runtime does not support browser-style navigation")

    def act(
        self, session: ComputerSessionState, actions: List[ComputerAction]
    ) -> Dict[str, object]:
        if mouse is None or keyboard is None:
            raise RuntimeError("pywinauto mouse/keyboard controls are unavailable")
        applied: List[Dict[str, object]] = []
        for action in actions:
            kind = str(action.type or "").strip().lower()
            if kind == "click":
                mouse.click(coords=(int(action.x or 0), int(action.y or 0)))
            elif kind == "double_click":
                mouse.double_click(coords=(int(action.x or 0), int(action.y or 0)))
            elif kind == "scroll":
                mouse.scroll(
                    coords=(int(action.x or 0), int(action.y or 0)),
                    wheel_dist=int(action.delta_y or 0),
                )
            elif kind == "type":
                if action.text:
                    keyboard.send_keys(str(action.text), with_spaces=True, pause=0.01)
            elif kind in {"keypress", "key"}:
                keys = action.keys
                if isinstance(keys, list):
                    for item in keys:
                        if item:
                            keyboard.send_keys(str(item))
                elif keys:
                    keyboard.send_keys(str(keys))
            elif kind == "wait":
                time.sleep(max(0.0, int(action.ms or 0) / 1000.0))
            else:
                raise RuntimeError(f"Unsupported Windows action '{kind}'")
            applied.append(action.to_dict())
        observed = self.observe(session)
        observed["summary"] = f"Applied {len(applied)} Windows action(s)"
        observed["actions"] = applied
        return observed

    def list_windows(self, session: ComputerSessionState) -> Dict[str, object]:
        if Desktop is None:
            raise RuntimeError("pywinauto desktop enumeration is unavailable")
        windows: List[Dict[str, str]] = []
        for window in Desktop(backend="uia").windows():
            try:
                title = window.window_text()
            except Exception:
                title = ""
            if not title:
                continue
            windows.append({"title": title})
        return {"windows": windows, "count": len(windows)}

    def focus_window(
        self, session: ComputerSessionState, window_title: str
    ) -> Dict[str, str]:
        if Desktop is None:
            raise RuntimeError("pywinauto desktop enumeration is unavailable")
        needle = str(window_title or "").strip().lower()
        for window in Desktop(backend="uia").windows():
            try:
                title = window.window_text()
            except Exception:
                title = ""
            if not title or needle not in title.lower():
                continue
            window.set_focus()
            return {"focused": title}
        raise RuntimeError(f"No window matched '{window_title}'")

    def launch_app(
        self,
        session: ComputerSessionState,
        app: str,
        args: Optional[List[str]] = None,
    ) -> Dict[str, object]:
        command = [str(app)] + [str(item) for item in (args or []) if item is not None]
        proc = subprocess.Popen(command)  # noqa: S603,S607 - explicit user-approved tool
        return {"pid": proc.pid, "command": command}
