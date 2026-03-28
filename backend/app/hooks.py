"""Central hook registry and typed payloads for lifecycle events."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

# Event channel names
INGESTION_EVENT = "ingestion"
BEFORE_RETRIEVAL_EVENT = "before_retrieval"
AFTER_RETRIEVAL_EVENT = "after_retrieval"
BEFORE_LLM_CALL_EVENT = "before_llm_call"
AFTER_LLM_RESPONSE_EVENT = "after_llm_response"
MEMORY_WRITE_EVENT = "memory_write"
TOOL_EVENT = "tool_event"
ERROR_EVENT = "error_event"


@dataclass(slots=True)
class IngestionEvent:
    """Triggered whenever new knowledge is added to the vector store."""

    kind: str
    source: Optional[str]
    metadata: Dict[str, Any] = field(default_factory=dict)
    preview: Optional[str] = None
    size: Optional[int] = None


@dataclass(slots=True)
class RetrievalRequest:
    """Describes an upcoming retrieval query."""

    session_id: str
    query: str
    top_k: int
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RetrievalResult:
    """Carries retrieved matches that will feed an LLM request."""

    session_id: str
    query: str
    matches: List[Dict[str, Any]]
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LLMCallEvent:
    """Snapshot emitted right before the LLM receives a request."""

    session_id: str
    prompt: str
    model: Optional[str]
    context: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LLMResponseEvent:
    """Payload emitted after the LLM produces a response."""

    session_id: str
    response_text: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    raw_response: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MemoryWriteEvent:
    """Captures writes to the memory store."""

    key: str
    source: str
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolInvocationEvent:
    """Emitted whenever a tool is proposed, invoked, or fails."""

    name: str
    status: str
    args: Dict[str, Any] = field(default_factory=dict)
    result: Any = None
    session_id: Optional[str] = None
    message_id: Optional[str] = None
    request_id: Optional[str] = None


@dataclass(slots=True)
class ErrorEvent:
    """High-level error for observability hooks."""

    location: str
    exception_type: str
    detail: str
    context: Dict[str, Any] = field(default_factory=dict)


HookFn = Callable[[Any], Any]


class HookRegistry:
    """Keeps hook handlers and executes them in registration order."""

    def __init__(self) -> None:
        self._handlers: Dict[str, List[HookFn]] = {}

    def register(self, event: str, handler: HookFn) -> None:
        handlers = self._handlers.setdefault(event, [])
        handlers.append(handler)

    def unregister(self, event: str, handler: HookFn) -> None:
        handlers = self._handlers.get(event)
        if not handlers:
            return
        try:
            handlers.remove(handler)
        except ValueError:
            return

    def iter_handlers(self, event: str) -> Iterable[HookFn]:
        return tuple(self._handlers.get(event, ()))

    def emit(self, event: str, payload: Any) -> None:
        """Synchronously execute all handlers for *event* with *payload*."""
        for handler in self.iter_handlers(event):
            try:
                result = handler(payload)
                if asyncio.iscoroutine(result):
                    try:
                        loop = asyncio.get_running_loop()
                    except RuntimeError:
                        asyncio.run(result)
                    else:
                        loop.create_task(result)
            except Exception:
                logger.exception("hook %s failed for event %s", handler, event)


registry = HookRegistry()


def register_hook(event: str) -> Callable[[HookFn], HookFn]:
    """Decorator for registering a hook handler."""

    def decorator(func: HookFn) -> HookFn:
        registry.register(event, func)
        return func

    return decorator


def emit(event: str, payload: Any) -> None:
    registry.emit(event, payload)


__all__ = [
    "AFTER_LLM_RESPONSE_EVENT",
    "AFTER_RETRIEVAL_EVENT",
    "BEFORE_LLM_CALL_EVENT",
    "BEFORE_RETRIEVAL_EVENT",
    "ERROR_EVENT",
    "INGESTION_EVENT",
    "LLMCallEvent",
    "LLMResponseEvent",
    "MemoryWriteEvent",
    "RetrievalRequest",
    "RetrievalResult",
    "ToolInvocationEvent",
    "ErrorEvent",
    "HookRegistry",
    "IngestionEvent",
    "register_hook",
    "emit",
    "registry",
    "MEMORY_WRITE_EVENT",
    "TOOL_EVENT",
]
