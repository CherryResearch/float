from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
THEMES_DIR = REPO_ROOT / "data" / "themes"

THEME_SLOT_KEYS = [
    "c1Light",
    "c1Med",
    "c1Dark",
    "c2Light",
    "c2Med",
    "c2Dark",
    "veryLight",
    "veryDark",
]

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def themes_dir() -> Path:
    THEMES_DIR.mkdir(parents=True, exist_ok=True)
    return THEMES_DIR


def _slugify(value: str) -> str:
    raw = str(value or "").strip().lower()
    slug = _SLUG_RE.sub("-", raw).strip("-")
    return slug or "custom-theme"


def _normalize_slots(slots: Dict[str, Any]) -> Dict[str, str]:
    normalized: Dict[str, str] = {}
    for key in THEME_SLOT_KEYS:
        value = str(slots.get(key) or "").strip()
        if not _HEX_COLOR_RE.match(value):
            raise ValueError(f"Invalid color for {key}: expected #RRGGBB")
        normalized[key] = value.lower()
    return normalized


def _theme_path(theme_id: str) -> Path:
    safe_id = _slugify(theme_id)
    return themes_dir() / f"{safe_id}.json"


def list_themes() -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for path in sorted(themes_dir().glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        theme_id = str(payload.get("id") or path.stem).strip()
        label = str(payload.get("label") or theme_id).strip()
        slots_raw = payload.get("slots")
        if not theme_id or not isinstance(slots_raw, dict):
            continue
        try:
            slots = _normalize_slots(slots_raw)
        except ValueError:
            continue
        entries.append(
            {
                "id": theme_id,
                "label": label,
                "slots": slots,
            }
        )
    return entries


def save_theme(
    *,
    label: str,
    slots: Dict[str, Any],
    theme_id: Optional[str] = None,
) -> Dict[str, Any]:
    clean_label = str(label or "").strip()
    if not clean_label:
        raise ValueError("Theme name is required")
    normalized_slots = _normalize_slots(slots)
    requested_id = _slugify(theme_id or clean_label)
    target_path = _theme_path(requested_id)
    if not theme_id:
        counter = 2
        while target_path.exists():
            requested_id = f"{_slugify(clean_label)}-{counter}"
            target_path = _theme_path(requested_id)
            counter += 1
    payload = {
        "id": requested_id,
        "label": clean_label,
        "slots": normalized_slots,
    }
    target_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def delete_theme(theme_id: str) -> bool:
    path = _theme_path(theme_id)
    if not path.exists():
        return False
    path.unlink()
    return True
