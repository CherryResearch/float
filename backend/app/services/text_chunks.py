from __future__ import annotations

import re
from typing import List

SENTENCE_RE = re.compile(r"(?<=[.!?]) +")


def split_into_nuggets(text: str, max_tokens: int = 128) -> List[str]:
    """Split raw text into semantically coherent nuggets."""
    cleaned = str(text or "").strip()
    if not cleaned:
        return []
    sentences = SENTENCE_RE.split(cleaned)
    nuggets: List[str] = []
    current: List[str] = []
    tokens = 0
    for sent in sentences:
        sent_clean = str(sent or "").strip()
        if not sent_clean:
            continue
        sent_tokens = len(sent_clean.split())
        if tokens + sent_tokens > max_tokens and current:
            nuggets.append(" ".join(current))
            current = []
            tokens = 0
        current.append(sent_clean)
        tokens += sent_tokens
    if current:
        nuggets.append(" ".join(current))
    return nuggets


def chunk_text(text: str, max_tokens: int = 128) -> List[str]:
    """Public wrapper used by both RAG ingestion and thread generation."""
    return split_into_nuggets(text, max_tokens=max_tokens)
