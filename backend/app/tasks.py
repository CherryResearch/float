import base64
import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

import requests
from app import config as app_config
from app.services.rag_provider import ingest_calendar_event, try_ingest_text
from app.services.tts_service import TTSService
from app.utils import calendar_store, memory_store, user_settings
from app.utils.metrics import celery_task_duration_seconds, celery_task_executions_total
from app.utils.push import can_send_push, send_web_push
from app.utils.telemetry import set_request_id
from app.utils.time_resolution import resolve_timezone_name
from celery import Celery
from celery import signals as celery_signals
from dateutil import rrule

# Attempt to import service classes.
# Fallback to dummy implementations if unavailable.
try:
    from app.services import ETLTools, RefinedServices, ToolService
except ImportError:

    class ToolService:
        def call_tool(self, tool_id, *args, **kwargs):
            return "Tool {} executed with args {} and kwargs {}".format(
                tool_id, args, kwargs
            )

    class ETLTools:
        def extract(self, source: Dict):
            return {"status": "success", "data": "extracted data"}

        def transform(self, data: Dict, transform_config: Dict):
            return {
                "status": "success",
                "transformed_data": "transformed data",
            }

        def load(self, data: Dict):
            return {"status": "success", "loaded": True}

    class RefinedServices:
        def embed_and_store(self, content: Dict) -> Dict:
            return {"status": "success", "embedding": "dummy_embedding"}


# Configure Celery using environment variables (fall back to Redis)
broker_url = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
result_backend = os.getenv("CELERY_RESULT_BACKEND", broker_url)
celery_app = Celery("float_tasks", broker=broker_url, backend=result_backend)


def _resolve_memory_store_path() -> Path:
    cfg = app_config.load_config()
    raw_path = (
        cfg.get("memory_store_path")
        or cfg.get("memory_store_file")
        or cfg.get("memory_store")
    )
    try:
        return memory_store.resolve_path(raw_path)
    except Exception:
        return memory_store.resolve_path(None)


# Correlate Celery task logs with task_id via request_id context
@celery_signals.task_prerun.connect
def _celery_task_prerun(task_id: str, task, *args, **kwargs):  # type: ignore[override]
    try:
        set_request_id(task_id)
    except Exception:
        pass


@celery_signals.task_postrun.connect
def _celery_task_postrun(task_id: str, task, *args, **kwargs):  # type: ignore[override]
    try:
        set_request_id(None)
    except Exception:
        pass


# Record task failures to a JSONL file for diagnostics (readable by API)
@celery_signals.task_failure.connect
def _celery_task_failure(task_id, exception, args, kwargs, einfo, task, **kw):  # type: ignore[override]
    try:
        logs_dir = Path(__file__).resolve().parents[2] / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": time.time(),
            "id": str(task_id),
            "name": getattr(task, "name", None),
            # Truncate to avoid large payloads and reduce leak risk
            "args": repr(args)[:512],
            "kwargs": repr(kwargs)[:512],
            "exc_type": getattr(type(exception), "__name__", "Exception"),
            "exc": str(exception)[:512],
        }
        with open(logs_dir / "celery_failures.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        # Never crash worker due to logging failure
        pass


tool_service = ToolService()
etl_tools = ETLTools()
refined_services = RefinedServices()

# Lead time before event start (in seconds) to trigger prompt
EVENT_PROMPT_LEAD_TIME = int(os.getenv("EVENT_PROMPT_LEAD_TIME", "300"))

# Configure periodic task schedule for polling calendar events
celery_app.conf.beat_schedule = {
    "poll-calendar-events": {
        "task": "app.tasks.poll_calendar_events",
        "schedule": 60.0,
    }
}


@celery_app.task
def execute_tool(tool_id, *args, **kwargs):
    # executes the called tool in async context.
    try:
        _t0 = time.perf_counter()
        result = tool_service.call_tool(tool_id, *args, **kwargs)
        celery_task_executions_total.labels("execute_tool", "ok").inc()
        celery_task_duration_seconds.labels("execute_tool", "ok").observe(
            time.perf_counter() - _t0
        )
        return result
    except ValueError as e:
        celery_task_executions_total.labels("execute_tool", "bad_request").inc()
        return f"Tool {tool_id} not found: {e}"
    except Exception as e:
        celery_task_executions_total.labels("execute_tool", "error").inc()
        logging.error(f"Error during tool execution: {e}")
        return f"Error during tool execution: {e}"


@celery_app.task
def long_running_task():
    # placeholder for any long running tasks.
    _t0 = time.perf_counter()
    time.sleep(10)  # wait 10 seconds.
    celery_task_executions_total.labels("long_running_task", "ok").inc()
    celery_task_duration_seconds.labels("long_running_task", "ok").observe(
        time.perf_counter() - _t0
    )
    return "long task complete."


@celery_app.task
def execute_etl_pipeline(
    source: Dict, transform_config: Dict, load_target: Dict
) -> Dict:
    """
    Executes the full ETL pipeline asynchronously.
    - Extracts data from the source.
    - Transforms the data according to the transform configuration.
    - Loads the transformed data to the specified target.
    Input schema:
    {
        "source": Dict,  # Configuration for the extract step
        "transform_config": Dict,  # Configuration for the transform step
        "load_target": Dict  # Configuration for the load step
    }
    """
    # Extract
    _t0 = time.perf_counter()
    extract_result = etl_tools.extract(source)
    if extract_result.get("status") != "success":
        celery_task_executions_total.labels("execute_etl_pipeline", "error").inc()
        celery_task_duration_seconds.labels("execute_etl_pipeline", "error").observe(
            time.perf_counter() - _t0
        )
        return {
            "status": "error",
            "step": "extract",
            "message": extract_result.get("message"),
        }

    # Transform
    # Depending on the source type, choose the correct key.
    data_key = "html" if source.get("type") == "web" else "data"
    transform_result = etl_tools.transform(
        {"type": source.get("type"), "raw_data": extract_result.get(data_key)},
        transform_config,
    )
    if transform_result.get("status") != "success":
        celery_task_executions_total.labels("execute_etl_pipeline", "error").inc()
        celery_task_duration_seconds.labels("execute_etl_pipeline", "error").observe(
            time.perf_counter() - _t0
        )
        return {
            "status": "error",
            "step": "transform",
            "message": transform_result.get("message"),
        }

    # Load
    load_result = etl_tools.load(
        {"type": load_target.get("type"), "content": transform_result}
    )
    if load_result.get("status") != "success":
        celery_task_executions_total.labels("execute_etl_pipeline", "error").inc()
        celery_task_duration_seconds.labels("execute_etl_pipeline", "error").observe(
            time.perf_counter() - _t0
        )
        return {
            "status": "error",
            "step": "load",
            "message": load_result.get("message"),
        }

    celery_task_executions_total.labels("execute_etl_pipeline", "ok").inc()
    celery_task_duration_seconds.labels("execute_etl_pipeline", "ok").observe(
        time.perf_counter() - _t0
    )
    return {"status": "success"}


@celery_app.task
def generate_embedding_task(content: Dict) -> Dict:
    """
    Generates embeddings for given content and stores it asynchronously.
    Input schema:
    {
        "text": str,  # Text to embed
        "metadata": Dict  # Metadata to store alongside embeddings
    }
    """
    return refined_services.embed_and_store(content)


def _float_online() -> bool:
    """Return True if Float is considered online."""
    return os.getenv("FLOAT_ONLINE", "true").lower() == "true"


def _emit_calendar_notification(
    *,
    event_id: str,
    title: str,
    body: str,
    start_iso: str,
    description: str | None = None,
) -> None:
    try:
        from app.main import app as current_app
        from app.routes import emit_notification

        payload = {
            "event_id": event_id,
            "start": start_iso,
            "action_url": "/",
        }
        if description:
            payload["description"] = description
        emit_notification(
            current_app,
            title=title,
            body=body,
            category="calendar_event",
            data=payload,
        )
    except Exception:
        logging.exception("Unexpected error during calendar notification emit")


@celery_app.task
def send_event_prompt(event_id: str, occ_time: Optional[float] = None) -> str:
    """Send or queue a prompt for the specified calendar event."""
    event = calendar_store.load_event(event_id)
    if not event:
        return "event not found"
    status = event.get("status", "pending")
    if not event.get("rrule") and status != "pending":
        return "event already processed"

    if not _float_online():
        return "float offline"

    tz = ZoneInfo(resolve_timezone_name(event.get("timezone")))
    now = datetime.now(tz)
    start = occ_time or event.get("start_time", now.timestamp())
    start_dt = datetime.fromtimestamp(start, tz)
    title = event.get("title", event_id)
    if now.timestamp() >= start:
        message = (
            f"Event '{title}' started at {start_dt.isoformat()}. "
            "Send manually or discard?"
        )
    else:
        message = f"Upcoming event '{title}' at {start_dt.isoformat()}."

    event["status"] = "prompted"
    event["prompt_message"] = message
    event["last_triggered"] = start
    calendar_store.save_event(event_id, event)
    try:
        ingest_calendar_event(event_id, event)
    except Exception:
        pass
    _emit_calendar_notification(
        event_id=event_id,
        title=title,
        body=message,
        start_iso=start_dt.isoformat(),
        description=event.get("description"),
    )
    # Attempt web push if configured and user subscribed
    try:
        if can_send_push():
            settings = user_settings.load_settings()
            sub = settings.get("push_subscription")
            enabled = settings.get("push_enabled", False)
            if enabled and sub:
                payload = {
                    "title": title,
                    "body": message,
                    "data": {
                        "event_id": event_id,
                        "start": start_dt.isoformat(),
                        "action_url": "/",  # client can route accordingly
                    },
                }
                err = send_web_push(sub, payload)
                if err:
                    logging.warning("Failed to send web push: %s", err)
    except Exception:
        logging.exception("Unexpected error during web push send")

    logging.info(message)
    return message


def dispatch_due_calendar_prompts(*, enqueue: bool = True) -> list[dict[str, Any]]:
    """Scan stored events and trigger prompts for due reminders."""

    triggered: list[dict[str, Any]] = []
    try:
        settings = user_settings.load_settings()
        minutes = int(settings.get("calendar_notify_minutes", 0))
        lead_seconds = minutes * 60 if minutes > 0 else EVENT_PROMPT_LEAD_TIME
    except Exception:
        lead_seconds = EVENT_PROMPT_LEAD_TIME

    for event_id in calendar_store.list_events():
        event = calendar_store.load_event(event_id)
        if not isinstance(event, dict):
            continue
        tz = ZoneInfo(resolve_timezone_name(event.get("timezone")))
        now = datetime.now(tz)
        lead = timedelta(seconds=lead_seconds)
        rrule_str = event.get("rrule")
        if rrule_str:
            start_dt = datetime.fromtimestamp(
                event.get("start_time", now.timestamp()), tz
            )
            rule = rrule.rrulestr(rrule_str, dtstart=start_dt)
            last = event.get("last_triggered")
            after = datetime.fromtimestamp(last, tz) if last else now
            next_dt = rule.after(after)
            if next_dt and next_dt <= now + lead:
                occ_time = next_dt.timestamp()
                if enqueue:
                    send_event_prompt.delay(event_id, occ_time)
                else:
                    send_event_prompt.run(event_id, occ_time)
                triggered.append({"event_id": event_id, "occ_time": occ_time})
            continue

        if event.get("status", "pending") != "pending":
            continue
        start = event.get("start_time")
        if start is None:
            continue
        start_dt = datetime.fromtimestamp(start, tz)
        if start_dt <= now + lead:
            if enqueue:
                send_event_prompt.delay(event_id, start)
            else:
                send_event_prompt.run(event_id, start)
            triggered.append({"event_id": event_id, "occ_time": start})
    return triggered


@celery_app.task
def poll_calendar_events() -> None:
    """Scan stored events and dispatch prompts for upcoming ones."""

    dispatch_due_calendar_prompts(enqueue=True)


@celery_app.task
def rehydrate_memories(limit: Optional[int] = None) -> Dict[str, Any]:
    """Embed legacy memory items that lack vectors."""
    target = _resolve_memory_store_path()
    try:
        snapshot = memory_store.load(target)
    except Exception as exc:
        logging.warning("rehydrate_memories: failed to load store: %s", exc)
        return {"reindexed": 0, "scanned": 0, "error": "load_failed"}
    if not isinstance(snapshot, dict):
        return {"reindexed": 0, "scanned": 0, "error": "invalid_store"}
    max_items = None
    if limit is not None:
        try:
            max_items = max(0, int(limit))
        except Exception:
            max_items = None
    updated = 0
    scanned = 0
    changed = False
    for key in snapshot:
        if max_items is not None and scanned >= max_items:
            break
        entry = snapshot.get(key)
        if not isinstance(entry, dict):
            entry = {"value": entry}
        value = entry.get("value")
        if not isinstance(value, str) or not value.strip():
            continue
        scanned += 1
        if entry.get("vectorized_at"):
            continue
        metadata = {
            "kind": "memory_refresh",
            "memory_key": key,
            "namespace": entry.get("namespace"),
            "tags": entry.get("tags"),
            "sensitivity": entry.get("sensitivity"),
        }
        doc_id = try_ingest_text(value, metadata)
        if not doc_id:
            continue
        entry["vectorized_at"] = time.time()
        entry.setdefault("vectorize", True)
        snapshot[key] = entry
        updated += 1
        changed = True
    if changed:
        try:
            memory_store.save(snapshot, target)
        except Exception as exc:
            logging.warning("rehydrate_memories: failed to persist store: %s", exc)
    return {"reindexed": updated, "scanned": scanned}


@celery_app.task
def process_livekit_audio(data: bytes) -> Dict:
    """Transcribe and synthesize audio using external APIs."""
    cfg = app_config.load_config()
    api_key = cfg.get("api_key")
    if not api_key:
        return {"text": "", "audio": ""}

    headers = {"Authorization": f"Bearer {api_key}"}
    files = {"file": ("audio.wav", data, "audio/wav")}
    stt_model = cfg.get("stt_model", "whisper-1")
    tts_model = cfg.get("tts_model", "tts-1")
    voice_model = cfg.get("voice_model", "nova")
    try:
        resp = requests.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers=headers,
            files=files,
            data={"model": stt_model},
            timeout=30,
        )
        resp.raise_for_status()
        text = resp.json().get("text", "")
    except Exception:
        text = ""

    audio_b64 = ""
    if text:
        try:
            tts_result = TTSService().synthesize(
                text,
                cfg,
                model=tts_model,
                voice=voice_model,
                audio_format="wav",
            )
            audio_b64 = base64.b64encode(tts_result.audio).decode("ascii")
        except Exception as exc:
            logging.warning("process_livekit_audio: TTS failed: %s", exc)

    return {"text": text, "audio": audio_b64}
