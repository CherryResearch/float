import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app import config as app_config

REPO_ROOT = Path(__file__).resolve().parents[3]
SYNC_PATH = app_config.DEFAULT_DATABASES_DIR / "sync_state.json"
LEGACY_SYNC_PATH = REPO_ROOT / "sync_state.json"
MAX_SYNC_OPERATION_HISTORY = 24
SYNC_OPERATION_STOP_EFFECT = (
    "Stop records cancel intent and aborts the current local request where "
    "supported; remote work may continue if another device already accepted it."
)


def _load() -> Dict[str, Any]:
    for path in (SYNC_PATH, LEGACY_SYNC_PATH):
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                continue
            if path == LEGACY_SYNC_PATH and path != SYNC_PATH:
                _save(payload)
                try:
                    path.unlink()
                except Exception:
                    pass
            return payload
        except Exception:
            continue
    return {}


def _save(data: Dict[str, Any]) -> None:
    SYNC_PATH.parent.mkdir(parents=True, exist_ok=True)
    SYNC_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    if LEGACY_SYNC_PATH.exists() and LEGACY_SYNC_PATH != SYNC_PATH:
        try:
            LEGACY_SYNC_PATH.unlink()
        except Exception:
            pass


def get_cursor() -> str:
    data = _load()
    return str(data.get("cursor", "0"))


def set_cursor(cursor: str) -> None:
    data = _load()
    data["cursor"] = cursor
    _save(data)


def record_changes(changes: List[Dict[str, Any]]) -> str:
    """Append changes and return new cursor id (monotonic counter as str)."""
    data = _load()
    counter = int(data.get("counter", 0)) + 1
    data.setdefault("log", []).append({"id": str(counter), "changes": changes})
    data["counter"] = counter
    data["cursor"] = str(counter)
    _save(data)
    return str(counter)


def get_changes_since(cursor: str) -> Tuple[List[Dict[str, Any]], str]:
    data = _load()
    log = data.get("log", [])
    out: List[Dict[str, Any]] = []
    for entry in log:
        if entry.get("id") > str(cursor):
            out.extend(entry.get("changes", []))
    return out, str(data.get("cursor", "0"))


def _now() -> float:
    return time.time()


def _clean_text(value: Any, *, limit: int = 240) -> str:
    text = " ".join(str(value or "").strip().split())
    return text[:limit]


def _clean_id(value: Any) -> str:
    text = _clean_text(value, limit=120)
    return text or str(uuid.uuid4())


def _clean_sections(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [_clean_text(item, limit=80) for item in value if _clean_text(item)]


def _operation_elapsed(entry: Dict[str, Any]) -> float:
    started = float(entry.get("started_at") or entry.get("updated_at") or _now())
    finished = entry.get("finished_at")
    end = float(finished) if isinstance(finished, (int, float)) else _now()
    return max(0.0, end - started)


def _operation_state_explanation(entry: Dict[str, Any]) -> Dict[str, Any]:
    status = _clean_text(entry.get("status"), limit=40) or "running"
    status_key = status.lower()
    kind = _clean_text(entry.get("kind"), limit=40) or "sync"
    op_id = _clean_id(entry.get("id"))
    remote_label = _clean_text(entry.get("remote_label"), limit=160)
    remote_url = _clean_text(entry.get("remote_url"), limit=360)
    owner = _clean_text(entry.get("owner"), limit=160) or "this device"
    sections = _clean_sections(entry.get("sections"))
    error = _clean_text(entry.get("error"), limit=240)
    rows = [
        {"label": "Source", "value": "sync operation ledger"},
        {"label": "Operation", "value": f"{kind} / {op_id}"},
        {"label": "Status", "value": status},
        {"label": "Owner", "value": owner},
    ]
    if remote_label or remote_url:
        rows.append({"label": "Remote", "value": remote_label or remote_url})
    if sections:
        rows.append({"label": "Sections", "value": ", ".join(sections)})
    request_id = _clean_text(entry.get("request_id"), limit=160)
    if request_id:
        rows.append({"label": "Request", "value": request_id})
    if error:
        rows.append({"label": "Evidence", "value": error})
    if bool(entry.get("cancel_requested")) or status_key in {
        "running",
        "cancel_requested",
    }:
        next_action = SYNC_OPERATION_STOP_EFFECT
    elif status_key == "failed":
        next_action = (
            error or "Review the remote connection, then retry the sync action."
        )
    else:
        next_action = (
            "Recent sync attempts stay visible so retries can explain what changed."
        )
    rows.append({"label": "Next", "value": next_action})
    return {
        "title": "Why this sync operation is shown",
        "summary": (
            "Float records sync attempts locally so active ownership, retry state, "
            "and cancel intent are visible in the UI."
        ),
        "rows": rows,
    }


def _summarize_operation(entry: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(entry or {})
    item["id"] = _clean_id(item.get("id"))
    item["kind"] = _clean_text(item.get("kind"), limit=40) or "sync"
    item["status"] = _clean_text(item.get("status"), limit=40) or "running"
    item["remote_url"] = _clean_text(item.get("remote_url"), limit=360)
    item["remote_label"] = _clean_text(item.get("remote_label"), limit=160)
    item["workspace_mode"] = _clean_text(item.get("workspace_mode"), limit=40)
    item["owner"] = _clean_text(item.get("owner"), limit=160)
    item["request_id"] = _clean_text(item.get("request_id"), limit=160)
    item["sections"] = _clean_sections(item.get("sections"))
    item["cancel_requested"] = bool(item.get("cancel_requested"))
    item["stop_effect"] = SYNC_OPERATION_STOP_EFFECT
    item["elapsed_seconds"] = round(_operation_elapsed(item), 3)
    item["state_explanation"] = _operation_state_explanation(item)
    return item


def _operation_sort_key(entry: Dict[str, Any]) -> Tuple[float, float]:
    return (
        float(entry.get("updated_at") or 0),
        float(entry.get("started_at") or 0),
    )


def _load_operations(data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    raw = data.get("sync_operations")
    if not isinstance(raw, dict):
        return {}
    operations: Dict[str, Dict[str, Any]] = {}
    for key, value in raw.items():
        if isinstance(value, dict):
            op_id = _clean_id(value.get("id") or key)
            operations[op_id] = {**value, "id": op_id}
    return operations


def _trim_operations(
    operations: Dict[str, Dict[str, Any]]
) -> Dict[str, Dict[str, Any]]:
    active_statuses = {"running", "cancel_requested"}
    active = [
        entry
        for entry in operations.values()
        if _clean_text(entry.get("status")).lower() in active_statuses
    ]
    inactive = [
        entry
        for entry in operations.values()
        if _clean_text(entry.get("status")).lower() not in active_statuses
    ]
    inactive.sort(key=_operation_sort_key, reverse=True)
    kept = active + inactive[:MAX_SYNC_OPERATION_HISTORY]
    return {str(entry["id"]): entry for entry in kept if entry.get("id")}


def start_operation(
    *,
    kind: str,
    operation_id: Optional[str] = None,
    owner: Optional[str] = None,
    remote_url: Optional[str] = None,
    remote_label: Optional[str] = None,
    sections: Optional[List[str]] = None,
    workspace_mode: Optional[str] = None,
    request_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    data = _load()
    operations = _load_operations(data)
    now = _now()
    op_id = _clean_id(operation_id)
    previous = operations.get(op_id) or {}
    cancel_requested = bool(previous.get("cancel_requested"))
    entry = {
        **previous,
        "id": op_id,
        "kind": _clean_text(kind, limit=40) or "sync",
        "status": "cancel_requested" if cancel_requested else "running",
        "started_at": now,
        "updated_at": now,
        "finished_at": None,
        "remote_url": _clean_text(remote_url, limit=360),
        "remote_label": _clean_text(remote_label, limit=160),
        "sections": _clean_sections(sections or []),
        "workspace_mode": _clean_text(workspace_mode, limit=40),
        "owner": _clean_text(owner, limit=160),
        "request_id": _clean_text(request_id, limit=160),
        "cancel_requested": cancel_requested,
        "cancel_requested_at": previous.get("cancel_requested_at")
        if cancel_requested
        else None,
        "error": "",
        "metadata": dict(metadata or {}),
    }
    operations[op_id] = entry
    data["sync_operations"] = _trim_operations(operations)
    data["last_sync_operation_id"] = op_id
    _save(data)
    return _summarize_operation(entry)


def finish_operation(
    operation_id: str,
    *,
    status: str = "completed",
    error: Optional[str] = None,
    result: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    op_id = _clean_id(operation_id)
    data = _load()
    operations = _load_operations(data)
    entry = operations.get(op_id)
    if not isinstance(entry, dict):
        return None
    now = _now()
    entry = {
        **entry,
        "status": _clean_text(status, limit=40) or "completed",
        "updated_at": now,
        "finished_at": now,
        "error": _clean_text(error, limit=500) if error else "",
    }
    if isinstance(result, dict):
        entry["result"] = dict(result)
    operations[op_id] = entry
    data["sync_operations"] = _trim_operations(operations)
    data["last_sync_operation_id"] = op_id
    _save(data)
    return _summarize_operation(entry)


def cancel_operation(operation_id: str) -> Dict[str, Any]:
    op_id = _clean_id(operation_id)
    data = _load()
    operations = _load_operations(data)
    now = _now()
    entry = operations.get(op_id) or {
        "id": op_id,
        "kind": "sync",
        "status": "cancel_requested",
        "started_at": now,
        "remote_url": "",
        "remote_label": "",
        "sections": [],
        "workspace_mode": "",
        "owner": "",
        "request_id": "",
    }
    current_status = _clean_text(entry.get("status")).lower()
    next_status = (
        "cancel_requested" if current_status == "running" else entry.get("status")
    )
    entry = {
        **entry,
        "status": next_status or "cancel_requested",
        "cancel_requested": True,
        "cancel_requested_at": now,
        "updated_at": now,
    }
    operations[op_id] = entry
    data["sync_operations"] = _trim_operations(operations)
    data["last_sync_operation_id"] = op_id
    _save(data)
    return _summarize_operation(entry)


def operations_snapshot(*, recent_limit: int = 8) -> Dict[str, Any]:
    operations = list(_load_operations(_load()).values())
    operations.sort(key=_operation_sort_key, reverse=True)
    active_statuses = {"running", "cancel_requested"}
    active = [
        _summarize_operation(entry)
        for entry in operations
        if _clean_text(entry.get("status")).lower() in active_statuses
    ]
    recent = [
        _summarize_operation(entry) for entry in operations[: max(0, recent_limit)]
    ]
    return {
        "active_operation": active[0] if active else None,
        "last_attempt": recent[0] if recent else None,
        "recent": recent,
    }
