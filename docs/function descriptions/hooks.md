# Hook System Overview

Float now exposes a lightweight hook registry so ingestion, retrieval, memory, tool, and ops events can be observed or augmented without rewriting core services. Hooks live in `backend/app/hooks.py` and are intentionally simple: they are synchronous callables that receive typed payloads, with optional support for returning coroutines (which are scheduled automatically when a loop is running).

## Event Catalog

| Event | Payload | When it fires |
| --- | --- | --- |
| `hooks.INGESTION_EVENT` | `hooks.IngestionEvent` | Every time the RAG service ingests new text/file/calendar/doc content (all `RAGService.ingest_*` helpers) |
| `hooks.BEFORE_RETRIEVAL_EVENT` | `hooks.RetrievalRequest` | Right before `/chat` issues a vector search (one per user turn when `use_rag` is enabled) |
| `hooks.AFTER_RETRIEVAL_EVENT` | `hooks.RetrievalResult` | Immediately after retrieval completes, providing the normalized match list |
| `hooks.BEFORE_LLM_CALL_EVENT` | `hooks.LLMCallEvent` | After the chat context is assembled and just before `LLMService.generate` executes |
| `hooks.AFTER_LLM_RESPONSE_EVENT` | `hooks.LLMResponseEvent` | Once the assistant reply (text/thought/tools) is finalized and persisted |
| `hooks.MEMORY_WRITE_EVENT` | `hooks.MemoryWriteEvent` | Whenever the `MemoryManager` updates or upserts a record (direct API calls or tools) |
| `hooks.TOOL_EVENT` | `hooks.ToolInvocationEvent` | Tool proposal/decision/invocation flows, including REST calls and pending approvals |
| `hooks.ERROR_EVENT` | `hooks.ErrorEvent` | FastAPI exception handlers emit this for unhandled errors or validation issues |

Hooks are expected to be fast, idempotent, and side-effect safe. Long-running work should enqueue a Celery job or schedule async work instead of blocking the main thread.

## Payload shapes

All payloads are dataclasses with friendly attributes:

- `IngestionEvent(kind, source, metadata, preview, size)` – `kind` follows the knowledge schema (`document`, `memory`, `calendar_event`, etc.).
- `RetrievalRequest(session_id, query, top_k, metadata)`; `metadata` marks the channel (`chat`) plus future knobs.
- `RetrievalResult(session_id, query, matches, metadata)` – `matches` is the list already prepared for the prompt.
- `LLMCallEvent(session_id, prompt, model, context, metadata)` – `context` is a snapshot of `ModelContext.to_dict()`.
- `LLMResponseEvent(session_id, response_text, metadata, raw_response)` – `raw_response` is the dict returned by `LLMService`.
- `MemoryWriteEvent(key, source, payload)` – `payload` contains importance/sensitivity/preview so hooks can branch on policy without exposing full secrets.
- `ToolInvocationEvent(name, status, args, result, session_id, message_id, request_id)` – `status` includes values such as `proposed`, `invoked`, `denied`, `error`.
- `ErrorEvent(location, exception_type, detail, context)` – `context` includes HTTP method/path for API handlers.

## Registering hooks

Use the decorator or direct registration helpers:

```python
# backend/my_plugin.py
from app import hooks

@hooks.register_hook(hooks.MEMORY_WRITE_EVENT)
def mirror_memory(event: hooks.MemoryWriteEvent) -> None:
    if event.payload.get("sensitivity") == "secret":
        return  # respect access policies
    audit_log.write(
        f"{event.key} updated via {event.source}: {event.payload['preview']}"
    )

def setup():
    hooks.registry.register(
        hooks.ERROR_EVENT,
        lambda e: metrics.counter("errors", {"location": e.location}).inc(),
    )
```

Handlers should never raise; exceptions are caught and logged so they cannot break the primary flow. If an async hook returns a coroutine while running inside FastAPI, it is scheduled via `loop.create_task`; outside an event loop the registry falls back to `asyncio.run`.

## Current emitters

- Chat lifecycle (`backend/app/routes.py::chat`) emits retrieval and LLM events.
- `RAGService.ingest_*` (docs, files, calendar text, URL crawls) emits ingestion events.
- `MemoryManager.update_memory` and `MemoryManager.upsert_item` emit memory writes for API calls and tools alike.
- `/tools/invoke`, `/tools/propose`, and `/tools/decision` emit tool events for proposals, approvals, denials, successes, and failures.
- Top-level FastAPI exception handlers in `backend/app/main.py` emit error events, providing a single tap for alerting.

Extend this list by calling `hooks.emit(EVENT_NAME, Payload(...))` from any new lifecycle stage. Keep payloads JSON-serializable and include contextual metadata so downstream hooks can filter efficiently.

## Built-in observers

`backend/app/hooks_observers.py` registers default observers at import time (see `backend/app/main.py`). They currently:

- increment Prometheus counters/histograms for ingestion, retrieval, memory, tool, and error events (`app/utils/metrics.py` exposes the metrics), and
- emit lightweight `chat_log.log_event` entries (`ingestion`, `retrieval`, `memory_write`, `hook_error`) for auditing.

Importing `app.hooks_observers` is sufficient to enable them; additional observers can be added in that module or elsewhere using the decorator pattern.
