import base64
import json
import os
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

# Determine stable project root (same logic as conversation_store).
REPO_ROOT = Path(__file__).resolve().parents[3]


def calendar_events_dir() -> Path:
    """Resolve Calendar storage under the same data root as other local stores."""

    explicit = os.getenv("FLOAT_CALENDAR_DIR")
    if explicit:
        path = Path(explicit).expanduser()
    else:
        raw_data_root = os.getenv("FLOAT_DATA_DIR")
        data_root = (
            Path(raw_data_root).expanduser() if raw_data_root else REPO_ROOT / "data"
        )
        if os.getenv("FLOAT_DEV_MODE", "false").lower() == "true":
            path = data_root / "test_calendar_events"
        else:
            path = data_root / "databases" / "calendar_events"
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


EVENTS_DIR = calendar_events_dir()
EVENTS_DIR.mkdir(parents=True, exist_ok=True)

_PORTABLE_EVENT_ID_RE = re.compile(r"^[A-Za-z0-9_.@-]+$")
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_ACTIVE_RUN_STATUSES = {
    "authorization_approved",
    "cancel_requested",
    "claimed",
    "followup_pending",
    "followup_running",
    "in_progress",
    "prompt_resume_pending",
    "queued",
    "retrying",
    "running",
}


class CalendarEventActiveRunError(RuntimeError):
    """Raised when deletion would detach a running Calendar action."""


def _active_run_present(event: Dict[str, Any]) -> bool:
    def normalize(value: Any) -> str:
        return str(value or "").strip().lower().replace("-", "_")

    if normalize(event.get("status")) in _ACTIVE_RUN_STATUSES:
        return True
    actions = event.get("actions")
    return any(
        normalize(action.get("status")) in _ACTIVE_RUN_STATUSES
        for action in (actions if isinstance(actions, list) else [])
        if isinstance(action, dict)
    )


def _safe_filename(name: str) -> str:
    raw = str(name or "").strip()
    if (
        not raw
        or raw in {".", ".."}
        or "/" in raw
        or "\\" in raw
        or any(ord(char) < 32 for char in raw)
    ):
        raise ValueError("calendar event id must be a safe filename component")
    base_name = raw.split(".", 1)[0].upper()
    portable = (
        bool(_PORTABLE_EVENT_ID_RE.fullmatch(raw))
        and not raw.startswith("~")
        and not raw.endswith(".json")
        and not raw.endswith((".", " "))
        and base_name not in _WINDOWS_RESERVED_NAMES
    )
    if portable:
        filename = f"{raw}.json"
    else:
        encoded = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")
        filename = f"~{encoded.rstrip('=')}.json"
    root = EVENTS_DIR.resolve()
    try:
        candidate = (root / filename).resolve()
    except (OSError, ValueError) as exc:
        raise ValueError("calendar event id is not a valid filename") from exc
    if candidate.parent != root:
        raise ValueError("calendar event id escapes the calendar data directory")
    return filename


def _path(name: str) -> Path:
    return EVENTS_DIR / _safe_filename(name)


def _lock_path(name: str) -> Path:
    return EVENTS_DIR / f".{_safe_filename(name)}.lock"


@contextmanager
def _event_lock(name: str) -> Iterator[None]:
    """Hold a small cross-process lock for one event read-modify-write cycle."""

    lock_path = _lock_path(name)
    with lock_path.open("a+b") as lock_file:
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:  # pragma: no cover - exercised by Linux CI
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _load_path(fp: Path) -> Dict[str, Any]:
    if not fp.exists():
        return {}
    with fp.open("r", encoding="utf-8") as stream:
        data = json.load(stream)
    return data if isinstance(data, dict) else {}


def _write_path(fp: Path, event: Dict[str, Any]) -> None:
    temp = fp.with_name(f".{fp.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with temp.open("w", encoding="utf-8") as stream:
            json.dump(event, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, fp)
    finally:
        if temp.exists():
            temp.unlink()


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
        stored_id = data.get("id") if isinstance(data, dict) else None
        names.append(str(stored_id) if stored_id else p.stem)
    return names


def load_event(name: str) -> Dict[str, Any]:
    return _load_path(_path(name))


def save_event(name: str, event: Dict[str, Any]) -> None:
    fp = _path(name)
    with _event_lock(name):
        _write_path(fp, event)


def update_event(
    name: str,
    updater: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]],
    *,
    create: bool = False,
) -> Dict[str, Any]:
    """Atomically update one event and return the stored result.

    The callback runs while the cross-process event lock is held. Returning
    ``None`` leaves the file unchanged. Missing events stay missing unless
    ``create`` is explicitly true.
    """

    fp = _path(name)
    with _event_lock(name):
        current = _load_path(fp)
        if not current and not create:
            return {}
        updated = updater(dict(current))
        if updated is None:
            return current
        if not isinstance(updated, dict):
            raise TypeError("calendar updater must return a dictionary or None")
        _write_path(fp, updated)
        return dict(updated)


def delete_event(
    name: str,
    *,
    guard: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> bool:
    """Delete one event after an optional check under the event lock."""

    fp = _path(name)
    with _event_lock(name):
        if not fp.exists():
            return False
        current = _load_path(fp)
        if guard is not None:
            guard(dict(current))
        if _active_run_present(current):
            raise CalendarEventActiveRunError(str(name))
        fp.unlink()
        return True


def check_event_deletable(
    name: str,
    *,
    guard: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> bool:
    """Run the same deletion guards without mutating the event file."""

    fp = _path(name)
    with _event_lock(name):
        if not fp.exists():
            return False
        current = _load_path(fp)
        if guard is not None:
            guard(dict(current))
        if _active_run_present(current):
            raise CalendarEventActiveRunError(str(name))
        return True
