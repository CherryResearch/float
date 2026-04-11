from __future__ import annotations

import importlib.util
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

from .runtime_base import ComputerRuntime
from .types import ComputerAction, ComputerSessionState

ImageGrab = None  # type: ignore[assignment]
PIL_IMPORT_ERROR = None
_PIL_IMPORT_ATTEMPTED = False

Desktop = None  # type: ignore[assignment]
keyboard = None  # type: ignore[assignment]
mouse = None  # type: ignore[assignment]
PYWINAUTO_IMPORT_ERROR = None
_PYWINAUTO_IMPORT_ATTEMPTED = False


def _module_available(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except Exception:
        return False


def _ensure_imagegrab():
    global ImageGrab, PIL_IMPORT_ERROR, _PIL_IMPORT_ATTEMPTED
    if not _PIL_IMPORT_ATTEMPTED:
        try:  # pragma: no cover - optional dependency
            from PIL import ImageGrab as imported_imagegrab

            ImageGrab = imported_imagegrab  # type: ignore[assignment]
            PIL_IMPORT_ERROR = None
        except Exception as exc:  # pragma: no cover - optional dependency
            ImageGrab = None  # type: ignore[assignment]
            PIL_IMPORT_ERROR = exc
        _PIL_IMPORT_ATTEMPTED = True
    return ImageGrab


def _ensure_pywinauto():
    global Desktop, keyboard, mouse, PYWINAUTO_IMPORT_ERROR, _PYWINAUTO_IMPORT_ATTEMPTED
    if not _PYWINAUTO_IMPORT_ATTEMPTED:
        try:  # pragma: no cover - optional dependency
            from pywinauto import Desktop as imported_desktop
            from pywinauto import keyboard as imported_keyboard
            from pywinauto import mouse as imported_mouse

            Desktop = imported_desktop  # type: ignore[assignment]
            keyboard = imported_keyboard  # type: ignore[assignment]
            mouse = imported_mouse  # type: ignore[assignment]
            PYWINAUTO_IMPORT_ERROR = None
        except Exception as exc:  # pragma: no cover - optional dependency
            Desktop = None  # type: ignore[assignment]
            keyboard = None  # type: ignore[assignment]
            mouse = None  # type: ignore[assignment]
            PYWINAUTO_IMPORT_ERROR = exc
        _PYWINAUTO_IMPORT_ATTEMPTED = True
    return Desktop, keyboard, mouse


class WindowsComputerRuntime(ComputerRuntime):
    @property
    def name(self) -> str:
        return "windows"

    def available(self) -> bool:
        return (
            sys.platform.startswith("win")
            and _module_available("PIL.ImageGrab")
            and _module_available("pywinauto")
        )

    def _save_screenshot(self, session_id: str) -> Path:
        imagegrab = _ensure_imagegrab()
        if imagegrab is None:
            raise RuntimeError("Pillow ImageGrab support is unavailable")
        target = self.screenshot_root / f"{session_id}-{int(time.time() * 1000)}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        imagegrab.grab().save(target)
        return target

    def _active_window_title(self) -> Optional[str]:
        desktop, _, _ = _ensure_pywinauto()
        if desktop is None:
            return None
        try:
            return desktop(backend="uia").get_active().window_text()
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
        _, keyboard_module, mouse_module = _ensure_pywinauto()
        if mouse_module is None or keyboard_module is None:
            raise RuntimeError("pywinauto mouse/keyboard controls are unavailable")
        applied: List[Dict[str, object]] = []
        for action in actions:
            kind = str(action.type or "").strip().lower()
            if kind == "click":
                mouse_module.click(coords=(int(action.x or 0), int(action.y or 0)))
            elif kind == "double_click":
                mouse_module.double_click(
                    coords=(int(action.x or 0), int(action.y or 0))
                )
            elif kind == "scroll":
                mouse_module.scroll(
                    coords=(int(action.x or 0), int(action.y or 0)),
                    wheel_dist=int(action.delta_y or 0),
                )
            elif kind == "type":
                if action.text:
                    keyboard_module.send_keys(
                        str(action.text), with_spaces=True, pause=0.01
                    )
            elif kind in {"keypress", "key"}:
                keys = action.keys
                if isinstance(keys, list):
                    for item in keys:
                        if item:
                            keyboard_module.send_keys(str(item))
                elif keys:
                    keyboard_module.send_keys(str(keys))
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
        desktop, _, _ = _ensure_pywinauto()
        if desktop is None:
            raise RuntimeError("pywinauto desktop enumeration is unavailable")
        windows: List[Dict[str, str]] = []
        for window in desktop(backend="uia").windows():
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
        desktop, _, _ = _ensure_pywinauto()
        if desktop is None:
            raise RuntimeError("pywinauto desktop enumeration is unavailable")
        needle = str(window_title or "").strip().lower()
        for window in desktop(backend="uia").windows():
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
        proc = subprocess.Popen(
            command
        )  # noqa: S603,S607 - explicit user-approved tool
        return {"pid": proc.pid, "command": command}
