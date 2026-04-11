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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    new_p.parent.mkdir(parents=True, exist_ok=True)
    if old_p.exists():
        old_p.rename(new_p)
        _prune_empty_dirs(old_p.parent)
    new_meta.parent.mkdir(parents=True, exist_ok=True)
    if old_meta.exists():
        old_meta.rename(new_meta)
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
