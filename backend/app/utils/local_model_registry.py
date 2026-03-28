from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app import config as app_config
from app.utils import user_settings

_ALLOWED_MODEL_TYPES = {
    "transformer",
    "stt",
    "tts",
    "vision",
    "voice",
    "other",
}

_MODEL_TYPE_ALIASES = {
    "language": "transformer",
    "llm": "transformer",
    "chat": "transformer",
    "asr": "stt",
    "speech": "stt",
    "vlm": "vision",
}


def normalize_model_type(value: Optional[str]) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return "other"
    return _MODEL_TYPE_ALIASES.get(raw, raw if raw in _ALLOWED_MODEL_TYPES else "other")


def _coerce_path(path_value: Any) -> Optional[Path]:
    raw = str(path_value or "").strip()
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = app_config.REPO_ROOT / candidate
    try:
        return candidate.resolve()
    except Exception:
        return candidate


def _sanitize_alias(alias: Optional[str]) -> str:
    raw = str(alias or "").strip()
    if not raw:
        return ""
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", raw)
    clean = clean.strip("-_.")
    return clean


def infer_model_type(alias: str, path: Path) -> str:
    hint = f"{alias} {path.name}".lower()
    if any(token in hint for token in ("whisper", "speech", "wav2vec", "asr")):
        return "stt"
    if any(token in hint for token in ("tts", "kokoro", "kitten", "bark")):
        return "tts"
    if any(
        token in hint
        for token in (
            "clip",
            "paligemma",
            "pixtral",
            "llava",
            "vision",
            "siglip",
            "blip",
        )
    ):
        return "vision"
    if any(token in hint for token in ("voice", "voxtral", "realtime")):
        return "voice"
    return "transformer"


def _normalize_entry(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    path_obj = _coerce_path(raw.get("path"))
    if path_obj is None:
        return None
    alias = _sanitize_alias(raw.get("alias"))
    if not alias:
        alias = _sanitize_alias(path_obj.stem if path_obj.is_file() else path_obj.name)
    if not alias:
        return None
    model_type = normalize_model_type(raw.get("model_type"))
    if model_type == "other":
        model_type = infer_model_type(alias, path_obj)
    exists = path_obj.exists()
    source_type = "file" if exists and path_obj.is_file() else "directory"
    if not exists:
        source_type = "missing"
    return {
        "alias": alias,
        "path": str(path_obj),
        "model_type": model_type,
        "source_type": source_type,
        "exists": bool(exists),
        "updated_at": float(raw.get("updated_at") or 0.0),
    }


def list_local_model_entries(*, include_missing: bool = False) -> List[Dict[str, Any]]:
    settings = user_settings.load_settings()
    raw_entries = settings.get("local_model_registrations", [])
    if not isinstance(raw_entries, list):
        return []
    deduped: Dict[str, Dict[str, Any]] = {}
    for raw in raw_entries:
        entry = _normalize_entry(raw)
        if entry is None:
            continue
        if not include_missing and not entry.get("exists"):
            continue
        deduped[entry["alias"]] = entry
    return [deduped[key] for key in sorted(deduped)]


def upsert_local_model_entry(
    *,
    path: str,
    alias: Optional[str] = None,
    model_type: Optional[str] = None,
) -> Dict[str, Any]:
    path_obj = _coerce_path(path)
    if path_obj is None:
        raise ValueError("path is required")
    if not path_obj.exists():
        raise ValueError("path does not exist")
    alias_value = _sanitize_alias(alias)
    if not alias_value:
        alias_value = _sanitize_alias(
            path_obj.stem if path_obj.is_file() else path_obj.name
        )
    if not alias_value:
        raise ValueError("alias is required")
    normalized_type = normalize_model_type(model_type)
    if normalized_type == "other":
        normalized_type = infer_model_type(alias_value, path_obj)
    source_type = "file" if path_obj.is_file() else "directory"
    now = time.time()
    next_entry = {
        "alias": alias_value,
        "path": str(path_obj),
        "model_type": normalized_type,
        "source_type": source_type,
        "updated_at": now,
    }
    existing = list_local_model_entries(include_missing=True)
    filtered = [entry for entry in existing if entry.get("alias") != alias_value]
    filtered.append(next_entry)
    filtered.sort(key=lambda entry: str(entry.get("alias") or "").lower())
    user_settings.save_settings({"local_model_registrations": filtered})
    out = _normalize_entry(next_entry)
    if out is None:
        raise ValueError("failed to normalize registered model entry")
    return out


def remove_local_model_entry(alias: str) -> bool:
    alias_value = _sanitize_alias(alias)
    if not alias_value:
        return False
    existing = list_local_model_entries(include_missing=True)
    filtered = [entry for entry in existing if entry.get("alias") != alias_value]
    if len(filtered) == len(existing):
        return False
    user_settings.save_settings({"local_model_registrations": filtered})
    return True


def resolve_registered_model_path(
    model_name: str,
    *,
    for_loading: bool = False,
) -> Optional[Path]:
    target = _sanitize_alias(model_name)
    if not target:
        return None
    for entry in list_local_model_entries(include_missing=False):
        alias = _sanitize_alias(entry.get("alias"))
        if alias != target:
            continue
        path_obj = _coerce_path(entry.get("path"))
        if path_obj is None or not path_obj.exists():
            return None
        if for_loading and path_obj.is_file():
            return path_obj.parent
        return path_obj
    return None
