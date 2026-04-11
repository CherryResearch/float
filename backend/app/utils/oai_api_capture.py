"""Best-effort raw OpenAI API capture logging for local debugging."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
LOG_DIR = REPO_ROOT / "logs" / "oai_api"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _response_id(payload: Dict[str, Any]) -> str:
    direct = payload.get("id")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    nested = payload.get("response")
    if isinstance(nested, dict):
        nested_id = nested.get("id")
        if isinstance(nested_id, str) and nested_id.strip():
            return nested_id.strip()
    return ""


def write_capture(
    *,
    endpoint: str,
    request_payload: Dict[str, Any],
    response_payload: Dict[str, Any],
    session_id: Optional[str] = None,
    message_id: Optional[str] = None,
) -> Optional[str]:
    response_id = _response_id(response_payload)
    stem = (
        response_id
        or f"capture-{int(datetime.now(tz=timezone.utc).timestamp() * 1000)}"
    )
    target = LOG_DIR / f"{stem}.json"
    record = {
        "captured_at": _now_iso(),
        "endpoint": endpoint,
        "session_id": str(session_id or "").strip() or None,
        "message_id": str(message_id or "").strip() or None,
        "request_payload": request_payload,
        "response_payload": response_payload,
    }
    try:
        target.write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return str(target)
    except Exception:
        return None
