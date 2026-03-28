"""Lightweight server-mode LLM logging utilities.

Writes JSONL records to a repository-local logs/llm_server.log file to aid
debugging OpenAI-compatible servers (e.g. LM Studio) without mixing into chat.log.

Notes/limitations:
- Best-effort only; never raises.
- Minimal redaction; avoid logging full prompts or full model outputs.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

# Mirror conversation_store's stable project root resolution so logs live in
# the repository's top-level `logs` directory regardless of CWD.
REPO_ROOT = Path(__file__).resolve().parents[3]
LOG_DIR = REPO_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "llm_server.log"

LOGGER = logging.getLogger("float.llm_server")


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def log_event(event: str, data: Dict[str, Any] | None = None) -> None:
    """Append a structured event to llm_server.log."""
    record = {"time": _now_iso(), "event": event, **(data or {})}
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass
    try:
        # Keep console noise low: only emit warnings/errors.
        if event.endswith("error") or event.endswith("failed"):
            LOGGER.warning("%s %s", event, record)
        else:
            LOGGER.debug("%s %s", event, record)
    except Exception:
        pass

