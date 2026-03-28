import json
import os
from pathlib import Path
from typing import Any, Dict, List

# Determine stable project root (same logic as conversation_store)
REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "data" / "databases" / "calendar_events"

DEV_MODE = os.getenv("FLOAT_DEV_MODE", "false").lower() == "true"
if DEV_MODE:
    DEFAULT_DIR = REPO_ROOT / "test_calendar_events"
else:
    DEFAULT_DIR = DATA_ROOT
EVENTS_DIR = Path(os.getenv("FLOAT_CALENDAR_DIR", DEFAULT_DIR))
EVENTS_DIR.mkdir(parents=True, exist_ok=True)


def _path(name: str) -> Path:
    if not name.endswith(".json"):
        name += ".json"
    return EVENTS_DIR / name


def list_events() -> List[str]:
    """List all events, removing any empty ones."""
    names: List[str] = []
    for p in EVENTS_DIR.glob("*.json"):
        try:
            with p.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if not data:
            try:
                p.unlink()
            except Exception:
                pass
            continue
        names.append(p.stem)
    return names


def load_event(name: str) -> Dict[str, Any]:
    fp = _path(name)
    if not fp.exists():
        return {}
    with fp.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_event(name: str, event: Dict[str, Any]) -> None:
    fp = _path(name)
    with fp.open("w", encoding="utf-8") as f:
        json.dump(event, f, indent=2)


def delete_event(name: str) -> None:
    fp = _path(name)
    if fp.exists():
        fp.unlink()
