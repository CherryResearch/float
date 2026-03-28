"""Default observers that consume lifecycle hooks for logging/metrics."""

from __future__ import annotations

import logging
from typing import Any

from app import hooks
from app.utils.chat_log import log_event
from app.utils.metrics import (
    error_events_total,
    memory_writes_total,
    rag_ingestion_total,
    retrieval_events_total,
    retrieval_matches_histogram,
    tool_events_total,
)

logger = logging.getLogger(__name__)


def _clean_label(value: Any, default: str = "unknown") -> str:
    try:
        text = str(value or "").strip()
    except Exception:
        return default
    return text or default


@hooks.register_hook(hooks.INGESTION_EVENT)
def _observe_ingestion(event: hooks.IngestionEvent) -> None:
    kind = _clean_label(event.kind, "document")
    rag_ingestion_total.labels(kind).inc()
    log_event(
        "ingestion",
        {
            "kind": kind,
            "source": event.source,
            "size": event.size,
            "metadata": event.metadata,
        },
    )


@hooks.register_hook(hooks.AFTER_RETRIEVAL_EVENT)
def _observe_retrieval(event: hooks.RetrievalResult) -> None:
    channel = _clean_label(event.metadata.get("channel") if isinstance(event.metadata, dict) else None, "chat")
    match_count = len(event.matches or [])
    retrieval_events_total.labels(channel).inc()
    retrieval_matches_histogram.observe(match_count)
    log_event(
        "retrieval",
        {
            "channel": channel,
            "session_id": event.session_id,
            "matches": match_count,
        },
    )


@hooks.register_hook(hooks.MEMORY_WRITE_EVENT)
def _observe_memory(event: hooks.MemoryWriteEvent) -> None:
    source = _clean_label(event.source, "api")
    memory_writes_total.labels(source).inc()
    payload = event.payload or {}
    log_event(
        "memory_write",
        {
            "key": event.key,
            "source": source,
            "importance": payload.get("importance"),
            "sensitivity": payload.get("sensitivity"),
        },
    )


@hooks.register_hook(hooks.TOOL_EVENT)
def _observe_tool(event: hooks.ToolInvocationEvent) -> None:
    name = _clean_label(event.name, "unknown")
    status = _clean_label(event.status, "event")
    tool_events_total.labels(name, status).inc()


@hooks.register_hook(hooks.ERROR_EVENT)
def _observe_error(event: hooks.ErrorEvent) -> None:
    location = _clean_label(event.location, "unknown")
    error_events_total.labels(location).inc()
    log_event(
        "hook_error",
        {
            "location": location,
            "exception": event.exception_type,
            "detail": event.detail,
        },
    )
