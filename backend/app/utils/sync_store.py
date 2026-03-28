import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from app import config as app_config

REPO_ROOT = Path(__file__).resolve().parents[3]
SYNC_PATH = app_config.DEFAULT_DATABASES_DIR / "sync_state.json"
LEGACY_SYNC_PATH = REPO_ROOT / "sync_state.json"


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
