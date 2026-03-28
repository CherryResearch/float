"""Auto-title conversations based on early chat context."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

from app import hooks
from app.utils import conversation_store

logger = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'/-]*")
_SESSION_RE = re.compile(r"^sess-\d+$")
_PENDING_TITLES: Dict[str, Dict[str, str]] = {}
_MAX_WORDS = 8
_MIN_WORDS = 2
_MAX_MESSAGES = 4


def _coerce_message_count(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _generate_candidate_text(messages: list[dict]) -> str:
    user_chunks: list[str] = []
    assistant_chunks: list[str] = []
    for entry in messages:
        role = (entry.get("role") or entry.get("speaker") or "").lower()
        text = entry.get("text") or entry.get("content") or ""
        if not text:
            continue
        if role == "user":
            user_chunks.append(text)
        elif role in {"assistant", "ai"}:
            assistant_chunks.append(text)
    source = " ".join(user_chunks[-2:] or user_chunks[-1:] or [])
    if not source and assistant_chunks:
        source = assistant_chunks[0]
    if not source and messages:
        source = messages[0].get("text") or messages[0].get("content") or ""
    return _normalize_text(source)[:400]


def _generate_title(messages: list[dict]) -> Optional[str]:
    candidate = _generate_candidate_text(messages)
    tokens = [_token.strip("-'") for _token in _WORD_RE.findall(candidate)]
    if len(tokens) < _MIN_WORDS:
        for entry in messages:
            text = entry.get("text") or entry.get("content") or ""
            if text:
                tokens.extend(_WORD_RE.findall(text))
            if len(tokens) >= _MIN_WORDS:
                break
    tokens = [tok for tok in tokens if tok]
    if len(tokens) < _MIN_WORDS:
        return None
    trimmed = tokens[:_MAX_WORDS]
    title = " ".join(trimmed)
    return title.title()


def _should_attempt(meta: Dict[str, Any], session_name: str) -> bool:
    if not _SESSION_RE.match(session_name or ""):
        return False
    if meta.get("manual_title"):
        return False
    if meta.get("auto_title_applied"):
        return False
    count = _coerce_message_count(meta.get("message_count"))
    if isinstance(count, int) and count < 2:
        return False
    if isinstance(count, int) and count > _MAX_MESSAGES:
        return False
    return True


@hooks.register_hook(hooks.AFTER_LLM_RESPONSE_EVENT)
def _auto_title_after_response(event: hooks.LLMResponseEvent) -> None:
    metadata = event.metadata or {}
    session_name = metadata.get("session_name")
    if not session_name:
        return
    try:
        meta = conversation_store.get_metadata(session_name)
    except Exception:
        logger.debug("Auto-title metadata fetch failed", exc_info=True)
        return
    if not _should_attempt(meta, session_name):
        return
    try:
        messages = conversation_store.load_conversation(session_name)
    except Exception:
        logger.debug("Auto-title conversation load failed", exc_info=True)
        return
    if not isinstance(messages, list) or len(messages) < 2:
        return
    if len(messages) > _MAX_MESSAGES:
        return
    title = _generate_title(messages)
    if not title:
        return
    try:
        conversation_store.set_display_name(
            session_name,
            title,
            auto_generated=True,
            manual=False,
        )
    except Exception:
        logger.debug("Failed to persist auto-title for %s", session_name, exc_info=True)
        return
    _PENDING_TITLES[session_name] = {"display_name": title}


def consume_pending_title(session_name: str) -> Optional[Dict[str, str]]:
    """Return the pending auto-title (if any) for ``session_name``."""
    return _PENDING_TITLES.pop(session_name, None)
