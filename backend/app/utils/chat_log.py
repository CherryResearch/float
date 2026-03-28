"""Lightweight chat logging utilities.

Writes JSONL records to a repository-local logs/chat.log file to aid
development and debugging. This is intentionally simple and avoids
interfering with existing telemetry or structured logging.

Notes/limitations:
- No log rotation yet. TODO: integrate with `logging.handlers.RotatingFileHandler`.
- No multi-process file locking. TODO: use a lock file or portalocker when
  running with multiple Uvicorn workers.
- Minimal redaction. TODO: redact PII or opt-in message content logging for production.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable

# Mirror conversation_store's stable project root resolution so logs live in
# the repository's top-level `logs` directory regardless of CWD.
REPO_ROOT = Path(__file__).resolve().parents[3]
LOG_DIR = REPO_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "chat.log"


CHAT_LOGGER = logging.getLogger("float.chat")


def _truncate(value: object, limit: int = 160) -> str:
    """Return a shortened, single-line representation for console output."""

    try:
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    except Exception:
        text = str(value)
    text = (text or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    truncated = text[: limit - 1].rstrip()
    return f"{truncated}…"


def _format_args(args: Dict[str, Any] | Iterable[tuple[str, Any]] | None) -> str:
    if not args:
        return "{}"
    try:
        return json.dumps(args, ensure_ascii=False)
    except Exception:
        try:
            return str(dict(args))  # type: ignore[arg-type]
        except Exception:
            return str(args)


def _emit_console(event: str, data: Dict[str, Any]) -> None:
    if not CHAT_LOGGER.handlers and not logging.getLogger().handlers:
        # No handlers configured yet; avoid implicit stream creation.
        return

    session = data.get("session_id") or "-"
    if event == "chat_request":
        model = data.get("model") or "unknown"
        snippet = _truncate(data.get("prompt", ""))
        CHAT_LOGGER.info("[session=%s] prompt (%s): %s", session, model, snippet)
    elif event == "chat_response":
        meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        status = meta.get("status") if isinstance(meta, dict) else None
        status_part = f" status={status}" if status else ""
        snippet = _truncate(data.get("text", ""))
        CHAT_LOGGER.info("[session=%s] response%s: %s", session, status_part, snippet)
    elif event == "thought_delta":
        offset = data.get("offset")
        offset_part = f"#{offset}" if isinstance(offset, int) else ""
        snippet = _truncate(data.get("thought", ""))
        CHAT_LOGGER.info("[session=%s] thought%s: %s", session, offset_part, snippet)
    elif event == "tool_event":
        name = data.get("name") or data.get("tool") or "unknown"
        status = data.get("status") or data.get("state") or "event"
        message_id = data.get("message_id") or data.get("chain_id") or "-"
        request_id = data.get("request_id") or "-"
        args = _format_args(data.get("args"))
        result = data.get("result")
        result_snippet = _truncate(result) if result is not None else ""
        CHAT_LOGGER.info(
            "[session=%s] tool %s (%s) mid=%s rid=%s args=%s result=%s",
            session,
            name,
            status,
            message_id,
            request_id,
            _truncate(args, 120),
            result_snippet,
        )
    elif event == "history_save":
        count = data.get("messages")
        CHAT_LOGGER.debug("[session=%s] history saved (%s messages)", session, count)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def log_event(event: str, data: Dict[str, Any] | None = None) -> None:
    """Append a structured event to chat.log.

    Parameters
    - event: short event type (e.g., "chat_request", "chat_response", "chat_error").
    - data: optional dictionary with additional context (session_id, snippets, errors).
    """
    record = {
        "time": _now_iso(),
        "event": event,
        **(data or {}),
    }
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        # Best-effort logging only; never raise.
        pass
    try:
        _emit_console(event, record)
    except Exception:
        # Console logging should never interfere with main flow.
        pass


def log_chat_request(session_id: str, prompt_snippet: str, model: str | None) -> None:
    log_event(
        "chat_request",
        {"session_id": session_id, "prompt": prompt_snippet, "model": model},
    )


def log_chat_response(session_id: str, text_snippet: str, meta: Dict[str, Any] | None) -> None:
    # Trim overly large metadata; keep a shallow copy
    safe_meta: Dict[str, Any] | None = None
    if isinstance(meta, dict):
        try:
            safe_meta = {k: (v if isinstance(v, (str, int, float, bool)) else str(v)) for k, v in meta.items()}
        except Exception:
            safe_meta = None
    log_event(
        "chat_response",
        {"session_id": session_id, "text": text_snippet, "metadata": safe_meta},
    )


def log_thought_delta(
    session_id: str,
    thought: str,
    message_id: str | None = None,
    offset: int | None = None,
) -> None:
    payload: Dict[str, Any] = {"session_id": session_id, "thought": thought}
    if message_id:
        payload["message_id"] = message_id
    if offset is not None:
        payload["offset"] = offset
    log_event("thought_delta", payload)
    try:
        from app.utils import conversation_timeline

        conversation_timeline.log_thought(
            session_id=session_id,
            message_id=message_id,
            thought=thought,
            offset=offset,
            source="chat",
        )
    except Exception:
        pass


def log_history_save(session_id: str, count: int) -> None:
    log_event("history_save", {"session_id": session_id, "messages": count})


def log_tool_event(
    session_id: str | None,
    name: str,
    status: str,
    *,
    args: Dict[str, Any] | None = None,
    result: Any | None = None,
    message_id: str | None = None,
    request_id: str | None = None,
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
    log_event("tool_event", payload)
    try:
        from app.utils import conversation_timeline

        conversation_timeline.log_tool(
            session_id=session_id,
            message_id=message_id,
            request_id=request_id,
            name=name,
            status=status,
            args=args,
            result=result,
            source="chat",
        )
    except Exception:
        pass

