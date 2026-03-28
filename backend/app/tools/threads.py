from __future__ import annotations

from typing import Any, Dict, Optional

from app.services import threads_service


def generate_threads_tool(
    infer_topics: bool = True,
    tags: Optional[list[str]] = None,
    openai_key: Optional[str] = None,
    **_: Any,
) -> Dict[str, Any]:
    """Tool wrapper to generate semantic threads from conversations."""
    return threads_service.generate_threads(
        infer_topics=infer_topics, tags=tags, openai_key=openai_key
    )


def read_threads_summary_tool(**_: Any) -> Dict[str, Any]:
    """Tool wrapper to read the last generated summary of threads."""
    return threads_service.read_summary()


