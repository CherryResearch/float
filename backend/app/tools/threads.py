from __future__ import annotations

from typing import Any, Dict, Optional

from app.services import threads_service


def generate_threads_tool(
    infer_topics: bool = True,
    tags: Optional[list[str]] = None,
    openai_key: Optional[str] = None,
    embedding_model: Optional[str] = None,
    sensitive_mode: bool = True,
    topic_suggestion_provider: Optional[str] = None,
    topic_suggestion_model: Optional[str] = None,
    manual_threads: Optional[list[str]] = None,
    preferred_k: Optional[int] = 8,
    max_k: Optional[int] = 16,
    **_: Any,
) -> Dict[str, Any]:
    """Tool wrapper to generate semantic threads from conversations."""
    return threads_service.generate_threads(
        infer_topics=infer_topics,
        tags=tags,
        openai_key=openai_key,
        embedding_model=embedding_model,
        sensitive_mode=sensitive_mode,
        topic_suggestion_provider=topic_suggestion_provider,
        topic_suggestion_model=topic_suggestion_model,
        manual_threads=manual_threads,
        preferred_k=preferred_k,
        max_k=max_k,
    )


def read_threads_summary_tool(**_: Any) -> Dict[str, Any]:
    """Tool wrapper to read the last generated summary of threads."""
    return threads_service.read_summary()
