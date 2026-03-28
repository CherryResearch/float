"""Structured conversation timeline logging.

Writes JSONL records to logs/conversation_timeline.jsonl with a stable schema
for sequencing chat events (messages, thoughts, tools) in a readable way.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# Mirror conversation_store's stable project root resolution so logs live in
# the repository's top-level `logs` directory regardless of CWD.
REPO_ROOT = Path(__file__).resolve().parents[3]
LOG_DIR = REPO_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "conversation_timeline.jsonl"


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def log_event(event: str, data: Dict[str, Any] | None = None) -> None:
    """Append a structured timeline event to the JSONL log."""
    record = {"time": _now_iso(), "event": event, **(data or {})}
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        # Best-effort logging only; never raise.
        return


def log_message(
    *,
    session_id: str,
    message_id: str,
    role: str,
    text: str,
    source: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    payload: Dict[str, Any] = {
        "session_id": session_id,
        "message_id": message_id,
        "role": role,
        "text": text,
    }
    if source:
        payload["source"] = source
    if metadata:
        payload["metadata"] = metadata
    log_event("message", payload)


def log_thought(
    *,
    session_id: str,
    message_id: Optional[str],
    thought: str,
    offset: Optional[int],
    source: Optional[str] = None,
) -> None:
    payload: Dict[str, Any] = {
        "session_id": session_id,
        "thought": thought,
    }
    if message_id:
        payload["message_id"] = message_id
    if offset is not None:
        payload["offset"] = offset
    if source:
        payload["source"] = source
    log_event("thought", payload)


def log_tool(
    *,
    session_id: Optional[str],
    message_id: Optional[str],
    request_id: Optional[str],
    name: str,
    status: str,
    args: Dict[str, Any] | None = None,
    result: Any | None = None,
    source: Optional[str] = None,
) -> None:
    payload: Dict[str, Any] = {
        "session_id": session_id or "-",
        "name": name,
        "status": status,
    }
    if message_id:
        payload["message_id"] = message_id
    if request_id:
        payload["request_id"] = request_id
    if args is not None:
        payload["args"] = args
    if result is not None:
        payload["result"] = result if isinstance(result, (str, int, float, bool)) else str(result)
    if source:
        payload["source"] = source
    log_event("tool", payload)
