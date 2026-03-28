from __future__ import annotations

import time
from typing import Any, Dict

from fastapi import FastAPI, Request, Response

try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        Counter,
        Histogram,
        generate_latest,
        REGISTRY,
    )
except Exception:  # pragma: no cover - optional dependency
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"

    class _Noop:
        def labels(self, *_, **__):  # type: ignore[no-redef]
            return self

        def inc(self, *_args, **_kwargs):
            return None

        def observe(self, *_args, **_kwargs):
            return None

    def Counter(*_args, **_kwargs):  # type: ignore[no-redef]
        return _Noop()

    def Histogram(*_args, **_kwargs):  # type: ignore[no-redef]
        return _Noop()

    def generate_latest(*_args, **_kwargs):  # type: ignore[no-redef]
        return b""

    class _Reg:
        def collect(self):
            return []

    REGISTRY = _Reg()  # type: ignore


# Application-level metrics
http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "route", "status"],
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "route", "status"],
    buckets=(
        0.005,
        0.01,
        0.025,
        0.05,
        0.1,
        0.25,
        0.5,
        1.0,
        2.5,
        5.0,
        10.0,
    ),
)

sse_events_total = Counter(
    "sse_events_total", "Total Server-Sent Events emitted", ["event_type"]
)

tool_invocations_total = Counter(
    "tool_invocations_total",
    "Total tool invocations",
    ["tool", "status"],
)

rag_ingestion_total = Counter(
    "rag_ingestion_total",
    "Documents or notes ingested into the knowledge base",
    ["kind"],
)

retrieval_events_total = Counter(
    "retrieval_events_total",
    "Retrieval requests executed before LLM calls",
    ["channel"],
)

retrieval_matches_histogram = Histogram(
    "retrieval_matches_per_request",
    "Number of RAG matches returned for each retrieval call",
    buckets=(0, 1, 2, 3, 5, 8, 13, 21),
)

memory_writes_total = Counter(
    "memory_writes_total",
    "Memory write/update events observed via hooks",
    ["source"],
)

tool_events_total = Counter(
    "tool_events_total",
    "Tool proposals/decisions/invocations observed via hooks",
    ["tool", "status"],
)

error_events_total = Counter(
    "hook_error_events_total",
    "Error events captured via lifecycle hooks",
    ["location"],
)

llm_generate_requests_total = Counter(
    "llm_generate_requests_total",
    "Total LLM generate requests",
    ["mode", "response_format"],
)

llm_generate_duration_seconds = Histogram(
    "llm_generate_duration_seconds",
    "LLM generate duration in seconds",
    ["mode", "response_format"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
)

celery_task_executions_total = Counter(
    "celery_task_executions_total",
    "Celery task executions",
    ["task", "status"],
)

celery_task_duration_seconds = Histogram(
    "celery_task_duration_seconds",
    "Celery task duration in seconds",
    ["task", "status"],
    buckets=(0.01, 0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
)


def _get_route_label(request: Request) -> str:
    try:
        route = request.scope.get("route")
        # Prefer FastAPI path template if available
        return getattr(route, "path", str(request.url.path)) or str(request.url.path)
    except Exception:
        return str(request.url.path)


def init_metrics(app: FastAPI, config: Dict[str, Any]) -> None:
    if not bool(config.get("metrics_enabled", True)):
        return

    @app.middleware("http")
    async def metrics_middleware(request: Request, call_next):
        method = request.method
        route_label = _get_route_label(request)
        start = time.perf_counter()
        response: Response
        try:
            response = await call_next(request)
            status = str(getattr(response, "status_code", 0))
            return response
        finally:
            duration = time.perf_counter() - start
            # Re-read status from response if available
            status = locals().get("status", "500")
            http_requests_total.labels(method, route_label, status).inc()
            http_request_duration_seconds.labels(method, route_label, status).observe(
                duration
            )

    @app.get("/metrics", include_in_schema=False)
    async def metrics_endpoint() -> Response:  # type: ignore
        data = generate_latest(REGISTRY)
        return Response(content=data, media_type=CONTENT_TYPE_LATEST)


