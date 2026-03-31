from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.capture_service import get_capture_service
from app.services.computer_service import get_computer_service
from app.utils import verify_signature
from app.utils.blob_store import BLOBS_DIR, put_asset


def computer_session_start(
    runtime: str = "browser",
    session_id: Optional[str] = None,
    start_url: Optional[str] = None,
    width: int = 1280,
    height: int = 720,
    *,
    user: str,
    signature: str,
) -> Dict[str, Any]:
    payload = {
        "runtime": runtime,
        "session_id": session_id,
        "start_url": start_url,
        "width": int(width),
        "height": int(height),
    }
    verify_signature(signature, user, "computer.session.start", payload)
    service = get_computer_service()
    session = service.start_session(
        runtime=runtime,
        session_id=session_id,
        start_url=start_url,
        width=int(width),
        height=int(height),
        metadata={"requested_by": user},
    )
    try:
        observed = service.observe(str(session["id"]))
    except Exception:
        return session
    if "session" not in observed:
        observed["session"] = session
    observed.setdefault("summary", "Started computer session")
    return observed


def computer_session_stop(
    session_id: str,
    *,
    user: str,
    signature: str,
) -> Dict[str, Any]:
    payload = {"session_id": session_id}
    verify_signature(signature, user, "computer.session.stop", payload)
    return get_computer_service().stop_session(session_id)


def computer_observe(
    session_id: str,
    *,
    user: str,
    signature: str,
) -> Dict[str, Any]:
    payload = {"session_id": session_id}
    verify_signature(signature, user, "computer.observe", payload)
    return get_computer_service().observe(session_id)


def computer_navigate(
    session_id: str,
    url: str,
    *,
    user: str,
    signature: str,
) -> Dict[str, Any]:
    payload = {"session_id": session_id, "url": url}
    verify_signature(signature, user, "computer.navigate", payload)
    return get_computer_service().navigate(session_id, url)


def computer_act(
    session_id: str,
    actions: List[Dict[str, Any]],
    *,
    user: str,
    signature: str,
) -> Dict[str, Any]:
    payload = {"session_id": session_id, "actions": actions}
    verify_signature(signature, user, "computer.act", payload)
    return get_computer_service().act(session_id, actions)


def computer_windows_list(
    session_id: str,
    *,
    user: str,
    signature: str,
) -> Dict[str, Any]:
    payload = {"session_id": session_id}
    verify_signature(signature, user, "computer.windows.list", payload)
    return get_computer_service().list_windows(session_id)


def computer_windows_focus(
    session_id: str,
    window_title: str,
    *,
    user: str,
    signature: str,
) -> Dict[str, Any]:
    payload = {"session_id": session_id, "window_title": window_title}
    verify_signature(signature, user, "computer.windows.focus", payload)
    return get_computer_service().focus_window(session_id, window_title)


def computer_app_launch(
    session_id: str,
    app: str,
    args: Optional[List[str]] = None,
    *,
    user: str,
    signature: str,
) -> Dict[str, Any]:
    payload = {"session_id": session_id, "app": app, "args": args or []}
    verify_signature(signature, user, "computer.app.launch", payload)
    return get_computer_service().launch_app(session_id, app=app, args=args or [])


def camera_capture(
    *,
    user: str,
    signature: str,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    verify_signature(signature, user, "camera.capture", payload)
    raise RuntimeError(
        "camera.capture requires a connected client with camera access and must be resolved from the UI."
    )


def capture_list(
    source: str = "",
    *,
    user: str,
    signature: str,
) -> Dict[str, Any]:
    payload = {"source": source or ""}
    verify_signature(signature, user, "capture.list", payload)
    captures = get_capture_service().list_captures(
        source=str(source or "").strip().lower() or None
    )
    return {"captures": captures, "count": len(captures)}


def capture_promote(
    capture_id: str,
    *,
    user: str,
    signature: str,
) -> Dict[str, Any]:
    payload = {"capture_id": capture_id}
    verify_signature(signature, user, "capture.promote", payload)
    service = get_capture_service()
    capture = service.get_capture(capture_id)
    if capture is None:
        raise FileNotFoundError(f"Unknown capture '{capture_id}'")
    existing_ref = capture.get("attachment_ref")
    if isinstance(existing_ref, dict) and existing_ref.get("content_hash"):
        promoted = service.mark_promoted(capture_id, attachment_ref=existing_ref)
        return {"capture": promoted, "attachment": existing_ref}
    target = service.capture_path(capture_id)
    if target is None:
        raise FileNotFoundError(f"Unknown capture '{capture_id}'")
    data = target.read_bytes()
    filename = str(capture.get("filename") or target.name).strip() or target.name
    content_type = (
        str(capture.get("content_type") or "image/png").strip() or "image/png"
    )
    asset_info = put_asset(data, filename=filename, origin="captured")
    content_hash = asset_info["content_hash"]
    uploaded_at = (
        datetime.now(tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    meta_path = BLOBS_DIR / f"{content_hash}.json"
    meta_path.write_text(
        json.dumps(
            {
                "filename": filename,
                "content_type": content_type,
                "size": len(data),
                "uploaded_at": uploaded_at,
                "origin": "captured",
                "relative_path": asset_info.get("relative_path"),
                "path": asset_info.get("path"),
                "capture_source": capture.get("capture_source")
                or capture.get("source"),
                "capture_id": capture_id,
                "capture_sensitivity": capture.get("sensitivity"),
                "caption_status": "pending",
                "index_status": "not_applicable",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    attachment_ref = {
        "content_hash": content_hash,
        "filename": filename,
        "content_type": content_type,
        "size": len(data),
        "url": f"/api/attachments/{content_hash}/{filename}",
        "uploaded_at": uploaded_at,
        "origin": "captured",
        "relative_path": asset_info.get("relative_path"),
    }
    promoted = service.mark_promoted(capture_id, attachment_ref=attachment_ref)
    return {"capture": promoted, "attachment": attachment_ref}


def capture_delete(
    capture_id: str,
    *,
    user: str,
    signature: str,
) -> Dict[str, Any]:
    payload = {"capture_id": capture_id}
    verify_signature(signature, user, "capture.delete", payload)
    return get_capture_service().delete_capture(capture_id)


def shell_exec(
    command: str,
    cwd: str = "",
    timeout_seconds: int = 20,
    *,
    user: str,
    signature: str,
) -> Dict[str, Any]:
    payload = {
        "command": command,
        "cwd": cwd or "",
        "timeout_seconds": int(timeout_seconds),
    }
    verify_signature(signature, user, "shell.exec", payload)
    run_cwd = Path(cwd).expanduser() if cwd else None
    completed = subprocess.run(  # noqa: S603
        command,
        cwd=str(run_cwd) if run_cwd else None,
        capture_output=True,
        text=True,
        shell=True,  # noqa: S602 - explicit shell tool
        timeout=max(1, int(timeout_seconds)),
    )
    return {
        "command": command,
        "cwd": str(run_cwd) if run_cwd else None,
        "exit_code": int(completed.returncode),
        "stdout": completed.stdout[-12000:],
        "stderr": completed.stderr[-12000:],
        "ok": completed.returncode == 0,
    }


def patch_apply(
    path: str,
    content: str,
    mode: str = "replace",
    *,
    user: str,
    signature: str,
) -> Dict[str, Any]:
    payload = {"path": path, "content": content, "mode": mode}
    verify_signature(signature, user, "patch.apply", payload)
    target = Path(path).expanduser()
    if mode == "create" and target.exists():
        raise FileExistsError(f"File already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if mode == "append":
        existing = (
            target.read_text(encoding="utf-8", errors="ignore")
            if target.exists()
            else ""
        )
        target.write_text(existing + content, encoding="utf-8")
    else:
        target.write_text(content, encoding="utf-8")
    return {
        "path": target.as_posix(),
        "mode": mode,
        "bytes_written": len(content.encode("utf-8")),
        "status": "written",
    }


def mcp_call(
    server: str,
    method: str,
    arguments: Optional[Dict[str, Any]] = None,
    *,
    user: str,
    signature: str,
) -> Dict[str, Any]:
    payload = {
        "server": server,
        "method": method,
        "arguments": arguments or {},
    }
    verify_signature(signature, user, "mcp.call", payload)
    return {
        "status": "unimplemented",
        "server": server,
        "method": method,
        "arguments": arguments or {},
        "message": "MCP call passthrough is not configured in this runtime yet.",
    }
