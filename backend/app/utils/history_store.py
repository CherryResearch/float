import json
from pathlib import Path
from typing import Any, Dict, List

from . import conversation_store

HISTORY_DIR = conversation_store.DATA_DIR / "history"
HISTORY_DIR.mkdir(parents=True, exist_ok=True)


def _normalize_name(name: str) -> str:
    cleaned = str(name or "").strip().replace("\\", "/")
    return cleaned.lstrip("/")


def _path(name: str) -> Path:
    normalized = _normalize_name(name)
    if not normalized.endswith(".json"):
        normalized += ".json"
    return HISTORY_DIR / normalized


def load_history(name: str) -> List[Dict[str, Any]]:
    fp = _path(name)
    if not fp.exists():
        return []
    try:
        with fp.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return []
    return data if isinstance(data, list) else []


def save_history(name: str, items: List[Dict[str, Any]]) -> None:
    fp = _path(name)
    fp.parent.mkdir(parents=True, exist_ok=True)
    with fp.open("w", encoding="utf-8") as handle:
        json.dump(items, handle, indent=2)
