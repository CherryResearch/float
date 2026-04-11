from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from uuid import uuid4

from app.computer.playwright_runtime import PlaywrightComputerRuntime
from app.computer.runtime_base import ComputerRuntime
from app.computer.session_store import ComputerSessionStore
from app.computer.types import (
    DEFAULT_DISPLAY_HEIGHT,
    DEFAULT_DISPLAY_WIDTH,
    ComputerAction,
    ComputerObservation,
)
from app.services.capture_service import get_capture_service


class _UnavailableRuntime(ComputerRuntime):
    def __init__(self, *, screenshot_root: Path, runtime_name: str, detail: str):
        super().__init__(screenshot_root=screenshot_root)
        self._runtime_name = runtime_name
        self._detail = detail

    @property
    def name(self) -> str:
        return self._runtime_name

    def available(self) -> bool:
        return False

    def start_session(self, **kwargs):
        raise RuntimeError(self._detail)

    def stop_session(self, session):
        return {"status": "stopped", "session_id": session.id}

    def observe(self, session):
        raise RuntimeError(self._detail)

    def navigate(self, session, url: str):
        raise RuntimeError(self._detail)

    def act(self, session, actions):
        raise RuntimeError(self._detail)


def _build_windows_runtime(*, screenshot_root: Path) -> ComputerRuntime:
    try:
        runtime_module = importlib.import_module("app.computer.windows_runtime")
        runtime_cls = getattr(runtime_module, "WindowsComputerRuntime")
        return runtime_cls(screenshot_root=screenshot_root)
    except Exception as exc:
        return _UnavailableRuntime(
            screenshot_root=screenshot_root,
            runtime_name="windows",
            detail=(
                "Windows desktop control is unavailable. "
                f"Optional dependency import failed: {exc}"
            ),
        )


class ComputerService:
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        cfg = dict(config or {})
        capture_service = get_capture_service(cfg)
        screenshot_root = (capture_service.transient_root / "computer").resolve()
        screenshot_root.mkdir(parents=True, exist_ok=True)
        self.store = ComputerSessionStore(screenshot_root=screenshot_root)
        self.runtimes = {
            "browser": PlaywrightComputerRuntime(screenshot_root=screenshot_root),
            "windows": _build_windows_runtime(screenshot_root=screenshot_root),
        }
        self.capture_service = capture_service

    def capabilities(self) -> Dict[str, Any]:
        return {
            "runtimes": {
                name: {
                    "available": runtime.available(),
                    "status": "live" if runtime.available() else "unavailable",
                }
                for name, runtime in self.runtimes.items()
            },
            "tools": [
                "computer.session.start",
                "computer.session.stop",
                "computer.observe",
                "computer.act",
                "computer.navigate",
                "computer.windows.list",
                "computer.windows.focus",
                "computer.app.launch",
                "camera.capture",
                "capture.list",
                "capture.promote",
                "capture.delete",
                "shell.exec",
                "patch.apply",
                "mcp.call",
            ],
        }

    def _runtime(self, name: str):
        runtime_name = str(name or "").strip().lower() or "browser"
        runtime = self.runtimes.get(runtime_name)
        if runtime is None:
            raise RuntimeError(f"Unsupported computer runtime '{runtime_name}'")
        return runtime

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        session = self.store.get(session_id)
        return session.to_dict() if session else None

    def start_session(
        self,
        *,
        runtime: str = "browser",
        session_id: Optional[str] = None,
        width: int = DEFAULT_DISPLAY_WIDTH,
        height: int = DEFAULT_DISPLAY_HEIGHT,
        start_url: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        runtime_impl = self._runtime(runtime)
        actual_session_id = str(session_id or uuid4())
        existing = self.store.get(actual_session_id)
        if existing is not None:
            return existing.to_dict()
        session = runtime_impl.start_session(
            session_id=actual_session_id,
            width=int(width),
            height=int(height),
            start_url=start_url,
            metadata=metadata,
        )
        self.store.put(session)
        if start_url and runtime == "browser":
            self.store.touch(actual_session_id, current_url=start_url)
        return session.to_dict()

    def ensure_session(
        self,
        *,
        runtime: str = "browser",
        session_id: Optional[str] = None,
        width: int = DEFAULT_DISPLAY_WIDTH,
        height: int = DEFAULT_DISPLAY_HEIGHT,
        start_url: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        existing = self.store.get(str(session_id or "").strip()) if session_id else None
        if existing is not None:
            return existing.to_dict()
        return self.start_session(
            runtime=runtime,
            session_id=session_id,
            width=width,
            height=height,
            start_url=start_url,
            metadata=metadata,
        )

    def stop_session(self, session_id: str) -> Dict[str, Any]:
        session = self.store.get(session_id)
        if session is None:
            raise RuntimeError(f"Unknown computer session '{session_id}'")
        runtime = self._runtime(session.runtime)
        result = runtime.stop_session(session)
        self.store.delete(session.id)
        return {"session": session.to_dict(), "result": result}

    def shutdown(self) -> Dict[str, Any]:
        stopped_sessions: List[str] = []
        runtime_results: Dict[str, Any] = {}
        errors: List[Dict[str, str]] = []

        for session in list(self.store.all().values()):
            try:
                runtime = self._runtime(session.runtime)
                runtime.stop_session(session)
                stopped_sessions.append(session.id)
            except Exception as exc:
                errors.append({"session_id": session.id, "error": str(exc)})
            finally:
                self.store.delete(session.id)

        for name, runtime in self.runtimes.items():
            try:
                runtime_results[name] = runtime.shutdown()
            except Exception as exc:
                errors.append({"runtime": name, "error": str(exc)})

        return {
            "status": "stopped",
            "stopped_sessions": stopped_sessions,
            "runtime_results": runtime_results,
            "errors": errors,
        }

    def _attachment_path_to_url(self, path_value: str) -> str:
        return f"/api/computer/screenshots/{Path(path_value).name}"

    def _wrap_observation(
        self,
        session_id: str,
        *,
        summary: str,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        session = self.store.get(session_id)
        if session is None:
            raise RuntimeError(f"Unknown computer session '{session_id}'")
        updates: Dict[str, Any] = {}
        for key in ("current_url", "active_window", "last_screenshot_path"):
            value = result.get(key)
            if value is not None:
                updates[key] = value
        updated = self.store.touch(session_id, **updates) or session
        attachment = (
            dict(result.get("attachment"))
            if isinstance(result.get("attachment"), dict)
            else None
        )
        if attachment and attachment.get("url"):
            attachment["url"] = self._attachment_path_to_url(str(attachment["url"]))
        capture_payload: Optional[Dict[str, Any]] = None
        last_screenshot_path = result.get("last_screenshot_path")
        if isinstance(last_screenshot_path, str) and last_screenshot_path.strip():
            try:
                capture_payload = self.capture_service.register_existing_file(
                    last_screenshot_path,
                    source="computer",
                    content_type=str(
                        attachment.get("type")
                        if isinstance(attachment, dict)
                        else "image/png"
                    )
                    or "image/png",
                    filename=(
                        attachment.get("name") if isinstance(attachment, dict) else None
                    ),
                    capture_source=(
                        attachment.get("capture_source")
                        if isinstance(attachment, dict)
                        else session.runtime
                    ),
                    conversation_id=str(
                        updated.metadata.get("chat_session") or ""
                    ).strip()
                    or None,
                    message_id=str(updated.metadata.get("message_id") or "").strip()
                    or None,
                    computer_session_id=session_id,
                    current_url=str(result.get("current_url") or "").strip() or None,
                    active_window=str(result.get("active_window") or "").strip()
                    or None,
                )
            except Exception:
                capture_payload = None
        if capture_payload:
            attachment = dict(capture_payload.get("attachment") or {})
            attachment["capture_id"] = capture_payload.get("capture_id")
        observation = ComputerObservation(
            session=updated.to_dict(),
            summary=summary,
            attachment=attachment,
            data={
                key: value
                for key, value in result.items()
                if key not in {"attachment", "attachments", "image_attachments"}
            },
        )
        return observation.to_dict()

    def observe(self, session_id: str) -> Dict[str, Any]:
        session = self.store.get(session_id)
        if session is None:
            raise RuntimeError(f"Unknown computer session '{session_id}'")
        runtime = self._runtime(session.runtime)
        result = runtime.observe(session)
        return self._wrap_observation(
            session_id,
            summary=str(result.get("summary") or "Captured computer state"),
            result=result,
        )

    def navigate(self, session_id: str, url: str) -> Dict[str, Any]:
        session = self.store.get(session_id)
        if session is None:
            raise RuntimeError(f"Unknown computer session '{session_id}'")
        runtime = self._runtime(session.runtime)
        result = runtime.navigate(session, str(url))
        return self._wrap_observation(
            session_id,
            summary=str(result.get("summary") or f"Navigated to {url}"),
            result=result,
        )

    def act(
        self,
        session_id: str,
        actions: Iterable[Dict[str, Any] | ComputerAction],
    ) -> Dict[str, Any]:
        session = self.store.get(session_id)
        if session is None:
            raise RuntimeError(f"Unknown computer session '{session_id}'")
        runtime = self._runtime(session.runtime)
        normalized: List[ComputerAction] = []
        for item in actions or []:
            if isinstance(item, ComputerAction):
                normalized.append(item)
                continue
            if isinstance(item, dict):
                normalized.append(ComputerAction(**item))
        result = runtime.act(session, normalized)
        return self._wrap_observation(
            session_id,
            summary=str(
                result.get("summary") or f"Applied {len(normalized)} action(s)"
            ),
            result=result,
        )

    def list_windows(self, session_id: str) -> Dict[str, Any]:
        session = self.store.get(session_id)
        if session is None:
            raise RuntimeError(f"Unknown computer session '{session_id}'")
        runtime = self._runtime(session.runtime)
        result = runtime.list_windows(session)
        updated = (
            self.store.touch(
                session_id,
                active_window=result.get("focused")
                if isinstance(result.get("focused"), str)
                else session.active_window,
            )
            or session
        )
        payload = dict(result)
        payload["session"] = updated.to_dict()
        return payload

    def focus_window(self, session_id: str, window_title: str) -> Dict[str, Any]:
        session = self.store.get(session_id)
        if session is None:
            raise RuntimeError(f"Unknown computer session '{session_id}'")
        runtime = self._runtime(session.runtime)
        result = runtime.focus_window(session, window_title)
        updated = (
            self.store.touch(
                session_id,
                active_window=str(result.get("focused") or window_title),
            )
            or session
        )
        payload = dict(result)
        payload["session"] = updated.to_dict()
        return payload

    def launch_app(
        self,
        session_id: str,
        *,
        app: str,
        args: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        session = self.store.get(session_id)
        if session is None:
            raise RuntimeError(f"Unknown computer session '{session_id}'")
        runtime = self._runtime(session.runtime)
        result = runtime.launch_app(session, app, args=args)
        payload = dict(result)
        payload["session"] = session.to_dict()
        return payload

    def legacy_open_url(self, url: str, *, user: str = "anonymous") -> Dict[str, Any]:
        session_id = f"open-url-{str(user or 'anonymous').strip() or 'anonymous'}"
        session = self.ensure_session(
            runtime="browser",
            session_id=session_id,
            start_url=url,
            metadata={"legacy_alias": "open_url"},
        )
        return self.navigate(session["id"], url)

    def build_chat_tools(
        self,
        *,
        session_id: str,
        runtime: str,
        width: int,
        height: int,
        start_url: Optional[str] = None,
        allowed_domains: Optional[List[str]] = None,
        allowed_apps: Optional[List[str]] = None,
        include_native: bool = False,
        native_tool_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        def _tool(
            name: str,
            description: str,
            parameters: Dict[str, Any],
            metadata: Optional[Dict[str, Any]] = None,
        ) -> Dict[str, Any]:
            payload = {
                "name": name,
                "description": description,
                "parameters": parameters,
            }
            if metadata:
                payload["metadata"] = metadata
            return payload

        default_session = str(session_id)
        base_tools = [
            _tool(
                "computer.observe",
                "Capture a screenshot and summary of the current computer session.",
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "session_id": {"type": "string", "default": default_session},
                    },
                },
                metadata={"computer_runtime": runtime},
            ),
            _tool(
                "computer.navigate",
                "Navigate the active browser session to a URL.",
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "session_id": {"type": "string", "default": default_session},
                        "url": {"type": "string"},
                    },
                    "required": ["url"],
                },
                metadata={"computer_runtime": runtime},
            ),
            _tool(
                "computer.act",
                "Apply one or more computer actions such as click, type, scroll, keypress, wait, or navigate.",
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "session_id": {"type": "string", "default": default_session},
                        "actions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": True,
                                "properties": {
                                    "type": {"type": "string"},
                                    "x": {"type": "integer"},
                                    "y": {"type": "integer"},
                                    "button": {"type": "string", "default": "left"},
                                    "text": {"type": "string"},
                                    "keys": {"type": ["string", "array"]},
                                    "delta_x": {"type": "integer"},
                                    "delta_y": {"type": "integer"},
                                    "ms": {"type": "integer"},
                                    "url": {"type": "string"},
                                },
                                "required": ["type"],
                            },
                        },
                    },
                    "required": ["actions"],
                },
                metadata={"computer_runtime": runtime},
            ),
            _tool(
                "camera.capture",
                "Capture a still image from a connected client camera and store it as a transient capture.",
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {},
                },
            ),
            _tool(
                "capture.list",
                "List recent transient captures from computer observations, camera stills, or screen stills.",
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "source": {
                            "type": "string",
                            "enum": ["", "computer", "camera", "screen"],
                            "default": "",
                        }
                    },
                },
            ),
            _tool(
                "capture.promote",
                "Promote a transient capture into durable attachment storage.",
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "capture_id": {"type": "string"},
                    },
                    "required": ["capture_id"],
                },
            ),
            _tool(
                "capture.delete",
                "Delete a transient capture from the cache.",
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "capture_id": {"type": "string"},
                    },
                    "required": ["capture_id"],
                },
            ),
        ]
        if runtime == "windows":
            base_tools.extend(
                [
                    _tool(
                        "computer.windows.list",
                        "List visible desktop windows in the Windows runtime.",
                        {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "session_id": {
                                    "type": "string",
                                    "default": default_session,
                                },
                            },
                        },
                        metadata={"computer_runtime": runtime},
                    ),
                    _tool(
                        "computer.windows.focus",
                        "Focus a visible desktop window by title.",
                        {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "session_id": {
                                    "type": "string",
                                    "default": default_session,
                                },
                                "window_title": {"type": "string"},
                            },
                            "required": ["window_title"],
                        },
                        metadata={"computer_runtime": runtime},
                    ),
                    _tool(
                        "computer.app.launch",
                        "Launch a desktop application in the Windows runtime.",
                        {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "session_id": {
                                    "type": "string",
                                    "default": default_session,
                                },
                                "app": {"type": "string"},
                                "args": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": ["app"],
                        },
                        metadata={"computer_runtime": runtime},
                    ),
                ]
            )
        base_tools.extend(
            [
                _tool(
                    "shell.exec",
                    "Run a shell command on the host and return stdout, stderr, and exit code.",
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "command": {"type": "string"},
                            "cwd": {"type": "string", "default": ""},
                            "timeout_seconds": {
                                "type": "integer",
                                "default": 20,
                                "minimum": 1,
                                "maximum": 300,
                            },
                        },
                        "required": ["command"],
                    },
                ),
                _tool(
                    "patch.apply",
                    "Write or append text content to a local file.",
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                            "mode": {
                                "type": "string",
                                "enum": ["replace", "append", "create"],
                                "default": "replace",
                            },
                        },
                        "required": ["path", "content"],
                    },
                ),
                _tool(
                    "mcp.call",
                    "Call an MCP endpoint or return a structured placeholder when no bridge is configured.",
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "server": {"type": "string"},
                            "method": {"type": "string"},
                            "arguments": {
                                "type": "object",
                                "additionalProperties": True,
                                "default": {},
                            },
                        },
                        "required": ["server", "method"],
                    },
                ),
            ]
        )
        if include_native:
            native_type = str(native_tool_type or "").strip() or "computer_use_preview"
            native_tool: Dict[str, Any] = {
                "type": native_type,
                "display_width": int(width),
                "display_height": int(height),
                "environment": runtime,
            }
            if start_url:
                native_tool["start_url"] = start_url
            if allowed_domains:
                native_tool["allowed_domains"] = list(allowed_domains)
            if allowed_apps:
                native_tool["allowed_apps"] = list(allowed_apps)
            base_tools.append(native_tool)
        return base_tools


_computer_service: Optional[ComputerService] = None


def get_computer_service(config: Optional[Dict[str, Any]] = None) -> ComputerService:
    global _computer_service
    if _computer_service is None:
        _computer_service = ComputerService(config=config)
    return _computer_service


def set_computer_service(service: Optional[ComputerService]) -> None:
    global _computer_service
    _computer_service = service
