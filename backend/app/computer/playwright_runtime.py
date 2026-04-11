from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .runtime_base import ComputerRuntime
from .types import ComputerAction, ComputerSessionState

try:  # pragma: no cover - optional dependency
    from playwright.sync_api import sync_playwright

    PLAYWRIGHT_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - optional dependency
    sync_playwright = None  # type: ignore[assignment]
    PLAYWRIGHT_IMPORT_ERROR = exc


class PlaywrightComputerRuntime(ComputerRuntime):
    def __init__(self, *, screenshot_root: Path):
        super().__init__(screenshot_root=screenshot_root)
        self._playwright = None
        self._sessions: Dict[str, Dict[str, Any]] = {}

    @property
    def name(self) -> str:
        return "browser"

    def available(self) -> bool:
        return sync_playwright is not None

    def _ensure_playwright(self):
        if sync_playwright is None:
            raise RuntimeError(
                "Playwright is not installed. Install the 'playwright' package and run 'playwright install chromium'."
            )
        if self._playwright is None:
            self._playwright = sync_playwright().start()
        return self._playwright

    def _page_handle(self, session_id: str) -> Dict[str, Any]:
        handle = self._sessions.get(session_id)
        if not isinstance(handle, dict):
            raise RuntimeError(f"Browser session '{session_id}' is not active")
        return handle

    def _close_handle(self, session_id: str) -> Dict[str, Any]:
        handle = self._sessions.pop(session_id, None) or {}
        browser = handle.get("browser")
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        profile_dir = handle.get("profile_dir")
        if isinstance(profile_dir, Path):
            shutil.rmtree(profile_dir, ignore_errors=True)
        return {"status": "stopped", "session_id": session_id}

    def _save_screenshot(self, session_id: str, page) -> Path:
        target = self.screenshot_root / f"{session_id}-{int(time.time() * 1000)}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(target), full_page=True)
        return target

    def _attachment_for(self, screenshot_path: Path) -> Dict[str, Any]:
        return {
            "name": screenshot_path.name,
            "type": "image/png",
            "url": screenshot_path.as_posix(),
            "origin": "computer_use",
            "relative_path": screenshot_path.name,
            "capture_source": "browser",
        }

    @staticmethod
    def _coerce_start_error(exc: Exception) -> Exception:
        detail = str(exc or "").strip()
        lowered = detail.lower()
        if "executable doesn't exist" in lowered or "playwright install" in lowered:
            return RuntimeError(
                "Playwright browser binaries are not installed. "
                "Run 'playwright install chromium' and try again."
            )
        compact = " ".join(detail.split())
        if compact:
            ascii_detail = compact.encode("ascii", "ignore").decode("ascii").strip()
            if ascii_detail and ascii_detail != detail:
                return RuntimeError(ascii_detail)
        return exc

    def start_session(
        self,
        *,
        session_id: str,
        width: int,
        height: int,
        start_url: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ComputerSessionState:
        pw = self._ensure_playwright()
        profile_dir = Path(tempfile.mkdtemp(prefix=f"float-browser-{session_id}-"))
        browser = None
        try:
            browser = pw.chromium.launch_persistent_context(
                str(profile_dir),
                headless=True,
                viewport={"width": int(width), "height": int(height)},
            )
            page = browser.pages[0] if browser.pages else browser.new_page()
            if start_url:
                page.goto(start_url, wait_until="load")
            now = time.time()
            session = ComputerSessionState(
                id=session_id,
                runtime=self.name,
                status="active",
                width=int(width),
                height=int(height),
                created_at=now,
                updated_at=now,
                current_url=page.url or start_url,
                active_window=page.title() if page else None,
                metadata=dict(metadata or {}),
            )
            self._sessions[session_id] = {
                "browser": browser,
                "page": page,
                "profile_dir": profile_dir,
            }
            return session
        except Exception as exc:
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass
            shutil.rmtree(profile_dir, ignore_errors=True)
            normalized_exc = self._coerce_start_error(exc)
            if normalized_exc is exc:
                raise
            raise normalized_exc from exc

    def stop_session(self, session: ComputerSessionState) -> Dict[str, Any]:
        return self._close_handle(session.id)

    def observe(self, session: ComputerSessionState) -> Dict[str, Any]:
        handle = self._page_handle(session.id)
        page = handle["page"]
        screenshot = self._save_screenshot(session.id, page)
        return {
            "summary": f"Captured browser state at {page.url or 'about:blank'}",
            "current_url": page.url,
            "active_window": page.title() or None,
            "last_screenshot_path": screenshot.as_posix(),
            "attachment": self._attachment_for(screenshot),
        }

    def navigate(self, session: ComputerSessionState, url: str) -> Dict[str, Any]:
        handle = self._page_handle(session.id)
        page = handle["page"]
        page.goto(str(url), wait_until="load")
        observed = self.observe(session)
        observed["summary"] = f"Navigated browser session to {page.url or url}"
        return observed

    def act(
        self, session: ComputerSessionState, actions: List[ComputerAction]
    ) -> Dict[str, Any]:
        handle = self._page_handle(session.id)
        page = handle["page"]
        applied: List[Dict[str, Any]] = []
        for action in actions:
            kind = str(action.type or "").strip().lower()
            if kind == "click":
                page.mouse.click(int(action.x or 0), int(action.y or 0))
            elif kind == "double_click":
                page.mouse.dblclick(int(action.x or 0), int(action.y or 0))
            elif kind == "scroll":
                page.mouse.wheel(int(action.delta_x or 0), int(action.delta_y or 0))
            elif kind == "type":
                if action.text:
                    page.keyboard.type(str(action.text))
            elif kind in {"keypress", "key"}:
                keys = action.keys
                if isinstance(keys, list):
                    for item in keys:
                        if item:
                            page.keyboard.press(str(item))
                elif keys:
                    page.keyboard.press(str(keys))
            elif kind == "wait":
                page.wait_for_timeout(max(0, int(action.ms or 0)))
            elif kind == "navigate":
                if action.url:
                    page.goto(str(action.url), wait_until="load")
            else:
                raise RuntimeError(f"Unsupported browser action '{kind}'")
            applied.append(action.to_dict())
        observed = self.observe(session)
        observed["summary"] = f"Applied {len(applied)} browser action(s)"
        observed["actions"] = applied
        return observed

    def shutdown(self) -> Dict[str, Any]:
        closed_sessions: List[str] = []
        for session_id in list(self._sessions):
            self._close_handle(session_id)
            closed_sessions.append(session_id)
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            finally:
                self._playwright = None
        return {
            "status": "stopped",
            "runtime": self.name,
            "closed_sessions": closed_sessions,
        }
