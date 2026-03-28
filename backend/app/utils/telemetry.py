from __future__ import annotations

# isort: skip_file

import logging
import os
import sys
import time
import uuid
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, Request

# Context to hold per-request ID for log enrichment
request_id_context: ContextVar[str | None] = ContextVar(
    "request_id",
    default=None,
)


def get_request_id() -> str | None:
    return request_id_context.get()


def set_request_id(request_id: str | None) -> None:
    request_id_context.set(request_id)


class RequestIdFilter(logging.Filter):
    """Injects request_id, trace_id and span_id into log records."""

    def filter(
        self,
        record: logging.LogRecord,
    ) -> bool:  # type: ignore[override]
        # Request ID
        setattr(record, "request_id", get_request_id() or "-")

        # OpenTelemetry trace context (optional)
        try:
            from opentelemetry.trace import get_current_span

            span = get_current_span()
            span_context = getattr(span, "get_span_context", lambda: None)()
            if (
                span_context
                and getattr(
                    span_context,
                    "is_valid",
                    lambda: False,
                )()
            ):
                trace_id = format(span_context.trace_id, "032x")
                span_id = format(span_context.span_id, "016x")
            else:
                trace_id = "-"
                span_id = "-"
        except Exception:
            trace_id = "-"
            span_id = "-"

        setattr(record, "trace_id", trace_id)
        setattr(record, "span_id", span_id)
        return True


def _build_json_formatter() -> logging.Formatter:
    """Return a JSON formatter if available; otherwise a key-value one."""
    try:
        from pythonjsonlogger import jsonlogger  # type: ignore

        # Use valid LogRecord attribute names in the format string.
        # They will be renamed in the output via rename_fields.
        formatter = jsonlogger.JsonFormatter(
            "asctime levelname name message request_id trace_id span_id",
            rename_fields={
                "asctime": "timestamp",
                "levelname": "level",
                "name": "logger",
            },
        )
        return formatter
    except Exception:
        # Fallback: structured-ish line format
        return logging.Formatter(
            fmt=(
                "%(asctime)s %(levelname)s %(name)s "
                "request_id=%(request_id)s trace_id=%(trace_id)s "
                "span_id=%(span_id)s - %(message)s"
            )
        )


def _build_console_formatter() -> logging.Formatter:
    """Concise, human-readable console formatter for general logs."""
    return logging.Formatter(
        fmt="%(asctime)s | %(levelname).1s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def _build_console_access_formatter() -> logging.Formatter:
    """Concise, human-readable formatter for HTTP access logs.

    Uses fields added via ``extra`` from the request middleware.
    """
    # Note: format spec for floats works with logging placeholders
    return logging.Formatter(
        fmt=(
            "%(asctime)s | %(levelname).1s | %(method)s %(path)s -> %(status_code)s "
            "in %(duration_ms).2f ms | %(client_ip)s"
        ),
        datefmt="%H:%M:%S",
    )


def _resolve_log_dir() -> Path:
    repo_root = Path(__file__).resolve().parents[3]
    log_dir = repo_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def configure_logging(config: Dict[str, Any] | None = None) -> None:
    """Configure root logger with JSON output and request/trace context.

    Safe to call multiple times.
    """
    level_name = (config or {}).get("log_level") or os.getenv(
        "FLOAT_LOG_LEVEL",
        "INFO",
    )
    level = getattr(logging, str(level_name).upper(), logging.INFO)

    root = logging.getLogger()
    # Clear existing handlers to avoid duplicate logs in reloads/tests
    for h in list(root.handlers):
        root.removeHandler(h)

    log_format = str((config or {}).get("log_format", os.getenv("FLOAT_LOG_FORMAT", "console"))).lower()

    handler = logging.StreamHandler(sys.stdout)
    if log_format == "json":
        handler.setFormatter(_build_json_formatter())
    else:
        handler.setFormatter(_build_console_formatter())
    handler.addFilter(RequestIdFilter())

    root.addHandler(handler)
    root.setLevel(level)

    file_level_name = (config or {}).get("file_log_level") or os.getenv(
        "FLOAT_FILE_LOG_LEVEL",
        "WARNING",
    )
    if str(file_level_name).upper() not in {"OFF", "NONE", "DISABLED"}:
        file_level = getattr(
            logging, str(file_level_name).upper(), logging.WARNING
        )
        log_dir = _resolve_log_dir()
        file_handler = RotatingFileHandler(
            log_dir / "server.log",
            maxBytes=5_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        if log_format == "json":
            file_handler.setFormatter(_build_json_formatter())
        else:
            file_handler.setFormatter(_build_console_formatter())
        file_handler.addFilter(RequestIdFilter())
        file_handler.setLevel(file_level)
        root.addHandler(file_handler)

    # Configure a dedicated access logger to avoid uvicorn's AccessFormatter
    # expecting positional arguments. By attaching our own handler and
    # disabling propagation, we ensure structured request logs without runtime
    # format errors.
    access_logger = logging.getLogger("float.access")
    access_logger.handlers.clear()
    access_logger.propagate = False
    access_handler = logging.StreamHandler(sys.stdout)
    if log_format == "json":
        access_handler.setFormatter(_build_json_formatter())
    else:
        access_handler.setFormatter(_build_console_access_formatter())
    access_handler.addFilter(RequestIdFilter())
    access_logger.addHandler(access_handler)
    access_logger.setLevel(level)


def configure_tracing(config: Dict[str, Any] | None = None) -> None:
    """Configure OpenTelemetry tracing/metrics if available.

    Does not raise if optional dependencies are missing.
    """
    cfg = config or {}
    telemetry_enabled = bool(cfg.get("telemetry_enabled", True))
    if not telemetry_enabled:
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.logging import LoggingInstrumentor
        from opentelemetry.instrumentation.requests import RequestsInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        service_name = str(cfg.get("service_name", "float-backend"))
        environment = str(
            cfg.get(
                "environment",
                os.getenv("FLOAT_ENV", "development"),
            )
        )

        resource = Resource.create(
            {
                "service.name": service_name,
                "service.namespace": "float",
                "service.version": cfg.get("service_version", "0.0.0"),
                "deployment.environment": environment,
            }
        )

        provider = TracerProvider(resource=resource)

        otlp_endpoint = cfg.get("otlp_endpoint") or os.getenv(
            "OTEL_EXPORTER_OTLP_ENDPOINT"
        )
        otlp_headers = cfg.get("otlp_headers") or os.getenv(
            "OTEL_EXPORTER_OTLP_HEADERS"
        )
        if otlp_endpoint:
            exporter = OTLPSpanExporter(
                endpoint=str(otlp_endpoint), headers=otlp_headers
            )
        else:
            # Fallback to console exporter for dev environments
            from opentelemetry.sdk.trace.export import ConsoleSpanExporter

            exporter = ConsoleSpanExporter()

        span_processor = BatchSpanProcessor(exporter)
        provider.add_span_processor(span_processor)
        trace.set_tracer_provider(provider)

        # Instrument logging to include trace context; formatter handles fields
        LoggingInstrumentor().instrument(set_logging_format=False)
        # Instrument common libraries
        RequestsInstrumentor().instrument()

        # Provide a hook usable by main to instrument the app
        def instrument_app(app: FastAPI) -> None:  # noqa: N801
            FastAPIInstrumentor.instrument_app(app)

        # Store on module for later import
        globals()["instrument_app"] = instrument_app
    except Exception:
        # Optional deps are missing; skip tracing
        pass


def init_telemetry(app: FastAPI, config: Dict[str, Any]) -> None:
    """Public entrypoint: configure logging, tracing, and HTTP middleware."""
    configure_logging(config)
    configure_tracing(config)

    # Attach request-id middleware
    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        incoming = request.headers.get("X-Request-ID")
        req_id = incoming or str(uuid.uuid4())
        token = request_id_context.set(req_id)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            # Always reset context to avoid leakage across tasks
            request_id_context.reset(token)
        duration_ms = (time.perf_counter() - start) * 1000.0
        response.headers["X-Request-ID"] = req_id

        # If tracing is active, attach W3C trace context header for clients
        try:
            from opentelemetry.trace import get_current_span

            span = get_current_span()
            sc = getattr(span, "get_span_context", lambda: None)()
            if sc and getattr(sc, "is_valid", lambda: False)():
                trace_id = format(sc.trace_id, "032x")
                span_id = format(sc.span_id, "016x")
                trace_flags = getattr(sc, "trace_flags", 0)
                sampled = "01" if trace_flags & 0x01 else "00"
                traceparent = f"00-{trace_id}-{span_id}-{sampled}"
                response.headers["traceparent"] = traceparent
        except Exception:
            pass

        # Log access line after response is ready using an app-specific logger
        logger = logging.getLogger("float.access")
        path = str(request.url.path)
        # Downgrade very chatty endpoints to DEBUG to keep console succinct
        noisy_prefixes = (
            "/health",
            "/api/health",
            "/metrics",
            "/docs",
            "/openapi.json",
            "/favicon.ico",
            "/static/",
            "/api/models/exists/",
            "/api/models/local-size/",
            "/api/models/verify/",
            "/api/models/info/",
            "/api/openai/models",
            "/api/rag/status",
            "/api/celery/status",
            "/api/celery/failures",
            "/api/mcp/status",
        )
        payload = {
            "method": request.method,
            "path": path,
            "status_code": getattr(response, "status_code", 0),
            "duration_ms": round(duration_ms, 2),
            "client_ip": request.client.host if request.client else "-",
            "user_agent": request.headers.get("user-agent", "-"),
        }
        status = int(payload["status_code"] or 0)
        if any(path.startswith(p) for p in noisy_prefixes):
            logger.debug("http_request", extra=payload)
        elif status >= 500:
            logger.error("http_request", extra=payload)
        elif status >= 400:
            logger.warning("http_request", extra=payload)
        else:
            logger.info("http_request", extra=payload)
        return response
