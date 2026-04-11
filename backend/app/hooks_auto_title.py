"""Auto-title conversations based on early chat context."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

from app import hooks
from app.utils import conversation_store

logger = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'./_-]*")
_SESSION_RE = re.compile(r"^sess-\d+$")
_PENDING_TITLES: Dict[str, Dict[str, str]] = {}
_MAX_WORDS = 8
_MIN_WORDS = 2
_MAX_MESSAGES = 4
_LEADING_FILLER_PATTERNS = (
    re.compile(r"^(?:please\s+)*(?:can|could|would|will)\s+you\s+", re.IGNORECASE),
    re.compile(r"^(?:please\s+)*(?:help|assist)\s+me\s+", re.IGNORECASE),
    re.compile(
        r"^(?:please\s+)*i\s+(?:need|want)\s+(?:help\s+)?(?:to\s+|with\s+)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:please\s+)*(?:show|tell|give|find|list)\s+me\s+",
        re.IGNORECASE,
    ),
)
_LEADING_INTERROGATIVE_RE = re.compile(
    r"^(?:why|how|what|when|where)\s+", re.IGNORECASE
)
_TITLE_SKIP_WORDS = {
    "a",
    "an",
    "the",
    "in",
    "me",
    "my",
    "your",
    "our",
    "you",
    "i",
    "we",
    "it",
    "is",
    "are",
    "be",
    "am",
    "do",
    "does",
    "did",
    "please",
    "why",
    "how",
    "what",
    "when",
    "where",
}
_TECHNICAL_CASING = {
    "ai": "AI",
    "api": "API",
    "cli": "CLI",
    "cpu": "CPU",
    "csv": "CSV",
    "gpu": "GPU",
    "http": "HTTP",
    "https": "HTTPS",
    "json": "JSON",
    "llm": "LLM",
    "lm": "LM",
    "mcp": "MCP",
    "pdf": "PDF",
    "rag": "RAG",
    "sse": "SSE",
    "stt": "STT",
    "tts": "TTS",
    "ui": "UI",
    "utf": "UTF",
    "wsl": "WSL",
    "xml": "XML",
}


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


def _strip_request_filler(text: str) -> str:
    cleaned = _normalize_text(text)
    previous = None
    while cleaned and cleaned != previous:
        previous = cleaned
        for pattern in _LEADING_FILLER_PATTERNS:
            cleaned = pattern.sub("", cleaned).strip()
        cleaned = _LEADING_INTERROGATIVE_RE.sub("", cleaned).strip()
    return cleaned


def _candidate_texts(messages: list[dict]) -> list[str]:
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
    candidates: list[str] = []
    user_source = " ".join(user_chunks[-2:] or user_chunks[-1:] or [])
    if user_source:
        candidates.append(user_source)
    if assistant_chunks:
        candidates.append(assistant_chunks[0])
    if messages:
        first_text = messages[0].get("text") or messages[0].get("content") or ""
        if first_text:
            candidates.append(first_text)
    deduped: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        normalized = _normalize_text(item)[:400]
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _format_title_token(token: str) -> str:
    cleaned = token.strip("-'")
    if not cleaned:
        return ""

    def _format_simple(part: str) -> str:
        lowered = part.lower()
        if lowered in _TECHNICAL_CASING:
            return _TECHNICAL_CASING[lowered]
        if part.isupper() and len(part) <= 6:
            return part
        if part.isdigit():
            return part
        return part[:1].upper() + part[1:]

    if any(sep in cleaned for sep in "/._-"):
        parts = re.split(r"([/._-])", cleaned)
        return "".join(
            _format_simple(part) if part not in {"/", ".", "_", "-"} else part
            for part in parts
        )
    return _format_simple(cleaned)


def _title_tokens(text: str) -> list[str]:
    cleaned = _strip_request_filler(text)
    raw_tokens = [_token.strip("-'") for _token in _WORD_RE.findall(cleaned)]
    raw_tokens = [token for token in raw_tokens if token]
    if not raw_tokens:
        return []
    filtered = [token for token in raw_tokens if token.lower() not in _TITLE_SKIP_WORDS]
    if len(filtered) >= _MIN_WORDS:
        return filtered[:_MAX_WORDS]
    return raw_tokens[:_MAX_WORDS]


def _generate_title(messages: list[dict]) -> Optional[str]:
    for candidate in _candidate_texts(messages):
        tokens = _title_tokens(candidate)
        if len(tokens) < _MIN_WORDS:
            continue
        title = " ".join(
            formatted
            for formatted in (_format_title_token(token) for token in tokens)
            if formatted
        )
        if title:
            return title
    return None


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
