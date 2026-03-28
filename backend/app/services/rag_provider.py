from __future__ import annotations

import hashlib
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from app import config as app_config
from app.utils.time_resolution import resolve_timezone_name
from fastapi import HTTPException

try:  # pragma: no cover - optional dependency
    from app.services.rag_service import RAGService

    RAG_IMPORT_ERROR = None
except ModuleNotFoundError as exc:  # pragma: no cover
    RAGService = None  # type: ignore
    RAG_IMPORT_ERROR = exc

try:  # pragma: no cover - standard library but optional on some platforms
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore

logger = logging.getLogger(__name__)

_rag_service = None
_rag_signature: Optional[Tuple[Any, ...]] = None
_clip_rag_service = None
_clip_rag_signature: Optional[Tuple[Any, ...]] = None
_config_snapshot: Optional[Dict[str, Any]] = None

_CLIP_MODEL_ALIASES = {
    "clip-vit-base-patch32": "ViT-B-32",
}


def _is_fatal_base_exception(exc: BaseException) -> bool:
    return isinstance(exc, (KeyboardInterrupt, SystemExit))


def _normalize_clip_model(value: Optional[str]) -> str:
    if not value:
        return "ViT-B-32"
    cleaned = str(value).strip()
    if not cleaned:
        return "ViT-B-32"
    normalized = _CLIP_MODEL_ALIASES.get(cleaned, cleaned)
    lowered = normalized.lower()
    if "paligemma" in lowered or "pixtral" in lowered:
        logger.warning(
            "Unsupported CLIP model '%s' configured; falling back to ViT-B-32.",
            normalized,
        )
        return "ViT-B-32"
    return normalized


def update_cached_config(config: Dict[str, Any]) -> None:
    """Store the latest app config so background helpers can reuse it."""
    global _config_snapshot, _rag_service, _clip_rag_service
    _config_snapshot = dict(config or {})
    if _rag_service is not None:
        try:
            _rag_service.update_api_settings(
                api_url=_config_snapshot.get("api_url"),
                api_key=_config_snapshot.get("api_key"),
            )
        except Exception:
            pass
    if _clip_rag_service is not None:
        try:
            _clip_rag_service.update_api_settings(
                api_url=_config_snapshot.get("api_url"),
                api_key=_config_snapshot.get("api_key"),
            )
        except Exception:
            pass


def _resolve_config(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if config:
        return config
    if _config_snapshot is not None:
        return _config_snapshot
    return app_config.load_config()


def _build_signature(cfg: Dict[str, Any]) -> Tuple[Any, ...]:
    return (
        cfg.get("rag_backend", "chroma"),
        cfg.get("chroma_persist_dir", str(app_config.DEFAULT_CHROMA_DIR)),
        cfg.get("weaviate_url"),
        cfg.get("weaviate_grpc_host"),
        cfg.get("weaviate_grpc_port"),
        cfg.get("auto_start_weaviate"),
        cfg.get("rag_embedding_model"),
        cfg.get("memory_store_path"),
        cfg.get("api_url"),
        hashlib.sha1(str(cfg.get("api_key") or "").encode("utf-8")).hexdigest(),
    )


def _build_clip_signature(cfg: Dict[str, Any]) -> Tuple[Any, ...]:
    return (
        "clip",
        cfg.get("rag_backend", "chroma"),
        cfg.get("chroma_persist_dir", str(app_config.DEFAULT_CHROMA_DIR)),
        cfg.get("weaviate_url"),
        cfg.get("weaviate_grpc_host"),
        cfg.get("weaviate_grpc_port"),
        cfg.get("auto_start_weaviate"),
        _normalize_clip_model(cfg.get("rag_clip_model") or os.getenv("RAG_CLIP_MODEL")),
        cfg.get("memory_store_path"),
        cfg.get("api_url"),
        hashlib.sha1(str(cfg.get("api_key") or "").encode("utf-8")).hexdigest(),
    )


def _ensure_rag_service(cfg: Dict[str, Any]):
    global _rag_service, _rag_signature
    if RAGService is None:
        detail = "RAG service unavailable"
        missing = getattr(RAG_IMPORT_ERROR, "name", None)
        if missing:
            detail = f"RAG service dependency '{missing}' is not installed"
        raise HTTPException(status_code=503, detail=detail)
    sig = _build_signature(cfg)
    if _rag_service is None or _rag_signature != sig:
        backend_choice = cfg.get("rag_backend", "chroma")
        persist_dir = cfg.get("chroma_persist_dir", str(app_config.DEFAULT_CHROMA_DIR))
        embedding_model = cfg.get("rag_embedding_model")
        weaviate_url = cfg.get("weaviate_url")
        logger.info(
            "Initializing shared RAG service (backend=%s, embedding_model=%s).",
            backend_choice,
            embedding_model or "simple",
        )
        try:
            _rag_service = RAGService(
                backend=backend_choice,
                persist_dir=persist_dir,
                url=weaviate_url,
                embedding_model=embedding_model,
                api_url=cfg.get("api_url"),
                api_key=cfg.get("api_key"),
                sqlite_path=cfg.get("memory_store_path"),
            )
        except BaseException as exc:  # pragma: no cover - defensive guard
            if _is_fatal_base_exception(exc):
                raise
            logger.error("Failed to initialise RAG service: %s", exc)
            raise HTTPException(status_code=503, detail=f"RAG init failed: {exc}")
        _rag_signature = sig
    return _rag_service


def _ensure_clip_rag_service(cfg: Dict[str, Any]):
    global _clip_rag_service, _clip_rag_signature
    if RAGService is None:
        detail = "RAG service unavailable"
        missing = getattr(RAG_IMPORT_ERROR, "name", None)
        if missing:
            detail = f"RAG service dependency '{missing}' is not installed"
        raise HTTPException(status_code=503, detail=detail)
    sig = _build_clip_signature(cfg)
    if _clip_rag_service is None or _clip_rag_signature != sig:
        backend_choice = cfg.get("rag_backend", "chroma")
        persist_dir = cfg.get("chroma_persist_dir", str(app_config.DEFAULT_CHROMA_DIR))
        weaviate_url = cfg.get("weaviate_url")
        clip_model = _normalize_clip_model(
            cfg.get("rag_clip_model") or os.getenv("RAG_CLIP_MODEL")
        )
        logger.info(
            "Initializing shared CLIP RAG service (backend=%s, clip_model=%s).",
            backend_choice,
            clip_model,
        )
        try:
            _clip_rag_service = RAGService(
                class_name="KnowledgeClip",
                backend=backend_choice,
                persist_dir=persist_dir,
                url=weaviate_url,
                embedding_model=f"clip:{clip_model}",
                sqlite_path=cfg.get("memory_store_path"),
                enable_canonical_store=False,
            )
        except BaseException as exc:  # pragma: no cover - defensive guard
            if _is_fatal_base_exception(exc):
                raise
            logger.error("Failed to initialise CLIP RAG service: %s", exc)
            raise HTTPException(
                status_code=503,
                detail=f"CLIP RAG init failed: {exc}",
            )
        _clip_rag_signature = sig
    return _clip_rag_service


def get_rag_service(
    config: Optional[Dict[str, Any]] = None,
    *,
    raise_http: bool = True,
):
    """Return the shared RAG service, optionally suppressing HTTP errors."""
    cfg = _resolve_config(config)
    try:
        return _ensure_rag_service(cfg)
    except HTTPException:
        if raise_http:
            raise
        return None
    except BaseException as exc:
        if _is_fatal_base_exception(exc):
            raise
        logger.error("RAG provider runtime failure: %s", exc)
        if raise_http:
            raise HTTPException(status_code=503, detail=f"RAG runtime failed: {exc}")
        return None


def get_clip_rag_service(
    config: Optional[Dict[str, Any]] = None,
    *,
    raise_http: bool = True,
):
    """Return the shared CLIP index service, optionally suppressing HTTP errors."""
    cfg = _resolve_config(config)
    try:
        return _ensure_clip_rag_service(cfg)
    except HTTPException:
        if raise_http:
            raise
        return None
    except BaseException as exc:
        if _is_fatal_base_exception(exc):
            raise
        logger.error("CLIP RAG provider runtime failure: %s", exc)
        if raise_http:
            raise HTTPException(
                status_code=503,
                detail=f"CLIP RAG runtime failed: {exc}",
            )
        return None


def get_aux_model_status(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = _resolve_config(config)
    text_service = _rag_service
    clip_service = _clip_rag_service

    text_status: Dict[str, Any]
    if text_service is not None and hasattr(text_service, "embedding_runtime_status"):
        text_status = dict(text_service.embedding_runtime_status())
    else:
        model = str(cfg.get("rag_embedding_model") or "simple").strip() or "simple"
        lowered = model.lower()
        if lowered in {"simple", "hash"}:
            mode = "hash"
            state = "ready"
        elif lowered.startswith("api:"):
            mode = "api"
            state = "remote"
        else:
            mode = "clip" if lowered.startswith("clip:") else "sentence_transformer"
            state = "idle"
        text_status = {
            "model": model,
            "mode": mode,
            "state": state,
            "loaded": False,
            "init_attempted": False,
            "error": None,
        }
    text_status["service_initialized"] = text_service is not None

    clip_model = _normalize_clip_model(
        cfg.get("rag_clip_model") or os.getenv("RAG_CLIP_MODEL")
    )
    clip_status: Dict[str, Any]
    if clip_service is not None and hasattr(clip_service, "embedding_runtime_status"):
        clip_status = dict(clip_service.embedding_runtime_status())
    else:
        clip_status = {
            "model": f"clip:{clip_model}",
            "mode": "clip",
            "state": "idle",
            "loaded": False,
            "init_attempted": False,
            "error": None,
        }
    clip_status["service_initialized"] = clip_service is not None
    return {
        "text_embeddings": text_status,
        "clip_embeddings": clip_status,
    }


def try_ingest_text(
    text: str,
    metadata: Optional[Dict[str, Any]] = None,
    *,
    config: Optional[Dict[str, Any]] = None,
    mirror_vector: bool = True,
) -> Optional[str]:
    """Helper for non-request contexts; swallows most errors."""
    if not isinstance(text, str) or not text.strip():
        return None
    service = get_rag_service(config, raise_http=False)
    if not service:
        return None
    meta = dict(metadata or {})
    meta.setdefault("kind", "document")
    # Keep `type` and `kind` aligned; docs prefer `type` but the backend hooks
    # currently use `kind`.
    meta.setdefault("type", meta.get("kind"))
    meta.setdefault("created_at", time.time())
    try:
        return service.ingest_text(text, meta, mirror_vector=mirror_vector)
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.warning("RAG ingest failed: %s", exc)
        return None


def _format_calendar_timestamp(value: Any, tz_name: str) -> Optional[str]:
    try:
        ts = float(value)
    except Exception:
        return None
    tz = timezone.utc
    if ZoneInfo is not None:
        try:
            tz = ZoneInfo(resolve_timezone_name(tz_name))
        except Exception:
            tz = timezone.utc
    dt = datetime.fromtimestamp(ts, tz)
    return dt.strftime("%Y-%m-%d %H:%M %Z")


def calendar_event_to_text(event: Dict[str, Any]) -> str:
    """Render a calendar event dict as a natural-language snippet."""
    if not isinstance(event, dict):
        return ""
    title = event.get("title") or "Untitled event"
    tz_name = resolve_timezone_name(event.get("timezone"))
    start_str = _format_calendar_timestamp(event.get("start_time"), tz_name)
    end_str = _format_calendar_timestamp(event.get("end_time"), tz_name)
    window = start_str or "unscheduled"
    if start_str and end_str:
        window = f"{start_str} to {end_str}"
    elif start_str:
        window = f"starting {start_str}"
    status = event.get("status") or "pending"
    rrule = event.get("rrule")
    recurrence = f" Recurs via {rrule}." if rrule else ""
    notes = event.get("notes") or []
    note_text = ""
    if isinstance(notes, list) and notes:
        extracted = [
            str(item.get("content")).strip()
            for item in notes
            if isinstance(item, dict) and item.get("content")
        ]
        if extracted:
            note_text = " Notes: " + " | ".join(extracted[:3])
    return (
        f"{title} ({status}) scheduled {window} in {tz_name}."
        f"{recurrence}{note_text}"
    ).strip()


def ingest_calendar_event(
    event_id: str,
    event: Dict[str, Any],
    *,
    config: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Vectorize a calendar event into the RAG corpus."""
    if not event_id or not isinstance(event, dict):
        return None
    text = calendar_event_to_text(event)
    if not text:
        return None
    metadata: Dict[str, Any] = {
        "kind": "calendar_event",
        "event_id": event_id,
        "title": event.get("title"),
        "start_time": event.get("start_time"),
        "end_time": event.get("end_time"),
        "status": event.get("status"),
        "timezone": event.get("timezone"),
    }
    if event.get("rrule"):
        metadata["rrule"] = event["rrule"]
    return try_ingest_text(text, metadata, config=config)
