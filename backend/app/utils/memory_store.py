import json
import logging
import os
import sqlite3
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Optional, Union

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATA_DIR = REPO_ROOT / "data"
DEFAULT_DATABASES_DIR = DEFAULT_DATA_DIR / "databases"
DEFAULT_MEMORY_FILE = DEFAULT_DATABASES_DIR / "memory.sqlite3"
LEGACY_MEMORY_FILE = DEFAULT_DATA_DIR / "memory.json"
LEGACY_MEMORY_SQLITE = DEFAULT_DATA_DIR / "memory.sqlite3"
SQLITE_EXTENSIONS = {".db", ".sqlite", ".sqlite3"}

PathLike = Union[str, os.PathLike[str]]


def resolve_path(path: Optional[PathLike] = None) -> Path:
    """Return a fully-resolved path for the memory store, ensuring the parent directory exists."""
    if path is not None:
        candidate = Path(path)
    else:
        env_override = os.getenv("FLOAT_MEMORY_FILE")
        candidate = Path(env_override) if env_override else DEFAULT_MEMORY_FILE
    candidate = candidate.expanduser()
    try:
        candidate.parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(
            "memory_store: unable to create parent directory %s: %s",
            candidate.parent,
            exc,
        )
    return candidate


def _is_sqlite_path(path: Path) -> bool:
    return path.suffix.lower() in SQLITE_EXTENSIONS


def _load_json(target: Path) -> Dict[str, Any]:
    if not target.exists():
        return {}
    try:
        with target.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        logger.warning(
            "memory_store.load: expected dict in %s, received %s",
            target,
            type(data).__name__,
        )
    except Exception as exc:
        logger.warning("memory_store.load: failed to read %s (%s)", target, exc)
    return {}


def _ensure_sqlite_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memories (
            key TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            created_at REAL,
            updated_at REAL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memories_updated_at ON memories(updated_at)"
    )


def _load_sqlite(target: Path) -> Dict[str, Any]:
    if not target.exists():
        legacy = LEGACY_MEMORY_FILE
        if legacy.exists():
            legacy_data = _load_json(legacy)
            if legacy_data:
                _save_sqlite(legacy_data, target)
                return legacy_data
        legacy_sqlite = LEGACY_MEMORY_SQLITE
        if legacy_sqlite.exists():
            legacy_data = _load_sqlite(legacy_sqlite)
            if legacy_data:
                _save_sqlite(legacy_data, target)
                return legacy_data
        return {}
    data: Dict[str, Any] = {}
    try:
        with sqlite3.connect(str(target)) as conn:
            _ensure_sqlite_schema(conn)
            cur = conn.execute("SELECT key, payload FROM memories")
            for key, payload in cur.fetchall():
                try:
                    value = json.loads(payload)
                except Exception:
                    value = payload
                if isinstance(value, dict):
                    data[str(key)] = value
                else:
                    data[str(key)] = {"value": value}
    except Exception as exc:
        logger.warning("memory_store.load: failed to read %s (%s)", target, exc)
    return data


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except Exception:
        return None


def _save_json(store: Dict[str, Any], target: Path) -> None:
    tmp_path = target.with_suffix(target.suffix + ".tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(store, f, indent=2, default=_default_serializer)
        tmp_path.replace(target)
    except Exception as exc:
        logger.warning("memory_store.save: failed to persist %s (%s)", target, exc)
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass


def _save_sqlite(store: Dict[str, Any], target: Path) -> None:
    try:
        with sqlite3.connect(str(target)) as conn:
            _ensure_sqlite_schema(conn)
            cur = conn.execute("SELECT key FROM memories")
            existing = {row[0] for row in cur.fetchall()}
            incoming = {str(key) for key in store.keys()}
            stale = existing - incoming
            if stale:
                conn.executemany(
                    "DELETE FROM memories WHERE key = ?",
                    [(key,) for key in stale],
                )
            now = time.time()
            rows = []
            for key, item in store.items():
                key_str = str(key)
                payload = json.dumps(item, indent=2, default=_default_serializer)
                if isinstance(item, dict):
                    created_at = _safe_float(item.get("created_at")) or now
                    updated_at = _safe_float(item.get("updated_at")) or created_at
                else:
                    created_at = now
                    updated_at = now
                rows.append((key_str, payload, created_at, updated_at))
            conn.executemany(
                """
                INSERT INTO memories (key, payload, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    payload=excluded.payload,
                    created_at=excluded.created_at,
                    updated_at=excluded.updated_at
                """,
                rows,
            )
            conn.commit()
    except Exception as exc:
        logger.warning("memory_store.save: failed to persist %s (%s)", target, exc)


def load(path: Optional[PathLike] = None) -> Dict[str, Any]:
    """Load the persisted memory store from disk."""
    target = resolve_path(path)
    if _is_sqlite_path(target):
        return _load_sqlite(target)
    return _load_json(target)


def save(store: Dict[str, Any], path: Optional[PathLike] = None) -> None:
    """Persist the provided memory store snapshot to disk atomically."""
    target = resolve_path(path)
    if _is_sqlite_path(target):
        _save_sqlite(store, target)
    else:
        _save_json(store, target)


def _default_serializer(obj: Any) -> Any:
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return str(obj)


__all__ = ["resolve_path", "load", "save"]
