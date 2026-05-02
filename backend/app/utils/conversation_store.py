import json  # Standard library for JSON operations
import os  # Standard library for environment and file paths
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from uuid import uuid4

# Determine a stable project root so that the conversations directory is
# always the one that lives in the repository root, regardless of the
# process working directory (which can be ``backend`` when the API is
# launched from there).
#
# ``conversation_store`` lives under ``backend/app/utils`` – three levels
# deep from the repository root (utils -> app -> backend -> REPO_ROOT).
# Walking three parents up from this file therefore gives us the stable
# root of the project.  Using this path avoids accidentally creating a
# second ``conversations`` folder inside ``backend`` when the API is
# started with the working directory set to ``backend``.

REPO_ROOT = Path(__file__).resolve().parents[3]


def _resolve_path(value: str) -> Path:
    """Resolve a user-supplied path relative to the repo root when needed."""
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = REPO_ROOT / candidate
    try:
        return candidate.resolve()
    except Exception:
        return candidate


def _data_dir() -> Path:
    env = os.getenv("FLOAT_DATA_DIR")
    if env:
        return _resolve_path(env)
    return (REPO_ROOT / "data").resolve()


def _migrate_legacy_conversations(*, legacy_dir: Path, target_dir: Path) -> None:
    """Best-effort migrate repo-root conversations into the `data/` tree.

    This only runs when the user has not explicitly set `FLOAT_CONV_DIR` and
    copies only files that are missing from the target tree.
    """
    try:
        if legacy_dir.resolve() == target_dir.resolve():
            return
        if not legacy_dir.exists() or not legacy_dir.is_dir():
            return
        candidates = [path for path in legacy_dir.rglob("*.json") if path.is_file()]
        if not candidates:
            return
        target_dir.mkdir(parents=True, exist_ok=True)
        for src in candidates:
            try:
                relative = src.relative_to(legacy_dir)
            except Exception:
                relative = Path(src.name)
            dest = target_dir / relative
            if dest.exists():
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(str(src), str(dest))
            except Exception:
                try:
                    shutil.copyfile(str(src), str(dest))
                except Exception:
                    continue
    except Exception:
        # Never block module import on migration failures.
        return


DEV_MODE = os.getenv("FLOAT_DEV_MODE", "false").lower() == "true"
DATA_DIR = _data_dir()
DEFAULT_DIR = DATA_DIR / ("test_conversations" if DEV_MODE else "conversations")
legacy_default = REPO_ROOT / ("test_conversations" if DEV_MODE else "conversations")
conv_dir_env = os.getenv("FLOAT_CONV_DIR")
if conv_dir_env:
    CONV_DIR = _resolve_path(conv_dir_env)
else:
    _migrate_legacy_conversations(legacy_dir=legacy_default, target_dir=DEFAULT_DIR)
    CONV_DIR = DEFAULT_DIR
CONV_DIR.mkdir(parents=True, exist_ok=True)

SESSION_RE = re.compile(r"^sess-(\d+)$")
CONVERSATION_PRIVACY_TO_SENSITIVITY = {
    "default": "personal",
    "protected": "protected",
    "secret": "secret",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_conversation_privacy_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in CONVERSATION_PRIVACY_TO_SENSITIVITY else "default"


def conversation_privacy_to_sensitivity(value: Any) -> str:
    mode = normalize_conversation_privacy_mode(value)
    return CONVERSATION_PRIVACY_TO_SENSITIVITY[mode]


def conversation_privacy_mode_from_sensitivity(value: Any) -> str:
    level = str(value or "").strip().lower()
    if level in {"protected", "secret"}:
        return level
    if level in {"mundane", "public", "personal"}:
        return "default"
    return "default"


def _humanize_session_name(name: str) -> str:
    raw = str(name or "").strip().replace("\\", "/")
    base = raw.split("/")[-1] if raw else ""
    match = SESSION_RE.match(base or "")
    if not match:
        return base or name
    try:
        ts_ms = int(match.group(1))
    except (ValueError, TypeError):
        return base or name
    try:
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return base or name
    return dt.strftime("New Chat %Y-%m-%d %H:%M")


def _normalize_name(name: str) -> str:
    cleaned = str(name or "").strip().replace("\\", "/")
    return cleaned.lstrip("/")


def _path(name: str) -> Path:
    normalized = _normalize_name(name)
    if not normalized.endswith(".json"):
        normalized += ".json"
    return CONV_DIR / normalized


def _meta_path(name: str) -> Path:
    normalized = _normalize_name(name)
    return CONV_DIR / f"{normalized}.meta.json"


def _safe_snapshot_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip())
    cleaned = cleaned.strip("-.")
    return cleaned or str(uuid4())


def _context_snapshot_dir(name: str) -> Path:
    normalized = _normalize_name(name) or "conversation"
    return CONV_DIR / ".context_snapshots" / normalized


def _context_snapshot_path(name: str, snapshot_id: str) -> Path:
    return _context_snapshot_dir(name) / f"{_safe_snapshot_id(snapshot_id)}.json"


def _snapshot_relative_name(path: Path) -> str:
    try:
        return path.relative_to(CONV_DIR).as_posix()
    except Exception:
        return path.as_posix()


def _iter_conversation_files() -> List[Path]:
    if not CONV_DIR.exists():
        return []
    files: List[Path] = []
    for path in CONV_DIR.rglob("*.json"):
        if path.name.endswith(".meta.json"):
            continue
        if not _looks_like_conversation_array_file(path):
            continue
        files.append(path)
    return files


def _looks_like_conversation_array_file(path: Path) -> bool:
    """Cheaply filter out non-conversation JSON artifacts.

    Conversation history files are JSON arrays. A few other repo-local helper
    artifacts live under the same tree as JSON objects, so we inspect only the
    first non-whitespace character instead of fully parsing every file during
    listing.
    """
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            while True:
                chunk = handle.read(256)
                if not chunk:
                    return False
                for char in chunk:
                    if char.isspace():
                        continue
                    return char == "["
    except Exception:
        return False


def _is_empty_conversation_file(path: Path) -> bool:
    """Fast empty-check without fully parsing large history files."""
    try:
        size = path.stat().st_size
    except Exception:
        return False
    if size == 0:
        return True
    # Keep the fast path cheap: only inspect very small payloads.
    if size > 48:
        return False
    try:
        snippet = path.read_text(encoding="utf-8", errors="ignore").strip()
    except Exception:
        return False
    return snippet in {"", "[]", "{}"}


def _relative_name(path: Path) -> str:
    try:
        relative = path.relative_to(CONV_DIR)
    except Exception:
        relative = path.name
    name = relative.as_posix()
    if name.endswith(".json"):
        name = name[:-5]
    return name


def _prune_empty_dirs(start: Path) -> None:
    try:
        base = CONV_DIR.resolve()
    except Exception:
        return
    try:
        current = start.resolve()
    except Exception:
        current = start
    while True:
        if current == base:
            break
        if not current.exists() or not current.is_dir():
            break
        try:
            next(current.iterdir())
            break
        except StopIteration:
            try:
                current.rmdir()
            except Exception:
                break
        except Exception:
            break
        current = current.parent


def _load_meta(name: str) -> Dict[str, Any]:
    meta_fp = _meta_path(name)
    if not meta_fp.exists():
        return {}
    try:
        with meta_fp.open("r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _write_meta(name: str, meta: Dict[str, Any]) -> None:
    meta_fp = _meta_path(name)
    meta_fp.parent.mkdir(parents=True, exist_ok=True)
    with meta_fp.open("w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)


def _infer_timestamp(name: str) -> str:
    fp = _path(name)
    if fp.exists():
        try:
            ts = fp.stat().st_mtime
            return datetime.fromtimestamp(ts, timezone.utc).isoformat()
        except Exception:
            pass
    return _now_iso()


def _ensure_metadata(name: str) -> Dict[str, Any]:
    meta = _load_meta(name)
    if not isinstance(meta, dict):
        meta = {}
    changed = False
    if not meta.get("id"):
        meta["id"] = str(uuid4())
        changed = True
    if not meta.get("created_at"):
        meta["created_at"] = _infer_timestamp(name)
        changed = True
    if not meta.get("updated_at"):
        meta["updated_at"] = meta["created_at"]
        changed = True
    display = meta.get("display_name")
    if not display:
        meta["display_name"] = _humanize_session_name(name)
        changed = True
    if "auto_title_applied" not in meta:
        meta["auto_title_applied"] = False
        changed = True
    if "manual_title" not in meta:
        meta["manual_title"] = False
        changed = True
    if meta.get("name") != name:
        meta["name"] = name
        changed = True
    if changed:
        _write_meta(name, meta)
    return meta


def get_metadata(name: str) -> Dict[str, Any]:
    """Return metadata for ``name`` (ensuring defaults exist)."""
    return _ensure_metadata(name)


def set_display_name(
    name: str,
    display_name: str,
    *,
    auto_generated: Optional[bool] = None,
    manual: Optional[bool] = None,
) -> None:
    """Persist a human-friendly display name for a conversation."""
    meta = _ensure_metadata(name)
    meta["display_name"] = display_name
    if auto_generated is not None:
        meta["auto_title_applied"] = bool(auto_generated)
    if manual is not None:
        meta["manual_title"] = bool(manual)
    _write_meta(name, meta)


def merge_metadata(name: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    """Merge arbitrary metadata into the sidecar file for ``name``."""

    def _merge(dst: Dict[str, Any], src: Dict[str, Any]) -> Dict[str, Any]:
        merged = dict(dst)
        for key, value in src.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = _merge(merged[key], value)
            else:
                merged[key] = value
        return merged

    meta = _ensure_metadata(name)
    if isinstance(updates, dict):
        meta = _merge(meta, updates)
        _write_meta(name, meta)
    return meta


def list_conversations(
    include_metadata: bool = False,
) -> List[Union[str, Dict[str, Any]]]:
    """
    List all conversations, removing any empty ones (auto-delete empty files).
    """
    names: List[str] = []
    for p in _iter_conversation_files():
        # Avoid json-loading large histories on every sidebar refresh.
        if _is_empty_conversation_file(p):
            try:
                p.unlink()
            except Exception:
                pass
            _prune_empty_dirs(p.parent)
            continue
        names.append(_relative_name(p))
    names.sort()
    if not include_metadata:
        return names
    detailed: List[Dict[str, Any]] = []
    for name in names:
        try:
            meta = _ensure_metadata(name)
        except Exception:
            # Keep listing resilient even if a sidecar read/write fails for one item.
            inferred_ts = _infer_timestamp(name)
            detailed.append(
                {
                    "name": name,
                    "id": None,
                    "created_at": inferred_ts,
                    "updated_at": inferred_ts,
                    "message_count": None,
                    "display_name": _humanize_session_name(name),
                    "auto_title_applied": False,
                    "manual_title": False,
                    "path": name,
                }
            )
            continue
        detailed.append(
            {
                "name": name,
                "id": meta.get("id"),
                "created_at": meta.get("created_at"),
                "updated_at": meta.get("updated_at"),
                "message_count": meta.get("message_count"),
                "display_name": meta.get("display_name") or name,
                "auto_title_applied": bool(meta.get("auto_title_applied")),
                "manual_title": bool(meta.get("manual_title")),
                "workflow": meta.get("workflow"),
                "workflow_profile": meta.get("workflow_profile"),
                "provenance": meta.get("provenance"),
                "handoff": meta.get("handoff"),
                "sensitivity": str(meta.get("sensitivity") or "").strip().lower(),
                "privacy_mode": normalize_conversation_privacy_mode(
                    meta.get("privacy_mode")
                    or conversation_privacy_mode_from_sensitivity(
                        meta.get("sensitivity")
                    )
                ),
                "context_snapshots": meta.get("context_snapshots") or [],
                "active_context_snapshot_id": meta.get("active_context_snapshot_id"),
                "path": name,
            }
        )
    return detailed


def load_conversation(name: str) -> List[Dict[str, Any]]:
    fp = _path(name)
    if not fp.exists():
        return []
    try:
        with fp.open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return []
    if isinstance(payload, list):
        return payload
    return []


def get_or_create_conversation_id(name: str) -> str:
    """Return a stable UUID for a conversation name, creating it if missing."""
    meta = _ensure_metadata(name)
    conv_id = meta.get("id")
    if conv_id:
        return str(conv_id)
    conv_id = str(uuid4())
    meta["id"] = conv_id
    _write_meta(name, meta)
    return conv_id


def save_conversation(name: str, messages: List[Dict[str, Any]]) -> None:
    # Ensure sidecar id exists
    meta = _ensure_metadata(name)
    fp = _path(name)
    fp.parent.mkdir(parents=True, exist_ok=True)

    def _serialize(obj: Any) -> Any:
        if isinstance(obj, bytes):
            return obj.decode("utf-8", errors="replace")
        raise TypeError(
            f"Object of type {obj.__class__.__name__} is not JSON serializable"
        )

    with fp.open("w", encoding="utf-8") as f:
        json.dump(messages, f, indent=2, default=_serialize)
    meta["updated_at"] = _now_iso()
    meta.setdefault("created_at", meta["updated_at"])
    meta["message_count"] = len(messages)
    _write_meta(name, meta)


def _build_context_snapshot_ref(
    name: str,
    snapshot: Dict[str, Any],
    path: Path,
) -> Dict[str, Any]:
    return {
        "id": str(snapshot.get("id") or snapshot.get("snapshot_id") or "").strip(),
        "created_at": snapshot.get("created_at"),
        "conversation_id": name,
        "source_conversation_id": snapshot.get("source_conversation_id"),
        "target_conversation_id": snapshot.get("target_conversation_id"),
        "marker_message_id": snapshot.get("marker_message_id"),
        "source_message_count": snapshot.get("source_message_count"),
        "retained_start_index": snapshot.get("retained_start_index"),
        "retained_end_index": snapshot.get("retained_end_index"),
        "summary_method": snapshot.get("summary_method"),
        "summary_workflow": snapshot.get("summary_workflow"),
        "budget_status": snapshot.get("budget_status"),
        "path": _snapshot_relative_name(path),
    }


def list_context_snapshot_refs(name: str) -> List[Dict[str, Any]]:
    meta = _ensure_metadata(name)
    refs = meta.get("context_snapshots")
    if isinstance(refs, list):
        return [ref for ref in refs if isinstance(ref, dict)]
    return []


def load_context_snapshot(name: str, snapshot_id: str) -> Dict[str, Any]:
    fp = _context_snapshot_path(name, snapshot_id)
    if not fp.exists():
        return {}
    try:
        with fp.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def save_context_snapshot(
    name: str,
    snapshot: Dict[str, Any],
    *,
    max_refs: int = 50,
) -> Dict[str, Any]:
    payload = dict(snapshot) if isinstance(snapshot, dict) else {}
    snapshot_id = _safe_snapshot_id(
        str(payload.get("id") or payload.get("snapshot_id") or uuid4())
    )
    payload["id"] = snapshot_id
    payload["snapshot_id"] = snapshot_id
    payload["conversation_id"] = name
    payload.setdefault("created_at", _now_iso())
    fp = _context_snapshot_path(name, snapshot_id)
    fp.parent.mkdir(parents=True, exist_ok=True)
    with fp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    meta = _ensure_metadata(name)
    refs = [
        ref
        for ref in list_context_snapshot_refs(name)
        if str(ref.get("id") or "").strip() != snapshot_id
    ]
    refs.append(_build_context_snapshot_ref(name, payload, fp))
    refs.sort(key=lambda item: str(item.get("created_at") or ""))
    meta["context_snapshots"] = refs[-max_refs:]
    meta["active_context_snapshot_id"] = snapshot_id
    _write_meta(name, meta)
    return payload


def copy_context_snapshot(
    source_name: str,
    target_name: str,
    snapshot_id: str,
) -> Dict[str, Any]:
    snapshot = load_context_snapshot(source_name, snapshot_id)
    if not snapshot:
        return {}
    copied = dict(snapshot)
    copied["conversation_id"] = target_name
    copied["copied_from"] = {
        "conversation_id": source_name,
        "snapshot_id": str(snapshot_id or "").strip(),
    }
    return save_context_snapshot(target_name, copied)


def delete_conversation(name: str) -> None:
    fp = _path(name)
    if fp.exists():
        fp.unlink()
    meta_fp = _meta_path(name)
    if meta_fp.exists():
        meta_fp.unlink()
    _prune_empty_dirs(fp.parent)


def rename_conversation(old: str, new: str) -> None:
    meta_payload = _load_meta(old)
    old_base = _normalize_name(old).split("/")[-1]
    new_base = _normalize_name(new).split("/")[-1]
    old_p = _path(old)
    new_p = _path(new)
    old_meta = _meta_path(old)
    new_meta = _meta_path(new)
    old_snapshots = _context_snapshot_dir(old)
    new_snapshots = _context_snapshot_dir(new)
    new_p.parent.mkdir(parents=True, exist_ok=True)
    if old_p.exists():
        old_p.rename(new_p)
        _prune_empty_dirs(old_p.parent)
    new_meta.parent.mkdir(parents=True, exist_ok=True)
    if old_meta.exists():
        old_meta.rename(new_meta)
    if old_snapshots.exists():
        new_snapshots.parent.mkdir(parents=True, exist_ok=True)
        try:
            if new_snapshots.exists():
                shutil.rmtree(new_snapshots)
            old_snapshots.rename(new_snapshots)
        except Exception:
            pass
    if isinstance(meta_payload, dict):
        refs = meta_payload.get("context_snapshots")
        if isinstance(refs, list):
            updated_refs: List[Dict[str, Any]] = []
            old_snapshot_prefix = (
                _context_snapshot_dir(old).relative_to(CONV_DIR).as_posix()
            )
            new_snapshot_prefix = (
                _context_snapshot_dir(new).relative_to(CONV_DIR).as_posix()
            )
            for item in refs:
                if not isinstance(item, dict):
                    continue
                ref = dict(item)
                raw_path = str(ref.get("path") or "").strip()
                if raw_path.startswith(old_snapshot_prefix):
                    ref["path"] = raw_path.replace(
                        old_snapshot_prefix,
                        new_snapshot_prefix,
                        1,
                    )
                ref["conversation_id"] = new
                updated_refs.append(ref)
            meta_payload["context_snapshots"] = updated_refs
        _write_meta(new, meta_payload)
    if meta_payload and old_base == new_base:
        display_name = meta_payload.get("display_name") or new_base
        auto_generated = meta_payload.get("auto_title_applied")
        manual = meta_payload.get("manual_title")
        set_display_name(
            new,
            display_name,
            auto_generated=auto_generated,
            manual=manual,
        )
    else:
        set_display_name(
            new,
            new_base or new,
            auto_generated=False,
            manual=True,
        )
