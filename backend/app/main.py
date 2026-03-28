import asyncio
import logging
import os
from datetime import datetime, timezone
from contextlib import asynccontextmanager

import app.config as config
import app.hooks_auto_title as _hooks_auto_title  # noqa: F401 - register auto-title hook
import app.hooks_observers as _hooks_observers  # noqa: F401 - ensure lifecycle observers register
import app.routes as routes
import app.routes_tools as routes_tools
import app.services as services
from api.live import router as live_router
from api.sync import router as sync_router
from app import hooks
from app import routes as routes_module
from app import tools
from app.config import DEFAULT_MODELS_DIR
from app.mcp_loop import start_mcp_loop
from app.services.rag_provider import update_cached_config
from app.utils import metrics as metrics
from app.utils import telemetry as telemetry
from app.utils.device_visibility import device_access_rejection_detail
from app.utils.event_broker import EventBroker
from app.utils.hardware import (
    detect_compute_devices,
    pick_default_device,
    torch_cuda_diagnostics,
)
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from workers.task_evaluator import evaluate_pending_tasks
from workers.scheduled_tool_runner import scheduled_tool_runner

logger = logging.getLogger(__name__)

logger.info("App is loading :3")

# Prefer faster HF downloads by default (user can override via env)
os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "1")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Launch MCP loop and background task evaluator early in startup.

    MCP is core; attempt to start it regardless of environment, but never
    block overall app startup if it fails or the library is unavailable.
    """
    try:
        await start_mcp_loop(app)
    except Exception:
        # best-effort; MCP startup issues should not prevent app startup
        pass
    worker = asyncio.create_task(evaluate_pending_tasks(app))
    jobmon = asyncio.create_task(monitor_model_jobs(app))
    scheduled_tools = asyncio.create_task(scheduled_tool_runner(app))
    try:
        app.state.main_loop = asyncio.get_running_loop()
        action_history = getattr(app.state, "action_history_service", None)
        broker = getattr(app.state, "thought_broker", None)
        if action_history is not None and broker is not None:
            loop = app.state.main_loop

            def _emit_action_event(payload: dict) -> None:
                if not isinstance(payload, dict):
                    return
                async def _publish() -> None:
                    try:
                        await broker.publish(payload)
                    except Exception:
                        pass
                try:
                    running = asyncio.get_running_loop()
                except RuntimeError:
                    running = None
                if running is loop:
                    loop.create_task(_publish())
                else:
                    asyncio.run_coroutine_threadsafe(_publish(), loop)

            action_history.set_emitter(_emit_action_event)
        now_utc = datetime.now(tz=timezone.utc).isoformat()
        logger.info("Server startup (UTC): %s", now_utc)
    except Exception:
        logger.info("Server startup (UTC): <unavailable>")
    try:
        yield
    finally:
        # Ensure background tasks are cancelled and awaited without
        # propagating CancelledError (Python 3.11+ makes it a BaseException).
        worker.cancel()
        jobmon.cancel()
        scheduled_tools.cancel()
        await asyncio.gather(worker, jobmon, scheduled_tools, return_exceptions=True)


# Initialize FastAPI
app = FastAPI(lifespan=lifespan)


_DEVICE_ACCESS_PREFIXES = ("/devices", "/pairing", "/sync", "/gateway", "/stream")
_DEVICE_ACCESS_EXEMPT_PATHS = {"/sync/overview"}


def _normalized_device_access_path(path: str) -> str:
    raw = str(path or "").strip() or "/"
    return raw[4:] if raw.startswith("/api/") else raw


def _is_device_access_path(path: str) -> bool:
    normalized = _normalized_device_access_path(path)
    if normalized in _DEVICE_ACCESS_EXEMPT_PATHS:
        return False
    return any(
        normalized == prefix or normalized.startswith(f"{prefix}/")
        for prefix in _DEVICE_ACCESS_PREFIXES
    )


@app.middleware("http")
async def device_visibility_middleware(request: Request, call_next):
    if _is_device_access_path(request.url.path):
        detail = device_access_rejection_detail(request)
        if detail is not None:
            return JSONResponse(status_code=403, content={"detail": detail})
    return await call_next(request)


# Provide `app` as a dependency
def get_app():
    return app


# Health Check
@app.get("/health", tags=["Health"])
def health_check():
    return {"status": "healthy"}


@app.get("/api/health", tags=["Health"])
def api_health_check():
    """Mirror the root health endpoint under /api for frontend probes."""
    return health_check()


# Root Route
@app.get("/", tags=["Root"])
def read_root():
    return {"Hello": "World"}


# Load configurations
try:
    config = config.load_config()  # Correctly call `load_config`
    # Attach the loaded configuration to the application
    models_dir = config.get("models_folder")
    try:
        if isinstance(models_dir, str) and "huggingface" in models_dir.lower():
            config["models_folder"] = str(DEFAULT_MODELS_DIR)
    except Exception:
        config["models_folder"] = str(DEFAULT_MODELS_DIR)
    devices = detect_compute_devices()
    config["available_devices"] = devices
    config["cuda_diagnostics"] = torch_cuda_diagnostics(devices)
    default_device = pick_default_device(devices)
    config["default_inference_device"] = default_device
    existing_device = config.get("inference_device")
    ids = {device.get("id") for device in devices if isinstance(device, dict)}
    if not existing_device or existing_device not in ids:
        config["inference_device"] = default_device.get("id")
    app.state.config = config
    update_cached_config(config)
    # Initialize telemetry (logging/tracing)
    telemetry.init_telemetry(app, config)
    # Initialize Prometheus metrics (/metrics endpoint + HTTP middleware)
    metrics.init_metrics(app, config)
    # If tracing is available, instrument FastAPI app
    if hasattr(telemetry, "instrument_app"):
        try:
            telemetry.instrument_app(app)
        except Exception:
            logger.debug("OpenTelemetry instrumentation not active")
    logger.info("Configuration loaded successfully.")
except Exception as e:
    logger.error("Error loading configuration: %s", e)
    raise

# Add Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust based on security requirements
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize services
try:
    logger.info("Initializing services...")
    memory_manager = services.MemoryManager(config)
    logger.info("MemoryManager initialized.")

    rag_handler = services.RAGHandler(config)
    logger.info("RAGHandler initialized.")

    livekit_service = services.LiveKitService(config)
    logger.info("LiveKitService initialized.")

    sync_service = services.SyncService(os.getenv("SYNC_SECRET", "change-me"))
    logger.info("SyncService initialized.")

    action_history_service = services.ActionHistoryService(config)
    logger.info("ActionHistoryService initialized.")

    app.state.memory_manager = memory_manager
    app.state.rag_handler = rag_handler
    app.state.livekit_service = livekit_service
    app.state.sync_service = sync_service
    app.state.action_history_service = action_history_service
    # Broadcast stream so multiple consumers (main UI, dev panel, etc.) all see events.
    app.state.thought_broker = EventBroker(max_history=750, subscriber_queue_size=300)
    # Legacy attribute kept for older code paths; do not consume directly.
    app.state.thought_queue = asyncio.Queue()
    app.state.notify_queue = asyncio.Queue()
    app.state.pending_tasks = asyncio.Queue()
    app.state.agent_console_state = {"agents": {}}
    app.state.stream_sessions = {}

    memory_manager.set_action_history_service(action_history_service)

    # Bind memory tools to the runtime MemoryManager
    try:
        from app.tools.memory import set_manager as _set_mem_mgr  # type: ignore

        _set_mem_mgr(memory_manager)
        logger.info("Memory tools bound to MemoryManager")
    except Exception:
        # tools are optional; continue if import fails
        pass
    try:
        from app.tools.actions import set_action_history_service as _set_action_history  # type: ignore

        _set_action_history(action_history_service)
        logger.info("Action history tools bound to ActionHistoryService")
    except Exception:
        pass
    try:
        tools.register_builtin_tools(memory_manager)
        logger.info("Registered %d builtin tools", len(tools.BUILTIN_TOOLS))
    except Exception:
        logger.exception("Failed to register built-in tools")
except Exception as e:
    logger.error("Error initializing services: %s", e)
    raise

# Expose routes under both legacy "/" and current "/api" prefixes.
# Keeping both avoids breaking older clients and satisfies internal tests.
app.include_router(live_router)
app.include_router(sync_router)
app.include_router(routes.router)
app.include_router(routes.router, prefix="/api")
app.include_router(routes_tools.router)
app.include_router(routes_tools.router, prefix="/api")
logger.info("Registered %d routes", len(app.routes))


# Exception Handlers
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception(
        "unhandled_exception",
        extra={
            "path": str(request.url.path),
            "method": request.method,
        },
    )
    payload = {"message": "An unexpected error occurred.", "detail": str(exc)}
    try:
        hooks.emit(
            hooks.ERROR_EVENT,
            hooks.ErrorEvent(
                location="global_exception_handler",
                exception_type=type(exc).__name__,
                detail=str(exc),
                context={
                    "path": str(request.url.path),
                    "method": request.method,
                },
            ),
        )
    except Exception:
        logger.debug("failed to emit error hook", exc_info=True)
    return JSONResponse(status_code=500, content=payload)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    logger.warning(
        "validation_error",
        extra={
            "path": str(request.url.path),
            "method": request.method,
            "errors": exc.errors(),
        },
    )
    payload = {"message": "Validation error", "errors": exc.errors()}
    try:
        hooks.emit(
            hooks.ERROR_EVENT,
            hooks.ErrorEvent(
                location="validation_exception_handler",
                exception_type=type(exc).__name__,
                detail=str(exc),
                context={
                    "path": str(request.url.path),
                    "method": request.method,
                },
            ),
        )
    except Exception:
        logger.debug("failed to emit validation hook", exc_info=True)
    return JSONResponse(status_code=422, content=jsonable_encoder(payload))


# Run the application for development
if __name__ == "__main__":
    import uvicorn

    # Disable Uvicorn's default access log; we emit structured access logs
    # via our own middleware/logger configured in telemetry.
    uvicorn.run(app, host="0.0.0.0", port=8000, access_log=False)


async def monitor_model_jobs(app: FastAPI) -> None:
    """Periodically refresh download jobs and emit notifications on completion.

    This avoids relying on polling endpoints to detect completion.
    """
    while True:
        try:
            jobs = getattr(app.state, "model_jobs", {}) or {}
            for job in list(jobs.values()):
                try:
                    # Reuse routes' status refresh helper
                    routes_module._refresh_job_status(job)  # type: ignore[attr-defined]
                except Exception:
                    continue
                status = job.get("status")
                if status in {"completed", "canceled", "error"} and not job.get(
                    "_notified"
                ):
                    title = (
                        "Download complete"
                        if status == "completed"
                        else (
                            "Download canceled"
                            if status == "canceled"
                            else "Download error"
                        )
                    )
                    try:
                        routes_module.emit_notification(  # type: ignore[attr-defined]
                            app,
                            title=title,
                            body=str(job.get("model", "")),
                            category="download",
                            data={
                                "path": job.get("path"),
                                "repo": job.get("repo_id"),
                                "job_id": job.get("id"),
                                "status": status,
                            },
                        )
                    except Exception:
                        # best-effort; continue monitoring
                        pass
                    job["_notified"] = True
        except Exception:
            # Never crash the monitor; sleep and retry
            await asyncio.sleep(1)
        await asyncio.sleep(1)
