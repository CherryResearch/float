# isort: skip_file
import asyncio
import copy
import base64
import io
from collections import deque
from fnmatch import fnmatch
import hashlib
import mimetypes
import json
import logging
import os
import re
import time
import shutil
import textwrap
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Literal, Union

import jwt
import requests
import socket
from urllib.parse import quote, urlparse

from app import config as app_config, hooks
from app import tools
from app.agents.engine import get_engine
from app.models import ChatRequest, ChatResponse, Attachment
from app.models import ModelContext as ContextSchema
from app.models import Tool
from app.schemas import CalendarEvent, MemoryUpdateRequest
from app.services import LangExtractService, LiveKitService, LLMService, TTSService
from app.services import (
    ModelContext as ServiceContext,
    parse_google_calendar,
    parse_ics,
)
from app.services.livekit_service import (
    DEFAULT_REALTIME_CONNECT_URL,
    DEFAULT_REALTIME_MODEL,
    DEFAULT_REALTIME_SESSION_URL,
    DEFAULT_REALTIME_VOICE,
)
from app.services.rag_provider import (
    get_aux_model_status as _get_aux_model_status,
    get_clip_rag_service as _get_cached_clip_rag_service,
    get_rag_service as _get_cached_rag_service,
    ingest_calendar_event as _ingest_calendar_event,
    update_cached_config as _update_rag_config,
)
from app.services.instance_sync_service import (
    InstanceSyncService,
    RemoteFloatClient,
    SYNC_SECTION_LABELS,
    _resolve_remote_urls,
)
from app.tasks import (
    process_livekit_audio,
    rehydrate_memories as rehydrate_memories_task,
)
from app.hooks_auto_title import consume_pending_title
from app.utils import (
    calendar_store,
    conversation_store,
    generate_signature,
    sanitize_args,
    user_settings,
)
from app.utils.knowledge_store import KnowledgeStore
from app.utils.push import can_send_push, send_web_push, vapid_config
from app.utils.device_visibility import (
    advertised_device_access,
    candidate_device_urls,
)
from app.utils.attachment_media import build_attachment_media_descriptor
from app.utils.device_registry import (
    delete_device,
    get_device,
    register_or_update_device,
    update_device,
    list_devices,
    issue_device_token,
    touch_device,
)
from app.utils.rendezvous_store import (
    accept_offer as accept_rendezvous_offer,
    create_offer as create_rendezvous_offer,
    create_session as create_rendezvous_session,
)
from app.utils.sync_review_store import (
    create_review as create_sync_review,
    get_review as get_sync_review,
    list_reviews as list_sync_reviews,
    update_review as update_sync_review,
)
from app.utils.sync_store import (
    get_cursor as sync_get_cursor,
    get_changes_since as sync_get_changes_since,
    record_changes as sync_record_changes,
)
from app.utils.workspace_registry import (
    DEFAULT_WORKSPACE_ID,
    build_synced_workspace_profile,
    load_workspace_state,
    normalize_workspace_ids,
    resolve_synced_workspace_location,
    summarize_workspace_profile,
    workspace_profile_map,
)
from app.utils.blob_store import (
    put_blob,
    put_asset,
    get_blob,
    exists as blob_exists,
    delete as blob_delete,
    find_asset_path,
    normalize_asset_origin,
    BLOBS_DIR,
)
from celery.result import AsyncResult
from app.utils.hardware import torch_cuda_diagnostics
from app.utils.tokenizer import CustomTokenizer

try:
    # Prefer top-level import when available
    from dotenv import dotenv_values, set_key, unset_key  # type: ignore
except Exception:  # pragma: no cover - fallback for stubs/older libs
    try:
        from dotenv.main import set_key, unset_key  # type: ignore
        from dotenv import dotenv_values  # type: ignore
    except Exception:
        # Minimal fallback that appends/updates KEY=VALUE lines.
        def set_key(dotenv_path: str, key: str, value: str, quote_mode: str | None = None):  # type: ignore
            try:
                lines = []
                found = False
                try:
                    with open(dotenv_path, "r", encoding="utf-8") as f:
                        for line in f:
                            if line.startswith(f"{key}="):
                                lines.append(f"{key}={value}\n")
                                found = True
                            else:
                                lines.append(line)
                except FileNotFoundError:
                    pass
                if not found:
                    lines.append(f"{key}={value}\n")
                with open(dotenv_path, "w", encoding="utf-8") as f:
                    f.writelines(lines)
                return (key, value, True)
            except Exception as e:
                raise RuntimeError(f"Failed to set {key} in {dotenv_path}: {e}")

        def unset_key(dotenv_path: str, key: str, quote_mode: str | None = None):  # type: ignore
            try:
                lines = []
                changed = False
                try:
                    with open(dotenv_path, "r", encoding="utf-8") as f:
                        for line in f:
                            if line.startswith(f"{key}="):
                                changed = True
                                continue
                            lines.append(line)
                except FileNotFoundError:
                    return False
                with open(dotenv_path, "w", encoding="utf-8") as f:
                    f.writelines(lines)
                return changed
            except Exception as e:
                raise RuntimeError(f"Failed to unset {key} in {dotenv_path}: {e}")


from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    File as UploadFileType,
    HTTPException,
    Form,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

# NOTE: Avoid importing huggingface_hub at module import time to improve
# startup latency. We import it lazily inside the endpoints that use it.
import subprocess
import sys
from pydantic import BaseModel, Field, ConfigDict
from uuid import uuid4
from app.utils.telemetry import get_request_id
from app.utils.event_broker import BrokerEvent, EventBroker
from app.utils.stream_sanitize import InlineToolStreamFilter
from app.utils.tool_args import normalize_and_sanitize_tool_args, normalize_tool_args
from app.utils.local_model_registry import (
    list_local_model_entries,
    remove_local_model_entry,
    resolve_registered_model_path,
    upsert_local_model_entry,
)
from app.local_providers import LocalProviderManager
from app.utils.chat_log import (
    log_chat_request,
    log_chat_response,
    log_history_save,
    log_thought_delta,
    log_tool_event,
)
from app.utils.conversation_timeline import log_message as log_timeline_message
from app.utils.http_client import http_session
from app.utils.metrics import (
    sse_events_total,
    tool_invocations_total,
    llm_generate_requests_total,
    llm_generate_duration_seconds,
)
from app.services import memory_graph_service, threads_service
from config import load_model_catalog
from workers.multimodal import (
    VisionCaptioner,
    is_placeholder_caption,
    placeholder_caption,
)
from services.weaviate_client import autostart_weaviate
from app.model_registry import (
    MODEL_REPOS,
    filter_models_for_devices,
    get_download_allow_patterns,
)

logger = logging.getLogger(__name__)

router = APIRouter()
llm_service = LLMService()
livekit_service = LiveKitService(app_config.load_config())
engine = get_engine()
langextract_service = LangExtractService("Extract conversation data", [])
tts_service = TTSService()
provider_manager = LocalProviderManager(
    lambda: llm_service.config if isinstance(llm_service.config, dict) else {}
)

# In-memory notification buffer (recent only)
if not hasattr(asyncio, "__float_notifications__"):
    asyncio.__float_notifications__ = []  # type: ignore[attr-defined]


def _responses_api_base(api_url: Optional[str]) -> str:
    """Return the base URL used for Responses API calls.

    Falls back to the canonical OpenAI base when ``api_url`` is unset and
    strips either ``/responses`` or legacy ``/chat/completions`` suffixes to
    avoid duplicate path segments when constructing proxy URLs.
    """

    default_base = app_config.OPENAI_RESPONSES_URL.rsplit("/responses", 1)[0]
    trimmed = (api_url or "").strip()
    if not trimmed:
        return default_base
    normalized = trimmed.rstrip("/")
    for suffix in ("/responses", "/chat/completions"):
        if normalized.endswith(suffix):
            base = normalized[: -len(suffix)] or default_base
            return base.rstrip("/") or default_base
    return normalized


def _notifications_buffer():
    return asyncio.__float_notifications__  # type: ignore[attr-defined]


_MAX_AGENT_HISTORY = 120


def _ensure_agent_console_state(app) -> dict:
    if not hasattr(app.state, "agent_console_state"):
        app.state.agent_console_state = {"agents": {}, "resources": {}}
    state = app.state.agent_console_state  # type: ignore[attr-defined]
    if isinstance(state, dict):
        state.setdefault("agents", {})
        state.setdefault("resources", {})
    return state


def _get_action_history_service(app):
    service = getattr(app.state, "action_history_service", None)
    return service if service is not None else None


def _current_request_id() -> Optional[str]:
    try:
        request_id = get_request_id()
    except Exception:
        return None
    if request_id is None:
        return None
    text = str(request_id).strip()
    return text or None


def _lookup_message_runtime_hints(
    session_id: Optional[str],
    message_id: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    if not session_id or not message_id:
        return None, None
    model_hint = None
    mode_hint = None
    try:
        conv = conversation_store.load_conversation(session_id)
    except Exception:
        return None, None
    if not isinstance(conv, list):
        return None, None
    for item in conv:
        if not isinstance(item, dict) or item.get("id") != message_id:
            continue
        meta = item.get("metadata") or {}
        if isinstance(meta, dict):
            raw_model = meta.get("model")
            raw_mode = meta.get("mode")
            if isinstance(raw_model, str) and raw_model.strip():
                model_hint = raw_model.strip()
            if isinstance(raw_mode, str) and raw_mode.strip():
                mode_hint = raw_mode.strip()
        break
    return model_hint, mode_hint


def _build_action_context(
    *,
    session_id: Optional[str] = None,
    message_id: Optional[str] = None,
    chain_id: Optional[str] = None,
    request_id: Optional[str] = None,
    model: Optional[str] = None,
    mode: Optional[str] = None,
    agent_id: Optional[str] = None,
    agent_label: Optional[str] = None,
) -> Dict[str, Any]:
    response_id = str(chain_id or message_id or "").strip() or None
    conversation_id = str(session_id or "").strip() or None
    message_key = str(message_id or "").strip() or None
    chain_key = str(chain_id or "").strip() or None
    model_hint, mode_hint = _lookup_message_runtime_hints(
        conversation_id,
        message_key or chain_key,
    )
    request_key = str(request_id or _current_request_id() or "").strip() or None
    resolved_agent = (
        str(agent_id or chain_key or message_key or conversation_id or "").strip()
        or None
    )
    resolved_label = str(agent_label or "").strip() or None
    payload: Dict[str, Any] = {
        "conversation_id": conversation_id,
        "session_id": conversation_id,
        "message_id": message_key,
        "chain_id": chain_key,
        "response_id": response_id,
        "request_id": request_key,
        "model": (
            str(model).strip() if isinstance(model, str) and model.strip() else None
        )
        or model_hint,
        "mode": (str(mode).strip() if isinstance(mode, str) and mode.strip() else None)
        or mode_hint,
        "agent_id": resolved_agent,
        "agent_label": resolved_label,
    }
    return payload


def _record_sync_action(
    request: Request,
    *,
    name: str,
    summary: str,
    before_snapshot: Optional[Dict[str, Any]],
    after_snapshot: Optional[Dict[str, Any]],
    sections: List[str],
    args: Optional[Dict[str, Any]] = None,
    result: Any = None,
    batch_scope: Optional[Dict[str, Any]] = None,
) -> None:
    if not sections or before_snapshot is None or after_snapshot is None:
        return
    history = _get_action_history_service(request.app)
    if history is None:
        return
    try:
        history.record_snapshot_action(
            kind="sync",
            name=name,
            summary=summary,
            before_snapshot=before_snapshot,
            after_snapshot=after_snapshot,
            sections=sections,
            context=_build_action_context(
                request_id=_current_request_id(),
                agent_id="sync",
                agent_label="instance sync",
            ),
            args=args or {},
            result=result,
            batch_scope=batch_scope,
        )
    except Exception:
        logger.debug("Failed to record sync action", exc_info=True)


_AGENT_RESOURCE_TOKENIZER = None
_AGENT_RESOURCE_TOKENIZER_FAILED = False


def _get_agent_resource_tokenizer() -> Optional[CustomTokenizer]:
    global _AGENT_RESOURCE_TOKENIZER, _AGENT_RESOURCE_TOKENIZER_FAILED
    if _AGENT_RESOURCE_TOKENIZER_FAILED:
        return None
    if _AGENT_RESOURCE_TOKENIZER is None:
        try:
            _AGENT_RESOURCE_TOKENIZER = CustomTokenizer()
        except Exception:
            _AGENT_RESOURCE_TOKENIZER_FAILED = True
            return None
    return _AGENT_RESOURCE_TOKENIZER


def _estimate_token_count(text: Optional[str]) -> Optional[int]:
    if text is None:
        return None
    payload = str(text)
    if not payload:
        return 0
    tokenizer = _get_agent_resource_tokenizer()
    if tokenizer is None:
        return len(payload.split())
    try:
        return len(tokenizer.encode(payload))
    except Exception:
        return len(payload.split())


def _coerce_token_value(raw: Any) -> Optional[int]:
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _normalize_usage_counts(
    usage: Optional[Dict[str, Any]],
    prompt_text: str,
    response_text: str,
) -> Optional[Dict[str, Any]]:
    prompt_tokens = None
    completion_tokens = None
    total_tokens = None
    source = "estimate" if not usage else "provider"
    if isinstance(usage, dict):
        prompt_tokens = _coerce_token_value(
            usage.get("prompt_tokens") or usage.get("input_tokens")
        )
        completion_tokens = _coerce_token_value(
            usage.get("completion_tokens") or usage.get("output_tokens")
        )
        total_tokens = _coerce_token_value(
            usage.get("total_tokens") or usage.get("total")
        )
    if prompt_tokens is None:
        prompt_tokens = _estimate_token_count(prompt_text)
        if usage:
            source = "mixed"
    if completion_tokens is None:
        completion_tokens = _estimate_token_count(response_text)
        if usage:
            source = "mixed"
    if (
        total_tokens is None
        and prompt_tokens is not None
        and completion_tokens is not None
    ):
        total_tokens = prompt_tokens + completion_tokens
    if prompt_tokens is None and completion_tokens is None and total_tokens is None:
        return None
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "source": source,
    }


def _merge_usage(
    existing: Optional[Dict[str, Any]],
    normalized: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if normalized is None:
        return existing if isinstance(existing, dict) else None
    if not isinstance(existing, dict):
        return normalized
    merged = dict(existing)
    for key, value in normalized.items():
        if key not in merged or merged.get(key) is None:
            merged[key] = value
    if "source" not in merged:
        merged["source"] = normalized.get("source")
    return merged


def _reasoning_payload(
    thinking: Optional[Union[bool, str]]
) -> Optional[Dict[str, Any]]:
    if thinking is None:
        return None
    if isinstance(thinking, str):
        mode = thinking.strip().lower()
        if not mode or mode == "auto":
            return None
        if mode == "high":
            return {"effort": "high"}
        if mode == "low":
            return {"effort": "low"}
        return None
    # Responses API supports reasoning controls for GPT-5 / o-series models.
    return {"effort": "high" if thinking else "low"}


def _update_agent_resource_usage(
    app,
    agent_id: str,
    usage: Dict[str, Any],
    *,
    message_id: Optional[str] = None,
    session_id: Optional[str] = None,
    source: str = "chat",
) -> None:
    """Record per-agent token usage for resource tracking."""
    if not agent_id:
        return
    state = _ensure_agent_console_state(app)
    resources = state.setdefault("resources", {})
    if not isinstance(resources, dict):
        return
    entry = resources.get(agent_id)
    if not isinstance(entry, dict):
        entry = {
            "agent_id": agent_id,
            "prompt_tokens_total": 0,
            "completion_tokens_total": 0,
            "total_tokens": 0,
            "messages": 0,
        }
    prompt_tokens = _coerce_token_value(usage.get("prompt_tokens"))
    completion_tokens = _coerce_token_value(usage.get("completion_tokens"))
    if prompt_tokens is not None:
        entry["prompt_tokens_total"] = (
            int(entry.get("prompt_tokens_total") or 0) + prompt_tokens
        )
    if completion_tokens is not None:
        entry["completion_tokens_total"] = (
            int(entry.get("completion_tokens_total") or 0) + completion_tokens
        )
    if prompt_tokens is not None:
        entry["last_prompt_tokens"] = prompt_tokens
    if completion_tokens is not None:
        entry["last_completion_tokens"] = completion_tokens
    if prompt_tokens is not None or completion_tokens is not None:
        if prompt_tokens is None:
            prompt_tokens = int(entry.get("prompt_tokens_total") or 0)
        if completion_tokens is None:
            completion_tokens = int(entry.get("completion_tokens_total") or 0)
        entry["last_total_tokens"] = int(prompt_tokens or 0) + int(
            completion_tokens or 0
        )
    entry["last_source"] = usage.get("source") if isinstance(usage, dict) else None
    entry["total_tokens"] = int(entry.get("prompt_tokens_total") or 0) + int(
        entry.get("completion_tokens_total") or 0
    )
    entry["messages"] = int(entry.get("messages") or 0) + 1
    entry["last_message_id"] = message_id
    entry["session_id"] = session_id or agent_id
    entry["updated_at"] = float(time.time())
    entry["source"] = source
    # NOTE: This only tracks token usage for now. When sub-agent runtimes expose
    # CPU/GPU telemetry, merge it here and surface it via the same endpoints.
    resources[agent_id] = entry


def _rehydrate_pending_tool(app, request_id: str) -> dict[str, Any] | None:
    """Reconstruct a pending tool entry from the live console state.

    When the in-memory ``pending_tools`` registry is lost (e.g. after a reload)
    the UI may still surface the proposal from the console snapshot. This
    helper searches that snapshot for a matching tool event so approvals can
    continue to work without forcing the user to retry the entire interaction.
    """
    request_id_str = str(request_id)
    try:
        state = getattr(app.state, "agent_console_state", None)
    except AttributeError:
        return None
    if not isinstance(state, dict):
        return None
    agents = state.get("agents")
    if not isinstance(agents, dict):
        return None
    for record in agents.values():
        events = record.get("events")
        if not isinstance(events, list):
            continue
        for event in reversed(events):
            if not isinstance(event, dict):
                continue
            if event.get("type") != "tool":
                continue
            event_id = event.get("id") or event.get("request_id")
            if event_id is None or str(event_id) != request_id_str:
                continue
            status = (event.get("status") or "").lower() or "proposed"
            if status not in {"proposed", "pending"}:
                continue
            name = event.get("name")
            if not name:
                continue
            args = event.get("args") if isinstance(event.get("args"), dict) else {}
            session_id = event.get("session_id")
            message_id = event.get("message_id") or event.get("chain_id")
            chain_id = event.get("chain_id") or event.get("message_id")
            return {
                "id": str(event_id),
                "name": name,
                "args": args,
                "session_id": session_id,
                "message_id": message_id,
                "chain_id": chain_id,
                "status": status,
            }
    # Fallback: search persisted conversations for a matching tool event.
    try:
        conversation_ids = conversation_store.list_conversations()
    except Exception:
        conversation_ids = []
    # Iterate newest first so recent sessions are checked ahead of older logs.
    for conv_id in reversed(conversation_ids):
        try:
            messages = conversation_store.load_conversation(conv_id)
        except Exception:
            continue
        if not isinstance(messages, list):
            continue
        for message in messages:
            if not isinstance(message, dict):
                continue
            tools = message.get("tools")
            if not isinstance(tools, list):
                continue
            for tool in reversed(tools):
                if not isinstance(tool, dict):
                    continue
                event_id = tool.get("id") or tool.get("request_id")
                if event_id is None or str(event_id) != request_id_str:
                    continue
                status = (tool.get("status") or "").lower() or "proposed"
                if status not in {"proposed", "pending"}:
                    continue
                name = tool.get("name")
                if not name:
                    continue
                args = tool.get("args") if isinstance(tool.get("args"), dict) else {}
                # Conversation name is the session identifier.
                session_id = conv_id
                message_id = (
                    message.get("id")
                    or message.get("message_id")
                    or message.get("chain_id")
                )
                chain_id = message.get("chain_id") or message_id
                return {
                    "id": str(event_id),
                    "name": name,
                    "args": args,
                    "session_id": session_id,
                    "message_id": message_id,
                    "chain_id": chain_id,
                    "status": status,
                }
    return None


def _normalize_console_event(
    payload: dict[str, Any] | None, default_agent: str | None = None
) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    data = dict(payload)
    ts = data.get("timestamp")
    if not isinstance(ts, (int, float)):
        data["timestamp"] = float(time.time())
    else:
        data["timestamp"] = float(ts)
    agent_id = (
        data.get("agent_id")
        or data.get("chain_id")
        or data.get("message_id")
        or data.get("session_id")
        or default_agent
        or "orchestrator"
    )
    data["agent_id"] = agent_id
    data["agent_label"] = (
        data.get("agent_label")
        or data.get("agent_name")
        or data.get("name")
        or data.get("title")
        or agent_id
    )
    data["agent_status"] = data.get("agent_status") or data.get("status") or "active"
    return data


async def publish_console_event(
    app, payload: dict[str, Any], default_agent: str | None = None
) -> dict[str, Any] | None:
    event = _normalize_console_event(payload, default_agent=default_agent)
    if event is None:
        return None
    if event.get("request_id") is None:
        try:
            event["request_id"] = get_request_id()
        except Exception:
            pass

    broker: EventBroker | None = getattr(app.state, "thought_broker", None)

    state = _ensure_agent_console_state(app)
    agents: dict[str, dict[str, Any]] = state.setdefault("agents", {})
    resources = state.setdefault("resources", {})
    agent_id = event["agent_id"]
    record = agents.get(agent_id)
    if not isinstance(record, dict):
        record = {
            "id": agent_id,
            "label": event.get("agent_label"),
            "status": event.get("agent_status"),
            "summary": "",
            "updated_at": event.get("timestamp"),
            "events": [],
        }
        agents[agent_id] = record
    else:
        record["label"] = event.get("agent_label") or record.get("label")
        record["status"] = event.get("agent_status") or record.get("status")
    if isinstance(resources, dict) and agent_id in resources:
        record["resources"] = resources[agent_id]

    if event.get("type") == "thought":
        content = event.get("content")
        if isinstance(content, str) and content.strip():
            record["summary"] = content.strip()
    elif event.get("type") == "task":
        detail = event.get("content") or event.get("description")
        if isinstance(detail, str) and detail.strip():
            record["summary"] = detail.strip()

    record["updated_at"] = event.get("timestamp")
    history = record.setdefault("events", [])
    replaced_existing = False
    if isinstance(history, list) and event.get("type") == "tool":
        tool_identifier = event.get("id") or event.get("request_id")
        if tool_identifier is not None:
            tool_identifier = str(tool_identifier)
            for existing in reversed(history):
                if not isinstance(existing, dict):
                    continue
                existing_id = existing.get("id") or existing.get("request_id")
                if existing_id is None:
                    continue
                if str(existing_id) != tool_identifier:
                    continue
                for key, value in event.items():
                    if value is not None:
                        existing[key] = value
                replaced_existing = True
                break
    if isinstance(history, list) and not replaced_existing:
        history.append(event)
        if len(history) > _MAX_AGENT_HISTORY:
            del history[0 : len(history) - _MAX_AGENT_HISTORY]

    if broker is not None:
        try:
            await broker.publish(event)
        except Exception:
            pass
        return event

    # Fallback: legacy single-consumer queue (can drop events with multiple consumers).
    try:
        queue: asyncio.Queue = app.state.thought_queue  # type: ignore[attr-defined]
    except AttributeError:
        return event
    await queue.put(event)
    return event


def _append_conversation_entry(session_id: str, entry: Dict[str, Any]) -> None:
    """Append a conversation entry to the on-disk history (best-effort)."""
    try:
        existing = conversation_store.load_conversation(session_id)
        if not isinstance(existing, list):
            existing = []
        existing.append(entry)
        conversation_store.save_conversation(session_id, existing)
    except Exception:
        # Persistence issues should never surface to the primary chat flow.
        pass


def _update_conversation_entry(
    session_id: str,
    message_id: str,
    updates: Dict[str, Any],
) -> None:
    """Merge updates into a stored conversation entry (best-effort)."""
    try:
        conv = conversation_store.load_conversation(session_id)
        if not isinstance(conv, list):
            return
        target = None
        for item in conv:
            if isinstance(item, dict) and item.get("id") == message_id:
                target = item
                break
        if target is None:
            return
        for key, value in updates.items():
            if value is None:
                continue
            if key == "metadata" and isinstance(value, dict):
                existing_meta = target.get("metadata")
                if isinstance(existing_meta, dict):
                    existing_meta.update(value)
                else:
                    target["metadata"] = dict(value)
            elif key == "thought_trace" and isinstance(value, list):
                target["thought_trace"] = value
            else:
                target[key] = value
        conversation_store.save_conversation(session_id, conv)
    except Exception:
        pass


def _append_tool_event_to_conversation(
    session_id: str,
    message_id: str,
    name: str,
    args: Dict[str, Any] | None,
    result: Any | None,
    status: str,
    request_id: str | None = None,
    model: str | None = None,
    mode: str | None = None,
) -> None:
    """Append a tool event to a message in the saved conversation.

    Best-effort: if the conversation or message cannot be found, this function
    returns silently. Adds/creates a ``tools`` array on the message.
    """
    try:
        conv = conversation_store.load_conversation(session_id)
        if not isinstance(conv, list):
            return
        target = None
        for m in conv:
            if isinstance(m, dict) and m.get("id") == message_id:
                target = m
                break
        if not target:
            return
        tools = target.get("tools")
        if not isinstance(tools, list):
            tools = []
            target["tools"] = tools
        entry: Dict[str, Any] | None = None
        if request_id:
            for item in tools:
                if isinstance(item, dict) and item.get("id") == request_id:
                    entry = item
                    break
        if entry is None:
            entry = {"id": request_id} if request_id else {}
            tools.append(entry)
        entry.update(
            {
                "name": name,
                "args": args or {},
                "result": result,
                "status": status,
                "timestamp": time.time(),
            }
        )
        if model:
            entry["model"] = model
        if mode:
            entry["mode"] = mode
        conversation_store.save_conversation(session_id, conv)
    except Exception:
        # Don't let audit persistence affect primary flow
        pass


def _normalize_tool_name(name: Any) -> str:
    if isinstance(name, str):
        return name.strip()
    try:
        return str(name or "").strip()
    except Exception:
        return ""


def _normalize_tool_args_for_proposal(name: str, args: Any) -> Dict[str, Any]:
    candidate = args if isinstance(args, dict) else {}
    if not name:
        return sanitize_args(candidate)
    try:
        return normalize_tool_args(name, candidate)
    except Exception:
        return sanitize_args(candidate)


def _tool_signature(name: str, args: Dict[str, Any]) -> Optional[str]:
    normalized_name = _normalize_tool_name(name)
    if not normalized_name:
        return None
    try:
        args_key = json.dumps(args or {}, sort_keys=True, ensure_ascii=False)
    except Exception:
        args_key = str(args or {})
    return f"{normalized_name}:{args_key}"


def _tool_hint_from_args(args: Dict[str, Any]) -> Optional[str]:
    if not isinstance(args, dict):
        return None
    for key in ("path", "key", "query", "url", "file", "filename"):
        value = args.get(key)
        if not isinstance(value, str):
            continue
        cleaned = value.strip()
        if not cleaned:
            continue
        if len(cleaned) > 56:
            cleaned = cleaned[:53].rstrip() + "..."
        return cleaned
    return None


def _tool_descriptor_for_placeholder(tool: Dict[str, Any]) -> Optional[str]:
    if not isinstance(tool, dict):
        return None
    name = _normalize_tool_name(tool.get("name")) or "tool"
    args = tool.get("args") if isinstance(tool.get("args"), dict) else {}
    hint = _tool_hint_from_args(args)
    return f"{name} ({hint})" if hint else name


def _pending_tool_placeholder_text(tools_used: Any) -> str:
    descriptors: list[str] = []
    seen: set[str] = set()
    if isinstance(tools_used, list):
        for tool in tools_used:
            if not isinstance(tool, dict):
                continue
            descriptor = _tool_descriptor_for_placeholder(tool)
            if not descriptor or descriptor in seen:
                continue
            seen.add(descriptor)
            descriptors.append(descriptor)
    if not descriptors:
        return "Requested tool. Awaiting approval."
    noun = "tool" if len(descriptors) == 1 else "tools"
    return f"Requested {noun} " + ", ".join(descriptors) + ". Awaiting approval."


def _existing_tool_signatures_for_message(
    app, session_id: str | None, message_id: str | None
) -> set[str]:
    signatures: set[str] = set()
    normalized_session = str(session_id or "").strip()
    normalized_message = str(message_id or "").strip()
    if not normalized_session or not normalized_message:
        return signatures

    registry: dict | None = getattr(app.state, "pending_tools", None)
    if isinstance(registry, dict):
        for rec in registry.values():
            if not isinstance(rec, dict):
                continue
            rec_session = str(rec.get("session_id") or "").strip()
            rec_message = str(
                rec.get("message_id") or rec.get("chain_id") or ""
            ).strip()
            if rec_session != normalized_session or rec_message != normalized_message:
                continue
            status = str(rec.get("status") or "proposed").strip().lower()
            if status in {"denied", "error", "cancelled", "timeout"}:
                continue
            rec_name = _normalize_tool_name(rec.get("name"))
            rec_args = rec.get("args") if isinstance(rec.get("args"), dict) else {}
            signature = _tool_signature(rec_name, rec_args)
            if signature:
                signatures.add(signature)

    try:
        conv = conversation_store.load_conversation(normalized_session)
    except Exception:
        conv = []
    if not isinstance(conv, list):
        return signatures
    for message in conv:
        if not isinstance(message, dict):
            continue
        if str(message.get("id") or "").strip() != normalized_message:
            continue
        for tool in message.get("tools") or []:
            if not isinstance(tool, dict):
                continue
            status = str(tool.get("status") or "").strip().lower()
            if status in {"denied", "error", "cancelled", "timeout"}:
                continue
            tool_name = _normalize_tool_name(tool.get("name"))
            tool_args = tool.get("args") if isinstance(tool.get("args"), dict) else {}
            signature = _tool_signature(tool_name, tool_args)
            if signature:
                signatures.add(signature)
        break
    return signatures


async def _register_tool_proposals(
    request: Request,
    *,
    tools: Any,
    session_id: str | None,
    message_id: str | None,
    model: str | None,
    mode: str | None,
    default_agent: str | None,
) -> list[dict[str, Any]]:
    if not isinstance(tools, list) or not tools:
        return []

    registry: dict | None = getattr(request.app.state, "pending_tools", None)
    if registry is None:
        registry = {}
        setattr(request.app.state, "pending_tools", registry)

    emitted: list[dict[str, Any]] = []
    known_signatures = _existing_tool_signatures_for_message(
        request.app, session_id, message_id
    )

    for tool in tools:
        if not isinstance(tool, dict):
            continue
        tool_name = _normalize_tool_name(tool.get("name"))
        if not tool_name:
            continue
        tool_args = _normalize_tool_args_for_proposal(tool_name, tool.get("args"))
        signature = _tool_signature(tool_name, tool_args)
        if signature and signature in known_signatures:
            logger.info(
                "Suppressed duplicate tool proposal %s for session=%s message=%s",
                tool_name,
                session_id,
                message_id,
            )
            continue

        proposal_id = str(uuid4())
        record = {
            "id": proposal_id,
            "name": tool_name,
            "args": tool_args,
            "session_id": session_id,
            "message_id": message_id,
            "chain_id": message_id or session_id,
            "model": model,
            "mode": mode,
            "status": "proposed",
        }
        registry[proposal_id] = record
        if signature:
            known_signatures.add(signature)

        tool_payload = dict(tool)
        tool_payload["id"] = proposal_id
        tool_payload["name"] = tool_name
        tool_payload["args"] = tool_args
        tool_payload["status"] = "proposed"
        emitted.append(tool_payload)

        try:
            if session_id and message_id:
                _append_tool_event_to_conversation(
                    session_id,
                    message_id,
                    tool_name,
                    tool_args,
                    None,
                    status="proposed",
                    model=model,
                    mode=mode,
                    request_id=proposal_id,
                )
        except Exception:
            pass
        try:
            await publish_console_event(
                request.app,
                {
                    "type": "tool",
                    "id": proposal_id,
                    "name": tool_name,
                    "args": tool_args,
                    "result": None,
                    "chain_id": message_id or session_id,
                    "message_id": message_id,
                    "status": "proposed",
                    "session_id": session_id,
                    "model": model,
                    "mode": mode,
                },
                default_agent=default_agent,
            )
        except Exception:
            pass
        log_tool_event(
            session_id,
            tool_name,
            "proposed",
            args=tool_args,
            message_id=message_id,
            request_id=proposal_id,
        )
        _emit_tool_hook(
            tool_name,
            "proposed",
            args=tool_args,
            session_id=session_id,
            message_id=message_id,
            request_id=proposal_id,
        )
    _emit_tool_resolution_notification(request.app, proposals=emitted)
    return emitted


def _tool_resolution_notifications_enabled() -> bool:
    try:
        settings_payload = user_settings.load_settings()
    except Exception:
        return False
    if not bool(settings_payload.get("tool_resolution_notifications", True)):
        return False
    approval_level = (
        str(settings_payload.get("approval_level") or "all").strip().lower()
    )
    return approval_level != "auto"


def _emit_tool_resolution_notification(
    app,
    *,
    proposals: list[dict[str, Any]] | None,
) -> None:
    if not proposals or not _tool_resolution_notifications_enabled():
        return
    names = [
        str(item.get("name") or "").strip()
        for item in proposals
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]
    if not names:
        return
    if len(names) == 1:
        title = "Tool review needed"
        body = f"{names[0]} is waiting for your review."
    else:
        preview = ", ".join(names[:2])
        if len(names) > 2:
            preview = f"{preview}, +{len(names) - 2} more"
        title = "Tools need review"
        body = f"{len(names)} proposed tools are waiting for review: {preview}."
    first = proposals[0] if proposals and isinstance(proposals[0], dict) else {}
    data = {
        "action_url": "/",
        "tool_ids": [
            str(item.get("id") or item.get("request_id") or "").strip()
            for item in proposals
            if isinstance(item, dict)
            and str(item.get("id") or item.get("request_id") or "").strip()
        ],
        "tool_names": names,
        "session_id": first.get("session_id"),
        "message_id": first.get("message_id"),
        "chain_id": first.get("chain_id") or first.get("message_id"),
    }
    emit_notification(
        app,
        title=title,
        body=body,
        category="tool_resolution",
        data=data,
    )


def _tool_outcome_payload(
    status: str,
    message: str | None = None,
    data: Any | None = None,
    ok: bool | None = None,
) -> dict[str, Any]:
    """Return a structured tool outcome wrapper."""
    # Preserve raw tool output in `data` without coercion.
    resolved_ok = ok if ok is not None else status.lower() not in {"error", "denied"}
    return {
        "status": status,
        "ok": bool(resolved_ok),
        "message": message,
        "data": data,
    }


TEST_PROMPTS_DIR = Path(__file__).resolve().parent / "tests" / "prompts"

# ---------------------------
# Helpers for model integrity
# ---------------------------


def _sha256_file(path: Path) -> str:
    """Compute SHA-256 for a file in streaming fashion."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _remote_manifest(
    repo_id: str,
    allow_patterns: Optional[list[str]] = None,
    token: Optional[str] = None,
) -> tuple[list[dict], int, str | None]:
    """Return (manifest, total_bytes, commit_sha) for a HF repo.

    Manifest entries are dicts: {"path": str, "size": int, "sha256": Optional[str]}.
    """
    # Lazy import to avoid heavy hub import on startup
    from huggingface_hub import HfApi

    api = HfApi(token=token) if token else HfApi()
    # Request file metadata so sibling sizes are populated (HF defaults to size=None).
    info = api.model_info(repo_id, files_metadata=True)
    manifest: list[dict] = []
    total = 0
    for s in getattr(info, "siblings", []) or []:
        # attribute names vary across hub versions; be defensive
        path = getattr(s, "rfilename", None) or getattr(s, "path", None)
        if path is not None:
            path = str(path)
            if not _path_matches_any(path, allow_patterns):
                continue
        size = getattr(s, "size", None)
        sha256 = None
        # Try LFS metadata first
        try:
            lfs = getattr(s, "lfs", None)
            if isinstance(lfs, dict):
                sha256 = lfs.get("sha256") or lfs.get("oid")
        except Exception:
            sha256 = None
        # Fallback to generic sha field if present
        if not sha256:
            sha = getattr(s, "sha", None)
            if isinstance(sha, str) and len(sha) in (40, 64):
                sha256 = sha if len(sha) == 64 else None
        if path is None:
            continue
        entry = {
            "path": str(path),
            "size": int(size or 0),
            "sha256": sha256,
        }
        manifest.append(entry)
        total += int(size or 0)
    commit = getattr(info, "sha", None)
    return manifest, int(total), commit


def _path_matches_any(rel_posix: str, patterns: Optional[list[str]]) -> bool:
    if not patterns:
        return True
    return any(fnmatch(rel_posix, pat) for pat in patterns)


def _folder_size_bytes(
    root: Path, *, include_patterns: Optional[list[str]] = None
) -> int:
    try:
        if root.is_file():
            return int(root.stat().st_size)
    except Exception:
        return 0
    total = 0
    for p in root.rglob("*"):
        try:
            if p.is_file():
                if ".cache" in p.parts:
                    continue
                name = p.name.lower()
                if (
                    name.endswith(".incomplete")
                    or name.endswith(".lock")
                    or name.endswith(".metadata")
                ):
                    continue
                rel = p.relative_to(root).as_posix()
                if not _path_matches_any(rel, include_patterns):
                    continue
                total += p.stat().st_size
        except Exception:
            continue
    return total


def _count_local_files(root: Path) -> int:
    try:
        if root.is_file():
            return 1
    except Exception:
        return 0
    count = 0
    for p in root.rglob("*"):
        try:
            if p.is_file():
                count += 1
        except Exception:
            continue
    return count


def _fallback_verification_from_job(
    request: Request,
    model_name: str,
    local_dir: Optional[Path],
    installed: int,
) -> Optional[dict]:
    if local_dir is None:
        return None
    try:
        jobs = _get_jobs_state(request.app)
    except Exception:
        return None
    for job in jobs.values():
        if job.get("model") != model_name:
            continue
        try:
            _refresh_job_status(job)
        except Exception:
            continue
        if job.get("status") != "completed":
            continue
        guessed_total = int(job.get("total") or 0)
        if guessed_total <= 0:
            guessed_total = int(installed)
        checked = _count_local_files(local_dir)
        verified = checked > 0 and installed > 0
        return {
            "exists": True,
            "verified": verified,
            "expected_bytes": guessed_total,
            "installed_bytes": int(installed),
            "checked_files": int(checked),
        }
    return None


def _naive_local_verification(
    local_dir: Optional[Path],
    installed: int,
) -> Optional[dict]:
    if local_dir is None or installed <= 0:
        return None
    checked = _count_local_files(local_dir)
    if checked <= 0:
        return None
    return {
        "exists": True,
        "verified": True,
        "expected_bytes": int(installed),
        "installed_bytes": int(installed),
        "checked_files": int(checked),
    }


def _last_user_message(conversation):
    if conversation and isinstance(conversation[0], list):
        messages = [msg for turn in conversation for msg in turn]
    else:
        messages = conversation
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return msg.get("content", "")
    return ""


def _require_scope(request: Request, scope: str) -> Dict[str, Any]:
    """Validate Authorization bearer token for a required device scope."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = auth.split(" ", 1)[1]
    secret = os.getenv("DEVICE_JWT_SECRET", "dev-secret-change-me")
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")
    scopes = payload.get("scopes", [])
    if scope not in scopes:
        raise HTTPException(status_code=403, detail="Insufficient scope")
    return payload


def _optional_device_claims(
    request: Request, scope: str = "sync"
) -> Optional[Dict[str, Any]]:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    return _require_scope(request, scope)


def _get_or_create_device_public_key() -> str:
    settings = user_settings.load_settings()
    existing = str(settings.get("device_public_key") or "").strip()
    if existing:
        return existing
    generated = str(uuid4())
    user_settings.save_settings({"device_public_key": generated})
    return generated


def _is_fatal_base_exception(exc: BaseException) -> bool:
    return isinstance(exc, (KeyboardInterrupt, SystemExit))


def _get_rag_service():
    try:
        service = _get_cached_rag_service()
    except BaseException as exc:
        if _is_fatal_base_exception(exc):
            raise
        logger.error("RAG provider runtime failure: %s", exc)
        raise HTTPException(status_code=503, detail=f"RAG runtime failed: {exc}")
    if not service:
        raise HTTPException(status_code=503, detail="RAG service unavailable")
    return service


def _get_clip_rag_service(*, raise_http: bool = True):
    """Return the CLIP index service (optional; may be unavailable)."""
    try:
        service = _get_cached_clip_rag_service(raise_http=raise_http)
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
    if not service and raise_http:
        raise HTTPException(
            status_code=503,
            detail="CLIP RAG service unavailable",
        )
    return service


def _persist_calendar_event(event_id: str, event_payload: dict) -> None:
    calendar_store.save_event(event_id, event_payload)
    try:
        _ingest_calendar_event(event_id, event_payload)
    except Exception:
        pass


_CALENDAR_STATUS_ALIASES = {
    "pending": "pending",
    "proposed": "scheduled",
    "scheduled": "scheduled",
    "prompted": "prompted",
    "acknowledge": "acknowledged",
    "acknowledged": "acknowledged",
    "complete": "acknowledged",
    "completed": "acknowledged",
    "done": "acknowledged",
    "skip": "skipped",
    "skipped": "skipped",
}


def _normalize_calendar_status(value: Any, *, default: str = "pending") -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return default
    return _CALENDAR_STATUS_ALIASES.get(raw, raw)


def _emit_tool_hook(
    name: str,
    status: str,
    *,
    args: Dict[str, Any] | None = None,
    result: Any | None = None,
    session_id: Optional[str] = None,
    message_id: Optional[str] = None,
    request_id: Optional[str] = None,
) -> None:
    event = hooks.ToolInvocationEvent(
        name=name,
        status=status,
        args=dict(args or {}),
        result=result,
        session_id=session_id,
        message_id=message_id,
        request_id=request_id,
    )
    try:
        hooks.emit(hooks.TOOL_EVENT, event)
    except Exception:
        logger.debug("tool hook emit failed", exc_info=True)


# Lightweight API health for /api prefix
@router.get("/health")
async def api_health():
    return {"status": "healthy"}


@router.get("/mcp/status")
async def mcp_status(request: Request):
    """Return MCP endpoint info and a quick reachability hint."""

    state = getattr(request.app, "state", object())

    cfg = getattr(state, "config", {}) or {}

    url = cfg.get("mcp_url") or os.getenv("MCP_SERVER_URL") or None

    provider = getattr(state, "mcp_provider", "unknown")

    reachable = True if url is None else False

    if url:
        try:
            parsed = urlparse(url)

            host = parsed.hostname

            port = parsed.port or (443 if parsed.scheme == "https" else 80)

            if host and port:
                with socket.create_connection((host, port), timeout=0.5):
                    reachable = True

        except Exception:
            reachable = False

    return {"url": url, "reachable": reachable, "provider": provider}


@router.get("/celery/status")
async def celery_status():
    """Return quick Celery worker status and lightweight queue stats.

    Uses a short timeout to avoid blocking the API when the broker is down.
    """
    try:
        from app.tasks import celery_app  # lazy import
    except Exception:
        return {"online": False, "workers": [], "details": {}}

    status: Dict[str, Any] = {"online": False, "workers": [], "details": {}}
    try:
        # Ping workers (broadcast); returns a list of {worker: {'ok': 'pong'}}
        try:
            replies = await asyncio.wait_for(
                asyncio.to_thread(celery_app.control.ping, timeout=0.5),
                timeout=1.0,
            )
        except asyncio.TimeoutError:
            status["timeout"] = True
            replies = []
        workers: list[str] = []
        if isinstance(replies, list):
            for entry in replies:
                if isinstance(entry, dict):
                    workers.extend(list(entry.keys()))
        status["workers"] = workers
        status["online"] = len(workers) > 0
        status.setdefault("details", {})
        if status["online"]:
            # Only query expensive inspect endpoints when at least one worker replied
            try:
                insp = celery_app.control.inspect(timeout=0.5)
                active = insp.active() or {}
                scheduled = insp.scheduled() or {}
                reserved = insp.reserved() or {}

                def _lens(d):
                    return (
                        {k: len(v or []) for k, v in d.items()}
                        if isinstance(d, dict)
                        else {}
                    )

                status["details"] = {
                    "active": _lens(active),
                    "scheduled": _lens(scheduled),
                    "reserved": _lens(reserved),
                }
            except Exception:
                pass
    except Exception:
        # broker down or no workers listening
        status["online"] = False
    return status


def _task_summary(t: dict, *, state: str | None = None) -> dict:
    """Return a compact summary for a Celery task dict from inspect()."""
    import hashlib

    name = t.get("name") or t.get("request", {}).get("name")
    tid = t.get("id") or t.get("uuid") or t.get("request", {}).get("id")
    args = t.get("args") or t.get("request", {}).get("args")
    kwargs = t.get("kwargs") or t.get("request", {}).get("kwargs")
    # Normalize args to string then hash for compactness
    try:
        norm = str(args)[:512] + str(kwargs)[:512]
        arg_hash = hashlib.sha256(norm.encode("utf-8", errors="ignore")).hexdigest()[
            :12
        ]
    except Exception:
        arg_hash = None
    eta = t.get("eta") or t.get("request", {}).get("eta")
    time_start = (
        t.get("time_start")
        or t.get("started")
        or t.get("request", {}).get("time_start")
    )

    # Convert eta/time_start to float timestamp when possible
    def _to_ts(v):
        try:
            if v is None:
                return None
            if isinstance(v, (int, float)):
                return float(v)
            # Celery sometimes uses ISO strings for eta
            from datetime import datetime

            return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
        except Exception:
            return None

    return {
        "id": tid,
        "name": name,
        "args_hash": arg_hash,
        "eta": _to_ts(eta),
        "time_start": _to_ts(time_start),
        "state": state,
        "agent_id": tid,
    }


@router.get("/celery/tasks")
async def celery_tasks(state: Optional[str] = "active", limit: int = 50) -> dict:
    """List Celery tasks by state: active, scheduled, reserved, or all.

    Returns a compact list of entries: [{worker, id, name, args_hash, eta, time_start, state}]
    """
    try:
        from app.tasks import celery_app  # lazy import
    except Exception:
        return {"tasks": [], "state": state or "active"}

    def _collect() -> list[dict]:
        insp = celery_app.control.inspect(timeout=0.5)
        rows: list[dict] = []

        def add(group: dict | None, tag: str):
            if not isinstance(group, dict):
                return
            for worker, items in group.items():
                for t in (items or [])[: max(0, limit - len(rows))]:
                    rows.append(
                        {
                            "worker": worker,
                            **_task_summary(t, state=tag),
                        }
                    )

        st = (state or "active").lower()
        if st in ("active", "all"):
            add(insp.active(), "active")
        if st in ("scheduled", "all"):
            add(insp.scheduled(), "scheduled")
        if st in ("reserved", "all"):
            add(insp.reserved(), "reserved")
        return rows

    try:
        rows = await asyncio.to_thread(_collect)
    except Exception:
        rows = []
    return {"tasks": rows, "state": state or "active"}


class CeleryRevokePayload(BaseModel):
    terminate: bool = True
    signal: Optional[str] = None


@router.post("/celery/tasks/{task_id}/revoke")
async def celery_revoke(task_id: str, payload: CeleryRevokePayload) -> dict:
    """Request task revoke; optionally terminate a running task.

    Payload: { terminate: bool = true, signal?: str }
    """
    try:
        from app.tasks import celery_app  # lazy import
    except Exception:
        raise HTTPException(status_code=503, detail="Celery not configured")
    try:
        # control.revoke is synchronous; run in thread to avoid blocking
        await asyncio.to_thread(
            celery_app.control.revoke,
            task_id,
            terminate=bool(payload.terminate),
            signal=payload.signal,
        )
        return {"status": "sent", "task_id": task_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Revoke failed: {e}")


def _require_confirm_for_action(confirm: Optional[bool]) -> None:
    """Honor approval settings: if not auto, require explicit confirm."""
    try:
        settings = user_settings.load_settings()
        approval = str(settings.get("approval_level", "all")).lower()
    except Exception:
        approval = "all"
    if approval != "auto" and not bool(confirm):
        # 412 signals that a precondition (confirm) is required
        raise HTTPException(
            status_code=412, detail="Confirmation required for this action"
        )


class CeleryRetryByIdPayload(BaseModel):
    confirm: Optional[bool] = None


@router.post("/celery/tasks/{task_id}/retry")
async def celery_retry_by_id(task_id: str, payload: CeleryRetryByIdPayload) -> dict:
    """Retry a Celery task by inspecting current queues for args/kwargs.

    Best-effort: works for tasks visible in active/reserved/scheduled sets.
    For historical failures not in inspect() output, use /api/celery/retry.
    """
    _require_confirm_for_action(payload.confirm)
    try:
        from app.tasks import celery_app  # lazy import
    except Exception:
        raise HTTPException(status_code=503, detail="Celery not configured")

    def _find_task() -> dict | None:
        insp = celery_app.control.inspect(timeout=0.5)
        for getter in (insp.active, insp.reserved, insp.scheduled):
            try:
                group = getter() or {}
                for worker, items in group.items():
                    for t in items or []:
                        tid = (
                            t.get("id")
                            or t.get("uuid")
                            or t.get("request", {}).get("id")
                        )
                        if tid == task_id:
                            return t
            except Exception:
                continue
        return None

    try:
        src = await asyncio.to_thread(_find_task)
    except Exception:
        src = None
    if not src:
        raise HTTPException(
            status_code=404, detail="Task not found or metadata unavailable"
        )
    name = src.get("name") or src.get("request", {}).get("name")
    args = src.get("args") or src.get("request", {}).get("args") or []
    kwargs = src.get("kwargs") or src.get("request", {}).get("kwargs") or {}
    if not name:
        raise HTTPException(status_code=404, detail="Task name unavailable for retry")
    try:
        # send a new task instance
        new_id = await asyncio.to_thread(
            celery_app.send_task, name, args=args, kwargs=kwargs
        )
        # send_task returns AsyncResult in newer Celery; normalize to id
        new_task_id = (
            getattr(new_id, "id", None)
            or getattr(new_id, "task_id", None)
            or str(new_id)
        )
        return {"status": "queued", "task_id": new_task_id, "name": name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Retry failed: {e}")


class CeleryRetryPayload(BaseModel):
    name: str
    args: Optional[list] = None
    kwargs: Optional[dict] = None
    confirm: Optional[bool] = None


@router.post("/celery/retry")
async def celery_retry(payload: CeleryRetryPayload) -> dict:
    """Retry/queue a task by name with optional args/kwargs."""
    _require_confirm_for_action(payload.confirm)
    try:
        from app.tasks import celery_app  # lazy import
    except Exception:
        raise HTTPException(status_code=503, detail="Celery not configured")
    try:
        res = await asyncio.to_thread(
            celery_app.send_task,
            payload.name,
            args=payload.args or [],
            kwargs=payload.kwargs or {},
        )
        task_id = getattr(res, "id", None) or getattr(res, "task_id", None) or str(res)
        return {"status": "queued", "task_id": task_id, "name": payload.name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Queue failed: {e}")


class CeleryPurgePayload(BaseModel):
    queue: Optional[str] = None
    terminate_active: bool = False
    include_reserved: bool = True
    include_scheduled: bool = True
    confirm: Optional[bool] = None


@router.post("/celery/purge")
async def celery_purge(payload: CeleryPurgePayload) -> dict:
    """Purge pending tasks from broker queues and optionally revoke running ones.

    If no queue is provided, attempts to purge all configured queues (default 'celery').
    """
    _require_confirm_for_action(payload.confirm)
    try:
        from app.tasks import celery_app, broker_url  # lazy import
    except Exception:
        raise HTTPException(status_code=503, detail="Celery not configured")

    purged_total = 0
    queues: list[str] = []
    # Collect queue names
    try:
        if payload.queue:
            queues = [payload.queue]
        else:
            # Celery config may define task_queues as kombu.Queue objects
            qconf = celery_app.conf.task_queues
            if qconf:
                try:
                    queues = [getattr(q, "name", str(q)) for q in qconf]
                except Exception:
                    queues = []
            if not queues:
                queues = [celery_app.conf.task_default_queue or "celery"]
    except Exception:
        queues = ["celery"]

    # Purge via kombu when available
    try:
        from kombu import Connection, Queue as KQueue  # type: ignore

        def _purge_all() -> int:
            total = 0
            with Connection(broker_url) as conn:
                for name in queues:
                    try:
                        q = KQueue(name)
                        q = q.bind(conn)
                        total += int(q.purge() or 0)
                    except Exception:
                        continue
            return total

        purged_total = await asyncio.to_thread(_purge_all)
    except Exception:
        # Fallback: try Celery app.control.purge() if present (purges default)
        try:
            purged_total = int(
                await asyncio.to_thread(getattr(celery_app.control, "purge"))
            )
        except Exception:
            purged_total = 0

    revoked_active = 0
    revoked_reserved = 0
    revoked_scheduled = 0
    # Optionally revoke running and queued (reserved/scheduled) tasks
    try:
        insp = celery_app.control.inspect(timeout=0.5)
        if payload.terminate_active:
            active = await asyncio.to_thread(insp.active)
            for worker, items in (active or {}).items():
                for t in items or []:
                    tid = t.get("id") or t.get("uuid") or t.get("request", {}).get("id")
                    if tid:
                        try:
                            await asyncio.to_thread(
                                celery_app.control.revoke, tid, terminate=True
                            )
                            revoked_active += 1
                        except Exception:
                            continue
        if payload.include_reserved:
            reserved = await asyncio.to_thread(insp.reserved)
            for worker, items in (reserved or {}).items():
                for t in items or []:
                    tid = t.get("id") or t.get("uuid") or t.get("request", {}).get("id")
                    if tid:
                        try:
                            await asyncio.to_thread(
                                celery_app.control.revoke, tid, terminate=False
                            )
                            revoked_reserved += 1
                        except Exception:
                            continue
        if payload.include_scheduled:
            scheduled = await asyncio.to_thread(insp.scheduled)
            for worker, items in (scheduled or {}).items():
                for t in items or []:
                    # scheduled entries may store task under 'request'
                    req = t.get("request", {}) if isinstance(t, dict) else {}
                    tid = req.get("id") or t.get("id")
                    if tid:
                        try:
                            await asyncio.to_thread(
                                celery_app.control.revoke, tid, terminate=False
                            )
                            revoked_scheduled += 1
                        except Exception:
                            continue
    except Exception:
        pass

    return {
        "status": "ok",
        "queues": queues,
        "purged": purged_total,
        "revoked": {
            "active": revoked_active,
            "reserved": revoked_reserved,
            "scheduled": revoked_scheduled,
        },
    }


@router.get("/celery/failures")
async def celery_failures(limit: int = 50) -> dict:
    """Return recent Celery task failures from JSONL log (best-effort)."""
    path = Path(__file__).resolve().parents[2] / "logs" / "celery_failures.jsonl"
    out: list[dict] = []
    try:
        if path.exists():
            # Efficient tail using deque
            from collections import deque

            q = deque(maxlen=max(1, min(limit, 500)))
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    q.append(line)
            for line in q:
                try:
                    rec = json.loads(line.strip())
                    out.append(rec)
                except Exception:
                    continue
            # Return newest last; invert to newest first
            out.reverse()
    except Exception:
        out = []
    return {"failures": out[:limit]}


# Maximum file size allowed for uploads (8MB)
MAX_UPLOAD_SIZE = 8 * 1024 * 1024
# Acceptable MIME types for uploaded files
ALLOWED_UPLOAD_TYPES = {
    "text/plain",
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "audio/mpeg",
    "audio/wav",
    # add common web video types for chat attachments
    "video/mp4",
    "video/webm",
}


@router.get("/stream/thoughts")
async def stream_thoughts(request: Request):
    """Server-Sent Events stream of thoughts and tool logs."""
    broker: EventBroker | None = getattr(request.app.state, "thought_broker", None)
    if broker is None:
        queue: asyncio.Queue = request.app.state.thought_queue

        async def generator():
            try:
                while True:
                    if await request.is_disconnected():
                        logger.info("SSE client disconnected")
                        break
                    event = await queue.get()
                    enriched = {**event, "request_id": get_request_id()}
                    sse_events_total.labels(enriched.get("type", "unknown")).inc()
                    yield "event: delta\n" + f"data: {json.dumps(enriched)}\n\n"
            except asyncio.CancelledError:
                logger.info("SSE stream cancelled")
            except Exception:
                logger.exception("Error in SSE stream")

        return StreamingResponse(generator(), media_type="text/event-stream")

    def _parse_last_event_id() -> int | None:
        raw = request.headers.get("Last-Event-ID") or request.query_params.get("since")
        if not raw:
            return None
        try:
            return int(str(raw).strip())
        except Exception:
            return None

    last_event_id = _parse_last_event_id()
    subscriber, backlog = await broker.subscribe(since=last_event_id)

    async def generator():
        try:
            for item in backlog:
                payload = dict(item.event)
                payload.setdefault("stream_id", item.seq)
                sse_events_total.labels(payload.get("type", "unknown")).inc()
                yield (
                    f"id: {item.seq}\n"
                    "event: delta\n"
                    f"data: {json.dumps(payload)}\n\n"
                )
            while True:
                if await request.is_disconnected():
                    logger.info("SSE client disconnected")
                    break
                try:
                    item: BrokerEvent = await asyncio.wait_for(
                        subscriber.get(), timeout=15
                    )
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                payload = dict(item.event)
                payload.setdefault("stream_id", item.seq)
                sse_events_total.labels(payload.get("type", "unknown")).inc()
                yield (
                    f"id: {item.seq}\n"
                    "event: delta\n"
                    f"data: {json.dumps(payload)}\n\n"
                )
        except asyncio.CancelledError:
            logger.info("SSE stream cancelled")
        except Exception:
            logger.exception("Error in SSE stream")
        finally:
            try:
                await broker.unsubscribe(subscriber)
            except Exception:
                pass

    return StreamingResponse(generator(), media_type="text/event-stream")


@router.websocket("/ws/thoughts")
async def ws_thoughts(websocket: WebSocket):
    """WebSocket stream of thoughts and tool logs."""
    await websocket.accept()

    broker: EventBroker | None = getattr(websocket.app.state, "thought_broker", None)
    keepalive_seconds = 15.0
    try:
        raw_keepalive = getattr(websocket.app.state, "config", {}).get(
            "thought_ws_keepalive_seconds"
        )
        if raw_keepalive is not None:
            keepalive_seconds = float(raw_keepalive)
    except Exception:
        keepalive_seconds = 15.0
    if broker is None:
        queue: asyncio.Queue = websocket.app.state.thought_queue
        try:
            while True:
                if keepalive_seconds > 0:
                    try:
                        event = await asyncio.wait_for(
                            queue.get(), timeout=keepalive_seconds
                        )
                    except asyncio.TimeoutError:
                        await websocket.send_text(
                            json.dumps({"type": "keepalive", "timestamp": time.time()})
                        )
                        continue
                else:
                    event = await queue.get()
                enriched = {**event, "request_id": get_request_id()}
                await websocket.send_text(json.dumps(enriched))
        except WebSocketDisconnect:
            logger.info("Thoughts websocket disconnected")
        except Exception:
            logger.exception("Error in thoughts websocket")
        return

    since: int | None = None
    raw_since = websocket.query_params.get("since")
    if raw_since:
        try:
            since = int(str(raw_since).strip())
        except Exception:
            since = None
    subscriber, backlog = await broker.subscribe(since=since)
    try:
        for item in backlog:
            payload = dict(item.event)
            payload.setdefault("stream_id", item.seq)
            await websocket.send_text(json.dumps(payload))
        while True:
            if keepalive_seconds > 0:
                try:
                    item: BrokerEvent = await asyncio.wait_for(
                        subscriber.get(), timeout=keepalive_seconds
                    )
                except asyncio.TimeoutError:
                    await websocket.send_text(
                        json.dumps({"type": "keepalive", "timestamp": time.time()})
                    )
                    continue
            else:
                item = await subscriber.get()
            payload = dict(item.event)
            payload.setdefault("stream_id", item.seq)
            await websocket.send_text(json.dumps(payload))
    except WebSocketDisconnect:
        logger.info("Thoughts websocket disconnected")
    except Exception:
        logger.exception("Error in thoughts websocket")
    finally:
        try:
            await broker.unsubscribe(subscriber)
        except Exception:
            pass


@router.post("/context/{context_id}")
async def create_context(context_id: str, context_schema: ContextSchema):
    """
    Create a new model context from the provided JSON schema.
    """
    # Convert Pydantic schema to service context
    service_ctx = ServiceContext(
        system_prompt=context_schema.system_prompt,
        messages=[
            {
                "role": m.role,
                "content": m.content,
                "metadata": m.metadata or {},
            }
            for m in context_schema.messages
        ],
        tools=[
            {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
                "metadata": t.metadata or {},
            }
            for t in context_schema.tools
        ],
        metadata=context_schema.metadata,
    )
    llm_service.set_context(service_ctx, context_id)
    return {"status": "success", "context": service_ctx.to_dict()}


@router.post("/context/")
async def create_context_auto(context_schema: ContextSchema):
    """
    Create a new model context with an auto-generated ID.
    Convenience alias for clients that don't choose an ID up front.
    """
    new_id = str(uuid4())
    service_ctx = ServiceContext(
        system_prompt=context_schema.system_prompt,
        messages=[
            {
                "role": m.role,
                "content": m.content,
                "metadata": m.metadata or {},
            }
            for m in context_schema.messages
        ],
        tools=[
            {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
                "metadata": t.metadata or {},
            }
            for t in context_schema.tools
        ],
        metadata=context_schema.metadata,
    )
    llm_service.set_context(service_ctx, new_id)
    return {
        "status": "success",
        "context_id": new_id,
        "context": service_ctx.to_dict(),
    }


@router.post("/context/{context_id}/message")
async def add_message(
    context_id: str,
    role: str,
    content: str,
    metadata: Optional[Dict[str, Any]] = None,
):
    """
    Add a message to the context.
    """
    context = llm_service.get_context(context_id)
    context.add_message(role, content, metadata)
    return {"status": "success", "context": context.to_dict()}


@router.post("/context/{context_id}/tool")
async def add_tool(context_id: str, tool: Tool):
    """
    Add a tool to the context. Expects a JSON body matching the Tool model.
    """
    context = llm_service.get_context(context_id)
    context.add_tool(
        tool.name,
        tool.description,
        tool.parameters,
        tool.metadata,
    )
    return {"status": "success", "context": context.to_dict()}


@router.post("/context/{context_id}/metadata")
async def set_metadata(context_id: str, key: str, value: Any):
    """
    Set metadata in the context.
    """
    context = llm_service.get_context(context_id)
    context.set_metadata(key, value)
    return {"status": "success", "context": context.to_dict()}


@router.get("/context/{context_id}")
async def get_context(context_id: str):
    """
    Get the current context.
    """
    context = llm_service.get_context(context_id)
    return {"status": "success", "context": context.to_dict()}


@router.delete("/context/{context_id}")
async def clear_context(context_id: str):
    """
    Clear the current context.
    """
    llm_service.clear_context(context_id)
    return {"status": "success", "message": "Context cleared"}


class BranchRequest(BaseModel):
    new_id: str


@router.post("/context/{context_id}/branch")
async def branch_context(context_id: str, payload: BranchRequest):
    """Branch an existing context to a new ID."""
    ctx = llm_service.branch_context(context_id, payload.new_id)
    return {"status": "success", "context": ctx.to_dict()}


class GenerateRequest(BaseModel):
    prompt: str
    mode: str = "api"
    model: Optional[str] = None
    # Optional per-message id for associating streamed deltas with a UI bubble.
    message_id: Optional[str] = None
    # Optional OpenAI-style response format (e.g. "harmony").
    # When omitted, backend defaults may apply (see `harmony_format` setting).
    response_format: Optional[str] = None
    context: Optional[ContextSchema] = None
    session_id: Optional[str] = None
    thinking: Optional[Union[bool, str]] = None
    attachments: List[Attachment] = Field(default_factory=list)
    vision_workflow: Optional[str] = "auto"


class ProviderControlRequest(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None
    context_length: Optional[int] = None


def _normalize_local_provider(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"lm-studio", "lm_studio"}:
        raw = "lmstudio"
    if raw in {"lmstudio", "ollama", "custom-openai-compatible"}:
        return raw
    return "lmstudio"


def _normalize_local_provider_mode(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"local-managed", "remote-unmanaged"}:
        return raw
    return "local-managed"


def _normalize_stream_backend(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"api", "livekit"}:
        return raw
    return "api"


def _default_local_provider_port(provider: str) -> int:
    return 11434 if provider == "ollama" else 1234


def _provider_marker_from_model(value: Any) -> Optional[str]:
    marker = str(value or "").strip().lower()
    if provider_manager.is_provider_marker(marker):
        return marker
    return None


def _provider_model_for_action(value: Any) -> Optional[str]:
    candidate = str(value or "").strip()
    if not candidate:
        return None
    if provider_manager.is_provider_marker(candidate):
        return None
    return candidate


def _effective_provider_for_runtime(
    cfg: Dict[str, Any] | None,
    *,
    requested_model: Optional[str] = None,
    explicit_provider: Optional[str] = None,
) -> Optional[str]:
    cfg_dict = cfg if isinstance(cfg, dict) else {}
    if explicit_provider:
        normalized = _normalize_local_provider(explicit_provider)
        if provider_manager.is_provider_marker(normalized):
            return normalized
    marker = _provider_marker_from_model(requested_model)
    if marker:
        return marker
    if isinstance(requested_model, str) and requested_model.strip():
        return None
    configured_marker = _provider_marker_from_model(cfg_dict.get("transformer_model"))
    if configured_marker:
        return configured_marker
    return None


def _resolve_provider_inference_target_or_none(
    cfg: Dict[str, Any] | None,
    *,
    requested_model: Optional[str] = None,
    explicit_provider: Optional[str] = None,
    allow_auto_start: bool = True,
) -> Optional[Dict[str, Any]]:
    provider = _effective_provider_for_runtime(
        cfg,
        requested_model=requested_model,
        explicit_provider=explicit_provider,
    )
    if not provider:
        return None
    return provider_manager.resolve_inference_target(
        provider=provider,
        requested_model=requested_model,
        allow_auto_start=allow_auto_start,
    )


@router.post("/llm/generate")
async def generate(request: Request, payload: GenerateRequest = Body(...)):
    """Generate text using the selected mode and context."""
    previous_mode = getattr(llm_service, "mode", "api")
    allowed_modes = {"api", "server", "local", "dynamic"}
    try:
        requested_mode_raw = payload.mode or previous_mode or "api"
        if isinstance(requested_mode_raw, str):
            mode_used = requested_mode_raw.strip().lower()
        else:
            mode_used = str(requested_mode_raw).strip().lower()
        if not mode_used or mode_used not in allowed_modes:
            fallback_mode = (
                previous_mode
                if isinstance(previous_mode, str) and previous_mode in allowed_modes
                else "api"
            )
            mode_used = fallback_mode
        llm_service.mode = mode_used
        provider_target: Optional[Dict[str, Any]] = None
        effective_mode = mode_used
        effective_model = payload.model
        if mode_used == "local":
            try:
                provider_target = _resolve_provider_inference_target_or_none(
                    request.app.state.config,
                    requested_model=payload.model,
                    allow_auto_start=True,
                )
            except Exception as exc:
                raise HTTPException(status_code=409, detail=str(exc))
            if provider_target:
                effective_mode = "server"
                llm_service.mode = "server"
                resolved_model = provider_target.get("model")
                if isinstance(resolved_model, str) and resolved_model.strip():
                    effective_model = resolved_model.strip()
        session_id = payload.session_id or "default"
        raw_message_id = (payload.message_id or "").strip()
        message_id = raw_message_id or str(uuid4())
        if payload.context:
            llm_service.set_context(payload.context, session_id)
        inline_attachments = [
            att.model_dump(exclude_none=True) for att in payload.attachments or []
        ]
        try:
            log_timeline_message(
                session_id=session_id,
                message_id=f"{message_id}:user",
                role="user",
                text=payload.prompt,
                source="generate",
                metadata={
                    "mode": mode_used,
                    "model_requested": payload.model,
                    "model_resolved": effective_model,
                    "provider": provider_target.get("provider")
                    if isinstance(provider_target, dict)
                    else None,
                },
            )
        except Exception:
            pass
        response_format = payload.response_format
        if response_format is None and llm_service.config.get("harmony_format"):
            response_format = "harmony"

        loop = asyncio.get_running_loop()
        tool_stream_filter = InlineToolStreamFilter()

        def _publish_stream_event(event: Dict[str, Any]) -> None:
            if not isinstance(event, dict):
                return
            event_type = event.get("type")
            if event_type in {"thought", "content"}:
                fragment = event.get("content")
                if not isinstance(fragment, str) or not fragment.strip():
                    return
                filtered = tool_stream_filter.filter(fragment)
                if not isinstance(filtered, str) or not filtered.strip():
                    return
                fragment = filtered
                payload_event: Dict[str, Any] = {
                    "type": event_type,
                    "content": fragment,
                    "session_id": session_id,
                    "message_id": message_id,
                }
                if event_type == "thought":
                    offset = event.get("offset")
                    if isinstance(offset, int) and offset >= 0:
                        payload_event["offset"] = offset
                    try:
                        log_thought_delta(session_id, fragment, message_id, offset)
                    except Exception:
                        pass
                try:
                    asyncio.run_coroutine_threadsafe(
                        publish_console_event(
                            request.app,
                            payload_event,
                            default_agent=session_id,
                        ),
                        loop,
                    )
                except Exception:
                    pass
                return

            if event_type in {"tool_call_delta", "stream_status"}:
                forwarded = dict(event)
                forwarded["session_id"] = session_id
                forwarded["message_id"] = message_id
                try:
                    asyncio.run_coroutine_threadsafe(
                        publish_console_event(
                            request.app,
                            forwarded,
                            default_agent=session_id,
                        ),
                        loop,
                    )
                except Exception:
                    pass

        stream_consumer = None
        # For OpenAI-compatible servers (e.g. LM Studio), try streaming first even
        # when this endpoint is used in non-streaming UIs. Some servers/models
        # behave better with `stream=true`, and `_generate_via_api` already
        # falls back to non-streaming automatically if streaming fails.
        if effective_mode == "server":
            stream_consumer = (
                _publish_stream_event if raw_message_id else (lambda _event: None)
            )
        elif mode_used == "api" and raw_message_id:
            stream_consumer = _publish_stream_event
        llm_generate_requests_total.labels(
            mode_used, payload.context and "set" or "none"
        ).inc()
        _t0 = time.perf_counter()
        generate_kwargs: Dict[str, Any] = {}
        reasoning = _reasoning_payload(payload.thinking)
        if reasoning is not None:
            generate_kwargs["reasoning"] = reasoning
        if isinstance(provider_target, dict):
            server_url = str(provider_target.get("base_url") or "").strip()
            if server_url:
                generate_kwargs["server_url"] = server_url
            api_token = str(provider_target.get("api_token") or "").strip()
            if api_token:
                generate_kwargs["api_key"] = api_token
        response = await asyncio.to_thread(
            llm_service.generate,
            payload.prompt,
            session_id=session_id,
            model=effective_model,
            attachments=inline_attachments,
            response_format=response_format,
            stream_consumer=stream_consumer,
            stream_message_id=message_id if stream_consumer is not None else None,
            **generate_kwargs,
        )
        if isinstance(response, dict):
            response_meta = response.get("metadata")
            if not isinstance(response_meta, dict):
                response_meta = {}
                response["metadata"] = response_meta
            response_meta.setdefault("message_id", message_id)
            response_meta.setdefault("session_id", session_id)
            if isinstance(provider_target, dict):
                response_meta.setdefault("provider", provider_target.get("provider"))
                response_meta.setdefault("server_url", provider_target.get("base_url"))
                response_meta.setdefault(
                    "provider_runtime",
                    provider_target.get("runtime")
                    if isinstance(provider_target.get("runtime"), dict)
                    else {},
                )
            usage_stats = _normalize_usage_counts(
                response_meta.get("usage")
                if isinstance(response_meta.get("usage"), dict)
                else None,
                payload.prompt or "",
                response.get("text") or "",
            )
            merged_usage = _merge_usage(
                response_meta.get("usage")
                if isinstance(response_meta.get("usage"), dict)
                else None,
                usage_stats,
            )
            if merged_usage is not None:
                response_meta["usage"] = merged_usage
                # NOTE: Per-agent resource tracking in local/server mode is best-effort.
                _update_agent_resource_usage(
                    request.app,
                    session_id,
                    merged_usage,
                    message_id=message_id,
                    session_id=session_id,
                    source="generate",
                )
        try:
            if isinstance(response, dict):
                meta = response.get("metadata")
                meta_payload = meta if isinstance(meta, dict) else {}
                log_timeline_message(
                    session_id=session_id,
                    message_id=message_id,
                    role="ai",
                    text=response.get("text") or "",
                    source="generate",
                    metadata=meta_payload,
                )
        except Exception:
            pass
        llm_generate_duration_seconds.labels(
            mode_used, payload.context and "set" or "none"
        ).observe(time.perf_counter() - _t0)
        return {"status": "success", "response": response}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        llm_service.mode = previous_mode


def _provider_runtime_response(runtime: Dict[str, Any]) -> Dict[str, Any]:
    mapped = dict(runtime or {})
    mapped["mode"] = "local"
    mapped["active_backend"] = "provider"
    mapped["loaded"] = bool(mapped.get("model_loaded"))
    mapped["load_state"] = "ready" if mapped.get("model_loaded") else "idle"
    mapped["load_error"] = mapped.get("last_error")
    provider_name = str(mapped.get("provider") or "").strip()
    loaded_model = str(mapped.get("loaded_model") or "").strip()
    if provider_name:
        mapped["model"] = provider_name
    elif loaded_model:
        mapped["model"] = loaded_model
    return mapped


def _resolve_provider_for_request(
    request: Request,
    *,
    requested_model: Optional[str] = None,
    explicit_provider: Optional[str] = None,
) -> Optional[str]:
    cfg = request.app.state.config if isinstance(request.app.state.config, dict) else {}
    return _effective_provider_for_runtime(
        cfg,
        requested_model=requested_model,
        explicit_provider=explicit_provider,
    )


@router.get("/llm/provider/status")
async def provider_status(
    request: Request,
    provider: Optional[str] = Query(default=None),
    model: Optional[str] = Query(default=None),
    quick: bool = Query(default=False),
):
    chosen_provider = _resolve_provider_for_request(
        request,
        requested_model=model,
        explicit_provider=provider,
    )
    if not chosen_provider:
        raise HTTPException(
            status_code=400,
            detail="Provider must be 'lmstudio' or 'ollama'.",
        )
    runtime = await run_in_threadpool(
        provider_manager.provider_status, chosen_provider, quick
    )
    return {"status": "success", "runtime": runtime}


@router.get("/llm/provider/models")
async def provider_models(
    request: Request,
    provider: Optional[str] = Query(default=None),
    model: Optional[str] = Query(default=None),
):
    chosen_provider = _resolve_provider_for_request(
        request,
        requested_model=model,
        explicit_provider=provider,
    )
    if not chosen_provider:
        raise HTTPException(
            status_code=400,
            detail="Provider must be 'lmstudio' or 'ollama'.",
        )
    models = provider_manager.provider_models(chosen_provider)
    runtime = provider_manager.provider_status(chosen_provider)
    return {"status": "success", "models": models.get("models", []), "runtime": runtime}


@router.post("/llm/provider/start")
async def provider_start(
    request: Request,
    payload: Optional[ProviderControlRequest] = Body(default=None),
):
    payload = payload or ProviderControlRequest()
    chosen_provider = _resolve_provider_for_request(
        request,
        requested_model=payload.model,
        explicit_provider=payload.provider,
    )
    if not chosen_provider:
        raise HTTPException(
            status_code=400,
            detail="Provider must be 'lmstudio' or 'ollama'.",
        )
    result = provider_manager.provider_start(chosen_provider)
    if not result.get("ok"):
        detail = (result.get("result") or {}).get(
            "error"
        ) or "Failed to start provider."
        raise HTTPException(status_code=409, detail=str(detail))
    return {"status": "success", **result}


@router.post("/llm/provider/stop")
async def provider_stop(
    request: Request,
    payload: Optional[ProviderControlRequest] = Body(default=None),
):
    payload = payload or ProviderControlRequest()
    chosen_provider = _resolve_provider_for_request(
        request,
        requested_model=payload.model,
        explicit_provider=payload.provider,
    )
    if not chosen_provider:
        raise HTTPException(
            status_code=400,
            detail="Provider must be 'lmstudio' or 'ollama'.",
        )
    result = provider_manager.provider_stop(chosen_provider)
    if not result.get("ok"):
        detail = (result.get("result") or {}).get("error") or "Failed to stop provider."
        raise HTTPException(status_code=409, detail=str(detail))
    return {"status": "success", **result}


@router.post("/llm/provider/load")
async def provider_load(
    request: Request,
    payload: Optional[ProviderControlRequest] = Body(default=None),
):
    payload = payload or ProviderControlRequest()
    chosen_provider = _resolve_provider_for_request(
        request,
        requested_model=payload.model,
        explicit_provider=payload.provider,
    )
    if not chosen_provider:
        raise HTTPException(
            status_code=400,
            detail="Provider must be 'lmstudio' or 'ollama'.",
        )
    result = provider_manager.provider_load(
        provider=chosen_provider,
        model=_provider_model_for_action(payload.model),
        context_length=payload.context_length,
    )
    if not result.get("ok"):
        detail = (result.get("result") or {}).get(
            "error"
        ) or "Failed to load provider model."
        raise HTTPException(status_code=409, detail=str(detail))
    return {"status": "success", **result}


@router.post("/llm/provider/unload")
async def provider_unload(
    request: Request,
    payload: Optional[ProviderControlRequest] = Body(default=None),
):
    payload = payload or ProviderControlRequest()
    chosen_provider = _resolve_provider_for_request(
        request,
        requested_model=payload.model,
        explicit_provider=payload.provider,
    )
    if not chosen_provider:
        raise HTTPException(
            status_code=400,
            detail="Provider must be 'lmstudio' or 'ollama'.",
        )
    result = provider_manager.provider_unload(
        provider=chosen_provider,
        model=_provider_model_for_action(payload.model),
    )
    if not result.get("ok"):
        detail = (result.get("result") or {}).get(
            "error"
        ) or "Failed to unload provider model."
        raise HTTPException(status_code=409, detail=str(detail))
    return {"status": "success", **result}


@router.get("/llm/provider/logs")
async def provider_logs(
    request: Request,
    provider: Optional[str] = Query(default=None),
    model: Optional[str] = Query(default=None),
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=2000),
):
    chosen_provider = _resolve_provider_for_request(
        request,
        requested_model=model,
        explicit_provider=provider,
    )
    if not chosen_provider:
        raise HTTPException(
            status_code=400,
            detail="Provider must be 'lmstudio' or 'ollama'.",
        )
    logs = provider_manager.provider_logs(
        provider=chosen_provider,
        cursor=cursor,
        limit=limit,
    )
    return {"status": "success", "logs": logs}


@router.get("/llm/local-status")
async def local_status(
    request: Request,
    model: Optional[str] = Query(default=None),
    provider: Optional[str] = Query(default=None),
    quick: bool = Query(default=True),
):
    """Return local inference load state and runtime memory snapshot."""
    cfg = request.app.state.config if isinstance(request.app.state.config, dict) else {}
    selected_model = str(model or cfg.get("transformer_model") or "").strip()
    chosen_provider = _resolve_provider_for_request(
        request,
        requested_model=selected_model,
        explicit_provider=provider,
    )
    if chosen_provider:
        runtime = await run_in_threadpool(
            provider_manager.provider_status, chosen_provider, quick
        )
        return {"status": "success", "runtime": _provider_runtime_response(runtime)}
    runtime = await run_in_threadpool(llm_service.local_runtime_status)
    return {"status": "success", "runtime": runtime}


@router.post("/llm/unload-local")
async def unload_local_model(
    request: Request,
    payload: Optional[ProviderControlRequest] = Body(default=None),
):
    """Unload the local model to free GPU memory."""
    cfg = request.app.state.config if isinstance(request.app.state.config, dict) else {}
    requested_model = (
        str(payload.model).strip()
        if payload and isinstance(payload.model, str)
        else str(cfg.get("transformer_model") or "").strip()
    )
    explicit_provider = payload.provider if payload else None
    chosen_provider = _resolve_provider_for_request(
        request,
        requested_model=requested_model,
        explicit_provider=explicit_provider,
    )
    if chosen_provider:
        result = provider_manager.provider_unload(
            provider=chosen_provider,
            model=_provider_model_for_action(requested_model),
        )
        if not result.get("ok"):
            detail = (result.get("result") or {}).get(
                "error"
            ) or "Failed to unload provider model."
            raise HTTPException(status_code=409, detail=str(detail))
        runtime = (
            result.get("runtime") if isinstance(result.get("runtime"), dict) else {}
        )
        return {
            "status": "success",
            "result": result.get("result") or {},
            "runtime": _provider_runtime_response(runtime),
        }
    result = llm_service.unload_local_model()
    return {"status": "success", "result": result}


@router.post("/llm/load-local")
async def load_local_model(
    request: Request,
    payload: Optional[ProviderControlRequest] = Body(default=None),
):
    """Load the configured local model without running a generation."""
    cfg = request.app.state.config if isinstance(request.app.state.config, dict) else {}
    requested_model = (
        str(payload.model).strip()
        if payload and isinstance(payload.model, str)
        else str(cfg.get("transformer_model") or "").strip()
    )
    explicit_provider = payload.provider if payload else None
    chosen_provider = _resolve_provider_for_request(
        request,
        requested_model=requested_model,
        explicit_provider=explicit_provider,
    )
    if chosen_provider:
        requested_ctx = payload.context_length if payload else None
        if requested_ctx is None:
            fallback_ctx = cfg.get("local_provider_default_context_length")
            try:
                requested_ctx = int(fallback_ctx)
            except Exception:
                requested_ctx = None
            if isinstance(requested_ctx, int) and requested_ctx <= 0:
                requested_ctx = None
        result = provider_manager.provider_load(
            provider=chosen_provider,
            model=_provider_model_for_action(requested_model),
            context_length=requested_ctx,
        )
        if not result.get("ok"):
            detail = (result.get("result") or {}).get(
                "error"
            ) or "Failed to load provider model."
            raise HTTPException(status_code=409, detail=str(detail))
        runtime = (
            result.get("runtime") if isinstance(result.get("runtime"), dict) else {}
        )
        return {
            "status": "success",
            "result": result.get("result") or {},
            "runtime": _provider_runtime_response(runtime),
        }
    llm_service.load_local_model(
        override_model_name=requested_model if requested_model else None
    )
    return {"status": "success", "runtime": llm_service.local_runtime_status()}


@router.post("/llm/start-dynamic")
async def start_dynamic_server():
    """
    Start the dynamic LLM server.
    """
    try:
        llm_service.start_dynamic_server()
        return {"status": "success", "message": "Dynamic server started."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/llm/stop-dynamic")
async def stop_dynamic_server():
    """
    Stop the dynamic LLM server.
    """
    try:
        llm_service.stop_dynamic_server()
        return {"status": "success", "message": "Dynamic server stopped."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat", response_model=ChatResponse)
async def chat(request: Request, chat_request: ChatRequest):
    """
    Endpoint for handling chat messages with context support.
    """
    try:
        mode_used = None
        if chat_request.mode is not None:
            mode_raw = str(chat_request.mode or "").strip().lower()
            if mode_raw:
                allowed_modes = {"api", "server", "local", "dynamic"}
                if mode_raw in allowed_modes:
                    llm_service.mode = mode_raw
                    mode_used = mode_raw
        if mode_used is None:
            mode_used = getattr(llm_service, "mode", "api")
        session_name = chat_request.session_id or "default"
        session_id = conversation_store.get_or_create_conversation_id(session_name)
        message_id = chat_request.message_id or str(uuid4())
        chat_request.message_id = message_id
        # Create or get context (use provided context if present)
        if chat_request.context:
            llm_service.set_context(chat_request.context, session_name)
        context = llm_service.get_context(session_name)
        # Rehydrate context from persisted conversation if empty
        if not context.messages:
            try:
                history = conversation_store.load_conversation(session_name)
                for entry in history:
                    role = entry.get("role")
                    text = entry.get("text") or entry.get("content")
                    if not role or not text:
                        continue
                    meta = entry.get("metadata") or {}
                    if isinstance(meta, dict):
                        meta = dict(meta)
                        saved_attachments = entry.get("attachments")
                        if saved_attachments and not meta.get("attachments"):
                            meta["attachments"] = saved_attachments
                    if entry.get("rag") and isinstance(meta, dict):
                        meta.setdefault("rag", {"matches": entry["rag"]})
                    context.add_message(role, text, metadata=meta)
            except Exception:
                pass

        incoming_attachments = [
            att.model_dump(exclude_none=True) for att in chat_request.attachments or []
        ]
        image_attachments = [
            att
            for att in incoming_attachments
            if str(
                att.get("type") or att.get("content_type") or att.get("mime_type") or ""
            )
            .strip()
            .lower()
            .startswith("image/")
        ]
        vision_workflow = _normalize_vision_workflow(chat_request.vision_workflow)
        if vision_workflow != "auto" and not image_attachments:
            raise HTTPException(
                status_code=400,
                detail="vision_workflow requires at least one image attachment",
            )
        if vision_workflow == "compare" and len(image_attachments) < 2:
            raise HTTPException(
                status_code=400,
                detail="vision_workflow=compare requires at least two image attachments",
            )
        now_ts = time.time()
        iso_timestamp = (
            datetime.fromtimestamp(now_ts, tz=timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        metadata = {
            "timestamp": now_ts,
            "iso_timestamp": iso_timestamp,
        }
        if incoming_attachments:
            metadata["attachments"] = incoming_attachments
        if image_attachments:
            metadata["vision"] = {
                "workflow": vision_workflow,
                "image_attachments": len(image_attachments),
            }
        rag_matches: list[Dict[str, Any]] = []
        rag_metadata: list[Dict[str, Any]] = []
        rag_prompt_text: Optional[str] = None
        cfg = request.app.state.config or {}

        def _coerce_int(value: Any, default: int, *, min_value: int = 0) -> int:
            try:
                candidate = int(value)
            except Exception:
                return default
            if candidate < min_value:
                return default
            return candidate

        def _coerce_float(
            value: Any,
            default: float,
            *,
            min_value: float | None = None,
            max_value: float | None = None,
        ) -> float:
            try:
                candidate = float(value)
            except Exception:
                return default
            if min_value is not None and candidate < min_value:
                return default
            if max_value is not None and candidate > max_value:
                return default
            return candidate

        rag_top_k = _coerce_int(cfg.get("rag_chat_top_k"), 3, min_value=0)
        rag_clip_top_k = _coerce_int(cfg.get("rag_chat_clip_top_k"), 0, min_value=0)
        rag_match_chars = _coerce_int(
            cfg.get("rag_chat_match_chars"), 1200, min_value=0
        )
        rag_prompt_snippet_chars = _coerce_int(
            cfg.get("rag_chat_prompt_snippet_chars"), 240, min_value=0
        )
        rag_prompt_max_chars = _coerce_int(
            cfg.get("rag_chat_prompt_max_chars"), 2200, min_value=0
        )
        rag_min_similarity = _coerce_float(
            cfg.get("rag_chat_min_similarity"),
            0.3,
            min_value=0.0,
            max_value=1.0,
        )
        rag_query_top_k = rag_top_k
        rag_query_clip_top_k = rag_clip_top_k
        if rag_min_similarity > 0:
            rag_query_top_k = max(rag_top_k * 5, rag_top_k)
            rag_query_clip_top_k = max(rag_clip_top_k * 5, rag_clip_top_k)

        def _truncate_for_rag(value: str, limit: int) -> str:
            if not isinstance(value, str):
                return ""
            cleaned = " ".join(value.split())
            if limit <= 0:
                return ""
            if len(cleaned) <= limit:
                return cleaned
            if limit <= 1:
                return cleaned[:limit]
            return cleaned[: max(0, limit - 1)].rstrip() + "…"

        def _truncate_block(value: str, limit: int) -> str:
            if not isinstance(value, str):
                return ""
            cleaned = value.strip()
            if limit <= 0:
                return ""
            if len(cleaned) <= limit:
                return cleaned
            if limit <= 1:
                return cleaned[:limit]
            return cleaned[: max(0, limit - 1)].rstrip() + "…"

        def _looks_visual_query(message: str, attachments: list[Any]) -> bool:
            for item in attachments or []:
                content_type = ""
                if isinstance(item, dict):
                    content_type = str(
                        item.get("content_type") or item.get("mime_type") or ""
                    ).strip()
                else:
                    content_type = str(getattr(item, "content_type", "") or "").strip()
                if content_type.lower().startswith("image/"):
                    return True
            text = str(message or "").lower()
            if not text:
                return False
            visual_terms = (
                "image",
                "photo",
                "picture",
                "screenshot",
                "diagram",
                "visual",
                "logo",
                "icon",
            )
            return any(term in text for term in visual_terms)

        retrieval_request_event = None
        if chat_request.use_rag is not False:
            try:
                service = _get_rag_service()
                clip_service = None
                retrieval_request_event = hooks.RetrievalRequest(
                    session_id=session_id,
                    query=chat_request.message or "",
                    top_k=rag_top_k,
                    metadata={"channel": "chat"},
                )
                hooks.emit(hooks.BEFORE_RETRIEVAL_EVENT, retrieval_request_event)

                external_llm = str(getattr(llm_service, "mode", "api")).lower() == "api"
                memory_manager = getattr(request.app.state, "memory_manager", None)

                def _memory_key_from_meta(meta: Dict[str, Any]) -> Optional[str]:
                    if not isinstance(meta, dict):
                        return None
                    memory_key = str(meta.get("memory_key") or "").strip()
                    if memory_key:
                        return memory_key
                    for field in ("source", "root_source"):
                        source = str(meta.get(field) or "").strip()
                        if source.startswith("memory:"):
                            return source.split("memory:", 1)[1] or None
                    return None

                def _canonical_memory_item(
                    meta: Dict[str, Any]
                ) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
                    memory_key = _memory_key_from_meta(meta)
                    if not memory_key or memory_manager is None:
                        return memory_key, None
                    try:
                        item = memory_manager.get_item(
                            memory_key,
                            include_pruned=True,
                            touch=False,
                        )
                    except TypeError:
                        item = memory_manager.get_item(memory_key)
                    if not isinstance(item, dict):
                        return memory_key, None
                    return memory_key, item

                def _apply_memory_lifecycle(
                    match: Dict[str, Any]
                ) -> Optional[Dict[str, Any]]:
                    if not isinstance(match, dict):
                        return None
                    cloned = dict(match)
                    meta = (
                        dict(match.get("metadata"))
                        if isinstance(match.get("metadata"), dict)
                        else {}
                    )
                    memory_key, canonical_item = _canonical_memory_item(meta)
                    if memory_key:
                        if canonical_item is None:
                            return None
                        multiplier = 1.0
                        if hasattr(memory_manager, "lifecycle_multiplier"):
                            try:
                                multiplier = float(
                                    memory_manager.lifecycle_multiplier(canonical_item)
                                )
                            except Exception:
                                multiplier = 1.0
                        if multiplier <= 0:
                            return None
                        meta["memory_key"] = memory_key
                        for field in (
                            "lifecycle",
                            "grounded_at",
                            "occurs_at",
                            "review_at",
                            "decay_at",
                            "pruned_at",
                            "last_confirmed_at",
                            "updated_at",
                            "sensitivity",
                            "hint",
                            "rag_excluded",
                        ):
                            value = canonical_item.get(field)
                            if value is not None:
                                meta[field] = value
                        if isinstance(cloned.get("score"), (int, float)):
                            cloned["score"] = float(cloned["score"]) * multiplier
                    cloned["metadata"] = meta
                    return cloned

                def _blocked(meta: Dict[str, Any]) -> bool:
                    if not isinstance(meta, dict):
                        return False
                    if meta.get("rag_excluded") or meta.get("excluded"):
                        return True
                    _memory_key, canonical_item = _canonical_memory_item(meta)
                    if canonical_item is not None:
                        if canonical_item.get("rag_excluded"):
                            return True
                        if hasattr(memory_manager, "lifecycle_multiplier"):
                            try:
                                if (
                                    float(
                                        memory_manager.lifecycle_multiplier(
                                            canonical_item
                                        )
                                    )
                                    <= 0
                                ):
                                    return True
                            except Exception:
                                pass
                        lvl = str(canonical_item.get("sensitivity", "")).lower()
                        if lvl == "secret":
                            return True
                        if external_llm and lvl == "protected":
                            return True
                        return False
                    if _is_external_knowledge_source(meta):
                        return True
                    lvl = str(meta.get("sensitivity", "")).lower()
                    if lvl == "secret":
                        return True
                    if external_llm and lvl == "protected":
                        return True
                    return False

                text_matches = (
                    service.query(chat_request.message, top_k=rag_query_top_k) or []
                )

                clip_matches: list[Dict[str, Any]] = []
                clip_query_allowed = _looks_visual_query(
                    chat_request.message, incoming_attachments
                )
                clip_requested = bool(rag_query_clip_top_k > 0 and clip_query_allowed)
                if clip_requested:
                    clip_service = _get_clip_rag_service(raise_http=False)
                clip_ready = bool(
                    clip_requested
                    and clip_service
                    and str(getattr(clip_service, "embedding_model", ""))
                    .lower()
                    .startswith("clip:")
                    and getattr(clip_service, "_embedding_encoder", None) is not None
                )
                if clip_ready:
                    raw_clip = (
                        clip_service.query(
                            chat_request.message, top_k=rag_query_clip_top_k
                        )
                        or []
                    )
                    for match in raw_clip:
                        if not isinstance(match, dict):
                            continue
                        match = _apply_memory_lifecycle(match)
                        if match is None:
                            continue
                        meta = (
                            match.get("metadata")
                            if isinstance(match.get("metadata"), dict)
                            else {}
                        )
                        caption_id = meta.get("caption_doc_id") or match.get("id")
                        trace = None
                        if caption_id:
                            try:
                                trace = service.trace(str(caption_id))
                            except Exception:
                                trace = None
                        trace_meta = (
                            trace.get("metadata")
                            if isinstance(trace, dict)
                            and isinstance(trace.get("metadata"), dict)
                            else {}
                        )
                        merged_meta = dict(trace_meta)
                        for key in (
                            "source",
                            "filename",
                            "content_hash",
                            "content_type",
                            "url",
                        ):
                            if key in meta and key not in merged_meta:
                                merged_meta[key] = meta[key]
                        merged_meta["retrieved_via"] = "clip"
                        if _blocked(merged_meta):
                            continue
                        clip_matches.append(
                            {
                                "id": str(caption_id or match.get("id") or ""),
                                "text": (
                                    trace.get("text")
                                    if isinstance(trace, dict)
                                    else None
                                )
                                or match.get("text", ""),
                                "metadata": merged_meta,
                                "score": match.get("score"),
                            }
                        )

                def _rag_match_key(match: Dict[str, Any]) -> str:
                    if not isinstance(match, dict):
                        return ""
                    meta = (
                        match.get("metadata")
                        if isinstance(match.get("metadata"), dict)
                        else {}
                    )
                    source = str(
                        meta.get("source") or match.get("source") or ""
                    ).strip()
                    root_source = str(meta.get("root_source") or "").strip()
                    content_hash = str(meta.get("content_hash") or "").strip()
                    text_val = str(match.get("text") or "").strip().lower()
                    text_sig = (
                        hashlib.sha1(text_val[:512].encode("utf-8")).hexdigest()
                        if text_val
                        else ""
                    )
                    if content_hash:
                        return f"hash:{content_hash.lower()}"
                    if root_source:
                        return f"root:{root_source.lower()}"
                    if source and text_sig:
                        return f"source:{source.lower()}|text:{text_sig}"
                    if source:
                        return f"source:{source.lower()}"
                    if text_sig:
                        return f"text:{text_sig}"
                    return ""

                combined: list[Dict[str, Any]] = []
                seen_ids: set[str] = set()
                seen_match_keys: set[str] = set()
                for match in clip_matches:
                    match_id = str(match.get("id") or "")
                    if not match_id or match_id in seen_ids:
                        continue
                    dedupe_key = _rag_match_key(match)
                    if dedupe_key and dedupe_key in seen_match_keys:
                        continue
                    seen_ids.add(match_id)
                    if dedupe_key:
                        seen_match_keys.add(dedupe_key)
                    combined.append(match)
                for match in text_matches:
                    if not isinstance(match, dict):
                        continue
                    match = _apply_memory_lifecycle(match)
                    if match is None:
                        continue
                    meta = (
                        match.get("metadata")
                        if isinstance(match.get("metadata"), dict)
                        else {}
                    )
                    if _blocked(meta):
                        continue
                    match_id = str(match.get("id") or "")
                    if match_id and match_id in seen_ids:
                        continue
                    dedupe_key = _rag_match_key(match)
                    if dedupe_key and dedupe_key in seen_match_keys:
                        continue
                    if match_id:
                        seen_ids.add(match_id)
                    if dedupe_key:
                        seen_match_keys.add(dedupe_key)
                    combined.append(match)
                    if len(combined) >= rag_query_top_k:
                        break
                combined.sort(
                    key=lambda item: (
                        float(item.get("score"))
                        if isinstance(item.get("score"), (int, float))
                        else 0.0
                    ),
                    reverse=True,
                )
                if rag_min_similarity > 0:
                    filtered: list[Dict[str, Any]] = []

                    def _score_ok(item: Dict[str, Any]) -> bool:
                        raw = item.get("score")
                        if not isinstance(raw, (int, float)):
                            return True
                        sim = max(0.0, min(1.0, float(raw)))
                        return sim >= rag_min_similarity

                    for item in combined:
                        if not isinstance(item, dict):
                            continue
                        if _score_ok(item):
                            filtered.append(item)
                    combined = filtered
                rag_matches = combined[:rag_top_k]
            except HTTPException:
                rag_matches = []
            except Exception as exc:
                logger.warning("RAG query failed: %s", exc)
                rag_matches = []
        if retrieval_request_event is not None:
            hooks.emit(
                hooks.AFTER_RETRIEVAL_EVENT,
                hooks.RetrievalResult(
                    session_id=session_id,
                    query=retrieval_request_event.query,
                    matches=list(rag_matches),
                    metadata={"channel": "chat"},
                ),
            )
        if rag_matches:
            rag_lines: List[str] = []
            prompt_header = "Retrieved knowledge:\n"
            for idx, match in enumerate(rag_matches, start=1):
                text_val = str(match.get("text") or "")
                stored_text = _truncate_for_rag(text_val, rag_match_chars)
                snippet = textwrap.shorten(
                    stored_text.replace("\n", " ").strip(),
                    width=rag_prompt_snippet_chars,
                    placeholder="…",
                )
                meta = match.get("metadata") or {}
                if not isinstance(meta, dict):
                    meta = {}
                else:
                    meta = dict(meta)
                meta = _sanitize_knowledge_metadata_for_api(meta)
                source = _sanitize_knowledge_source_for_api(
                    (meta.get("source") or match.get("source") or f"doc-{idx}"),
                    meta,
                )
                score_raw = match.get("score")
                score = (
                    float(score_raw) if isinstance(score_raw, (int, float)) else None
                )
                embedding_model = meta.get("embedding_model")
                if not embedding_model:
                    retrieved_via = str(meta.get("retrieved_via") or "").lower()
                    if retrieved_via == "clip" and clip_service is not None:
                        embedding_model = getattr(clip_service, "embedding_model", None)
                    else:
                        embedding_model = getattr(service, "embedding_model", None)
                if embedding_model:
                    meta["embedding_model"] = embedding_model
                rag_metadata.append(
                    {
                        "id": match.get("id"),
                        "text": stored_text,
                        "source": source,
                        "score": score,
                        "metadata": meta,
                    }
                )
                rag_lines.append(f"{idx}. {snippet}")
            rag_prompt_text = (
                prompt_header
                + _truncate_block("\n".join(rag_lines), rag_prompt_max_chars)
                + "\nUse this information when it helps answer the user."
            )
            metadata["rag"] = {
                "matches": rag_metadata,
                "top_k": rag_top_k,
                "clip_top_k": rag_clip_top_k,
                "min_similarity": rag_min_similarity,
                "limits": {
                    "match_chars": rag_match_chars,
                    "prompt_snippet_chars": rag_prompt_snippet_chars,
                    "prompt_max_chars": rag_prompt_max_chars,
                },
            }
            sources = [m.get("source") or m.get("id") for m in rag_metadata]
            logger.info("RAG retrieved %d matches: %s", len(rag_metadata), sources)
        metadata.setdefault("session_name", session_name)
        metadata.setdefault("session_id", session_id)
        context.add_message("user", chat_request.message, metadata=metadata)

        user_entry = {
            "id": f"{message_id}:user",
            "role": "user",
            "text": chat_request.message,
            "timestamp": now_ts,
            "metadata": (
                {"vision": metadata.get("vision")}
                if isinstance(metadata.get("vision"), dict)
                else {}
            ),
        }
        if incoming_attachments:
            user_entry["attachments"] = incoming_attachments
        if rag_metadata:
            user_entry["rag"] = rag_metadata
        _append_conversation_entry(session_name, user_entry)
        try:
            log_timeline_message(
                session_id=session_name,
                message_id=user_entry["id"],
                role="user",
                text=chat_request.message,
                source="chat",
                metadata={
                    "mode": mode_used,
                    "model_requested": chat_request.model,
                    "vision_workflow": vision_workflow,
                },
            )
        except Exception:
            pass
        assistant_placeholder = {
            "id": message_id,
            "role": "ai",
            "text": "",
            "thought": "",
            "metadata": (
                {"status": "pending", "rag": {"matches": rag_metadata}}
                if rag_metadata
                else {"status": "pending"}
            ),
            "timestamp": now_ts,
            "iso_timestamp": iso_timestamp,
        }
        _append_conversation_entry(session_name, assistant_placeholder)

        generation_ctx = ServiceContext(
            system_prompt=_effective_system_prompt(
                context.system_prompt,
                request=request,
            ),
            messages=list(context.messages),
            tools=list(context.tools),
            metadata=dict(context.metadata),
        )
        if rag_prompt_text:
            generation_ctx.add_message(
                "system",
                rag_prompt_text,
                metadata={"rag": {"matches": rag_metadata}, "ephemeral": True},
            )
        vision_instruction = _vision_workflow_instruction(
            vision_workflow,
            len(image_attachments),
        )
        if vision_instruction:
            generation_ctx.add_message(
                "system",
                vision_instruction,
                metadata={
                    "vision": {
                        "workflow": vision_workflow,
                        "image_attachments": len(image_attachments),
                    },
                    "ephemeral": True,
                },
            )
        context_snapshot = generation_ctx.to_dict()
        hooks.emit(
            hooks.BEFORE_LLM_CALL_EVENT,
            hooks.LLMCallEvent(
                session_id=session_id,
                prompt=chat_request.message or "",
                model=chat_request.model,
                context=context_snapshot,
                metadata={
                    "channel": "chat",
                    "rag_matches": len(rag_metadata),
                    "attachments": len(incoming_attachments),
                    "vision_workflow": vision_workflow,
                    "image_attachments": len(image_attachments),
                },
            ),
        )

        # Generate response
        # Log the incoming prompt (snippet only to avoid huge logs)
        try:
            prompt_snippet = (chat_request.message or "")[:200]
            log_chat_request(session_name, prompt_snippet, chat_request.model)
        except Exception:
            pass

        loop = asyncio.get_running_loop()
        analysis_trace: list[dict[str, Any]] = []
        thought_counter = 0

        tool_stream_filter = InlineToolStreamFilter()

        def stream_consumer(event: Dict[str, Any]) -> None:
            nonlocal thought_counter
            if not isinstance(event, dict):
                return
            event_type = event.get("type")
            if event_type == "thought":
                text_fragment = event.get("content")
                if not isinstance(text_fragment, str) or not text_fragment.strip():
                    return
                filtered = tool_stream_filter.filter(text_fragment)
                if not isinstance(filtered, str) or not filtered.strip():
                    return
                text_fragment = filtered
                offset = event.get("offset")
                if not isinstance(offset, int) or offset < 0:
                    offset = thought_counter
                thought_counter = offset + 1
                trace_entry = {
                    "index": offset,
                    "text": text_fragment,
                    "timestamp": time.time(),
                }
                analysis_trace.append(trace_entry)
                try:
                    log_thought_delta(session_id, text_fragment, message_id, offset)
                except Exception:
                    pass
                try:
                    asyncio.run_coroutine_threadsafe(
                        publish_console_event(
                            request.app,
                            {
                                "type": "thought",
                                "content": text_fragment,
                                "session_id": session_id,
                                "message_id": message_id,
                                "offset": offset,
                            },
                            default_agent=session_id,
                        ),
                        loop,
                    )
                except Exception:
                    pass
                return

            if event_type == "content":
                fragment = event.get("content")
                if not isinstance(fragment, str) or not fragment.strip():
                    return
                filtered = tool_stream_filter.filter(fragment)
                if not isinstance(filtered, str) or not filtered.strip():
                    return
                fragment = filtered
                try:
                    asyncio.run_coroutine_threadsafe(
                        publish_console_event(
                            request.app,
                            {
                                "type": "content",
                                "content": fragment,
                                "session_id": session_id,
                                "message_id": message_id,
                            },
                            default_agent=session_id,
                        ),
                        loop,
                    )
                except Exception:
                    pass
                return

            if event_type in {"tool_call_delta", "stream_status"}:
                try:
                    asyncio.run_coroutine_threadsafe(
                        publish_console_event(
                            request.app,
                            {
                                **event,
                                "session_id": session_id,
                                "message_id": message_id,
                            },
                            default_agent=session_id,
                        ),
                        loop,
                    )
                except Exception:
                    pass

        provider_target: Optional[Dict[str, Any]] = None
        effective_mode = mode_used
        effective_model = chat_request.model
        if mode_used == "local":
            try:
                provider_target = _resolve_provider_inference_target_or_none(
                    request.app.state.config,
                    requested_model=chat_request.model,
                    allow_auto_start=True,
                )
            except Exception as exc:
                raise HTTPException(status_code=409, detail=str(exc))
            if isinstance(provider_target, dict):
                effective_mode = "server"
                resolved_model = provider_target.get("model")
                if isinstance(resolved_model, str) and resolved_model.strip():
                    effective_model = resolved_model.strip()

        previous_service_mode = getattr(llm_service, "mode", mode_used)
        llm_service.mode = effective_mode
        try:
            generate_kwargs: Dict[str, Any] = {}
            reasoning = _reasoning_payload(chat_request.thinking)
            if reasoning is not None:
                generate_kwargs["reasoning"] = reasoning
            if isinstance(provider_target, dict):
                server_url = str(provider_target.get("base_url") or "").strip()
                if server_url:
                    generate_kwargs["server_url"] = server_url
                api_token = str(provider_target.get("api_token") or "").strip()
                if api_token:
                    generate_kwargs["api_key"] = api_token
            response = await asyncio.to_thread(
                llm_service.generate,
                chat_request.message,
                session_id=session_name,
                model=effective_model,
                attachments=incoming_attachments,
                vision_workflow=vision_workflow,
                response_format="harmony"
                if request.app.state.config.get("harmony_format")
                else None,
                context=generation_ctx,
                stream_consumer=stream_consumer,
                stream_message_id=message_id,
                **generate_kwargs,
            )
            llm_service.set_context(context, session_id)
        except Exception as exc:
            _update_conversation_entry(
                session_name,
                message_id,
                {
                    "metadata": {"status": "error", "error": str(exc)},
                    "updated_at": time.time(),
                },
            )
            llm_service.set_context(context, session_id)
            raise
        finally:
            llm_service.mode = mode_used if mode_used else previous_service_mode

        tools_used_response = (
            response.get("tools_used")
            if isinstance(response.get("tools_used"), list)
            else []
        )
        # Never surface "done" wording while tools are only proposed.
        if tools_used_response:
            text = _pending_tool_placeholder_text(tools_used_response)
            response["text"] = text
            metadata = response.get("metadata")
            metadata_update = dict(metadata) if isinstance(metadata, dict) else {}
            metadata_update["tool_response_pending"] = True
            response["metadata"] = metadata_update
        else:
            # Ensure we never return an empty string UI bubble.
            text = response.get("text") or ""
            if not text:
                # Provide a friendlier fallback when no assistant text is returned.
                metadata = response.get("metadata")
                err = metadata.get("error") if isinstance(metadata, dict) else None
                if not text:
                    text = (
                        f"I couldn't generate a reply ({err}). Please check model settings."
                        if err
                        else "I couldn't generate a reply. Please check model settings."
                    )
                response["text"] = text
            else:
                text = response.get("text") or ""

        trace_source = analysis_trace or response.get("thought_trace") or []
        conversation_trace: list[dict[str, Any]] = []
        trace_time = time.time()
        for idx, item in enumerate(trace_source):
            if isinstance(item, dict):
                idx_val = item.get("index", idx)
                text_val = item.get("text") or ""
                ts_val = item.get("timestamp")
            else:
                idx_val = idx
                text_val = str(item)
                ts_val = None
            if not text_val:
                continue
            if not isinstance(idx_val, int):
                try:
                    idx_val = int(idx_val)
                except Exception:
                    idx_val = len(conversation_trace)
            if not isinstance(ts_val, (int, float)):
                ts_val = trace_time
            conversation_trace.append(
                {"index": idx_val, "text": text_val, "timestamp": ts_val}
            )

        metadata_update = dict(response.get("metadata") or {})
        if isinstance(provider_target, dict):
            metadata_update.setdefault("provider", provider_target.get("provider"))
            metadata_update.setdefault("server_url", provider_target.get("base_url"))
            metadata_update.setdefault(
                "provider_runtime",
                provider_target.get("runtime")
                if isinstance(provider_target.get("runtime"), dict)
                else {},
            )
        status_value = "error" if metadata_update.get("error") else "complete"
        metadata_update["status"] = status_value
        metadata_update.setdefault("session_name", session_name)
        metadata_update.setdefault("conversation_id", session_id)
        if mode_used:
            metadata_update.setdefault("mode", mode_used)
        if chat_request.model:
            metadata_update.setdefault("model", chat_request.model)
        metadata_update.setdefault("updated_at", trace_time)
        usage_stats = _normalize_usage_counts(
            metadata_update.get("usage")
            if isinstance(metadata_update.get("usage"), dict)
            else None,
            chat_request.message or "",
            text,
        )
        merged_usage = _merge_usage(
            metadata_update.get("usage")
            if isinstance(metadata_update.get("usage"), dict)
            else None,
            usage_stats,
        )
        if merged_usage is not None:
            metadata_update["usage"] = merged_usage
            # NOTE: Sub-agent resource tracking currently uses per-session token totals.
            # TODO: Replace estimates with model-native usage when available.
            _update_agent_resource_usage(
                request.app,
                session_name,
                merged_usage,
                message_id=message_id,
                session_id=session_name,
                source="chat",
            )
        if rag_metadata:
            rag_section = metadata_update.get("rag")
            if not isinstance(rag_section, dict):
                rag_section = {}
            rag_section.setdefault("matches", rag_metadata)
            metadata_update["rag"] = rag_section
        iso_response_ts = (
            datetime.fromtimestamp(trace_time, tz=timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        _update_conversation_entry(
            session_name,
            message_id,
            {
                "text": text,
                "thought": response.get("thought", ""),
                "thought_trace": conversation_trace,
                "metadata": metadata_update,
                "updated_at": trace_time,
                "iso_timestamp": iso_response_ts,
            },
        )
        if conversation_trace and not response.get("thought_trace"):
            response["thought_trace"] = conversation_trace
        response["metadata"] = metadata_update
        hooks.emit(
            hooks.AFTER_LLM_RESPONSE_EVENT,
            hooks.LLMResponseEvent(
                session_id=session_id,
                response_text=text,
                metadata=dict(metadata_update),
                raw_response=dict(response),
            ),
        )
        pending_title = consume_pending_title(session_name)
        if pending_title:
            display_name = pending_title.get("display_name")
            if display_name:
                metadata_update["session_display_name"] = display_name
                metadata_update["auto_title"] = True
                response["metadata"] = metadata_update

        if response.get("thought") and not analysis_trace:
            try:
                log_thought_delta(session_id, response.get("thought"), message_id, 0)
            except Exception:
                pass
            await publish_console_event(
                request.app,
                {
                    "type": "thought",
                    "content": response.get("thought"),
                    "session_id": session_id,
                },
                default_agent=session_id,
            )
        # Emit suggested tool calls (if any) to the thought stream so the UI
        # can render Accept/Deny/Edit actions before invocation.
        try:
            msg_id = message_id or None
            response["tools_used"] = await _register_tool_proposals(
                request,
                tools=response.get("tools_used") or [],
                session_id=session_name,
                message_id=msg_id,
                model=chat_request.model,
                mode=mode_used,
                default_agent=msg_id or session_id,
            )
        except Exception:
            pass
        for task in response.get("tasks", []) or []:
            await request.app.state.pending_tasks.put(task)
            task_id = str(task.get("id") or task.get("task_id") or uuid4())
            agent_id = task.get("agent_id") or task.get("agent") or task_id
            await publish_console_event(
                request.app,
                {
                    "type": "task",
                    "task_id": task_id,
                    "content": task.get("description", ""),
                    "session_id": session_id,
                    "agent_id": agent_id,
                    "agent_label": task.get("agent") or task.get("name") or agent_id,
                    "status": task.get("status") or "queued",
                },
                default_agent=agent_id,
            )

        # Add assistant response to context
        context.add_message(
            "assistant",
            response.get("text", ""),
            metadata={
                "timestamp": trace_time,
                "iso_timestamp": iso_response_ts,
            },
        )

        pydantic_ctx = ContextSchema(**context.to_dict())
        # Attach a request correlation id to metadata for client-side debugging.
        try:
            rid = get_request_id()
            md = response.get("metadata") or {}
            if isinstance(md, dict):
                md.setdefault("request_id", rid)
                response["metadata"] = md
        except Exception:
            pass

        # Log the assistant response (snippet) for debugging
        try:
            log_chat_response(session_name, text[:200], response.get("metadata"))
        except Exception:
            pass
        try:
            meta = response.get("metadata") if isinstance(response, dict) else None
            meta_payload = meta if isinstance(meta, dict) else {}
            log_timeline_message(
                session_id=session_name,
                message_id=message_id,
                role="ai",
                text=text,
                source="chat",
                metadata=meta_payload,
            )
        except Exception:
            pass
        return ChatResponse(
            message=text,
            thought=response.get("thought", ""),
            tools_used=response.get("tools_used", []),
            metadata=response.get("metadata", {}),
            context=pydantic_ctx,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Chat failed",
            exc_info=True,
            extra={
                "session_id": session_id,
                "prompt_snippet": chat_request.message[:20],
                # pydantic v2: use model_dump() to avoid deprecation
                "payload": chat_request.model_dump(),
            },
        )
        raise HTTPException(
            status_code=500,
            detail=f"Error processing chat request: {e}",
        )


@router.post("/memory/update/")
async def update_memory(request: Request, update_request: MemoryUpdateRequest):
    """
    Updates memory with the given key-value pair.
    """
    # Use the Request object to access the app state
    payload = update_request.model_dump()
    result = request.app.state.memory_manager.update_memory(payload)
    return {"status": "success", "updated": result}


class ChatContinueRequest(BaseModel):
    session_id: str
    message_id: str
    model: Optional[str] = None
    tools: Optional[list[dict[str, Any]]] = None
    thinking: Optional[Union[bool, str]] = None
    mode: Optional[str] = None


def _extract_image_attachment_from_tool_payload(value: Any) -> Optional[Dict[str, Any]]:
    candidate = value
    if isinstance(candidate, dict) and isinstance(candidate.get("attachment"), dict):
        candidate = candidate.get("attachment")
    if not isinstance(candidate, dict):
        return None
    name = str(
        candidate.get("name")
        or candidate.get("filename")
        or candidate.get("label")
        or candidate.get("content_hash")
        or "image"
    ).strip()
    url = str(candidate.get("url") or candidate.get("href") or "").strip()
    content_hash = str(candidate.get("content_hash") or "").strip()
    content_type = str(
        candidate.get("type")
        or candidate.get("content_type")
        or candidate.get("mime_type")
        or ""
    ).strip()
    if not content_type:
        guessed, _ = mimetypes.guess_type(name or url)
        content_type = guessed or ""
    if content_type and not content_type.lower().startswith("image/"):
        return None
    if not content_type and not content_hash:
        return None
    if not url and content_hash:
        safe_name = Path(name or content_hash).name or content_hash
        url = f"/api/attachments/{content_hash}/{safe_name}"
    if not url and not content_hash:
        return None
    attachment: Dict[str, Any] = {
        "name": Path(name or "image").name or "image",
    }
    if url:
        attachment["url"] = url
    if content_hash:
        attachment["content_hash"] = content_hash
    if content_type:
        attachment["type"] = content_type
    for field in ("origin", "relative_path", "capture_source"):
        raw = candidate.get(field)
        if isinstance(raw, str) and raw.strip():
            attachment[field] = raw.strip()
    return attachment


def _collect_tool_result_image_attachments(
    tool_events: Any,
) -> List[Dict[str, Any]]:
    attachments: List[Dict[str, Any]] = []
    seen: set[str] = set()

    def _add_attachment(raw: Any) -> None:
        attachment = _extract_image_attachment_from_tool_payload(raw)
        if not attachment:
            return
        key = str(
            attachment.get("content_hash")
            or attachment.get("url")
            or attachment.get("name")
            or ""
        ).strip()
        if not key or key in seen:
            return
        seen.add(key)
        attachments.append(attachment)

    def _walk(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                _walk(item)
            return
        if not isinstance(value, dict):
            return
        for key in (
            "image_attachments",
            "attachments",
            "images",
            "image_matches",
            "matches",
            "attachment",
            "value",
        ):
            nested = value.get(key)
            if isinstance(nested, list):
                for item in nested:
                    _add_attachment(item)
                    _walk(item)
            elif isinstance(nested, dict):
                _add_attachment(nested)
                _walk(nested)
        nested_data = value.get("data")
        if isinstance(nested_data, (dict, list)):
            _walk(nested_data)
        nested_result = value.get("result")
        if isinstance(nested_result, (dict, list)):
            _walk(nested_result)
        _add_attachment(value)

    _walk(tool_events)
    return attachments


def _stable_tool_continue_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        return [_stable_tool_continue_value(item) for item in value]
    if isinstance(value, dict):
        normalized: Dict[str, Any] = {}
        for key in sorted(value.keys(), key=lambda item: str(item)):
            normalized[str(key)] = _stable_tool_continue_value(value.get(key))
        return normalized
    return str(value)


def _tool_continue_signature(tool_events: Any) -> str:
    if not isinstance(tool_events, list):
        return ""
    normalized: list[dict[str, Any]] = []
    for entry in tool_events:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or entry.get("tool") or "").strip()
        request_id = str(entry.get("id") or entry.get("request_id") or "").strip()
        status = str(entry.get("status") or "").strip().lower()
        args = entry.get("args") if isinstance(entry.get("args"), dict) else {}
        result = entry.get("result") if "result" in entry else None
        normalized.append(
            {
                "id": request_id or None,
                "name": name,
                "status": status,
                "args": _stable_tool_continue_value(args),
                "result": _stable_tool_continue_value(result),
            }
        )
    if not normalized:
        return ""
    try:
        payload = json.dumps(
            normalized,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except Exception:
        return ""
    digest = 2166136261
    for char in payload:
        digest ^= ord(char)
        digest = (digest * 16777619) & 0xFFFFFFFF
    return f"{digest:08x}"


@router.post("/chat/continue", response_model=ChatResponse)
async def chat_continue(request: Request, payload: ChatContinueRequest):
    """Continue an assistant message after tool invocation.

    The client supplies tool results because the websocket tool stream is not
    part of the persisted `/history` transcript used to rehydrate contexts.
    """
    mode_used = None
    if payload.mode is not None:
        mode_raw = str(payload.mode or "").strip().lower()
        if mode_raw:
            allowed_modes = {"api", "server", "local", "dynamic"}
            if mode_raw in allowed_modes:
                llm_service.mode = mode_raw
                mode_used = mode_raw
    if mode_used is None:
        mode_used = getattr(llm_service, "mode", "api")

    session_name = payload.session_id or "default"
    context = llm_service.get_context(session_name)
    if not context.messages:
        try:
            history = conversation_store.load_conversation(session_name)
            for entry in history:
                role = entry.get("role")
                text = entry.get("text") or entry.get("content")
                if not role or not text:
                    continue
                meta = entry.get("metadata") or {}
                if isinstance(meta, dict):
                    meta = dict(meta)
                    saved_attachments = entry.get("attachments")
                    if saved_attachments and not meta.get("attachments"):
                        meta["attachments"] = saved_attachments
                if entry.get("rag") and isinstance(meta, dict):
                    meta.setdefault("rag", {"matches": entry["rag"]})
                context.add_message(role, text, metadata=meta)
        except Exception:
            pass

    generation_ctx = ServiceContext(
        system_prompt=_effective_system_prompt(context.system_prompt, request=request),
        messages=list(context.messages),
        tools=list(context.tools),
        metadata=dict(context.metadata),
    )

    tool_events = payload.tools or []
    tool_continue_signature = _tool_continue_signature(tool_events)
    recalled_image_attachments = _collect_tool_result_image_attachments(tool_events)
    tool_lines: list[str] = []
    for tool in tool_events:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "").strip() or "tool"
        status = str(tool.get("status") or "").strip()
        status_key = status.lower()
        request_id = str(tool.get("id") or tool.get("request_id") or "").strip()
        timestamp = tool.get("timestamp")
        timestamp_text = ""
        if isinstance(timestamp, (int, float)):
            try:
                timestamp_text = (
                    datetime.fromtimestamp(float(timestamp), tz=timezone.utc)
                    .replace(microsecond=0)
                    .isoformat()
                    .replace("+00:00", "Z")
                )
            except Exception:
                timestamp_text = ""
        args = tool.get("args") if isinstance(tool.get("args"), dict) else {}
        result = tool.get("result") if "result" in tool else None
        if result is None and status_key == "denied":
            result = _tool_outcome_payload("denied", "Denied by user.")
        elif result is None and status_key == "error":
            result = _tool_outcome_payload("error", "Tool error.")
        elif result is None and status_key in {"pending", "proposed"}:
            result = _tool_outcome_payload(status_key, "Awaiting approval.")
        if result is None:
            continue
        try:
            args_text = json.dumps(args, ensure_ascii=False)
        except Exception:
            args_text = str(args)
        try:
            result_text = json.dumps(result, ensure_ascii=False)
        except Exception:
            result_text = str(result)
        suffix = f" status={status}" if status else ""
        request_id_suffix = f" rid={request_id}" if request_id else ""
        timestamp_suffix = f" ts={timestamp_text}" if timestamp_text else ""
        tool_lines.append(
            f"- {name}{suffix}{request_id_suffix}{timestamp_suffix} "
            f"args={args_text} result={result_text}"
        )

    if tool_lines:
        generation_ctx.add_message(
            "system",
            (
                "Tool outcomes (chronological). Treat these as authoritative events. "
                "Denied/proposed tools did not execute; invoked outcomes reflect file/system state "
                "at their event timestamp.\n"
            )
            + "\n".join(tool_lines),
            metadata={"ephemeral": True, "tool_results": True},
        )

    generation_ctx.add_message(
        "user",
        "Continue your previous response using the tool outcomes above. "
        "Answer the original user message; do not mention this instruction.",
        metadata={"ephemeral": True, "continuation": True},
    )

    provider_target: Optional[Dict[str, Any]] = None
    effective_mode = mode_used
    effective_model = payload.model
    if mode_used == "local":
        try:
            provider_target = _resolve_provider_inference_target_or_none(
                request.app.state.config,
                requested_model=payload.model,
                allow_auto_start=True,
            )
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        if isinstance(provider_target, dict):
            effective_mode = "server"
            resolved_model = provider_target.get("model")
            if isinstance(resolved_model, str) and resolved_model.strip():
                effective_model = resolved_model.strip()

    previous_service_mode = getattr(llm_service, "mode", mode_used)
    llm_service.mode = effective_mode
    try:
        generate_kwargs: Dict[str, Any] = {}
        reasoning = _reasoning_payload(payload.thinking)
        if reasoning is not None:
            generate_kwargs["reasoning"] = reasoning
        if isinstance(provider_target, dict):
            server_url = str(provider_target.get("base_url") or "").strip()
            if server_url:
                generate_kwargs["server_url"] = server_url
            api_token = str(provider_target.get("api_token") or "").strip()
            if api_token:
                generate_kwargs["api_key"] = api_token
        response = await asyncio.to_thread(
            llm_service.generate,
            [],
            session_id=session_name,
            model=effective_model,
            attachments=recalled_image_attachments,
            response_format="harmony"
            if request.app.state.config.get("harmony_format")
            else None,
            context=generation_ctx,
            **generate_kwargs,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        llm_service.mode = mode_used if mode_used else previous_service_mode

    def _tool_signature(entry: Dict[str, Any]) -> Optional[tuple[str, str]]:
        if not isinstance(entry, dict):
            return None
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            return None
        args = entry.get("args") if isinstance(entry.get("args"), dict) else {}
        try:
            args_key = json.dumps(args, sort_keys=True, ensure_ascii=False)
        except Exception:
            args_key = str(args)
        return (name.strip(), args_key)

    def _text_is_placeholders(value: str) -> bool:
        if not isinstance(value, str):
            return True
        trimmed = value.strip()
        if not trimmed:
            return True
        scrubbed = re.sub(r"\[\[tool_call:\d+\]\]", "", trimmed).strip()
        return not scrubbed

    # Guard against tool-call loops: if the model only re-proposes the same tools
    # after we already supplied results, retry once with tools disabled.
    provided_tool_sigs = set()
    for tool in payload.tools or []:
        sig = _tool_signature(tool) if isinstance(tool, dict) else None
        if sig:
            provided_tool_sigs.add(sig)

    def _is_repeat_tool_loop(resp: Dict[str, Any]) -> bool:
        resp_tools = resp.get("tools_used") or []
        if not isinstance(resp_tools, list) or not resp_tools:
            return False
        if not provided_tool_sigs:
            return False
        if not all(
            _tool_signature(tool) in provided_tool_sigs
            for tool in resp_tools
            if isinstance(tool, dict)
        ):
            return False
        return _text_is_placeholders(resp.get("text") or "")

    unresolved_loop_prefix = "I couldn't finish the continuation from tool results."

    def _compact_summary_text(value: Any, *, limit: int = 120) -> Optional[str]:
        if not isinstance(value, str):
            return None
        text_value = re.sub(r"\s+", " ", value).strip()
        if not text_value:
            return None
        if len(text_value) > limit:
            return text_value[: max(0, limit - 3)].rstrip() + "..."
        return text_value

    def _normalize_tool_status(status_value: Any) -> str:
        status_key = str(status_value or "").strip().lower()
        if status_key in {"canceled", "cancelled"}:
            return "cancelled"
        if status_key in {"denied", "rejected"}:
            return "denied"
        if status_key in {"error", "failed", "failure"}:
            return "error"
        if status_key in {"timeout", "timed_out"}:
            return "timeout"
        if status_key in {
            "invoked",
            "ok",
            "success",
            "succeeded",
            "complete",
            "completed",
        }:
            return "invoked"
        if status_key in {"pending", "proposed"}:
            return status_key
        return status_key

    def _suggestion_keys(result_value: Any) -> list[str]:
        if not isinstance(result_value, dict):
            return []
        keys: list[str] = []
        seen: set[str] = set()

        def _add_key(value: Any) -> None:
            if not isinstance(value, str):
                return
            cleaned = value.strip()
            if not cleaned or cleaned in seen:
                return
            seen.add(cleaned)
            keys.append(cleaned)

        suggestions_detail = result_value.get("suggestions_detail")
        if isinstance(suggestions_detail, list):
            for entry in suggestions_detail:
                if not isinstance(entry, dict):
                    continue
                _add_key(entry.get("key"))

        suggestions = result_value.get("suggestions")
        if isinstance(suggestions, list):
            for entry in suggestions:
                _add_key(entry)

        recent_keys = result_value.get("recent_keys")
        if isinstance(recent_keys, list):
            for entry in recent_keys:
                _add_key(entry)

        return keys

    def _tool_result_dict_excerpt(result_value: Any) -> Optional[str]:
        if not isinstance(result_value, dict):
            return None

        error_value = _compact_summary_text(result_value.get("error"))
        if error_value:
            if error_value.lower() == "not_found":
                suggestion_keys = _suggestion_keys(result_value)
                if suggestion_keys:
                    hint = ", ".join(suggestion_keys[:3])
                    summarized = _compact_summary_text(f"not_found (try: {hint})")
                    if summarized:
                        return summarized
            return error_value

        for key in ("detail", "message", "summary", "text", "answer"):
            compact = _compact_summary_text(result_value.get(key))
            if compact:
                return compact
        return None

    def _tool_result_has_error(result_value: Any) -> bool:
        if not isinstance(result_value, dict):
            return False
        if _compact_summary_text(result_value.get("error")):
            return True
        wrapped = result_value.get("data")
        if isinstance(wrapped, dict) and _compact_summary_text(wrapped.get("error")):
            return True
        return False

    def _tool_result_excerpt(result_value: Any) -> Optional[str]:
        if isinstance(result_value, str):
            return _compact_summary_text(result_value)
        if isinstance(result_value, list):
            return f"{len(result_value)} items" if result_value else None
        if not isinstance(result_value, dict):
            return None

        direct_excerpt = _tool_result_dict_excerpt(result_value)
        if direct_excerpt:
            return direct_excerpt

        wrapped = result_value.get("data")
        if isinstance(wrapped, list):
            return f"{len(wrapped)} items" if wrapped else None
        if isinstance(wrapped, dict):
            wrapped_excerpt = _tool_result_dict_excerpt(wrapped)
            if wrapped_excerpt:
                return wrapped_excerpt
            results = wrapped.get("results")
            if isinstance(results, list) and results:
                first = results[0] if isinstance(results[0], dict) else {}
                title = _compact_summary_text(
                    first.get("title") or first.get("name") or first.get("label")
                )
                query = _compact_summary_text(wrapped.get("query") or wrapped.get("q"))
                if query and title:
                    return f'"{query}" -> {title}'
                if query:
                    return f'"{query}"'
                return f"{len(results)} results"
            for list_key in ("items", "matches"):
                items = wrapped.get(list_key)
                if isinstance(items, list) and items:
                    return f"{len(items)} {list_key}"
            count_value = wrapped.get("count")
            if isinstance(count_value, int):
                return f"count={count_value}"
        return None

    def _tool_outcome_lines(entries: Any) -> list[str]:
        if not isinstance(entries, list):
            return []
        lines: list[str] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name_raw = entry.get("name")
            try:
                tool_name = str(name_raw).strip()
            except Exception:
                tool_name = ""
            tool_name = tool_name or "tool"
            status_key = _normalize_tool_status(entry.get("status"))
            if status_key == "invoked" and _tool_result_has_error(entry.get("result")):
                status_key = "error"
            excerpt = _tool_result_excerpt(entry.get("result"))
            if not excerpt:
                excerpt = _compact_summary_text(entry.get("error"))

            if status_key == "denied":
                line = f"{tool_name}: denied"
                if excerpt and excerpt.lower() not in {"denied", "denied by user"}:
                    line = f"{line} - {excerpt}"
            elif status_key == "cancelled":
                line = f"{tool_name}: cancelled"
                if excerpt:
                    line = f"{line} - {excerpt}"
            elif status_key == "error":
                line = f"{tool_name}: error"
                if excerpt:
                    line = f"{line} - {excerpt}"
            elif status_key == "timeout":
                line = f"{tool_name}: timeout"
                if excerpt:
                    line = f"{line} - {excerpt}"
            elif status_key == "invoked":
                line = f"{tool_name}: {excerpt}" if excerpt else f"{tool_name}: invoked"
            elif status_key in {"pending", "proposed"}:
                line = f"{tool_name}: {status_key}"
            else:
                line = f"{tool_name}: {excerpt}" if excerpt else tool_name
            lines.append(line)

        max_lines = 4
        if len(lines) > max_lines:
            extra = len(lines) - max_lines
            lines = lines[:max_lines] + [f"... {extra} more step(s)"]
        return lines

    if _is_repeat_tool_loop(response):
        try:
            retry_ctx = ServiceContext(
                system_prompt=context.system_prompt,
                messages=list(generation_ctx.messages),
                tools=[],
                metadata=dict(generation_ctx.metadata),
            )
            retry_ctx.add_message(
                "system",
                "Tools are unavailable for this continuation. Respond with a final answer using the tool outcomes above.",
                metadata={"ephemeral": True, "continuation": True},
            )
            retry_prev_mode = getattr(llm_service, "mode", mode_used)
            llm_service.mode = effective_mode
            try:
                retry_response = await asyncio.to_thread(
                    llm_service.generate,
                    [],
                    session_id=session_name,
                    model=effective_model,
                    response_format="harmony"
                    if request.app.state.config.get("harmony_format")
                    else None,
                    context=retry_ctx,
                    **generate_kwargs,
                )
            finally:
                llm_service.mode = mode_used if mode_used else retry_prev_mode
            if isinstance(retry_response, dict):
                retry_meta = dict(retry_response.get("metadata") or {})
                retry_meta["retry_without_tools"] = True
                retry_response["metadata"] = retry_meta
                response = retry_response
        except Exception:
            pass
        if _is_repeat_tool_loop(response):
            unresolved_text = unresolved_loop_prefix
            outcome_lines = _tool_outcome_lines(payload.tools or [])
            if outcome_lines:
                unresolved_text = f"{unresolved_text}\n\n" + "\n".join(
                    f"- {line}" for line in outcome_lines
                )
            response["text"] = unresolved_text
            retry_meta = dict(response.get("metadata") or {})
            retry_meta["unresolved_tool_loop"] = True
            response["metadata"] = retry_meta

    response_tools_used = (
        response.get("tools_used")
        if isinstance(response.get("tools_used"), list)
        else []
    )
    response_meta = (
        response.get("metadata") if isinstance(response.get("metadata"), dict) else {}
    )
    if response_tools_used and not response_meta.get("unresolved_tool_loop"):
        text = _pending_tool_placeholder_text(response_tools_used)
        response["text"] = text
        response_meta = dict(response_meta)
        response_meta["tool_response_pending"] = True
        response["metadata"] = response_meta
    else:
        text = response.get("text") or ""
        if not text:
            if response_tools_used:
                text = _pending_tool_placeholder_text(response_tools_used)
            else:
                text = "I couldn't continue the response. Try regenerate."
            response["text"] = text

    metadata_update = dict(response.get("metadata") or {})
    if isinstance(provider_target, dict):
        metadata_update.setdefault("provider", provider_target.get("provider"))
        metadata_update.setdefault("server_url", provider_target.get("base_url"))
        metadata_update.setdefault(
            "provider_runtime",
            provider_target.get("runtime")
            if isinstance(provider_target.get("runtime"), dict)
            else {},
        )
    status_value = "error" if metadata_update.get("error") else "complete"
    metadata_update["status"] = status_value
    metadata_update.setdefault("session_name", session_name)
    try:
        metadata_update.setdefault(
            "conversation_id",
            conversation_store.get_or_create_conversation_id(session_name),
        )
    except Exception:
        pass
    if mode_used:
        metadata_update.setdefault("mode", mode_used)
    if payload.model:
        metadata_update.setdefault("model", payload.model)
    metadata_update.setdefault("tool_continued", True)
    if tool_continue_signature:
        metadata_update["tool_continue_signature"] = tool_continue_signature
    usage_stats = _normalize_usage_counts(
        metadata_update.get("usage")
        if isinstance(metadata_update.get("usage"), dict)
        else None,
        "\n".join(tool_lines) if tool_lines else "continue",
        text,
    )
    merged_usage = _merge_usage(
        metadata_update.get("usage")
        if isinstance(metadata_update.get("usage"), dict)
        else None,
        usage_stats,
    )
    if merged_usage is not None:
        metadata_update["usage"] = merged_usage
        # NOTE: Tool continuations are treated as sub-agent branches; track tokens per branch.
        _update_agent_resource_usage(
            request.app,
            session_name,
            merged_usage,
            message_id=payload.message_id,
            session_id=session_name,
            source="tool_continue",
        )
    response["metadata"] = metadata_update

    def _merge_continuation_text(existing: str, continuation: str) -> str:
        existing_text = existing or ""
        continuation_text = continuation or ""
        if not continuation_text.strip():
            return existing_text
        if not existing_text.strip():
            return continuation_text
        if existing_text.rstrip().endswith(continuation_text.strip()):
            return existing_text
        return f"{existing_text.rstrip()}\n\n{continuation_text}".strip()

    def _should_replace_continuation_text(existing: str) -> bool:
        existing_text = (existing or "").strip()
        if not existing_text:
            return True
        placeholder_prefixes = (
            "Requested tool ",
            "Requested tools ",
            "Tool results:",
            "Tool results are available.",
            unresolved_loop_prefix,
        )
        if any(existing_text.startswith(prefix) for prefix in placeholder_prefixes):
            return True
        scrubbed = re.sub(r"\[\[tool_call:\d+\]\]", "", existing_text).strip()
        return not scrubbed

    merged_text = text
    try:
        conv = conversation_store.load_conversation(session_name)
        if isinstance(conv, list):
            for item in conv:
                if isinstance(item, dict) and item.get("id") == payload.message_id:
                    existing_text = item.get("text") or ""
                    if not isinstance(existing_text, str):
                        existing_text = str(existing_text)
                    if _should_replace_continuation_text(existing_text):
                        merged_text = text
                    else:
                        merged_text = _merge_continuation_text(existing_text, text)
                    break
    except Exception:
        merged_text = text

    # Best-effort: keep in-memory context moving forward.
    try:
        context.add_message("assistant", text, metadata={"ephemeral": True})
        llm_service.set_context(context, session_name)
    except Exception:
        pass

    # Persist continuation so reload/history sees the post-tool answer, not the
    # initial "Requested tool …" placeholder.
    try:
        trace_time = time.time()
        iso_response_ts = (
            datetime.fromtimestamp(trace_time, tz=timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        metadata_update.setdefault("updated_at", trace_time)
        _update_conversation_entry(
            session_name,
            payload.message_id,
            {
                "text": merged_text,
                "metadata": metadata_update,
                "updated_at": trace_time,
                "iso_timestamp": iso_response_ts,
            },
        )
    except Exception:
        pass

    pydantic_ctx = ContextSchema(**context.to_dict())
    if response.get("thought"):
        try:
            log_thought_delta(
                session_name, response.get("thought"), payload.message_id, 0
            )
        except Exception:
            pass
        try:
            await publish_console_event(
                request.app,
                {
                    "type": "thought",
                    "content": response.get("thought"),
                    "session_id": session_name,
                    "message_id": payload.message_id,
                },
                default_agent=session_name,
            )
        except Exception:
            pass
    # Emit suggested tool calls (if any) to the thought stream so the UI can
    # render Accept/Deny/Edit actions before invocation.
    try:
        msg_id = payload.message_id or None
        response["tools_used"] = await _register_tool_proposals(
            request,
            tools=response.get("tools_used") or [],
            session_id=session_name,
            message_id=msg_id,
            model=payload.model,
            mode=mode_used,
            default_agent=msg_id or session_name,
        )
    except Exception:
        pass
    return ChatResponse(
        message=text,
        thought=response.get("thought", "") or "",
        tools_used=response.get("tools_used", []) or [],
        metadata=metadata_update,
        context=pydantic_ctx,
    )


# Extended memory management endpoints


class MemoryItemUpsert(BaseModel):
    value: Any
    importance: Optional[float] = None
    evergreen: Optional[bool] = None
    end_time: Optional[float] = None
    archived: Optional[bool] = None
    lifecycle: Optional[str] = None
    grounded_at: Optional[float] = None
    occurs_at: Optional[float] = None
    review_at: Optional[float] = None
    decay_at: Optional[float] = None
    sensitivity: Optional[str] = None
    hint: Optional[str] = None
    pinned: Optional[bool] = None
    importance_floor: Optional[float] = None
    # RAG flags (optional): vectorize stores the memory in the knowledge index;
    # rag_excluded keeps it stored but omits it from default retrieval.
    vectorize: Optional[bool] = None
    rag_excluded: Optional[bool] = None


class MemoryRename(BaseModel):
    new_key: str


class MemoryDecay(BaseModel):
    rate: Optional[float] = 0.95


class MemorySearchRequest(BaseModel):
    query: str
    limit: Optional[int] = 10


class MemoryRehydrateRequest(BaseModel):
    limit: Optional[int] = None
    allow_protected: bool = False
    allow_secret: bool = False
    include_archived: bool = False
    dry_run: bool = False


def _memory_value_to_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    try:
        return json.dumps(value, ensure_ascii=False).strip()
    except Exception:
        return str(value).strip()


def _memory_allowed_for_rag(
    item: Dict[str, Any],
    *,
    allow_protected: bool = False,
    allow_secret: bool = False,
) -> bool:
    lvl = str(item.get("sensitivity", "mundane")).lower()
    if lvl == "secret":
        return bool(allow_secret)
    if lvl == "protected":
        return bool(allow_protected)
    return True


def _vectorize_memory_key(
    request: Request,
    key: str,
    *,
    allow_protected: bool = False,
    allow_secret: bool = False,
) -> Optional[str]:
    mgr = request.app.state.memory_manager
    item = mgr.get_item(key, touch=False)
    if item is None:
        return None
    if not _memory_allowed_for_rag(
        item, allow_protected=allow_protected, allow_secret=allow_secret
    ):
        return None
    value_text = _memory_value_to_text(item.get("value"))
    if not value_text:
        return None
    # Include the memory key + hint in the embedded text so key-based queries
    # (e.g., "tea_party_menu_ideas_2025-11-24") can still retrieve the entry via
    # semantic search. Metadata alone is not embedded.
    parts = [f"key: {key}"]
    hint = item.get("hint")
    if hint:
        parts.append(f"hint: {hint}")
    parts.append(value_text)
    text = "\n".join(parts)
    service = _get_rag_service()
    meta: Dict[str, Any] = {
        "kind": "memory",
        "type": "memory",
        "memory_key": key,
        "key": key,
        "title": key,
        "source": f"memory:{key}",
        "importance": item.get("importance"),
        "importance_floor": item.get("importance_floor"),
        "pinned": item.get("pinned"),
        "lifecycle": item.get("lifecycle"),
        "grounded_at": item.get("grounded_at"),
        "occurs_at": item.get("occurs_at"),
        "review_at": item.get("review_at"),
        "decay_at": item.get("decay_at"),
        "pruned_at": item.get("pruned_at"),
        "last_confirmed_at": item.get("last_confirmed_at"),
        "sensitivity": item.get("sensitivity"),
        "hint": item.get("hint"),
        "updated_at": item.get("updated_at"),
    }
    if item.get("rag_excluded") is not None:
        meta["rag_excluded"] = bool(item.get("rag_excluded"))
    doc_id = service.ingest_text(text, meta)
    if doc_id:
        mgr.update_item_fields(
            key,
            {
                "vectorize": True,
                "vectorized_at": time.time(),
                "rag_doc_id": doc_id,
            },
        )
    return doc_id


def _forget_memory_key(request: Request, key: str) -> bool:
    mgr = request.app.state.memory_manager
    item = mgr.get_item(key, include_pruned=True, touch=False)
    if item is None:
        return False
    source = f"memory:{key}"
    try:
        service = _get_rag_service()
        service.delete_source(source)
    except Exception:
        pass
    mgr.update_item_fields(key, {"vectorize": False, "vectorized_at": None})
    return True


@router.get("/memory")
async def memory_list(
    request: Request,
    detailed: bool = False,
    for_external: bool = False,
    allow_protected: bool = False,
):
    mgr = request.app.state.memory_manager
    if not detailed:
        return {"keys": mgr.list_items()}
    if for_external:
        exported = mgr.export_items(for_external=True, allow_protected=allow_protected)
        out = [{"key": k, **v} for k, v in exported.items()]
        return {"items": out}
    else:
        out = [{"key": k, **item} for k, item in mgr.iter_items(touch=False)]
        return {"items": out}


@router.get("/memory/graph")
async def memory_graph(
    request: Request,
    limit: int = Query(default=72, ge=1, le=240),
    include_archived: bool = False,
    focus_key: str | None = None,
    include_thread_projection: bool = True,
):
    mgr = request.app.state.memory_manager
    items: list[dict[str, Any]] = []
    for key, item in mgr.iter_items(
        include_pruned=include_archived,
        touch=False,
    ):
        if item is None:
            continue
        items.append({"key": key, **item})
    thread_summary: dict[str, Any] | None = None
    if include_thread_projection:
        try:
            thread_summary = threads_service.read_summary()
        except Exception:
            logger.debug("memory graph thread projection unavailable", exc_info=True)
    graph = memory_graph_service.build_memory_graph(
        items,
        limit=limit,
        focus_key=focus_key,
        thread_summary=thread_summary,
    )
    return {"graph": graph}


@router.get("/memory/{key}")
async def memory_get(
    request: Request,
    key: str,
    for_external: bool = False,
    allow_protected: bool = False,
):
    mgr = request.app.state.memory_manager
    item = mgr.get_item(key, touch=False)
    if item is None:
        raise HTTPException(status_code=404, detail="not found")
    if for_external:
        lvl = str(item.get("sensitivity", "mundane")).lower()
        if lvl == "secret":
            # redact entirely
            safe = {k: v for k, v in item.items() if k != "value"}
            safe["redacted"] = True
            return {"key": key, **safe}
        if lvl == "protected" and not allow_protected:
            raise HTTPException(
                status_code=403, detail="protected item not allowed in external context"
            )
    return {"key": key, **item}


@router.post("/memory/{key}")
async def memory_upsert(request: Request, key: str, payload: MemoryItemUpsert):
    mgr = request.app.state.memory_manager
    existing = mgr.store.get(key) if hasattr(mgr, "store") else None
    was_vectorized = bool(
        isinstance(existing, dict)
        and (existing.get("vectorize") or existing.get("vectorized_at"))
    )
    extra_kwargs: dict[str, Any] = {}
    fields_set = getattr(payload, "model_fields_set", set())
    if "pinned" in fields_set:
        extra_kwargs["pinned"] = payload.pinned
    if "importance_floor" in fields_set:
        extra_kwargs["importance_floor"] = payload.importance_floor
    item = mgr.upsert_item(
        key,
        payload.value,
        payload.importance,
        payload.evergreen,
        payload.end_time,
        payload.archived,
        payload.sensitivity,
        payload.hint,
        lifecycle=payload.lifecycle,
        grounded_at=payload.grounded_at,
        occurs_at=payload.occurs_at,
        review_at=payload.review_at,
        decay_at=payload.decay_at,
        **extra_kwargs,
    )
    if "rag_excluded" in fields_set:
        mgr.update_item_fields(key, {"rag_excluded": bool(payload.rag_excluded)})
        item = mgr.get_item(key, include_pruned=True, touch=False) or item
    if "vectorize" in fields_set and payload.vectorize is False:
        _forget_memory_key(request, key)
        item = mgr.get_item(key, include_pruned=True, touch=False) or item
    else:
        should_vectorize = (
            bool(payload.vectorize) if "vectorize" in fields_set else was_vectorized
        )
        if should_vectorize and item.get("pruned_at") is None:
            _vectorize_memory_key(request, key)
            item = mgr.get_item(key, include_pruned=True, touch=False) or item
    # emit a thought event for debug/visibility
    try:
        await publish_console_event(
            request.app,
            {
                "type": "thought",
                "content": f"memory upserted: {key}",
            },
        )
    except Exception:
        pass
    return {"key": key, **item}


@router.post("/memory/{key}/rename")
async def memory_rename(request: Request, key: str, payload: MemoryRename):
    mgr = request.app.state.memory_manager
    new_key = str(payload.new_key or "").strip()
    if not new_key:
        raise HTTPException(status_code=400, detail="new_key required")
    if new_key == key:
        item = mgr.get_item(key, include_pruned=True, touch=False)
        if item is None:
            raise HTTPException(status_code=404, detail="not found")
        return {"key": key, **item}

    store = getattr(mgr, "store", None)
    if not isinstance(store, dict):
        raise HTTPException(status_code=503, detail="memory store unavailable")
    if new_key in store:
        raise HTTPException(status_code=409, detail="target already exists")
    existing = store.get(key)
    if existing is None:
        raise HTTPException(status_code=404, detail="not found")

    now = time.time()
    if isinstance(existing, dict):
        snapshot = copy.deepcopy(existing)
    else:
        snapshot = {
            "value": existing,
            "importance": 1.0,
            "created_at": now,
            "updated_at": now,
            "last_accessed_at": now,
        }

    should_vectorize = bool(
        isinstance(snapshot, dict)
        and (snapshot.get("vectorize") or snapshot.get("vectorized_at"))
        and snapshot.get("pruned_at") is None
    )
    if isinstance(snapshot, dict):
        snapshot["updated_at"] = now
        snapshot["last_accessed_at"] = now
        snapshot["vectorize"] = True if should_vectorize else False
        snapshot["vectorized_at"] = None
        snapshot["rag_doc_id"] = None

    if should_vectorize:
        try:
            _forget_memory_key(request, key)
        except Exception:
            pass

    store.pop(key, None)
    store[new_key] = snapshot
    try:
        mgr._persist()
    except Exception:
        pass
    try:
        mgr._emit_memory_hook(new_key, "rename_item", snapshot)
    except Exception:
        pass

    if should_vectorize:
        lvl = (
            str(snapshot.get("sensitivity", "mundane")).lower()
            if isinstance(snapshot, dict)
            else "mundane"
        )
        _vectorize_memory_key(
            request,
            new_key,
            allow_protected=lvl == "protected",
            allow_secret=lvl == "secret",
        )

    item = mgr.get_item(new_key, include_pruned=True, touch=False)
    if item is None:
        raise HTTPException(status_code=500, detail="rename failed")
    return {"key": new_key, **item}


class MemoryRagRehydrate(BaseModel):
    limit: Optional[int] = None
    allow_protected: bool = False
    allow_secret: bool = False
    include_archived: bool = False
    dry_run: bool = False


@router.post("/memory/rag/rehydrate")
async def memory_rag_rehydrate(request: Request, payload: MemoryRagRehydrate):
    """Synchronously vectorize existing memories into the knowledge index.

    This is a non-Celery fallback so local dev runs can still populate RAG.
    """
    mgr = request.app.state.memory_manager
    keys = mgr.list_items(include_pruned=payload.include_archived)
    max_items = None
    if payload.limit is not None:
        try:
            max_items = max(0, int(payload.limit))
        except Exception:
            max_items = None
    scanned = 0
    updated = 0
    skipped = 0
    for key in keys:
        if max_items is not None and scanned >= max_items:
            break
        item = mgr.get_item(
            key,
            include_pruned=payload.include_archived,
            touch=False,
        )
        if item is None:
            continue
        if getattr(mgr, "lifecycle_multiplier", None):
            try:
                if float(mgr.lifecycle_multiplier(item)) <= 0:
                    skipped += 1
                    continue
            except Exception:
                pass
        if not _memory_allowed_for_rag(
            item,
            allow_protected=payload.allow_protected,
            allow_secret=payload.allow_secret,
        ):
            skipped += 1
            continue
        text = _memory_value_to_text(item.get("value"))
        if not text:
            skipped += 1
            continue
        scanned += 1
        if payload.dry_run:
            continue
        doc_id = _vectorize_memory_key(
            request,
            key,
            allow_protected=payload.allow_protected,
            allow_secret=payload.allow_secret,
        )
        if doc_id:
            updated += 1
        else:
            skipped += 1
    return {"scanned": scanned, "reindexed": updated, "skipped": skipped}


class MemoryRagToggle(BaseModel):
    value: bool = True


@router.post("/memory/{key}/memorize")
async def memory_rag_memorize(request: Request, key: str, payload: MemoryRagToggle):
    if not payload.value:
        ok = _forget_memory_key(request, key)
        if not ok:
            raise HTTPException(status_code=404, detail="not found")
        return {"status": "forgotten"}
    doc_id = _vectorize_memory_key(request, key)
    if not doc_id:
        raise HTTPException(status_code=400, detail="memory not vectorizable")
    return {"status": "memorized", "id": doc_id}


@router.post("/memory/{key}/exclude")
async def memory_rag_exclude(request: Request, key: str, payload: MemoryRagToggle):
    mgr = request.app.state.memory_manager
    item = mgr.get_item(key, include_pruned=True, touch=False)
    if item is None:
        raise HTTPException(status_code=404, detail="not found")
    mgr.update_item_fields(key, {"rag_excluded": bool(payload.value)})
    item = mgr.get_item(key, include_pruned=True, touch=False) or {}
    if (
        bool(item.get("vectorize") or item.get("vectorized_at"))
        and item.get("pruned_at") is None
    ):
        _vectorize_memory_key(request, key)
    return {"status": "updated", "rag_excluded": bool(payload.value)}


@router.delete("/memory/{key}")
async def memory_delete(request: Request, key: str):
    mgr = request.app.state.memory_manager
    try:
        _forget_memory_key(request, key)
    except Exception:
        pass
    ok = mgr.delete_item(key)
    if not ok:
        raise HTTPException(status_code=404, detail="not found")
    return {"status": "deleted"}


@router.post("/memory/search")
async def memory_search(request: Request, payload: MemorySearchRequest):
    query = (payload.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query is required")
    mgr = getattr(request.app.state, "memory_manager", None)
    if mgr is None:
        raise HTTPException(status_code=503, detail="Memory manager unavailable")
    limit = payload.limit or 10
    try:
        limit = max(1, min(int(limit), 200))
    except Exception:
        limit = 10
    q_lower = query.lower()
    results: list[dict[str, Any]] = []
    for key, item in mgr.iter_items(touch=False):
        if not item:
            continue
        value = item.get("value")
        if isinstance(value, str):
            text = value
        else:
            try:
                text = json.dumps(value, ensure_ascii=False)
            except Exception:
                text = str(value)
        if q_lower in text.lower():
            snippet = text if len(text) <= 240 else text[:237] + "..."
            results.append(
                {
                    "key": key,
                    "snippet": snippet,
                    "importance": item.get("importance"),
                    "updated_at": item.get("updated_at"),
                }
            )
            if len(results) >= limit:
                break
    return {"results": results, "query": query}


@router.post("/memory/decay")
async def memory_decay(request: Request, payload: MemoryDecay):
    mgr = request.app.state.memory_manager
    sweep = mgr.sweep_lifecycle()
    return {
        "status": "swept",
        "deprecated": True,
        "detail": "memory lifecycle hooks run automatically",
        "sweep": sweep,
    }


@router.post("/memory/rehydrate")
async def memory_rehydrate(payload: MemoryRehydrateRequest):
    args = {}
    if payload.limit is not None:
        try:
            args["limit"] = max(1, int(payload.limit))
        except Exception:
            args["limit"] = 1
    task = rehydrate_memories_task.delay(**args)
    return {"task_id": task.id}


class MemoryArchive(BaseModel):
    archived: bool = True


@router.post("/memory/{key}/archive")
async def memory_archive(request: Request, key: str, payload: MemoryArchive):
    mgr = request.app.state.memory_manager
    item = mgr.archive_item(key, payload.archived)
    if item is None:
        raise HTTPException(status_code=404, detail="not found")
    if (
        bool(item.get("vectorize") or item.get("vectorized_at"))
        and item.get("pruned_at") is None
    ):
        _vectorize_memory_key(request, key, allow_protected=True, allow_secret=False)
    return {"key": key, **item}


@router.get("/tools/")
async def get_tools(request: Request):
    """
    Returns the list of available tools.
    """
    return {"tools": request.app.state.memory_manager.list_tools()}


@router.get("/tools/specs")
async def get_tool_specs(request: Request):
    """Return UI-facing tool schemas for the currently-registered tools."""
    try:
        from app.tool_specs import get_tool_specs as _get_specs

        tools_list = request.app.state.memory_manager.list_tools()
        return {"tools": _get_specs(list(tools_list) if tools_list else [])}
    except Exception:
        # Never hard fail tool discovery; the UI can fall back to raw JSON.
        return {"tools": []}


class ToolRegistration(BaseModel):
    name: str


@router.post("/tools/register")
async def register_tool(request: Request, payload: ToolRegistration):
    """Register a built-in tool by name."""
    func = tools.BUILTIN_TOOLS.get(payload.name)
    if func is None:
        raise HTTPException(status_code=400, detail="Unknown tool")
    request.app.state.memory_manager.register_tool(payload.name, func)
    return {"status": "registered", "tool": payload.name}


class ToolInvoke(BaseModel):
    name: str
    args: Optional[Dict[str, Any]] = None
    chain_id: Optional[str] = None
    session_id: Optional[str] = None
    message_id: Optional[str] = None


class ToolProposal(BaseModel):
    name: str
    args: Optional[Dict[str, Any]] = None
    session_id: Optional[str] = None
    message_id: Optional[str] = None
    chain_id: Optional[str] = None


class ToolDecision(BaseModel):
    request_id: str
    decision: Literal["accept", "deny"]
    args: Optional[Dict[str, Any]] = None
    name: Optional[str] = None
    session_id: Optional[str] = None
    message_id: Optional[str] = None
    chain_id: Optional[str] = None


class ToolSchedule(BaseModel):
    request_id: str
    event_id: str
    args: Optional[Dict[str, Any]] = None
    name: Optional[str] = None
    prompt: Optional[str] = None
    conversation_mode: Optional[str] = None
    session_id: Optional[str] = None
    message_id: Optional[str] = None
    chain_id: Optional[str] = None


@router.post("/tools/invoke")
async def invoke_tool(request: Request, payload: ToolInvoke):
    """Invoke a previously registered tool."""
    user = request.headers.get("X-User", "anonymous")
    raw_args = payload.args or {}
    model_hint, mode_hint = _lookup_message_runtime_hints(
        payload.session_id,
        payload.message_id or payload.chain_id,
    )
    action_context = _build_action_context(
        session_id=payload.session_id,
        message_id=payload.message_id,
        chain_id=payload.chain_id,
        model=model_hint,
        mode=mode_hint,
    )
    try:
        _, args = normalize_and_sanitize_tool_args(payload.name, raw_args)
        signature = generate_signature(user, payload.name, args)
        raw_result = request.app.state.memory_manager.invoke_tool(
            payload.name,
            user=user,
            signature=signature,
            _action_context=action_context,
            **args,
        )
        result = _tool_outcome_payload("invoked", data=raw_result, ok=True)
        tool_invocations_total.labels(payload.name, "ok").inc()
        _emit_tool_hook(
            payload.name,
            "invoked",
            args=args,
            result=result,
            session_id=payload.session_id,
            message_id=payload.message_id or payload.chain_id,
        )
    except ValueError as e:
        tool_invocations_total.labels(payload.name, "bad_request").inc()
        log_tool_event(
            payload.session_id,
            payload.name,
            "error",
            args=raw_args,
            result=str(e),
            message_id=payload.message_id or payload.chain_id,
        )
        _emit_tool_hook(
            payload.name,
            "error",
            args=raw_args,
            result=str(e),
            session_id=payload.session_id,
            message_id=payload.message_id or payload.chain_id,
        )
        raise HTTPException(status_code=400, detail=str(e))
    except PermissionError:
        tool_invocations_total.labels(payload.name, "forbidden").inc()
        log_tool_event(
            payload.session_id,
            payload.name,
            "forbidden",
            args=raw_args,
            message_id=payload.message_id or payload.chain_id,
        )
        _emit_tool_hook(
            payload.name,
            "forbidden",
            args=raw_args,
            session_id=payload.session_id,
            message_id=payload.message_id or payload.chain_id,
        )
        raise HTTPException(status_code=403, detail="Invalid signature")
    except KeyError:
        tool_invocations_total.labels(payload.name, "not_found").inc()
        log_tool_event(
            payload.session_id,
            payload.name,
            "not_found",
            args=raw_args,
            message_id=payload.message_id or payload.chain_id,
        )
        _emit_tool_hook(
            payload.name,
            "not_found",
            args=raw_args,
            session_id=payload.session_id,
            message_id=payload.message_id or payload.chain_id,
        )
        raise HTTPException(status_code=404, detail="Tool not registered")
    except Exception as e:  # pragma: no cover - runtime errors
        tool_invocations_total.labels(payload.name, "error").inc()
        log_tool_event(
            payload.session_id,
            payload.name,
            "error",
            args=args if "args" in locals() else raw_args,
            result=str(e),
            message_id=payload.message_id or payload.chain_id,
        )
        _emit_tool_hook(
            payload.name,
            "error",
            args=args if "args" in locals() else raw_args,
            result=str(e),
            session_id=payload.session_id,
            message_id=payload.message_id or payload.chain_id,
        )
        raise HTTPException(status_code=500, detail=str(e))
    # Best-effort: persist to conversation (audit)
    try:
        sid = payload.session_id
        mid = payload.message_id or payload.chain_id
        if sid and mid:
            _append_tool_event_to_conversation(
                sid,
                mid,
                payload.name,
                args,
                result,
                status="invoked",
                model=model_hint if isinstance(model_hint, str) else None,
                mode=mode_hint if isinstance(mode_hint, str) else None,
            )
    except Exception:
        pass
    await publish_console_event(
        request.app,
        {
            "type": "tool",
            "name": payload.name,
            "args": args,
            "result": result,
            "chain_id": payload.chain_id,
            "message_id": payload.message_id or payload.chain_id,
            "status": "invoked",
            "session_id": payload.session_id,
            "model": model_hint if isinstance(model_hint, str) else None,
            "mode": mode_hint if isinstance(mode_hint, str) else None,
        },
        default_agent=payload.chain_id or payload.session_id,
    )
    log_tool_event(
        payload.session_id,
        payload.name,
        "invoked",
        args=args,
        result=result,
        message_id=payload.message_id or payload.chain_id,
    )
    return {"result": result}


@router.post("/tools/propose")
async def propose_tool(request: Request, payload: ToolProposal):
    """Record a tool proposal and emit it to the thought stream.

    The proposal is kept in a lightweight in-memory registry until a
    decision is posted. This is a best-effort dev feature; pending
    proposals are not persisted across restarts.
    """
    if not hasattr(request.app.state, "pending_tools"):
        request.app.state.pending_tools = {}
    tool_name = _normalize_tool_name(payload.name)
    tool_args = _normalize_tool_args_for_proposal(tool_name, payload.args or {})
    target_session = payload.session_id
    target_message = payload.message_id or payload.chain_id
    signature = _tool_signature(tool_name, tool_args)
    if signature:
        for existing in request.app.state.pending_tools.values():
            if not isinstance(existing, dict):
                continue
            existing_status = str(existing.get("status") or "proposed").strip().lower()
            if existing_status not in {"proposed", "pending"}:
                continue
            if (existing.get("session_id") or None) != (target_session or None):
                continue
            existing_message = existing.get("message_id") or existing.get("chain_id")
            if (existing_message or None) != (target_message or None):
                continue
            existing_sig = _tool_signature(
                _normalize_tool_name(existing.get("name")),
                existing.get("args") if isinstance(existing.get("args"), dict) else {},
            )
            if existing_sig == signature:
                return {"id": existing.get("id"), "status": "proposed"}
    rid = str(uuid4())
    record = {
        "id": rid,
        "name": tool_name,
        "args": tool_args,
        "session_id": target_session,
        "message_id": target_message,
        "chain_id": payload.chain_id or payload.message_id or target_session,
        "status": "proposed",
    }
    request.app.state.pending_tools[rid] = record
    try:
        await publish_console_event(
            request.app,
            {
                "type": "tool",
                "id": rid,
                "name": record["name"],
                "args": record["args"],
                "result": None,
                "chain_id": record["chain_id"],
                "message_id": record["message_id"],
                "status": "proposed",
                "session_id": record.get("session_id"),
            },
            default_agent=record.get("chain_id") or record.get("session_id"),
        )
    except Exception:
        pass
    log_tool_event(
        payload.session_id,
        tool_name,
        "proposed",
        args=tool_args,
        message_id=payload.message_id or payload.chain_id,
        request_id=rid,
    )
    _emit_tool_hook(
        tool_name,
        "proposed",
        args=tool_args,
        session_id=payload.session_id,
        message_id=payload.message_id or payload.chain_id,
        request_id=rid,
    )
    _emit_tool_resolution_notification(request.app, proposals=[record])
    return {"id": rid, "status": "proposed"}


@router.post("/tools/decision")
async def decide_tool(request: Request, payload: ToolDecision):
    """Accept or deny a previously proposed tool."""
    registry: dict | None = getattr(request.app.state, "pending_tools", None)
    if registry is None:
        registry = {}
        setattr(request.app.state, "pending_tools", registry)
    rec = registry.get(payload.request_id)
    if not rec:
        recovered = _rehydrate_pending_tool(request.app, payload.request_id)
        if recovered:
            registry[payload.request_id] = recovered
            rec = recovered
    if not rec and payload.name:
        # Allow the client to supply enough context to reconstruct the decision
        # when the in-memory registry and conversation log are unavailable.
        rec = {
            "id": payload.request_id,
            "name": payload.name,
            "args": payload.args or {},
            "session_id": payload.session_id,
            "message_id": payload.message_id,
            "chain_id": payload.chain_id or payload.message_id or payload.session_id,
            "status": "proposed",
        }
        registry[payload.request_id] = rec
    if not rec:
        raise HTTPException(status_code=404, detail="Request not found")

    if payload.decision == "deny":
        rec["status"] = "denied"
        # Provide a structured result payload so /chat/continue can advance after denials.
        deny_result = _tool_outcome_payload("denied", "Denied by user.")
        try:
            await publish_console_event(
                request.app,
                {
                    "type": "tool",
                    "id": payload.request_id,
                    "name": rec["name"],
                    "args": rec["args"],
                    "result": deny_result,
                    "chain_id": rec.get("chain_id"),
                    "message_id": rec.get("message_id"),
                    "status": "denied",
                    "session_id": rec.get("session_id"),
                    "model": rec.get("model"),
                    "mode": rec.get("mode"),
                },
                default_agent=rec.get("chain_id") or rec.get("session_id"),
            )
        except Exception:
            pass
        try:
            if rec.get("session_id") and rec.get("message_id"):
                _append_tool_event_to_conversation(
                    rec["session_id"],
                    rec["message_id"],
                    rec["name"],
                    rec.get("args", {}),
                    deny_result,
                    status="denied",
                    model=rec.get("model"),
                    mode=rec.get("mode"),
                    request_id=payload.request_id,
                )
        except Exception:
            pass
        registry.pop(payload.request_id, None)
        log_tool_event(
            rec.get("session_id"),
            rec["name"],
            "denied",
            args=rec.get("args"),
            result=deny_result,
            message_id=rec.get("message_id"),
            request_id=payload.request_id,
        )
        _emit_tool_hook(
            rec["name"],
            "denied",
            args=rec.get("args") or {},
            session_id=rec.get("session_id"),
            message_id=rec.get("message_id"),
            request_id=payload.request_id,
        )
        return {"status": "denied", "result": deny_result}

    # accept => invoke
    user = request.headers.get("X-User", "anonymous")
    raw_args = payload.args if payload.args is not None else rec.get("args", {})
    try:
        _, args = normalize_and_sanitize_tool_args(rec["name"], raw_args)
    except ValueError as exc:
        rec["status"] = "error"
        error_text = str(exc)
        error_result = _tool_outcome_payload("error", error_text)
        try:
            await publish_console_event(
                request.app,
                {
                    "type": "tool",
                    "id": payload.request_id,
                    "name": rec["name"],
                    "args": raw_args if isinstance(raw_args, dict) else {},
                    "result": error_result,
                    "chain_id": rec.get("chain_id"),
                    "message_id": rec.get("message_id"),
                    "status": "error",
                    "session_id": rec.get("session_id"),
                    "model": rec.get("model"),
                    "mode": rec.get("mode"),
                },
                default_agent=rec.get("chain_id") or rec.get("session_id"),
            )
        except Exception:
            pass
        try:
            if rec.get("session_id") and rec.get("message_id"):
                _append_tool_event_to_conversation(
                    rec["session_id"],
                    rec["message_id"],
                    rec["name"],
                    raw_args if isinstance(raw_args, dict) else {},
                    error_result,
                    status="error",
                    model=rec.get("model"),
                    mode=rec.get("mode"),
                    request_id=payload.request_id,
                )
        except Exception:
            pass
        log_tool_event(
            rec.get("session_id"),
            rec["name"],
            "error",
            args=raw_args if isinstance(raw_args, dict) else {},
            result=error_result,
            message_id=rec.get("message_id"),
            request_id=payload.request_id,
        )
        _emit_tool_hook(
            rec["name"],
            "error",
            args=raw_args if isinstance(raw_args, dict) else {},
            result=error_result,
            session_id=rec.get("session_id"),
            message_id=rec.get("message_id"),
            request_id=payload.request_id,
        )
        return {"status": "error", "result": error_result, "error": error_text}
    signature = generate_signature(user, rec["name"], args)
    action_context = _build_action_context(
        session_id=rec.get("session_id"),
        message_id=rec.get("message_id"),
        chain_id=rec.get("chain_id"),
        request_id=payload.request_id,
        model=rec.get("model"),
        mode=rec.get("mode"),
    )
    try:
        raw_result = request.app.state.memory_manager.invoke_tool(
            rec["name"],
            user=user,
            signature=signature,
            _action_context=action_context,
            **args,
        )
        result = _tool_outcome_payload("invoked", data=raw_result, ok=True)
        rec["status"] = "invoked"
        try:
            await publish_console_event(
                request.app,
                {
                    "type": "tool",
                    "id": payload.request_id,
                    "name": rec["name"],
                    "args": args,
                    "result": result,
                    "chain_id": rec.get("chain_id"),
                    "message_id": rec.get("message_id"),
                    "status": "invoked",
                    "session_id": rec.get("session_id"),
                    "model": rec.get("model"),
                    "mode": rec.get("mode"),
                },
                default_agent=rec.get("chain_id") or rec.get("session_id"),
            )
        except Exception:
            pass
        try:
            if rec.get("session_id") and rec.get("message_id"):
                _append_tool_event_to_conversation(
                    rec["session_id"],
                    rec["message_id"],
                    rec["name"],
                    args,
                    result,
                    status="invoked",
                    model=rec.get("model"),
                    mode=rec.get("mode"),
                    request_id=payload.request_id,
                )
        except Exception:
            pass
        registry.pop(payload.request_id, None)
        tool_invocations_total.labels(rec["name"], "ok").inc()
        log_tool_event(
            rec.get("session_id"),
            rec["name"],
            "invoked",
            args=args,
            result=result,
            message_id=rec.get("message_id"),
            request_id=payload.request_id,
        )
        _emit_tool_hook(
            rec["name"],
            "invoked",
            args=args,
            result=result,
            session_id=rec.get("session_id"),
            message_id=rec.get("message_id"),
            request_id=payload.request_id,
        )
        return {"status": "invoked", "result": result}
    except ValueError as e:
        tool_invocations_total.labels(rec["name"], "bad_request").inc()
        error_text = str(e)
        error_result = _tool_outcome_payload("error", error_text)
        _emit_tool_hook(
            rec["name"],
            "error",
            args=args,
            result=error_result,
            session_id=rec.get("session_id"),
            message_id=rec.get("message_id"),
            request_id=payload.request_id,
        )
        rec["status"] = "error"
        try:
            await publish_console_event(
                request.app,
                {
                    "type": "tool",
                    "id": payload.request_id,
                    "name": rec["name"],
                    "args": args,
                    "result": error_result,
                    "chain_id": rec.get("chain_id"),
                    "message_id": rec.get("message_id"),
                    "status": "error",
                    "session_id": rec.get("session_id"),
                },
                default_agent=rec.get("chain_id") or rec.get("session_id"),
            )
        except Exception:
            pass
        try:
            if rec.get("session_id") and rec.get("message_id"):
                _append_tool_event_to_conversation(
                    rec["session_id"],
                    rec["message_id"],
                    rec["name"],
                    args,
                    error_result,
                    status="error",
                    request_id=payload.request_id,
                )
        except Exception:
            pass
        log_tool_event(
            rec.get("session_id"),
            rec["name"],
            "error",
            args=args,
            result=error_result,
            message_id=rec.get("message_id"),
            request_id=payload.request_id,
        )
        return {"status": "error", "result": error_result, "error": error_text}
    except PermissionError as e:
        tool_invocations_total.labels(rec["name"], "forbidden").inc()
        _emit_tool_hook(
            rec["name"],
            "forbidden",
            args=args,
            session_id=rec.get("session_id"),
            message_id=rec.get("message_id"),
            request_id=payload.request_id,
        )
        rec["status"] = "error"
        detail = str(e) or "Invalid signature"
        error_result = _tool_outcome_payload("error", detail)
        try:
            await publish_console_event(
                request.app,
                {
                    "type": "tool",
                    "id": payload.request_id,
                    "name": rec["name"],
                    "args": args,
                    "result": error_result,
                    "chain_id": rec.get("chain_id"),
                    "message_id": rec.get("message_id"),
                    "status": "error",
                    "session_id": rec.get("session_id"),
                },
                default_agent=rec.get("chain_id") or rec.get("session_id"),
            )
        except Exception:
            pass
        try:
            if rec.get("session_id") and rec.get("message_id"):
                _append_tool_event_to_conversation(
                    rec["session_id"],
                    rec["message_id"],
                    rec["name"],
                    args,
                    error_result,
                    status="error",
                    request_id=payload.request_id,
                )
        except Exception:
            pass
        log_tool_event(
            rec.get("session_id"),
            rec["name"],
            "error",
            args=args,
            result=error_result,
            message_id=rec.get("message_id"),
            request_id=payload.request_id,
        )
        return {"status": "error", "result": error_result, "error": "Invalid signature"}
    except KeyError:
        tool_invocations_total.labels(rec["name"], "not_found").inc()
        _emit_tool_hook(
            rec["name"],
            "not_found",
            args=args,
            session_id=rec.get("session_id"),
            message_id=rec.get("message_id"),
            request_id=payload.request_id,
        )
        rec["status"] = "error"
        error_result = _tool_outcome_payload("error", "Tool not registered")
        try:
            await publish_console_event(
                request.app,
                {
                    "type": "tool",
                    "id": payload.request_id,
                    "name": rec["name"],
                    "args": args,
                    "result": error_result,
                    "chain_id": rec.get("chain_id"),
                    "message_id": rec.get("message_id"),
                    "status": "error",
                    "session_id": rec.get("session_id"),
                },
                default_agent=rec.get("chain_id") or rec.get("session_id"),
            )
        except Exception:
            pass
        log_tool_event(
            rec.get("session_id"),
            rec["name"],
            "error",
            args=args,
            result=error_result,
            message_id=rec.get("message_id"),
            request_id=payload.request_id,
        )
        return {
            "status": "error",
            "result": error_result,
            "error": "not_found",
        }
    except Exception as e:
        tool_invocations_total.labels(rec["name"], "error").inc()
        error_text = str(e)
        error_result = _tool_outcome_payload("error", error_text)
        # Emit error status to stream
        try:
            await publish_console_event(
                request.app,
                {
                    "type": "tool",
                    "id": payload.request_id,
                    "name": rec["name"],
                    "args": args,
                    "result": error_result,
                    "chain_id": rec.get("chain_id"),
                    "message_id": rec.get("message_id"),
                    "status": "error",
                    "session_id": rec.get("session_id"),
                },
                default_agent=rec.get("chain_id") or rec.get("session_id"),
            )
        except Exception:
            pass
        try:
            if rec.get("session_id") and rec.get("message_id"):
                _append_tool_event_to_conversation(
                    rec["session_id"],
                    rec["message_id"],
                    rec["name"],
                    args,
                    error_result,
                    status="error",
                    request_id=payload.request_id,
                )
        except Exception:
            pass
        log_tool_event(
            rec.get("session_id"),
            rec["name"],
            "error",
            args=args,
            result=error_result,
            message_id=rec.get("message_id"),
            request_id=payload.request_id,
        )
        _emit_tool_hook(
            rec["name"],
            "error",
            args=args,
            result=error_result,
            session_id=rec.get("session_id"),
            message_id=rec.get("message_id"),
            request_id=payload.request_id,
        )
        rec["status"] = "error"
        return {"status": "error", "result": error_result, "error": error_text}


@router.post("/tools/schedule")
async def schedule_tool(request: Request, payload: ToolSchedule) -> dict:
    """Mark a proposed tool as scheduled and link it to a calendar event."""
    registry: dict | None = getattr(request.app.state, "pending_tools", None)
    if registry is None:
        registry = {}
        setattr(request.app.state, "pending_tools", registry)
    user = request.headers.get("X-User", "anonymous")
    rec = registry.get(payload.request_id)
    if not rec:
        recovered = _rehydrate_pending_tool(request.app, payload.request_id)
        if recovered:
            registry[payload.request_id] = recovered
            rec = recovered
    if not rec and payload.name:
        rec = {
            "id": payload.request_id,
            "name": payload.name,
            "args": payload.args or {},
            "session_id": payload.session_id,
            "message_id": payload.message_id,
            "chain_id": payload.chain_id or payload.message_id or payload.session_id,
            "status": "proposed",
        }
        registry[payload.request_id] = rec
    if not rec:
        raise HTTPException(status_code=404, detail="Request not found")

    name = payload.name or rec.get("name")
    raw_args = payload.args if payload.args is not None else rec.get("args", {})
    if name:
        _, args = normalize_and_sanitize_tool_args(str(name), raw_args)
    else:
        args = sanitize_args(raw_args if isinstance(raw_args, dict) else {})
    session_id = payload.session_id or rec.get("session_id")
    message_id = payload.message_id or rec.get("message_id")
    chain_id = payload.chain_id or rec.get("chain_id") or message_id or session_id
    prompt: str | None = None
    if payload.prompt is not None:
        try:
            prompt = str(payload.prompt).strip()
        except Exception:
            prompt = None
        if not prompt:
            prompt = ""
    conversation_mode = None
    if payload.conversation_mode is not None:
        try:
            mode_raw = str(payload.conversation_mode or "").strip().lower()
        except Exception:
            mode_raw = ""
        if mode_raw in {
            "inline",
            "current_chat",
            "same_chat",
            "current_thread",
            "same_thread",
        }:
            conversation_mode = "inline"
        elif mode_raw in {
            "new",
            "new_chat",
            "new_thread",
            "separate_chat",
            "separate_thread",
        }:
            conversation_mode = "new_chat"

    rec["status"] = "scheduled"
    rec["args"] = args
    if name:
        rec["name"] = name

    try:
        await publish_console_event(
            request.app,
            {
                "type": "tool",
                "id": payload.request_id,
                "name": name,
                "args": args,
                "result": {"scheduled_event_id": payload.event_id},
                "chain_id": chain_id,
                "message_id": message_id,
                "status": "scheduled",
                "scheduled_event_id": payload.event_id,
                "session_id": session_id,
            },
            default_agent=chain_id or session_id,
        )
    except Exception:
        pass

    try:
        if session_id and message_id and name:
            _append_tool_event_to_conversation(
                session_id,
                message_id,
                name,
                args,
                {"scheduled_event_id": payload.event_id},
                status="scheduled",
                request_id=payload.request_id,
            )
    except Exception:
        pass

    # Persist the scheduled tool payload onto the calendar event so the
    # "upcoming tasks" panel can show/edit/run the action reliably.
    try:
        event_payload = calendar_store.load_event(payload.event_id) or {}
        if isinstance(event_payload, dict):
            event_payload.setdefault("id", payload.event_id)
            if not event_payload.get("title"):
                event_payload["title"] = f"Schedule tool: {name or 'tool'}"
            actions = event_payload.get("actions")
            if not isinstance(actions, list):
                actions = []
                event_payload["actions"] = actions
            action: Dict[str, Any] | None = None
            for item in actions:
                if not isinstance(item, dict):
                    continue
                item_id = item.get("request_id") or item.get("id")
                if item_id is None:
                    continue
                if str(item_id) == str(payload.request_id):
                    action = item
                    break
            if action is None:
                action = {"id": payload.request_id}
                actions.append(action)
            action.update(
                {
                    "kind": "tool",
                    "name": name,
                    "args": args,
                    "request_id": payload.request_id,
                    "status": "scheduled",
                    "scheduled_event_id": payload.event_id,
                    "scheduled_for": event_payload.get("start_time"),
                    "session_id": session_id,
                    "message_id": message_id,
                    "chain_id": chain_id,
                    "user": user,
                    "updated_at": time.time(),
                }
            )
            if conversation_mode:
                action["conversation_mode"] = conversation_mode
            if prompt is not None:
                if prompt:
                    action["prompt"] = prompt
                else:
                    action.pop("prompt", None)
            tool_json = json.dumps(
                {"tool": name or "tool", "args": args},
                indent=2,
                ensure_ascii=False,
            )
            desc = event_payload.get("description")
            replace_desc = False
            if not isinstance(desc, str) or not desc.strip():
                replace_desc = True
            else:
                try:
                    parsed = json.loads(desc)
                except Exception:
                    parsed = None
                if isinstance(parsed, dict) and "tool" in parsed and "args" in parsed:
                    replace_desc = True
            if replace_desc:
                event_payload["description"] = tool_json
            event_payload["status"] = "scheduled"
            _persist_calendar_event(payload.event_id, event_payload)
    except Exception:
        pass

    registry.pop(payload.request_id, None)
    log_tool_event(
        session_id,
        str(name or "tool"),
        "scheduled",
        args=args,
        result={"scheduled_event_id": payload.event_id},
        message_id=message_id,
        request_id=payload.request_id,
    )
    _emit_tool_hook(
        str(name or "tool"),
        "scheduled",
        args=args,
        result={"scheduled_event_id": payload.event_id},
        session_id=session_id,
        message_id=message_id,
        request_id=payload.request_id,
    )
    return {"status": "scheduled", "event_id": payload.event_id}


# ---------------------------------------------------------------------------
# Conversation persistence


class ConversationPayload(BaseModel):
    name: str
    messages: list[dict[str, Any]]


@router.get("/conversations")
async def list_conversations(detailed: bool = False):
    return {
        "conversations": conversation_store.list_conversations(
            include_metadata=detailed
        )
    }


@router.get("/conversations/reveal/{name:path}")
async def reveal_conversation(name: str):
    """Reveal the conversation JSON file on the server host."""
    filename = name
    if not filename.endswith(".json"):
        filename = f"{filename}.json"
    base_dir = Path(conversation_store.CONV_DIR).resolve()
    target = (base_dir / filename).resolve()
    try:
        target.relative_to(base_dir)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid conversation path")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Conversation not found")
    folder = target if target.is_dir() else target.parent
    opened = False
    try:
        if sys.platform.startswith("linux"):
            subprocess.Popen(["xdg-open", str(folder)])
            opened = True
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
            opened = True
        elif os.name == "nt":
            if target.is_file():
                subprocess.Popen(["explorer", "/select,", str(target)])
            else:
                subprocess.Popen(["explorer", str(folder)])
            opened = True
    except Exception:
        opened = False
    return {"path": str(target), "opened": opened}


@router.get("/conversations/export-all")
async def export_all_conversations(
    format: str = "md",
    include_chat: bool = True,
    include_thoughts: bool = True,
    include_tools: bool = True,
):
    """Export all conversations into a single zip archive."""
    from app.utils import conversation_io

    def _sanitize_segment(value: str) -> str:
        cleaned = re.sub('[<>:"\\\\|?*\x00-\x1F]', "-", value).strip()
        cleaned = re.sub(r"\\s+", "_", cleaned)
        return cleaned or "conversation"

    def _sanitize_path(value: str) -> str:
        normalized = str(value or "").strip().replace("\\", "/")
        if not normalized:
            return "conversation"
        segments = [seg for seg in normalized.split("/") if seg]
        if not segments:
            return "conversation"
        safe_segments = [_sanitize_segment(seg) for seg in segments]
        return "/".join(safe_segments)

    fmt = (format or "md").strip().lower()
    if fmt in {"markdown"}:
        fmt = "md"
    if fmt in {"txt", "text"}:
        fmt = "text"
        ext = "txt"
    elif fmt in {"json"}:
        ext = "json"
    elif fmt in {"md"}:
        ext = "md"
    else:
        raise HTTPException(status_code=400, detail="Unsupported export format")

    names = conversation_store.list_conversations()
    buffer = io.BytesIO()
    used_paths: Dict[str, int] = {}

    def _unique_path(path: str) -> str:
        if path not in used_paths:
            used_paths[path] = 1
            return path
        used_paths[path] += 1
        stem, sep, suffix = path.rpartition(".")
        if not sep:
            return f"{path}-{used_paths[path]}"
        return f"{stem}-{used_paths[path]}.{suffix}"

    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in names:
            if not name:
                continue
            try:
                messages = conversation_store.load_conversation(name)
            except Exception:
                continue
            try:
                meta = conversation_store.get_metadata(name)
            except Exception:
                meta = None
            if fmt == "json":
                payload = conversation_io.export_conversation_json(
                    name=name,
                    messages=messages,
                    metadata=meta,
                    include_chat=include_chat,
                    include_thoughts=include_thoughts,
                    include_tools=include_tools,
                )
                content = json.dumps(payload, indent=2)
            elif fmt == "text":
                content = conversation_io.export_conversation_text(
                    name=name,
                    messages=messages,
                    metadata=meta,
                    include_chat=include_chat,
                    include_thoughts=include_thoughts,
                    include_tools=include_tools,
                )
            else:
                content = conversation_io.export_conversation_markdown(
                    name=name,
                    messages=messages,
                    metadata=meta,
                    include_chat=include_chat,
                    include_thoughts=include_thoughts,
                    include_tools=include_tools,
                )
            base = _sanitize_path(name)
            path = _unique_path(f"{base}.{ext}")
            zf.writestr(path, content)

    buffer.seek(0)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"float-conversations-{timestamp}.zip"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(buffer, media_type="application/zip", headers=headers)


class RenamePayload(BaseModel):
    new_name: str


@router.post("/conversations/{name:path}/rename")
async def rename_conversation(name: str, payload: RenamePayload):
    conversation_store.rename_conversation(name, payload.new_name)
    return {"status": "renamed"}


@router.delete("/conversations/{name:path}")
async def delete_conversation(name: str):
    conversation_store.delete_conversation(name)
    return {"status": "deleted"}


_TITLE_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'/-]*")
_TITLE_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "my",
    "of",
    "on",
    "or",
    "please",
    "the",
    "to",
    "we",
    "what",
    "with",
}


def _conversation_message_text(message: Dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
                    continue
            text = item.get("content")
            if isinstance(text, str):
                parts.append(text)
        if parts:
            return " ".join(parts)
    text = message.get("text")
    if isinstance(text, str):
        return text
    return ""


def _conversation_base_name(name: str) -> str:
    normalized = str(name or "").strip().replace("\\", "/")
    if not normalized:
        return "conversation"
    segments = [segment for segment in normalized.split("/") if segment]
    return segments[-1] if segments else normalized


_TOOL_DISCOVERY_PROMPT_HINT = (
    "If you need to discover or verify available tools, use tool_help to list them "
    "and tool_info to inspect one tool's purpose, arguments, and limits. "
    "Do that before claiming a capability is unavailable. "
    "For reminders, tasks, events, or scheduled follow-ups, inspect/use create_task. "
    "For local workspace browsing or edits, inspect/use list_dir, read_file, and write_file. "
    "For local files, use list_dir to discover paths first and keep read_file "
    "requests narrowly chunked. "
    "Check runtime and sandbox metadata before assuming Python, REPL, shell, network, or filesystem access. "
    "Keep chatter between obvious tool steps brief."
)


def _ensure_tool_discovery_hint(prompt: str) -> str:
    base = (prompt or "").strip()
    lowered = base.lower()
    if "tool_help" in lowered and "tool_info" in lowered:
        return base
    if not base:
        return _TOOL_DISCOVERY_PROMPT_HINT
    return f"{base}\n\n{_TOOL_DISCOVERY_PROMPT_HINT}"


def _effective_system_prompt(
    base_prompt: str, request: Optional[Request] = None
) -> str:
    """Combine immutable base system prompt with user-editable custom additions."""
    base = (base_prompt or "").strip()
    if request is not None:
        settings_snapshot = user_settings.load_settings()
        configured_base = (settings_snapshot.get("system_prompt_base") or "").strip()
        custom = (settings_snapshot.get("system_prompt_custom") or "").strip()
    else:
        configured_base = ""
        custom = ""
    if configured_base and configured_base != base:
        base = configured_base
    if not base and request is not None:
        base = request.app.state.config.get(
            "system_prompt",
            app_config.load_config().get("system_prompt", ""),
        )
    base = _ensure_tool_discovery_hint(base)
    if not custom:
        return base
    if not base:
        return _ensure_tool_discovery_hint(custom)
    return f"{base}\n\n{custom}"


def _suggest_conversation_title(name: str, messages: List[Dict[str, Any]]) -> str:
    if not isinstance(messages, list):
        return _conversation_base_name(name)
    user_snippets: list[str] = []
    fallback_snippets: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or message.get("speaker") or "").strip().lower()
        text = _conversation_message_text(message)
        if not text:
            continue
        normalized = re.sub(r"\s+", " ", text).strip()
        if not normalized:
            continue
        if role == "user":
            user_snippets.append(normalized)
        else:
            fallback_snippets.append(normalized)
        if len(user_snippets) >= 2:
            break
    source = " ".join(user_snippets or fallback_snippets[:1]).strip()
    if not source:
        return _conversation_base_name(name)

    raw_tokens = [
        _token.strip("-'") for _token in _TITLE_TOKEN_RE.findall(source[:600])
    ]
    tokens = [token for token in raw_tokens if token]
    if not tokens:
        return _conversation_base_name(name)

    filtered = [token for token in tokens if token.lower() not in _TITLE_STOPWORDS]
    chosen = filtered if len(filtered) >= 2 else tokens
    title = " ".join(chosen[:7]).strip()
    if not title:
        return _conversation_base_name(name)
    title = re.sub(r"\s+", " ", title)
    return title.title()[:96]


class ConversationExportParams(BaseModel):
    format: str = "md"
    include_thoughts: bool = True


class ConversationImportPayload(BaseModel):
    name: Optional[str] = None
    format: str = "md"
    content: Optional[str] = None
    messages: Optional[List[Dict[str, Any]]] = None


def _sanitize_import_folder_path(value: str) -> str:
    raw = (value or "").replace("\\", "/").strip()
    if not raw:
        return ""
    segments = [
        re.sub(r'[<>:"/\\|?*\x00-\x1F]', "-", segment.strip())
        .replace("/", "-")
        .replace("\\", "-")
        for segment in raw.split("/")
        if segment.strip() and segment not in {".", ".."}
    ]
    return "/".join(filter(None, segments))


def _sanitize_import_conversation_name(
    value: str, fallback_prefix: str = "import"
) -> str:
    base = (value or "").strip()
    base = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "-", base)
    base = re.sub(r"\s+", "_", base).strip("-_.")
    if base in {".", ".."}:
        base = ""
    return base or fallback_prefix


def _parse_import_selected_files(
    raw: Optional[Union[str, List[Any], List[str]]]
) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        candidates = raw
    elif isinstance(raw, str):
        trimmed = raw.strip()
        if not trimmed:
            return []
        try:
            parsed = json.loads(trimmed)
            if isinstance(parsed, list):
                candidates = parsed
            else:
                candidates = [trimmed]
        except Exception:
            candidates = [trimmed]
    else:
        candidates = [raw]
    out = []
    for item in candidates:
        if item is None:
            continue
        normalized = str(item).strip()
        if normalized and normalized not in out:
            out.append(normalized)
    return out


def _next_import_name(
    base_name: str, used_names: set[str], destination_folder: str
) -> str:
    sanitized_name = _sanitize_import_conversation_name(base_name)
    base = (
        f"{_sanitize_import_folder_path(destination_folder)}/{sanitized_name}"
        if destination_folder
        else sanitized_name
    ).strip("/")
    candidate = base
    if candidate not in used_names:
        used_names.add(candidate)
        return candidate
    counter = 2
    while True:
        suffix_name = f"{sanitized_name}-{counter}"
        next_name = (
            f"{_sanitize_import_folder_path(destination_folder)}/{suffix_name}"
            if destination_folder
            else suffix_name
        ).strip("/")
        if next_name not in used_names:
            used_names.add(next_name)
            return next_name
        counter += 1


@router.post("/conversations/import/preview")
async def import_conversation_preview(file: UploadFile = UploadFileType(...)):
    from app.utils import conversation_io

    lower_name = (file.filename or "").lower()
    raw = await file.read()
    if lower_name.endswith(".zip"):
        try:
            detected_files = conversation_io.list_openai_conversation_zip_candidates(
                raw,
                filename=file.filename,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail="Unable to inspect zip import file"
            ) from exc
        return {"detected_files": detected_files}
    if lower_name.endswith(".json"):
        try:
            detected_files = conversation_io.list_openai_conversation_json_candidates(
                raw, filename=file.filename
            )
        except MemoryError as exc:
            raise HTTPException(
                status_code=413,
                detail="JSON export is too large to preview safely. Export smaller ranges and retry.",
            ) from exc
        except Exception:
            detected_files = []
        return {"detected_files": detected_files}
    return {"detected_files": []}


@router.get("/conversations/{name:path}/export")
async def export_conversation(
    name: str,
    format: str = "md",
    include_chat: bool = True,
    include_thoughts: bool = True,
    include_tools: bool = True,
):
    """Export a conversation to markdown or JSON."""
    from app.utils import conversation_io

    messages = conversation_store.load_conversation(name)
    meta = conversation_store.get_metadata(name)
    fmt = (format or "md").strip().lower()
    if fmt in {"json"}:
        payload = conversation_io.export_conversation_json(
            name=name,
            messages=messages,
            metadata=meta,
            include_chat=include_chat,
            include_thoughts=include_thoughts,
            include_tools=include_tools,
        )
        return payload
    if fmt in {"txt", "text"}:
        text = conversation_io.export_conversation_text(
            name=name,
            messages=messages,
            metadata=meta,
            include_chat=include_chat,
            include_thoughts=include_thoughts,
            include_tools=include_tools,
        )
        return PlainTextResponse(text)
    if fmt not in {"md", "markdown"}:
        raise HTTPException(status_code=400, detail="Unsupported export format")
    markdown = conversation_io.export_conversation_markdown(
        name=name,
        messages=messages,
        metadata=meta,
        include_chat=include_chat,
        include_thoughts=include_thoughts,
        include_tools=include_tools,
    )
    return PlainTextResponse(markdown)


@router.post("/conversations/import")
async def import_conversation(
    request: Request,
    payload: Optional[ConversationImportPayload] = Body(default=None),
    file: Optional[UploadFile] = UploadFileType(default=None),
    name: Optional[str] = Form(default=None),
    format: Optional[str] = Form(default=None),
    content: Optional[str] = Form(default=None),
    destination_folder: Optional[str] = Form(default=None),
    selected_files: Optional[Any] = Form(default=None),
):
    """Import a conversation from markdown, JSON, or OpenAI export zip."""
    from app.utils import conversation_io

    def parse_payload_messages(payload_obj: Any) -> List[Dict[str, Any]]:
        if payload_obj is None or not isinstance(payload_obj, (dict, list)):
            return []
        parsed = conversation_io.import_openai_conversation_json(payload_obj)
        if parsed:
            return parsed
        raw = conversation_io.import_conversation_json_raw(payload_obj)
        return raw if isinstance(raw, list) else []

    def parse_json_text(raw_text: Optional[str]) -> List[Dict[str, Any]]:
        if not isinstance(raw_text, str) or not raw_text.strip():
            return []
        try:
            parsed = json.loads(raw_text)
        except Exception:
            return []
        return parse_payload_messages(parsed)

    import_payload = payload
    if import_payload is None and file is None and not format:
        try:
            raw_payload = await request.json()
            import_payload = ConversationImportPayload(**raw_payload)
        except Exception:
            import_payload = ConversationImportPayload()
    if import_payload is None:
        import_payload = ConversationImportPayload()
    resolved_name = (name or import_payload.name or "").strip()
    fmt = (format or import_payload.format or "").strip().lower()
    request_content = content if content is not None else import_payload.content
    requested_destination = _sanitize_import_folder_path(destination_folder)
    parsed_selected_files = _parse_import_selected_files(selected_files)
    messages: List[Dict[str, Any]] = []
    if file is not None and format is None:
        fmt = ""
    if not fmt and not file:
        fmt = "md"

    if file is not None:
        raw = await file.read()
        lower_name = (file.filename or "").lower()
        if not fmt:
            if lower_name.endswith(".zip"):
                fmt = "zip"
            elif lower_name.endswith(".json"):
                fmt = "json"
            elif lower_name.endswith(".md") or lower_name.endswith(".markdown"):
                fmt = "markdown"
            elif lower_name.endswith(".txt"):
                fmt = "text"
        if not fmt:
            fmt = "md"
        text_body = raw.decode("utf-8", errors="ignore")

        if fmt in {"md", "markdown", "text"}:
            messages = conversation_io.import_conversation_markdown(text_body)
        elif fmt == "zip":
            if parsed_selected_files:
                candidate_list = [
                    item.strip() for item in parsed_selected_files if item.strip()
                ]
                candidate_map = conversation_io.extract_openai_zip_messages(
                    raw, selected_files=candidate_list
                )
                if not candidate_map:
                    raise HTTPException(
                        status_code=400,
                        detail="No selected files were importable from this zip",
                    )
                used_names = set(conversation_store.list_conversations())
                imported_names: List[Dict[str, Any]] = []
                for source_name, payload_messages in candidate_map.items():
                    if not isinstance(payload_messages, list):
                        continue
                    import_base = Path(source_name).stem
                    next_name = _next_import_name(
                        import_base,
                        used_names=used_names,
                        destination_folder=requested_destination,
                    )
                    conversation_store.save_conversation(next_name, payload_messages)
                    imported_names.append(
                        {
                            "name": next_name,
                            "message_count": len(payload_messages),
                            "source_file": source_name,
                        }
                    )
                if not imported_names:
                    raise HTTPException(
                        status_code=400, detail="No valid conversations found in zip"
                    )
                # Preserve existing single-import API shape when possible.
                if len(imported_names) == 1:
                    return {
                        "status": "imported",
                        "name": imported_names[0]["name"],
                        "message_count": imported_names[0]["message_count"],
                        "imports": imported_names,
                    }
                return {
                    "status": "imported",
                    "count": len(imported_names),
                    "message_count": sum(
                        item["message_count"] for item in imported_names
                    ),
                    "imports": imported_names,
                }
            else:
                messages = conversation_io.import_openai_conversation_zip(
                    raw, filename=file.filename
                )
                if not messages:
                    # Fallback for simple zip bundles that store a JSON conversation payload.
                    try:
                        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                            json_names = [
                                member
                                for member in archive.namelist()
                                if member.lower().endswith(".json")
                            ]
                            if len(json_names) == 1:
                                messages = parse_payload_messages(
                                    json.loads(
                                        archive.read(json_names[0]).decode(
                                            "utf-8", errors="ignore"
                                        )
                                    )
                                )
                    except Exception:
                        pass
        elif fmt == "json":
            try:
                openai_multi_candidates = (
                    conversation_io.list_openai_conversation_json_candidates(raw)
                )
            except MemoryError as exc:
                raise HTTPException(
                    status_code=413,
                    detail="JSON export is too large to import safely. Split the export and retry.",
                ) from exc
            except Exception:
                openai_multi_candidates = []
            if parsed_selected_files:
                candidate_map = conversation_io.extract_openai_json_conversations(
                    raw, selected_files=parsed_selected_files
                )
                if candidate_map:
                    used_names = set(conversation_store.list_conversations())
                    imported_names: List[Dict[str, Any]] = []
                    for source_name, payload_messages in candidate_map.items():
                        if not isinstance(payload_messages, list):
                            continue
                        next_name = _next_import_name(
                            source_name,
                            used_names=used_names,
                            destination_folder=requested_destination,
                        )
                        conversation_store.save_conversation(
                            next_name, payload_messages
                        )
                        imported_names.append(
                            {
                                "name": next_name,
                                "message_count": len(payload_messages),
                                "source_file": source_name,
                            }
                        )
                    if not imported_names:
                        raise HTTPException(
                            status_code=400,
                            detail="No selected conversations were importable from this JSON",
                        )
                    if len(imported_names) == 1:
                        return {
                            "status": "imported",
                            "name": imported_names[0]["name"],
                            "message_count": imported_names[0]["message_count"],
                            "imports": imported_names,
                        }
                    return {
                        "status": "imported",
                        "count": len(imported_names),
                        "message_count": sum(
                            item["message_count"] for item in imported_names
                        ),
                        "imports": imported_names,
                    }
                if len(openai_multi_candidates) > 1:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "JSON export contains multiple conversations. "
                            "Use the import preview and select one or more conversations."
                        ),
                    )
                raise HTTPException(
                    status_code=400,
                    detail="No selected conversations were importable from this JSON",
                )
            if len(openai_multi_candidates) > 1:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "JSON export contains multiple conversations. "
                        "Use the import preview and select one or more conversations."
                    ),
                )
            messages = parse_json_text(text_body)
        else:
            messages = parse_json_text(text_body)
    elif fmt in {"json", "zip"}:
        if fmt == "zip":
            raise HTTPException(
                status_code=400, detail="Zip import requires a file upload"
            )
        if import_payload.messages is not None:
            messages = import_payload.messages
        elif request_content:
            messages = parse_json_text(request_content)
        else:
            raise HTTPException(status_code=400, detail="Provide messages or content")
    elif fmt in {"md", "markdown", "text"}:
        if request_content is None:
            raise HTTPException(
                status_code=400, detail="Markdown import requires content"
            )
        messages = conversation_io.import_conversation_markdown(request_content)
    else:
        messages = parse_json_text(request_content or "")

    if not messages:
        if request_content:
            messages = parse_json_text(request_content)
        elif import_payload.messages:
            messages = parse_payload_messages(import_payload.messages)

    if not messages:
        if file is not None:
            raise HTTPException(status_code=400, detail="Unable to parse import file")
        raise HTTPException(status_code=400, detail="Provide messages or content")

    if not isinstance(messages, list):
        raise HTTPException(status_code=400, detail="Imported messages must be a list")

    name = (resolved_name or "").strip() or f"import-{int(time.time())}"
    conversation_store.save_conversation(name, messages)
    return {"status": "imported", "name": name, "message_count": len(messages)}


@router.get("/conversations/{name:path}/suggest-name")
async def suggest_conversation_name(name: str):
    messages = conversation_store.load_conversation(name)
    suggestion = _suggest_conversation_title(name, messages)
    return {"name": name, "suggested_name": suggestion}


@router.get("/conversations/{name:path}")
async def get_conversation(name: str):
    return {"messages": conversation_store.load_conversation(name)}


@router.post("/conversations/{name:path}")
async def save_conversation(name: str, payload: ConversationPayload):
    conversation_store.save_conversation(name, payload.messages)
    display_name = (payload.name or "").strip()
    if display_name:
        try:
            conversation_store.set_display_name(name, display_name)
        except Exception:
            pass
    return {"status": "saved"}


# ---------------------------------------------------------------------------
# Calendar event import


@router.post("/calendar/import/google")
async def import_google_calendar(payload: Dict[str, Any] = Body(...)):
    events = parse_google_calendar(payload)
    for event in events:
        _persist_calendar_event(event.id, event.model_dump())
    return {"imported": len(events)}


@router.post("/calendar/import/ics")
async def import_ics_calendar(file: UploadFile = UploadFileType(...)):
    data = await file.read()
    events = parse_ics(data)
    for event in events:
        _persist_calendar_event(event.id, event.model_dump())
    return {"imported": len(events)}


# Calendar event persistence


@router.get("/calendar/events")
async def list_calendar_events(detailed: bool = False):
    """List calendar events.

    When ``detailed`` is false (default), returns a list of event IDs for
    compatibility with earlier clients. When ``detailed`` is true, returns a
    list of event objects including their IDs and metadata.
    """
    if not detailed:
        return {"events": calendar_store.list_events()}
    out = []
    try:
        for eid in calendar_store.list_events():
            ev = calendar_store.load_event(eid) or {}
            if isinstance(ev, dict):
                ev.setdefault("id", eid)
                ev["status"] = _normalize_calendar_status(ev.get("status"))
                out.append(ev)
    except Exception:
        # Defensive: never fail the listing entirely
        pass
    return {"events": out}


@router.post("/calendar/reminders/flush")
async def flush_calendar_reminders():
    """Synchronously trigger any due calendar reminders for catch-up on launch."""

    try:
        from app.tasks import dispatch_due_calendar_prompts

        triggered = dispatch_due_calendar_prompts(enqueue=False)
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to flush reminders: {exc}"
        ) from exc
    return {"triggered": triggered, "count": len(triggered)}


class CalendarRagRehydrate(BaseModel):
    limit: Optional[int] = None
    dry_run: bool = False


@router.post("/calendar/rag/rehydrate")
async def calendar_rag_rehydrate(payload: CalendarRagRehydrate):
    """Synchronously (re)index stored calendar events into the knowledge base."""
    max_items = None
    if payload.limit is not None:
        try:
            max_items = max(0, int(payload.limit))
        except Exception:
            max_items = None
    scanned = 0
    updated = 0
    for event_id in calendar_store.list_events():
        if max_items is not None and scanned >= max_items:
            break
        event = calendar_store.load_event(event_id)
        if not isinstance(event, dict):
            continue
        scanned += 1
        if payload.dry_run:
            continue
        try:
            _ingest_calendar_event(event_id, event)
            updated += 1
        except Exception:
            pass
    return {"scanned": scanned, "reindexed": updated}


@router.get("/calendar/events/{event_id}")
async def get_calendar_event(event_id: str):
    event = calendar_store.load_event(event_id)
    if isinstance(event, dict) and event:
        event.setdefault("id", event_id)
        event["status"] = _normalize_calendar_status(event.get("status"))
    return {"event": event}


@router.post("/calendar/events/{event_id}")
async def save_calendar_event(event_id: str, event: CalendarEvent):
    # Merge updates to avoid wiping fields the client didn't send (e.g. description,
    # location, or scheduled tool metadata stored alongside the event).
    existing = calendar_store.load_event(event_id) or {}
    update = event.model_dump(exclude_unset=True)
    merged: Dict[str, Any]
    if isinstance(existing, dict) and existing:
        merged = dict(existing)
        merged.update(update)
    else:
        merged = dict(update)
    merged["id"] = event_id
    merged["status"] = _normalize_calendar_status(merged.get("status"))
    _persist_calendar_event(event_id, merged)
    return {"status": "saved"}


@router.delete("/calendar/events/{event_id}")
async def delete_calendar_event(event_id: str):
    calendar_store.delete_event(event_id)
    return {"status": "deleted"}


class PromptAction(BaseModel):
    action: str


@router.post("/calendar/events/{event_id}/prompt")
async def handle_event_prompt(event_id: str, payload: PromptAction):
    event = calendar_store.load_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    action = _normalize_calendar_status(payload.action, default="")
    if action == "acknowledged":
        event["status"] = "acknowledged"
    elif action == "skipped":
        event["status"] = "skipped"
    else:
        raise HTTPException(status_code=400, detail="Invalid action")
    _persist_calendar_event(event_id, event)
    return {"status": event["status"]}


@router.post("/calendar/events/{event_id}/run")
async def run_calendar_event_action(
    request: Request,
    event_id: str,
    action_id: Optional[str] = None,
    force: bool = False,
):
    """Execute a scheduled tool action stored on a calendar event."""
    try:
        from workers.scheduled_tool_runner import run_scheduled_tools_for_event
    except Exception as exc:  # pragma: no cover - optional module in older builds
        raise HTTPException(status_code=500, detail=str(exc))
    return await run_scheduled_tools_for_event(
        request.app, event_id, action_id=action_id, force=force
    )


# ---------------------------------------------------------------------------
# Web Push subscription endpoints


class PushSubscribePayload(BaseModel):
    subscription: Dict[str, Any]
    enabled: bool = True
    calendar_notify_minutes: int | None = None


@router.get("/push/public-key")
async def push_public_key():
    vapid = vapid_config()
    return {
        "publicKey": vapid.get("publicKey", ""),
        "enabled": bool(vapid.get("publicKey")),
    }


@router.post("/push/subscribe")
async def push_subscribe(payload: PushSubscribePayload):
    # Persist subscription and prefs in user settings
    data: Dict[str, Any] = {
        "push_subscription": payload.subscription,
        "push_enabled": payload.enabled,
    }
    if payload.calendar_notify_minutes is not None:
        data["calendar_notify_minutes"] = payload.calendar_notify_minutes
    user_settings.save_settings(data)
    return {"status": "saved"}


@router.post("/push/unsubscribe")
async def push_unsubscribe():
    user_settings.save_settings(
        {
            "push_subscription": None,
            "push_enabled": False,
        }
    )
    return {"status": "removed"}


class PushTestPayload(BaseModel):
    title: str = "Float notification"
    body: str = "Push is working."
    data: Optional[Dict[str, Any]] = None


@router.post("/push/test")
async def push_test(payload: PushTestPayload):
    if not can_send_push():
        raise HTTPException(
            status_code=400, detail="Push not configured (missing VAPID keys)"
        )
    settings = user_settings.load_settings()
    sub = settings.get("push_subscription")
    if not sub:
        raise HTTPException(status_code=404, detail="No subscription")
    err = send_web_push(
        sub,
        {
            "title": payload.title,
            "body": payload.body,
            "data": payload.data or {},
        },
    )
    if err:
        raise HTTPException(status_code=500, detail=err)
    return {"status": "sent"}


# ---------------------------------------------------------------------------
# Device and identity endpoints (Phase 1)


class DeviceRegisterPayload(BaseModel):
    public_key: str
    name: Optional[str] = None
    capabilities: Optional[Dict[str, Any]] = None


SYNC_DEVICE_SCOPE_ORDER = ("sync", "stream", "files")


def _normalize_sync_scopes(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    seen: set[str] = set()
    normalized: List[str] = []
    for item in value:
        scope = str(item or "").strip().lower()
        if scope not in SYNC_DEVICE_SCOPE_ORDER or scope in seen:
            continue
        seen.add(scope)
        normalized.append(scope)
    return normalized


def _coerce_saved_peer(entry: Any, index: int = 0) -> Optional[Dict[str, Any]]:
    if not isinstance(entry, dict):
        return None
    remote_url = str(entry.get("remote_url") or "").strip()
    if not remote_url:
        return None
    peer_id = str(entry.get("id") or "").strip() or f"peer-{index + 1}"
    scopes = _normalize_sync_scopes(entry.get("scopes"))
    profiles, active_workspace_id, selected_workspace_ids = load_workspace_state()
    local_workspace_ids = normalize_workspace_ids(
        entry.get("local_workspace_ids"), profiles
    ) or list(selected_workspace_ids)
    return {
        "id": peer_id,
        "label": str(entry.get("label") or "").strip() or remote_url,
        "remote_url": remote_url,
        "scopes": scopes or ["sync"],
        "remote_device_id": str(entry.get("remote_device_id") or "").strip(),
        "public_key": str(entry.get("public_key") or "").strip(),
        "remote_public_key": str(entry.get("remote_public_key") or "").strip(),
        "remote_device_name": str(entry.get("remote_device_name") or "").strip(),
        "last_used_at": str(entry.get("last_used_at") or "").strip(),
        "local_workspace_ids": local_workspace_ids,
        "remote_workspace_ids": [
            str(item).strip()
            for item in (entry.get("remote_workspace_ids") or [])
            if str(item or "").strip()
        ],
        "workspace_mode": (
            "import"
            if str(entry.get("workspace_mode") or "").strip().lower() == "import"
            else "merge"
        ),
        "local_target_workspace_id": (
            str(entry.get("local_target_workspace_id") or "").strip()
            or active_workspace_id
        ),
        "remote_target_workspace_id": (
            str(entry.get("remote_target_workspace_id") or "").strip()
            or DEFAULT_WORKSPACE_ID
        ),
    }


def _load_saved_peers() -> List[Dict[str, Any]]:
    settings = user_settings.load_settings()
    raw = settings.get("sync_saved_peers")
    if not isinstance(raw, list):
        return []
    peers: List[Dict[str, Any]] = []
    for index, entry in enumerate(raw):
        normalized = _coerce_saved_peer(entry, index)
        if normalized is not None:
            peers.append(normalized)
    return peers


def _workspace_state_summary(
    settings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    profiles, active_workspace_id, selected_workspace_ids = load_workspace_state(
        settings
    )
    return {
        "profiles": [summarize_workspace_profile(profile) for profile in profiles],
        "active_workspace_id": active_workspace_id,
        "selected_workspace_ids": selected_workspace_ids,
    }


def _workspace_profile_from_state(
    workspace_state: Dict[str, Any], workspace_id: Optional[str]
) -> Optional[Dict[str, Any]]:
    target_id = str(workspace_id or "").strip()
    profiles = workspace_state.get("profiles")
    if not isinstance(profiles, list):
        return None
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        if str(profile.get("id") or "").strip() == target_id:
            return profile
    return None


def _workspace_namespace_prefix(profile: Optional[Dict[str, Any]]) -> str:
    if not isinstance(profile, dict):
        return ""
    return str(profile.get("namespace") or "").strip().replace("\\", "/").strip("/")


def _workspace_join_namespace(*parts: str) -> str:
    cleaned = [str(part or "").strip().replace("\\", "/").strip("/") for part in parts]
    return "/".join(part for part in cleaned if part)


def _workspace_target_namespace(
    *,
    mode: str,
    target_profile: Optional[Dict[str, Any]],
    source_device_name: Optional[str],
    source_workspace_profile: Optional[Dict[str, Any]],
) -> str:
    base_namespace = _workspace_namespace_prefix(target_profile)
    if str(mode or "").strip().lower() != "import":
        return base_namespace
    source_workspace = source_workspace_profile or {}
    location = resolve_synced_workspace_location(
        parent_profile=target_profile,
        source_device_name=str(source_device_name or "").strip(),
        source_workspace_id=str(source_workspace.get("id") or "").strip(),
        source_workspace_name=str(source_workspace.get("name") or "").strip(),
        source_workspace_slug=str(source_workspace.get("slug") or "").strip(),
    )
    return str(location.get("namespace") or "").strip()


def _filter_recursive_workspace_ids(
    local_profiles: List[Dict[str, Any]],
    workspace_ids: List[str],
    paired_device: Optional[Dict[str, Any]],
) -> tuple[List[str], List[str]]:
    if not workspace_ids:
        return [], []
    profile_by_id = workspace_profile_map(local_profiles)
    peer_id = str((paired_device or {}).get("id") or "").strip()
    remote_name = str(
        (paired_device or {}).get("remote_device_name")
        or (paired_device or {}).get("label")
        or ""
    ).strip()
    filtered: List[str] = []
    ignored: List[str] = []
    for workspace_id in workspace_ids:
        profile = profile_by_id.get(workspace_id) or {}
        source_peer_id = str(profile.get("source_peer_id") or "").strip()
        source_device_name = str(profile.get("source_device_name") or "").strip()
        if (peer_id and source_peer_id and source_peer_id == peer_id) or (
            remote_name and source_device_name and source_device_name == remote_name
        ):
            ignored.append(workspace_id)
            continue
        filtered.append(workspace_id)
    return filtered, ignored


def _normalize_workspace_mode(value: Any) -> str:
    return "import" if str(value or "").strip().lower() == "import" else "merge"


def _upsert_workspace_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    settings = user_settings.load_settings()
    profiles, active_workspace_id, selected_workspace_ids = load_workspace_state(
        settings
    )
    next_profiles: List[Dict[str, Any]] = []
    replaced = False
    for existing in profiles:
        existing_id = str(existing.get("id") or "").strip()
        if existing_id == str(profile.get("id") or "").strip():
            next_profiles.append(profile)
            replaced = True
        elif existing_id != DEFAULT_WORKSPACE_ID:
            next_profiles.append(existing)
    if not replaced and str(profile.get("id") or "").strip():
        next_profiles.append(profile)
    user_settings.save_settings(
        {
            "workspace_profiles": [
                item
                for item in next_profiles
                if str(item.get("id") or "").strip() != DEFAULT_WORKSPACE_ID
            ],
            "active_workspace_id": active_workspace_id,
            "sync_selected_workspace_ids": selected_workspace_ids,
        }
    )
    return profile


def _persist_saved_peer_state(
    pairing: Optional[Dict[str, Any]],
    *,
    remote_label: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    normalized = _coerce_saved_peer(pairing or {})
    if normalized is None:
        return None
    peers = _load_saved_peers()
    peer_id = normalized["id"]
    remote_name = str(
        remote_label or normalized.get("remote_device_name") or ""
    ).strip()
    now = datetime.now(tz=timezone.utc).isoformat()
    next_peer = {
        **normalized,
        "remote_device_name": remote_name,
        "last_used_at": now,
    }
    updated = False
    next_peers: List[Dict[str, Any]] = []
    for peer in peers:
        if str(peer.get("id") or "").strip() == peer_id:
            next_peers.append({**peer, **next_peer})
            updated = True
        else:
            next_peers.append(peer)
    if not updated:
        return None
    user_settings.save_settings({"sync_saved_peers": next_peers})
    return next_peer


def _remove_saved_peer_state(peer_id: str) -> None:
    needle = str(peer_id or "").strip()
    if not needle:
        return
    peers = [
        peer
        for peer in _load_saved_peers()
        if str(peer.get("id") or "").strip() != needle
    ]
    user_settings.save_settings({"sync_saved_peers": peers})


def _candidate_urls_for_request(request: Optional[Request]) -> List[str]:
    return candidate_device_urls(request)


def _device_has_sync_capabilities(record: Dict[str, Any]) -> bool:
    capabilities = record.get("capabilities") if isinstance(record, dict) else {}
    if not isinstance(capabilities, dict):
        return False
    requested_scopes = capabilities.get("requested_scopes")
    if isinstance(requested_scopes, list) and requested_scopes:
        return True
    return any(
        bool(capabilities.get(key))
        for key in ("instance_sync", "paired_via_offer", "sync", "stream", "files")
    )


def _looks_like_legacy_browser_name(name: str) -> bool:
    lowered = str(name or "").strip().lower()
    if not lowered:
        return False
    return lowered.startswith("mozilla/5.0") or "applewebkit" in lowered


def _summarize_inbound_device(device_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
    capabilities = (
        record.get("capabilities")
        if isinstance(record.get("capabilities"), dict)
        else {}
    )
    name = str(record.get("name") or f"device-{str(device_id)[:8]}").strip()
    requested_scopes = (
        capabilities.get("requested_scopes")
        if isinstance(capabilities.get("requested_scopes"), list)
        else []
    )
    legacy_browser = not _device_has_sync_capabilities(
        record
    ) and _looks_like_legacy_browser_name(name)
    status = "legacy_browser_record" if legacy_browser else "trusted_device"
    status_label = "Legacy browser record" if legacy_browser else "Trusted device"
    last_seen = float(record.get("last_seen") or 0)
    if (
        not legacy_browser
        and last_seen > 0
        and (time.time() - last_seen) <= 600
        and _device_has_sync_capabilities(record)
    ):
        status = "connected_device"
        status_label = "Connected device"
    if capabilities.get("paired_via_offer"):
        status = "paired_device"
        status_label = "Paired device"
    return {
        "id": str(device_id),
        "name": name,
        "public_key": record.get("public_key"),
        "capabilities": capabilities,
        "created_at": float(record.get("created_at") or 0),
        "last_seen": last_seen,
        "status": status,
        "status_label": status_label,
        "legacy_browser_record": legacy_browser,
        "scopes": [
            str(item).strip().lower()
            for item in requested_scopes
            if str(item or "").strip()
        ],
    }


def _sync_review_summary(review: Dict[str, Any]) -> Dict[str, Any]:
    requested_sections = (
        review.get("requested_sections")
        if isinstance(review.get("requested_sections"), list)
        else []
    )
    return {
        "id": str(review.get("id") or "").strip(),
        "status": str(review.get("status") or "").strip() or "pending",
        "created_at": float(review.get("created_at") or 0),
        "updated_at": float(review.get("updated_at") or 0),
        "source_label": str(review.get("source_label") or "").strip()
        or "remote device",
        "device_name": str(review.get("device_name") or "").strip(),
        "device_id": str(review.get("device_id") or "").strip(),
        "requested_sections": requested_sections,
        "requested_section_labels": [
            SYNC_SECTION_LABELS.get(section, section.title())
            for section in requested_sections
        ],
        "decision": str(review.get("decision") or "").strip(),
        "note": str(review.get("note") or "").strip(),
        "effective_namespace": str(review.get("effective_namespace") or "").strip(),
    }


def _sync_reviews_snapshot(
    *, pending_limit: int = 12, recent_limit: int = 8
) -> Dict[str, Any]:
    pending = [
        _sync_review_summary(review)
        for review in list_sync_reviews(status="pending", limit=pending_limit)
    ]
    recent_source_limit = max(pending_limit + recent_limit, recent_limit * 4, 16)
    recent = [
        _sync_review_summary(review)
        for review in list_sync_reviews(limit=recent_source_limit)
        if str(review.get("status") or "").strip().lower() != "pending"
    ][:recent_limit]
    return {
        "pending": pending,
        "recent": recent,
        "counts": {
            "pending": len(pending),
            "recent": len(recent),
        },
    }


def _peer_connectivity_status(remote_url: str) -> Dict[str, Any]:
    urls = _resolve_remote_urls(remote_url)
    response = http_session.get(f"{urls['api_base']}/sync/overview", timeout=8)
    response.raise_for_status()
    payload = response.json()
    overview = payload if isinstance(payload, dict) else {}
    current = (
        overview.get("current_device")
        if isinstance(overview.get("current_device"), dict)
        else {}
    )
    device_access = (
        overview.get("device_access")
        if isinstance(overview.get("device_access"), dict)
        else {}
    )
    sync_defaults = (
        overview.get("sync_defaults")
        if isinstance(overview.get("sync_defaults"), dict)
        else {}
    )
    return {
        "reachable": True,
        "instance_base": urls["instance_base"],
        "display_name": str(current.get("display_name") or "").strip(),
        "hostname": str(current.get("hostname") or "").strip(),
        "source_namespace": str(current.get("source_namespace") or "").strip(),
        "visible_on_lan": bool(
            (device_access.get("visibility") or {}).get("lan_enabled")
            or sync_defaults.get("visible_on_lan")
        ),
        "advertised_lan_url": str(
            (device_access.get("advertised_urls") or {}).get("lan") or ""
        ).strip(),
        "advertised_local_url": str(
            (device_access.get("advertised_urls") or {}).get("local") or ""
        ).strip(),
        "workspaces": (
            overview.get("workspaces")
            if isinstance(overview.get("workspaces"), dict)
            else _workspace_state_summary({})
        ),
    }


def _log_remote_sync_failure(
    action: str,
    *,
    remote_url: str,
    paired_device: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Any]] = None,
    exc: requests.RequestException,
) -> None:
    pair = _coerce_saved_peer(paired_device or {}) or {}
    try:
        remote_base = _resolve_remote_urls(remote_url).get("instance_base", remote_url)
    except Exception:
        remote_base = str(remote_url or "").strip()
    response = getattr(exc, "response", None)
    response_excerpt = ""
    if response is not None:
        try:
            response_excerpt = textwrap.shorten(
                " ".join(str(response.text or "").split()),
                width=280,
                placeholder="...",
            )
        except Exception:
            response_excerpt = ""
    logger.warning(
        "Remote sync operation failed: action=%s remote=%s peer_id=%s peer_label=%s remote_device_id=%s remote_device_name=%s status=%s context=%s error=%s response=%s",
        action,
        remote_base,
        str(pair.get("id") or "").strip() or "-",
        str(pair.get("label") or "").strip() or "-",
        str(pair.get("remote_device_id") or "").strip() or "-",
        str(pair.get("remote_device_name") or "").strip() or "-",
        response.status_code if response is not None else "-",
        json.dumps(context or {}, ensure_ascii=False, sort_keys=True),
        exc,
        response_excerpt or "-",
    )


@router.post("/devices/register")
async def devices_register(payload: DeviceRegisterPayload):
    rec = register_or_update_device(
        payload.public_key,
        name=payload.name,
        capabilities=payload.capabilities,
    )
    return {"device": rec.__dict__}


class DeviceTokenRequest(BaseModel):
    device_id: str
    scopes: Optional[list[str]] = None
    ttl_seconds: Optional[int] = 3600


@router.post("/devices/token")
async def devices_token(payload: DeviceTokenRequest):
    if not get_device(payload.device_id):
        raise HTTPException(status_code=404, detail="Device not found")
    touch_device(payload.device_id)
    token = issue_device_token(
        payload.device_id, payload.scopes, payload.ttl_seconds or 3600
    )
    return {"token": token}


@router.get("/devices")
async def devices_list():
    return {"devices": list_devices()}


class DeviceUpdatePayload(BaseModel):
    name: Optional[str] = None
    capabilities: Optional[Dict[str, Any]] = None


@router.patch("/devices/{device_id}")
async def devices_update(
    device_id: str, payload: DeviceUpdatePayload, request: Request
):
    claims = _optional_device_claims(request, "sync")
    if (
        claims is not None
        and str(claims.get("sub") or "").strip() != str(device_id).strip()
    ):
        raise HTTPException(
            status_code=403, detail="Device token can only update its own record"
        )
    record = update_device(
        device_id,
        name=payload.name,
        capabilities=payload.capabilities,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Device not found")
    return {"device": record.__dict__}


@router.delete("/devices/{device_id}")
async def devices_delete(device_id: str, request: Request):
    claims = _optional_device_claims(request, "sync")
    if (
        claims is not None
        and str(claims.get("sub") or "").strip() != str(device_id).strip()
    ):
        raise HTTPException(
            status_code=403, detail="Device token can only delete its own record"
        )
    if not delete_device(device_id):
        raise HTTPException(status_code=404, detail="Device not found")
    return {"status": "deleted"}


@router.post("/devices/prune-legacy")
async def devices_prune_legacy():
    removed = 0
    for device_id, record in (list_devices() or {}).items():
        if not isinstance(record, dict):
            continue
        summary = _summarize_inbound_device(str(device_id), record)
        if not summary["legacy_browser_record"]:
            continue
        if delete_device(str(device_id)):
            removed += 1
    return {"removed": removed}


# ---------------------------------------------------------------------------
# Pairing and optional gateway endpoints


class PairingOfferPayload(BaseModel):
    requested_scopes: Optional[List[str]] = None
    ttl_seconds: Optional[int] = 600


class PairingAcceptPayload(BaseModel):
    code: str
    device_name: str
    public_key: str
    requested_scopes: Optional[List[str]] = None
    candidate_urls: Optional[List[str]] = None


class PairDevicePayload(BaseModel):
    peer_id: Optional[str] = None
    remote_url: str
    code: str
    label: Optional[str] = None
    scopes: Optional[List[str]] = None
    local_workspace_ids: Optional[List[str]] = None
    remote_workspace_ids: Optional[List[str]] = None
    workspace_mode: Optional[str] = "merge"
    local_target_workspace_id: Optional[str] = None
    remote_target_workspace_id: Optional[str] = None


class PairDeviceSyncPayload(BaseModel):
    paired_device: Dict[str, Any]


class SyncPeerStatusPayload(BaseModel):
    remote_url: str


class PairDeviceRevokePayload(BaseModel):
    paired_device: Dict[str, Any]
    remove_local_pair: bool = True


class GatewayOfferPayload(BaseModel):
    device_name: str
    public_key: str
    requested_scopes: Optional[List[str]] = None
    candidate_urls: Optional[List[str]] = None
    relay_url: Optional[str] = None
    ttl_seconds: Optional[int] = 600


class GatewayAcceptPayload(BaseModel):
    code: str
    device_name: str
    public_key: str
    candidate_urls: Optional[List[str]] = None
    relay_url: Optional[str] = None


class GatewaySessionPayload(BaseModel):
    peer_device_id: str
    scopes: Optional[List[str]] = None
    candidate_urls: Optional[List[str]] = None
    relay_url: Optional[str] = None
    ttl_seconds: Optional[int] = 900


@router.post("/pairing/offers")
async def pairing_create_offer(request: Request, payload: PairingOfferPayload):
    scopes = _normalize_sync_scopes(payload.requested_scopes) or ["sync"]
    offer = create_rendezvous_offer(
        device_name=str(
            user_settings.load_settings().get("device_display_name") or ""
        ).strip()
        or socket.gethostname(),
        public_key=_get_or_create_device_public_key(),
        requested_scopes=scopes,
        candidate_urls=_candidate_urls_for_request(request),
        ttl_seconds=int(payload.ttl_seconds or 600),
        metadata={"type": "pairing"},
    )
    return {
        "offer": {
            "code": offer["code"],
            "expires_at": float(offer["expires_at"]),
            "requested_scopes": offer["requested_scopes"],
            "candidate_urls": offer["candidate_urls"],
        }
    }


@router.post("/pairing/offers/accept")
async def pairing_accept_offer(request: Request, payload: PairingAcceptPayload):
    scopes = _normalize_sync_scopes(payload.requested_scopes) or ["sync"]
    offer = accept_rendezvous_offer(
        payload.code,
        device_name=payload.device_name,
        public_key=payload.public_key,
        candidate_urls=payload.candidate_urls or _candidate_urls_for_request(request),
    )
    incoming = register_or_update_device(
        payload.public_key,
        name=payload.device_name,
        capabilities={
            "instance_sync": True,
            "requested_scopes": scopes,
            "sync": "sync" in scopes,
            "stream": "stream" in scopes,
            "files": "files" in scopes,
            "paired_via_offer": True,
        },
    )
    service = _sync_service()
    return {
        "paired_device": {
            "remote_device_id": incoming.id,
            "public_key": payload.public_key,
            "remote_device_name": str(
                user_settings.load_settings().get("device_display_name") or ""
            ).strip()
            or socket.gethostname(),
            "remote_url": (_candidate_urls_for_request(request) or [""])[0],
            "scopes": scopes,
        },
        "current_device": {
            **service.current_instance_identity(),
            "display_name": str(
                user_settings.load_settings().get("device_display_name") or ""
            ).strip()
            or socket.gethostname(),
            "public_key": _get_or_create_device_public_key(),
        },
        "offer": {
            "code": offer.get("code"),
            "created_by": offer.get("device_name"),
            "requested_scopes": offer.get("requested_scopes") or [],
        },
    }


@router.post("/sync/pair")
async def sync_pair(payload: PairDevicePayload, request: Request):
    settings = user_settings.load_settings()
    device_name = (
        str(settings.get("device_display_name") or "").strip() or socket.gethostname()
    )
    public_key = _get_or_create_device_public_key()
    scopes = _normalize_sync_scopes(payload.scopes) or ["sync"]
    profiles, active_workspace_id, selected_workspace_ids = load_workspace_state(
        settings
    )
    local_workspace_ids = normalize_workspace_ids(
        payload.local_workspace_ids, profiles
    ) or list(selected_workspace_ids)
    try:
        urls = _resolve_remote_urls(payload.remote_url)
        response = http_session.post(
            f"{urls['api_base']}/pairing/offers/accept",
            json={
                "code": payload.code,
                "device_name": device_name,
                "public_key": public_key,
                "requested_scopes": scopes,
                "candidate_urls": _candidate_urls_for_request(request),
            },
            timeout=20,
        )
        response.raise_for_status()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except requests.RequestException as exc:
        _log_remote_sync_failure(
            "sync_pair",
            remote_url=payload.remote_url,
            context={
                "requested_scopes": scopes,
                "local_workspace_ids": local_workspace_ids,
                "remote_workspace_ids": payload.remote_workspace_ids or [],
                "workspace_mode": payload.workspace_mode or "merge",
            },
            exc=exc,
        )
        raise HTTPException(status_code=502, detail=f"Pairing failed: {exc}")
    parsed = response.json()
    result = parsed if isinstance(parsed, dict) else {}
    pair_id = str(payload.peer_id or "").strip() or str(uuid4())
    paired_device = _coerce_saved_peer(
        {
            "id": pair_id,
            "label": str(
                payload.label
                or result.get("current_device", {}).get("display_name")
                or payload.remote_url
            ).strip(),
            "remote_url": urls["instance_base"],
            "scopes": scopes,
            "remote_device_id": str(
                (result.get("paired_device") or {}).get("remote_device_id") or ""
            ).strip(),
            "public_key": public_key,
            "remote_public_key": str(
                (result.get("current_device") or {}).get("public_key") or ""
            ).strip(),
            "remote_device_name": str(
                (result.get("current_device") or {}).get("display_name") or ""
            ).strip(),
            "last_used_at": datetime.now(tz=timezone.utc).isoformat(),
            "local_workspace_ids": local_workspace_ids,
            "remote_workspace_ids": [
                str(item).strip()
                for item in (payload.remote_workspace_ids or [])
                if str(item or "").strip()
            ],
            "workspace_mode": (
                "import"
                if str(payload.workspace_mode or "").strip().lower() == "import"
                else "merge"
            ),
            "local_target_workspace_id": str(
                payload.local_target_workspace_id or active_workspace_id
            ).strip()
            or active_workspace_id,
            "remote_target_workspace_id": str(
                payload.remote_target_workspace_id or DEFAULT_WORKSPACE_ID
            ).strip()
            or DEFAULT_WORKSPACE_ID,
        }
    )
    if paired_device is None:
        raise HTTPException(status_code=400, detail="Pairing response was incomplete")
    peers = _load_saved_peers()
    peers = [
        peer
        for peer in peers
        if str(peer.get("remote_url") or "").strip() != urls["instance_base"]
        and str(peer.get("id") or "").strip() != pair_id
    ]
    peers.insert(0, paired_device)
    user_settings.save_settings(
        {
            "sync_remote_url": urls["instance_base"],
            "sync_saved_peers": peers,
        }
    )
    return {"paired_device": paired_device}


@router.post("/sync/peer/status")
async def sync_peer_status(payload: SyncPeerStatusPayload):
    try:
        return _peer_connectivity_status(payload.remote_url)
    except requests.RequestException as exc:
        _log_remote_sync_failure(
            "sync_peer_status",
            remote_url=payload.remote_url,
            exc=exc,
        )
        raise HTTPException(
            status_code=502, detail=f"Remote status check failed: {exc}"
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/sync/pair/update")
async def sync_pair_update(payload: PairDeviceSyncPayload):
    pairing = _coerce_saved_peer(payload.paired_device)
    if pairing is None:
        raise HTTPException(status_code=400, detail="Paired device payload is invalid")
    settings = user_settings.load_settings()
    remote = RemoteFloatClient(
        pairing["remote_url"],
        paired_device=pairing,
        device_name=str(settings.get("device_display_name") or "").strip()
        or socket.gethostname(),
    )
    try:
        updated_pair = remote.sync_device_registration()
    except requests.RequestException as exc:
        _log_remote_sync_failure(
            "sync_pair_update",
            remote_url=pairing["remote_url"],
            paired_device=pairing,
            exc=exc,
        )
        raise HTTPException(status_code=502, detail=f"Remote pair update failed: {exc}")
    persisted = _persist_saved_peer_state(
        updated_pair, remote_label=pairing.get("remote_device_name")
    )
    return {"paired_device": persisted or updated_pair}


@router.post("/sync/pair/revoke")
async def sync_pair_revoke(payload: PairDeviceRevokePayload):
    pairing = _coerce_saved_peer(payload.paired_device)
    if pairing is None:
        raise HTTPException(status_code=400, detail="Paired device payload is invalid")
    settings = user_settings.load_settings()
    remote = RemoteFloatClient(
        pairing["remote_url"],
        paired_device=pairing,
        device_name=str(settings.get("device_display_name") or "").strip()
        or socket.gethostname(),
    )
    try:
        remote.delete_remote_device()
    except requests.RequestException as exc:
        _log_remote_sync_failure(
            "sync_pair_revoke",
            remote_url=pairing["remote_url"],
            paired_device=pairing,
            context={"remove_local_pair": payload.remove_local_pair},
            exc=exc,
        )
        raise HTTPException(status_code=502, detail=f"Remote revoke failed: {exc}")
    if payload.remove_local_pair:
        _remove_saved_peer_state(pairing["id"])
    return {"status": "revoked", "paired_device_id": pairing["id"]}


@router.post("/gateway/rendezvous/offers")
async def gateway_create_offer(payload: GatewayOfferPayload):
    offer = create_rendezvous_offer(
        device_name=payload.device_name,
        public_key=payload.public_key,
        requested_scopes=_normalize_sync_scopes(payload.requested_scopes) or ["sync"],
        candidate_urls=payload.candidate_urls or [],
        relay_url=payload.relay_url,
        ttl_seconds=int(payload.ttl_seconds or 600),
        metadata={"type": "gateway"},
    )
    return {
        "offer_id": offer["offer_id"],
        "code": offer["code"],
        "expires_at": float(offer["expires_at"]),
        "relay_url": offer.get("relay_url"),
    }


@router.post("/gateway/rendezvous/accept")
async def gateway_accept_offer(payload: GatewayAcceptPayload):
    offer = accept_rendezvous_offer(
        payload.code,
        device_name=payload.device_name,
        public_key=payload.public_key,
        candidate_urls=payload.candidate_urls or [],
        relay_url=payload.relay_url,
    )
    created_by = {
        "device_name": offer.get("device_name"),
        "public_key": offer.get("public_key"),
    }
    return {
        "peer_device_name": created_by["device_name"],
        "peer_public_key": created_by["public_key"],
        "candidate_urls": offer.get("candidate_urls") or [],
        "relay_session_id": offer.get("offer_id"),
        "relay_url": offer.get("relay_url"),
    }


@router.post("/gateway/sessions")
async def gateway_create_session(payload: GatewaySessionPayload):
    session = create_rendezvous_session(
        peer_device_id=payload.peer_device_id,
        scopes=_normalize_sync_scopes(payload.scopes) or ["sync"],
        candidate_urls=payload.candidate_urls or [],
        relay_url=payload.relay_url,
        ttl_seconds=int(payload.ttl_seconds or 900),
    )
    return {
        "session_token": session["session_token"],
        "expires_at": float(session["expires_at"]),
        "candidate_urls": session["candidate_urls"],
        "relay_url": session.get("relay_url"),
    }


# ---------------------------------------------------------------------------
# Sync endpoints


class SyncSectionRequest(BaseModel):
    sections: Optional[List[str]] = None
    workspace_ids: Optional[List[str]] = None


class SyncIngestRequest(BaseModel):
    snapshot: Dict[str, Any]
    link_to_source: bool = False
    source_namespace: Optional[str] = None
    source_label: Optional[str] = None
    target_namespace: Optional[str] = None


class SyncPlanRequest(BaseModel):
    remote_url: str
    sections: Optional[List[str]] = None
    link_to_source: bool = False
    source_namespace: Optional[str] = None
    paired_device: Optional[Dict[str, Any]] = None
    local_workspace_ids: Optional[List[str]] = None
    remote_workspace_ids: Optional[List[str]] = None
    workspace_mode: str = "merge"
    local_target_workspace_id: Optional[str] = None
    remote_target_workspace_id: Optional[str] = None


class SyncApplyRequest(BaseModel):
    remote_url: str
    direction: Literal["pull", "push"] = "pull"
    sections: Optional[List[str]] = None
    item_selections: Optional[Dict[str, List[str]]] = None
    link_to_source: bool = False
    source_namespace: Optional[str] = None
    paired_device: Optional[Dict[str, Any]] = None
    local_workspace_ids: Optional[List[str]] = None
    remote_workspace_ids: Optional[List[str]] = None
    workspace_mode: str = "merge"
    local_target_workspace_id: Optional[str] = None
    remote_target_workspace_id: Optional[str] = None


def _sync_service() -> InstanceSyncService:
    return InstanceSyncService()


_SYNC_MANUAL_REFRESH_NOTE_SNIPPETS = (
    "run a reindex/rehydrate pass",
    "caption reindex pass later",
    "calendar rag rehydrate pass",
)


def _sync_section_applied(section_result: Any) -> int:
    if not isinstance(section_result, dict):
        return 0
    try:
        return max(0, int(section_result.get("applied") or 0))
    except Exception:
        return 0


async def _refresh_sync_result_indexes(result: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    section_results = result.get("sections")
    if not isinstance(section_results, dict):
        return {}
    refresh: Dict[str, Any] = {}
    if _sync_section_applied(section_results.get("knowledge")):
        try:
            refresh["knowledge"] = await knowledge_rag_rehydrate(
                KnowledgeRagRehydrate()
            )
        except Exception as exc:
            refresh["knowledge"] = {"error": str(exc)}
    if _sync_section_applied(section_results.get("attachments")):
        try:
            refresh["attachments"] = await attachments_rag_rehydrate(
                AttachmentsRagRehydrate()
            )
        except Exception as exc:
            refresh["attachments"] = {"error": str(exc)}
    if _sync_section_applied(section_results.get("calendar")):
        try:
            refresh["calendar"] = await calendar_rag_rehydrate(
                CalendarRagRehydrate()
            )
        except Exception as exc:
            refresh["calendar"] = {"error": str(exc)}
    if not refresh:
        return {}
    existing_notes = result.get("notes")
    cleaned_notes: List[str] = []
    if isinstance(existing_notes, list):
        for note in existing_notes:
            text = str(note or "").strip()
            if not text:
                continue
            lower = text.lower()
            if any(
                snippet in lower for snippet in _SYNC_MANUAL_REFRESH_NOTE_SNIPPETS
            ):
                continue
            cleaned_notes.append(text)
    for section, refresh_result in refresh.items():
        if isinstance(refresh_result, dict) and refresh_result.get("error"):
            cleaned_notes.append(
                f"Post-sync {section} refresh failed: {refresh_result['error']}"
            )
            continue
        scanned = int((refresh_result or {}).get("scanned") or 0)
        reindexed = int((refresh_result or {}).get("reindexed") or 0)
        if section == "knowledge":
            cleaned_notes.append(
                f"Semantic search refreshed for {reindexed} synced knowledge items ({scanned} scanned)."
            )
        elif section == "attachments":
            cleaned_notes.append(
                f"Attachment search mirrors refreshed for {reindexed} synced image attachments ({scanned} scanned)."
            )
        elif section == "calendar":
            cleaned_notes.append(
                f"Calendar retrieval refreshed for {reindexed} synced events ({scanned} scanned)."
            )
    result["notes"] = cleaned_notes
    result["post_refresh"] = refresh
    return refresh


async def _apply_sync_ingest(
    service: InstanceSyncService,
    request: Request,
    payload: SyncIngestRequest,
) -> Dict[str, Any]:
    sections = service.normalize_sections(
        list((payload.snapshot.get("sections") or {}).keys())
    )
    before_snapshot = service.build_snapshot(sections) if sections else None
    merged = service.merge_snapshot(
        payload.snapshot,
        link_to_source=payload.link_to_source,
        source_namespace=payload.source_namespace,
        source_label=payload.source_label,
        target_namespace=payload.target_namespace,
    )
    await _refresh_sync_result_indexes(merged)
    after_snapshot = service.build_snapshot(sections) if sections else None
    if sections:
        sync_record_changes(
            [
                {
                    "type": "sync_ingest",
                    "sections": sections,
                    "applied_at": merged.get("applied_at"),
                }
            ]
        )
        remote_instance = (
            payload.snapshot.get("instance")
            if isinstance(payload.snapshot.get("instance"), dict)
            else {}
        )
        source_label = (
            remote_instance.get("hostname")
            or remote_instance.get("source_namespace")
            or payload.source_namespace
            or "snapshot"
        )
        _record_sync_action(
            request,
            name="sync_ingest",
            summary=f"Sync ingest from {source_label}",
            before_snapshot=before_snapshot,
            after_snapshot=after_snapshot,
            sections=sections,
            args={
                "link_to_source": payload.link_to_source,
                "source_namespace": payload.source_namespace,
                "source_label": payload.source_label,
                "target_namespace": payload.target_namespace,
                "sections": sections,
            },
            result=merged,
            batch_scope={
                "scope": "sync_ingest",
                "sections": sections,
                "source_label": source_label,
            },
        )
    return merged


@router.get("/sync/overview")
async def sync_overview(request: Request):
    service = _sync_service()
    settings = user_settings.load_settings()
    identity = service.current_instance_identity(
        source_namespace=settings.get("sync_source_namespace"),
    )
    workspace_state = _workspace_state_summary(settings)
    access = advertised_device_access(request)
    display_name = str(settings.get("device_display_name") or "").strip()
    inbound_devices = [
        _summarize_inbound_device(str(device_id), record)
        for device_id, record in (list_devices() or {}).items()
        if isinstance(record, dict)
    ]
    inbound_devices.sort(
        key=lambda item: (
            float(item.get("last_seen") or 0),
            float(item.get("created_at") or 0),
        ),
        reverse=True,
    )
    trusted_devices = [
        item for item in inbound_devices if not item.get("legacy_browser_record")
    ]
    legacy_inbound_devices = [
        item for item in inbound_devices if item.get("legacy_browser_record")
    ]
    sync_reviews = _sync_reviews_snapshot(pending_limit=12, recent_limit=8)
    return {
        "current_device": {
            "display_name": display_name or identity.get("hostname") or "This device",
            "hostname": identity.get("hostname"),
            "public_key": _get_or_create_device_public_key(),
            "source_namespace": identity.get("source_namespace"),
            "link_to_source_default": bool(identity.get("link_to_source_default")),
        },
        "device_access": access,
        "sync_defaults": {
            "remote_url": str(settings.get("sync_remote_url") or "").strip(),
            "visible_on_lan": bool(settings.get("sync_visible_on_lan")),
            "visible_online": bool(settings.get("sync_visible_online")),
            "online_url": str(settings.get("sync_online_url") or "").strip(),
            "auto_accept_push": bool(settings.get("sync_auto_accept_push")),
            "link_to_source": bool(settings.get("sync_link_to_source_device")),
            "source_namespace": str(
                settings.get("sync_source_namespace") or ""
            ).strip(),
            "saved_peers": _load_saved_peers(),
        },
        "workspaces": workspace_state,
        "inbound_devices": trusted_devices,
        "legacy_inbound_devices": legacy_inbound_devices,
        "sync_reviews": {
            "pending": sync_reviews["pending"],
            "recent": sync_reviews["recent"],
        },
        "device_counts": {
            "paired": len(_load_saved_peers()),
            "trusted": len(trusted_devices),
            "legacy": len(legacy_inbound_devices),
            "pending_push_reviews": sync_reviews["counts"]["pending"],
        },
    }


@router.post("/sync/manifest")
async def sync_manifest(request: Request, payload: SyncSectionRequest):
    _require_scope(request, "sync")
    service = _sync_service()
    manifest = service.build_manifest(
        payload.sections, workspace_ids=payload.workspace_ids
    )
    manifest["instance"]["labels"] = SYNC_SECTION_LABELS
    return manifest


@router.post("/sync/export")
async def sync_export(request: Request, payload: SyncSectionRequest):
    _require_scope(request, "sync")
    service = _sync_service()
    snapshot = service.build_snapshot(
        payload.sections, workspace_ids=payload.workspace_ids
    )
    snapshot["labels"] = SYNC_SECTION_LABELS
    return snapshot


@router.post("/sync/ingest")
async def sync_ingest(request: Request, payload: SyncIngestRequest):
    claims = _require_scope(request, "sync")
    service = _sync_service()
    sections = service.normalize_sections(
        list((payload.snapshot.get("sections") or {}).keys())
    )
    settings = user_settings.load_settings()
    auto_accept = bool(settings.get("sync_auto_accept_push"))
    remote_instance = (
        payload.snapshot.get("instance")
        if isinstance(payload.snapshot.get("instance"), dict)
        else {}
    )
    source_label = (
        remote_instance.get("display_name")
        or remote_instance.get("hostname")
        or remote_instance.get("source_namespace")
        or payload.source_label
        or payload.source_namespace
        or "remote device"
    )
    if not auto_accept:
        review = create_sync_review(
            {
                "device_id": str(claims.get("sub") or "").strip(),
                "device_name": str(
                    remote_instance.get("display_name")
                    or payload.source_label
                    or source_label
                ).strip(),
                "source_label": source_label,
                "link_to_source": bool(payload.link_to_source),
                "source_namespace": str(payload.source_namespace or "").strip(),
                "target_namespace": str(payload.target_namespace or "").strip(),
                "requested_sections": sections,
                "snapshot": payload.snapshot,
            }
        )
        return {
            "status": "pending_review",
            "review_request_id": review["id"],
            "source_label": source_label,
            "requested_sections": sections,
        }
    return await _apply_sync_ingest(service, request, payload)


class SyncReviewDecisionPayload(BaseModel):
    note: Optional[str] = None


@router.post("/sync/reviews/{review_id}/approve")
async def sync_review_approve(
    review_id: str, request: Request, payload: SyncReviewDecisionPayload
):
    review = get_sync_review(review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="Sync review request not found")
    if str(review.get("status") or "").strip().lower() != "pending":
        raise HTTPException(
            status_code=409, detail="Sync review request is no longer pending"
        )
    sync_payload = SyncIngestRequest(
        snapshot=dict(review.get("snapshot") or {}),
        link_to_source=bool(review.get("link_to_source")),
        source_namespace=str(review.get("source_namespace") or "").strip() or None,
        source_label=str(review.get("source_label") or "").strip() or None,
        target_namespace=str(review.get("target_namespace") or "").strip() or None,
    )
    result = await _apply_sync_ingest(_sync_service(), request, sync_payload)
    updated = update_sync_review(
        review_id,
        {
            "status": "approved",
            "decision": "approved",
            "note": str(payload.note or "").strip(),
            "reviewed_at": time.time(),
            "effective_namespace": str(result.get("effective_namespace") or "").strip(),
        },
    )
    return {
        "status": "approved",
        "review": _sync_review_summary(updated or review),
        "result": result,
    }


@router.post("/sync/reviews/{review_id}/reject")
async def sync_review_reject(review_id: str, payload: SyncReviewDecisionPayload):
    review = get_sync_review(review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="Sync review request not found")
    if str(review.get("status") or "").strip().lower() != "pending":
        raise HTTPException(
            status_code=409, detail="Sync review request is no longer pending"
        )
    updated = update_sync_review(
        review_id,
        {
            "status": "rejected",
            "decision": "rejected",
            "note": str(payload.note or "").strip(),
            "reviewed_at": time.time(),
        },
    )
    return {"status": "rejected", "review": _sync_review_summary(updated or review)}


@router.post("/sync/plan")
async def sync_plan(payload: SyncPlanRequest):
    service = _sync_service()
    try:
        settings = user_settings.load_settings()
        local_workspace_state = _workspace_state_summary(settings)
        local_profiles = local_workspace_state["profiles"]
        pairing = _coerce_saved_peer(payload.paired_device or {})
        local_workspace_ids = normalize_workspace_ids(
            payload.local_workspace_ids, local_profiles
        ) or list(
            (pairing or {}).get("local_workspace_ids")
            or local_workspace_state["selected_workspace_ids"]
        )
        (
            local_workspace_ids,
            ignored_local_workspace_ids,
        ) = _filter_recursive_workspace_ids(
            local_profiles, local_workspace_ids, pairing
        )
        if not local_workspace_ids:
            raise HTTPException(
                status_code=400,
                detail="All selected local workspaces were ignored to avoid syncing a workspace back to its source device.",
            )
        remote = RemoteFloatClient(
            payload.remote_url,
            paired_device=pairing,
            device_name=str(settings.get("device_display_name") or "").strip()
            or socket.gethostname(),
        )
        remote_overview = remote.get_sync_overview()
        remote_workspace_state = (
            remote_overview.get("workspaces")
            if isinstance(remote_overview.get("workspaces"), dict)
            else _workspace_state_summary({})
        )
        remote_workspace_ids = [
            str(item).strip()
            for item in (
                payload.remote_workspace_ids
                or (pairing or {}).get("remote_workspace_ids")
                or remote_workspace_state.get("selected_workspace_ids")
                or [
                    remote_workspace_state.get("active_workspace_id")
                    or DEFAULT_WORKSPACE_ID
                ]
            )
            if str(item or "").strip()
        ]
        workspace_mode = _normalize_workspace_mode(
            payload.workspace_mode or (pairing or {}).get("workspace_mode")
        )
        local_target_workspace_id = (
            str(
                payload.local_target_workspace_id
                or (pairing or {}).get("local_target_workspace_id")
                or local_workspace_state["active_workspace_id"]
            ).strip()
            or local_workspace_state["active_workspace_id"]
        )
        remote_target_workspace_id = (
            str(
                payload.remote_target_workspace_id
                or (pairing or {}).get("remote_target_workspace_id")
                or remote_workspace_state.get("active_workspace_id")
                or DEFAULT_WORKSPACE_ID
            ).strip()
            or DEFAULT_WORKSPACE_ID
        )
        if workspace_mode == "import" and (
            len(remote_workspace_ids) != 1 or len(local_workspace_ids) != 1
        ):
            raise HTTPException(
                status_code=400,
                detail="Import mode currently supports one source workspace per side.",
            )
        local_manifest = service.build_manifest(
            payload.sections, workspace_ids=local_workspace_ids
        )
        local_manifest["instance"] = service.current_instance_identity(
            source_namespace=payload.source_namespace,
        )
        remote_manifest = remote.get_manifest(
            service.normalize_sections(payload.sections),
            workspace_ids=remote_workspace_ids,
        )
        local_instance = (
            local_manifest.get("instance")
            if isinstance(local_manifest.get("instance"), dict)
            else {}
        )
        remote_instance = (
            remote_manifest.get("instance")
            if isinstance(remote_manifest.get("instance"), dict)
            else {}
        )
        local_target_workspace = _workspace_profile_from_state(
            local_workspace_state, local_target_workspace_id
        )
        remote_target_workspace = _workspace_profile_from_state(
            remote_workspace_state, remote_target_workspace_id
        )
        local_source_workspace = _workspace_profile_from_state(
            local_workspace_state,
            local_workspace_ids[0] if local_workspace_ids else DEFAULT_WORKSPACE_ID,
        )
        remote_source_workspace = _workspace_profile_from_state(
            remote_workspace_state,
            remote_workspace_ids[0] if remote_workspace_ids else DEFAULT_WORKSPACE_ID,
        )
        pull_namespace = _workspace_target_namespace(
            mode=workspace_mode,
            target_profile=local_target_workspace,
            source_device_name=remote_instance.get("display_name")
            or remote_instance.get("hostname"),
            source_workspace_profile=remote_source_workspace,
        ) or service.resolve_source_namespace(
            link_to_source=payload.link_to_source,
            source_namespace=remote_instance.get("source_namespace"),
            source_label=remote_instance.get("display_name")
            or remote_instance.get("hostname"),
        )
        push_namespace = _workspace_target_namespace(
            mode=workspace_mode,
            target_profile=remote_target_workspace,
            source_device_name=local_instance.get("display_name")
            or local_instance.get("hostname"),
            source_workspace_profile=local_source_workspace,
        ) or service.resolve_source_namespace(
            link_to_source=payload.link_to_source,
            source_namespace=local_instance.get("source_namespace"),
            source_label=local_instance.get("display_name")
            or local_instance.get("hostname"),
        )
        pull_manifest = (
            service.namespace_manifest(remote_manifest, namespace=pull_namespace)
            if pull_namespace
            else remote_manifest
        )
        push_manifest = (
            service.namespace_manifest(local_manifest, namespace=push_namespace)
            if push_namespace
            else local_manifest
        )
        pull_comparison = service.compare_manifests(
            local_manifest,
            pull_manifest,
            payload.sections,
        )
        push_comparison = service.compare_manifests(
            push_manifest,
            remote_manifest,
            payload.sections,
        )
        pair_state = remote.get_pairing_state()
        if pairing is not None:
            pair_state.update(
                {
                    "local_workspace_ids": local_workspace_ids,
                    "remote_workspace_ids": remote_workspace_ids,
                    "workspace_mode": workspace_mode,
                    "local_target_workspace_id": local_target_workspace_id,
                    "remote_target_workspace_id": remote_target_workspace_id,
                }
            )
        persisted_pair = _persist_saved_peer_state(
            pair_state,
            remote_label=remote_instance.get("display_name")
            or remote_instance.get("hostname"),
        )
        return {
            "link_to_source": payload.link_to_source,
            "workspace_mode": workspace_mode,
            "local": local_instance,
            "remote": {
                **remote_instance,
                "base_url": remote.instance_base,
            },
            "paired_device": persisted_pair or pair_state,
            "effective_namespaces": {
                "pull": pull_namespace or None,
                "push": push_namespace or None,
            },
            "workspaces": {
                "local": {
                    **local_workspace_state,
                    "selected_workspace_ids": local_workspace_ids,
                    "target_workspace_id": local_target_workspace_id,
                    "ignored_workspace_ids": ignored_local_workspace_ids,
                },
                "remote": {
                    **remote_workspace_state,
                    "selected_workspace_ids": remote_workspace_ids,
                    "target_workspace_id": remote_target_workspace_id,
                },
            },
            "sections": pull_comparison,
            "pull_sections": pull_comparison,
            "push_sections": push_comparison,
        }
    except requests.RequestException as exc:
        _log_remote_sync_failure(
            "sync_plan",
            remote_url=payload.remote_url,
            paired_device=payload.paired_device,
            context={
                "sections": service.normalize_sections(payload.sections),
                "workspace_mode": payload.workspace_mode,
                "local_workspace_ids": payload.local_workspace_ids or [],
                "remote_workspace_ids": payload.remote_workspace_ids or [],
                "local_target_workspace_id": payload.local_target_workspace_id
                or "",
                "remote_target_workspace_id": payload.remote_target_workspace_id
                or "",
            },
            exc=exc,
        )
        raise HTTPException(status_code=502, detail=f"Remote sync probe failed: {exc}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/sync/apply")
async def sync_apply(request: Request, payload: SyncApplyRequest):
    service = _sync_service()
    sections = service.normalize_sections(payload.sections)
    item_selections = service.normalize_item_selections(
        sections, payload.item_selections
    )
    try:
        settings = user_settings.load_settings()
        local_workspace_state = _workspace_state_summary(settings)
        local_profiles = local_workspace_state["profiles"]
        pairing = _coerce_saved_peer(payload.paired_device or {})
        local_workspace_ids = normalize_workspace_ids(
            payload.local_workspace_ids, local_profiles
        ) or list(
            (pairing or {}).get("local_workspace_ids")
            or local_workspace_state["selected_workspace_ids"]
        )
        (
            local_workspace_ids,
            ignored_local_workspace_ids,
        ) = _filter_recursive_workspace_ids(
            local_profiles, local_workspace_ids, pairing
        )
        if not local_workspace_ids:
            raise HTTPException(
                status_code=400,
                detail="All selected local workspaces were ignored to avoid syncing a workspace back to its source device.",
            )
        remote = RemoteFloatClient(
            payload.remote_url,
            paired_device=pairing,
            device_name=str(settings.get("device_display_name") or "").strip()
            or socket.gethostname(),
        )
        remote_overview = remote.get_sync_overview()
        remote_workspace_state = (
            remote_overview.get("workspaces")
            if isinstance(remote_overview.get("workspaces"), dict)
            else _workspace_state_summary({})
        )
        remote_workspace_ids = [
            str(item).strip()
            for item in (
                payload.remote_workspace_ids
                or (pairing or {}).get("remote_workspace_ids")
                or remote_workspace_state.get("selected_workspace_ids")
                or [
                    remote_workspace_state.get("active_workspace_id")
                    or DEFAULT_WORKSPACE_ID
                ]
            )
            if str(item or "").strip()
        ]
        workspace_mode = _normalize_workspace_mode(
            payload.workspace_mode or (pairing or {}).get("workspace_mode")
        )
        local_target_workspace_id = (
            str(
                payload.local_target_workspace_id
                or (pairing or {}).get("local_target_workspace_id")
                or local_workspace_state["active_workspace_id"]
            ).strip()
            or local_workspace_state["active_workspace_id"]
        )
        remote_target_workspace_id = (
            str(
                payload.remote_target_workspace_id
                or (pairing or {}).get("remote_target_workspace_id")
                or remote_workspace_state.get("active_workspace_id")
                or DEFAULT_WORKSPACE_ID
            ).strip()
            or DEFAULT_WORKSPACE_ID
        )
        if workspace_mode == "import" and (
            len(remote_workspace_ids) != 1 or len(local_workspace_ids) != 1
        ):
            raise HTTPException(
                status_code=400,
                detail="Import mode currently supports one source workspace per side.",
            )
        local_identity = service.current_instance_identity(
            source_namespace=payload.source_namespace,
        )
        local_target_workspace = _workspace_profile_from_state(
            local_workspace_state, local_target_workspace_id
        )
        remote_target_workspace = _workspace_profile_from_state(
            remote_workspace_state, remote_target_workspace_id
        )
        local_source_workspace = _workspace_profile_from_state(
            local_workspace_state,
            local_workspace_ids[0] if local_workspace_ids else DEFAULT_WORKSPACE_ID,
        )
        remote_source_workspace = _workspace_profile_from_state(
            remote_workspace_state,
            remote_workspace_ids[0] if remote_workspace_ids else DEFAULT_WORKSPACE_ID,
        )
        push_target_namespace = _workspace_target_namespace(
            mode=workspace_mode,
            target_profile=remote_target_workspace,
            source_device_name=local_identity.get("display_name")
            or local_identity.get("hostname"),
            source_workspace_profile=local_source_workspace,
        )
        if payload.direction == "push":
            snapshot = service.build_snapshot(
                sections, workspace_ids=local_workspace_ids
            )
            snapshot = service.filter_snapshot_by_item_selections(
                snapshot, item_selections
            )
            snapshot["instance"] = local_identity
            remote_result = remote.ingest_snapshot(
                snapshot,
                link_to_source=payload.link_to_source or workspace_mode == "import",
                source_namespace=local_identity.get("source_namespace"),
                source_label=local_identity.get("display_name")
                or local_identity.get("hostname"),
                target_namespace=push_target_namespace or None,
            )
            pair_state = remote.get_pairing_state()
            if pairing is not None:
                pair_state.update(
                    {
                        "local_workspace_ids": local_workspace_ids,
                        "remote_workspace_ids": remote_workspace_ids,
                        "workspace_mode": workspace_mode,
                        "local_target_workspace_id": local_target_workspace_id,
                        "remote_target_workspace_id": remote_target_workspace_id,
                    }
                )
            persisted_pair = _persist_saved_peer_state(
                pair_state,
                remote_label=remote_result.get("source_label"),
            )
            return {
                "direction": "push",
                "sections": sections,
                "remote": remote.instance_base,
                "paired_device": persisted_pair or pair_state,
                "ignored_local_workspace_ids": ignored_local_workspace_ids,
                "workspace_mode": workspace_mode,
                "effective_namespace": remote_result.get("effective_namespace"),
                "item_selections": item_selections,
                "result": remote_result,
            }
        before_snapshot = (
            service.build_snapshot(sections, workspace_ids=local_workspace_ids)
            if sections
            else None
        )
        snapshot = remote.export_snapshot(sections, workspace_ids=remote_workspace_ids)
        snapshot = service.filter_snapshot_by_item_selections(snapshot, item_selections)
        remote_identity = (
            snapshot.get("instance")
            if isinstance(snapshot.get("instance"), dict)
            else {}
        )
        pull_target_namespace = _workspace_target_namespace(
            mode=workspace_mode,
            target_profile=local_target_workspace,
            source_device_name=remote_identity.get("display_name")
            or remote_identity.get("hostname"),
            source_workspace_profile=remote_source_workspace,
        )
        pair_state = remote.get_pairing_state()
        if pairing is not None:
            pair_state.update(
                {
                    "local_workspace_ids": local_workspace_ids,
                    "remote_workspace_ids": remote_workspace_ids,
                    "workspace_mode": workspace_mode,
                    "local_target_workspace_id": local_target_workspace_id,
                    "remote_target_workspace_id": remote_target_workspace_id,
                }
            )
        persisted_pair = _persist_saved_peer_state(
            pair_state,
            remote_label=remote_identity.get("display_name")
            or remote_identity.get("hostname"),
        )
        local_result = service.merge_snapshot(
            snapshot,
            link_to_source=payload.link_to_source or workspace_mode == "import",
            source_namespace=remote_identity.get("source_namespace"),
            source_label=remote_identity.get("display_name")
            or remote_identity.get("hostname"),
            target_namespace=pull_target_namespace or None,
        )
        await _refresh_sync_result_indexes(local_result)
        if (
            workspace_mode == "import"
            and len(remote_workspace_ids) == 1
            and persisted_pair is not None
        ):
            imported_profile = build_synced_workspace_profile(
                parent_profile=local_target_workspace,
                source_peer_id=str(persisted_pair.get("id") or "").strip(),
                source_device_name=str(
                    remote_identity.get("display_name")
                    or remote_identity.get("hostname")
                    or "Remote"
                ).strip(),
                source_workspace_id=str(
                    remote_source_workspace.get("id") if remote_source_workspace else ""
                ).strip(),
                source_workspace_name=str(
                    remote_source_workspace.get("name")
                    if remote_source_workspace
                    else ""
                ).strip(),
                source_workspace_slug=str(
                    remote_source_workspace.get("slug")
                    if remote_source_workspace
                    else ""
                ).strip(),
            )
            _upsert_workspace_profile(imported_profile)
        after_snapshot = (
            service.build_snapshot(sections, workspace_ids=local_workspace_ids)
            if sections
            else None
        )
        sync_record_changes(
            [
                {
                    "type": "sync_apply",
                    "direction": "pull",
                    "remote": remote.instance_base,
                    "sections": sections,
                    "applied_at": local_result.get("applied_at"),
                }
            ]
        )
        _record_sync_action(
            request,
            name="sync_pull",
            summary=f"Sync pull from {remote.instance_base}",
            before_snapshot=before_snapshot,
            after_snapshot=after_snapshot,
            sections=sections,
            args={
                "remote_url": payload.remote_url,
                "direction": payload.direction,
                "sections": sections,
                "link_to_source": payload.link_to_source,
                "source_namespace": payload.source_namespace,
                "workspace_mode": workspace_mode,
                "local_workspace_ids": local_workspace_ids,
                "remote_workspace_ids": remote_workspace_ids,
                "item_selections": item_selections,
            },
            result=local_result,
            batch_scope={
                "scope": "sync_pull",
                "remote": remote.instance_base,
                "sections": sections,
            },
        )
        return {
            "direction": "pull",
            "sections": sections,
            "remote": remote.instance_base,
            "paired_device": persisted_pair or pair_state,
            "ignored_local_workspace_ids": ignored_local_workspace_ids,
            "workspace_mode": workspace_mode,
            "effective_namespace": local_result.get("effective_namespace"),
            "item_selections": item_selections,
            "result": local_result,
        }
    except requests.RequestException as exc:
        _log_remote_sync_failure(
            f"sync_apply_{payload.direction}",
            remote_url=payload.remote_url,
            paired_device=payload.paired_device,
            context={
                "direction": payload.direction,
                "sections": sections,
                "item_selections": item_selections,
                "workspace_mode": payload.workspace_mode,
                "local_workspace_ids": payload.local_workspace_ids or [],
                "remote_workspace_ids": payload.remote_workspace_ids or [],
                "local_target_workspace_id": payload.local_target_workspace_id
                or "",
                "remote_target_workspace_id": payload.remote_target_workspace_id
                or "",
            },
            exc=exc,
        )
        raise HTTPException(status_code=502, detail=f"Remote sync failed: {exc}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---------------------------------------------------------------------------
# Legacy sync endpoints (cursor + changes + minimal blob up/download)


@router.get("/sync/cursor")
async def sync_cursor(request: Request):
    _require_scope(request, "sync")
    return {"cursor": sync_get_cursor(), "capabilities": {"blobs": True}}


class SyncChangesRequest(BaseModel):
    cursor: Optional[str] = None


@router.post("/sync/changes")
async def sync_changes(request: Request, payload: SyncChangesRequest):
    _require_scope(request, "sync")
    changes, next_cursor = sync_get_changes_since(payload.cursor or "0")
    return {"changes": changes, "next_cursor": next_cursor}


class SyncUploadPayload(BaseModel):
    # base64 or utf-8 text for minimal Phase 1; clients can send raw bytes via
    # /download later
    content: str


@router.post("/sync/upload")
async def sync_upload(request: Request, payload: SyncUploadPayload):
    _require_scope(request, "sync")
    data = payload.content.encode("utf-8")
    content_hash = put_blob(data)
    # record a change for clients to discover new blob
    sync_record_changes(
        [{"type": "blob", "content_hash": content_hash, "size": len(data)}]
    )
    return {"content_hash": content_hash}


@router.get("/sync/download/{content_hash}")
async def sync_download(request: Request, content_hash: str):
    _require_scope(request, "sync")
    if not blob_exists(content_hash):
        raise HTTPException(status_code=404, detail="Blob not found")
    data = get_blob(content_hash)
    return {
        "content": data.decode("utf-8", errors="ignore"),
        "content_hash": content_hash,
    }


# ---------------------------------------------------------------------------
# Streaming/signaling (server-mediated) minimal in-memory broker


class StreamSessionRequest(BaseModel):
    type: str  # "offer" or "answer"
    sdp: Optional[str] = None
    session_id: Optional[str] = None


@router.post("/stream/sessions")
async def stream_sessions(request: Request, payload: StreamSessionRequest):
    _require_scope(request, "stream")
    store: dict = request.app.state.stream_sessions
    if payload.type == "offer":
        sid = str(uuid4())
        store[sid] = {
            "offer": payload.sdp or "",
            "answer": None,
            "candidates": {"offer": [], "answer": []},
        }
        return {"session_id": sid}
    if payload.type == "answer":
        if not payload.session_id or payload.session_id not in store:
            raise HTTPException(status_code=404, detail="Session not found")
        store[payload.session_id]["answer"] = payload.sdp or ""
        return {"status": "ok"}
    raise HTTPException(
        status_code=400,
        detail="type must be 'offer' or 'answer'",
    )


@router.get("/stream/sessions/{session_id}")
async def stream_session_get(request: Request, session_id: str):
    _require_scope(request, "stream")
    store: dict = request.app.state.stream_sessions
    data = store.get(session_id)
    if not data:
        raise HTTPException(status_code=404, detail="Session not found")
    return data


class StreamCandidatesPayload(BaseModel):
    session_id: str
    role: str  # "offer" or "answer"
    candidates: List[Dict[str, Any]]


@router.post("/stream/candidates")
async def stream_candidates(
    request: Request,
    payload: StreamCandidatesPayload,
):
    _require_scope(request, "stream")
    store: dict = request.app.state.stream_sessions
    sess = store.get(payload.session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    if payload.role not in ("offer", "answer"):
        raise HTTPException(
            status_code=400,
            detail="role must be 'offer' or 'answer'",
        )
    sess["candidates"].setdefault(payload.role, [])
    sess["candidates"][payload.role].extend(payload.candidates)
    return {"status": "ok"}


@router.delete("/stream/sessions/{session_id}")
async def stream_session_delete(request: Request, session_id: str):
    _require_scope(request, "stream")
    store: dict = request.app.state.stream_sessions
    if session_id in store:
        del store[session_id]
    return {"status": "deleted"}


# User settings persistence


class UserSettingsPayload(BaseModel):
    history: List[str] = []
    approval_level: str = "all"
    theme: str = "light"
    action_history_retention_days: int = 7
    push_enabled: bool = False
    calendar_notify_minutes: int = 5
    tool_resolution_notifications: bool = True
    user_timezone: str = ""
    export_default_format: str = "md"
    export_default_include_chat: bool = True
    export_default_include_thoughts: bool = True
    export_default_include_tools: bool = True
    system_prompt_base: str = ""
    system_prompt_custom: str = ""
    conversation_folders: Dict[str, Dict[str, Any]] = {}
    tool_display_mode: str = "console"
    tool_link_behavior: str = "console"
    live_transcript_enabled: bool = True
    live_camera_default_enabled: bool = False
    device_display_name: str = ""
    sync_visible_on_lan: bool = False
    sync_visible_online: bool = False
    sync_online_url: str = ""
    sync_link_to_source_device: bool = False
    sync_remote_url: str = ""
    sync_source_namespace: str = ""
    sync_saved_peers: List[Dict[str, Any]] = []
    workspace_profiles: List[Dict[str, Any]] = []
    active_workspace_id: str = DEFAULT_WORKSPACE_ID
    sync_selected_workspace_ids: List[str] = [DEFAULT_WORKSPACE_ID]
    local_model_registrations: List[Dict[str, Any]] = []
    model_config = ConfigDict(extra="ignore")


class StatusResponse(BaseModel):
    status: str


class HistoryItem(BaseModel):
    role: Literal["user", "ai"]
    text: str


class HistoryPayload(BaseModel):
    session_id: str = Field(alias="sessionId")
    history: List[HistoryItem]
    model_config = ConfigDict(populate_by_name=True)


@router.get("/user-settings", response_model=UserSettingsPayload)
async def get_user_settings(request: Request):
    settings_payload = user_settings.load_settings()
    if not settings_payload.get("system_prompt_base"):
        settings_payload["system_prompt_base"] = request.app.state.config.get(
            "system_prompt",
            app_config.load_config().get("system_prompt", ""),
        )
    return UserSettingsPayload(**settings_payload)


@router.post("/user-settings", response_model=StatusResponse)
async def update_user_settings(payload: UserSettingsPayload):
    user_settings.save_settings(payload.model_dump(exclude_unset=True))
    return StatusResponse(status="saved")


@router.post("/history", response_model=StatusResponse)
async def save_history(payload: HistoryPayload):
    conversation_store.save_conversation(
        payload.session_id, [item.model_dump() for item in payload.history]
    )
    settings = user_settings.load_settings()
    history_ids = settings.get("history", [])
    if payload.session_id not in history_ids:
        history_ids.append(payload.session_id)
        user_settings.save_settings({"history": history_ids})
    try:
        log_history_save(payload.session_id, len(payload.history))
    except Exception:
        pass
    return StatusResponse(status="saved")


@router.get("/history/{session_id}", response_model=HistoryPayload)
async def get_history(session_id: str):
    history = conversation_store.load_conversation(session_id)
    return HistoryPayload(session_id=session_id, history=history)


# ---------------------------------------------------------------------------
# Chat log access (dev mode only)


def _parse_log_timestamp(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            ts = float(value)
        except (TypeError, ValueError):
            return None
        return ts / 1000.0 if ts > 1e12 else ts
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            ts = float(raw)
        except ValueError:
            ts = None
        if ts is not None:
            return ts / 1000.0 if ts > 1e12 else ts
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    return None


def _parse_log_bound(value: Optional[str], label: str) -> Optional[float]:
    if value is None:
        return None
    ts = _parse_log_timestamp(value)
    if ts is None:
        raise HTTPException(status_code=400, detail=f"Invalid {label} timestamp")
    return ts


@router.get("/logs/chat")
async def get_chat_log(
    limit: int = 100,
    request: Request = None,
    session_id: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
):
    """Return the last N chat log entries for debugging.

    Dev-only helper to inspect recent chat activity with optional filtering.
    Supports `session_id`, `since`, and `until` query params.
    """
    if request and not request.app.state.config.get("dev_mode"):
        raise HTTPException(status_code=404, detail="Dev mode disabled")
    try:
        from app.utils.chat_log import LOG_FILE

        if not LOG_FILE.exists():
            return {"entries": []}
        since_ts = _parse_log_bound(since, "since")
        until_ts = _parse_log_bound(until, "until")
        if since_ts is not None and until_ts is not None and since_ts > until_ts:
            raise HTTPException(status_code=400, detail="since must be <= until")

        max_entries = max(1, int(limit))
        filtered = deque(maxlen=max_entries)
        has_filter = bool(session_id) or since_ts is not None or until_ts is not None

        with LOG_FILE.open("r", encoding="utf-8") as handle:
            for line in handle:
                ln = line.strip()
                if not ln:
                    continue
                try:
                    entry = json.loads(ln)
                except Exception:
                    if not has_filter:
                        filtered.append({"raw": ln})
                    continue
                if session_id:
                    entry_session = entry.get("session_id")
                    if entry_session is None or str(entry_session) != session_id:
                        continue
                if since_ts is not None or until_ts is not None:
                    entry_ts = _parse_log_timestamp(entry.get("time"))
                    if entry_ts is None:
                        continue
                    if since_ts is not None and entry_ts < since_ts:
                        continue
                    if until_ts is not None and entry_ts > until_ts:
                        continue
                filtered.append(entry)
        return {"entries": list(filtered)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/logs/llm-server")
async def get_llm_server_log(
    limit: int = 200,
    request: Request = None,
    session_id: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    event: Optional[str] = None,
):
    """Return the last N llm_server log entries for debugging (dev mode only)."""
    if request and not request.app.state.config.get("dev_mode"):
        raise HTTPException(status_code=404, detail="Dev mode disabled")
    try:
        from app.utils.llm_server_log import LOG_FILE

        if not LOG_FILE.exists():
            return {"entries": []}
        since_ts = _parse_log_bound(since, "since")
        until_ts = _parse_log_bound(until, "until")
        if since_ts is not None and until_ts is not None and since_ts > until_ts:
            raise HTTPException(status_code=400, detail="since must be <= until")

        max_entries = max(1, int(limit))
        filtered = deque(maxlen=max_entries)
        has_filter = (
            bool(session_id)
            or bool(event)
            or since_ts is not None
            or until_ts is not None
        )

        with LOG_FILE.open("r", encoding="utf-8") as handle:
            for line in handle:
                ln = line.strip()
                if not ln:
                    continue
                try:
                    entry = json.loads(ln)
                except Exception:
                    if not has_filter:
                        filtered.append({"raw": ln})
                    continue
                if session_id:
                    entry_session = entry.get("session_id")
                    if entry_session is None or str(entry_session) != session_id:
                        continue
                if event:
                    entry_event = entry.get("event")
                    if entry_event is None or str(entry_event) != event:
                        continue
                if since_ts is not None or until_ts is not None:
                    entry_ts = _parse_log_timestamp(entry.get("time"))
                    if entry_ts is None:
                        continue
                    if since_ts is not None and entry_ts < since_ts:
                        continue
                    if until_ts is not None and entry_ts > until_ts:
                        continue
                filtered.append(entry)
        return {"entries": list(filtered)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Test prompt utilities (dev mode only)


@router.get("/test-prompts")
async def list_test_prompts(request: Request):
    if not request.app.state.config.get("dev_mode"):
        raise HTTPException(status_code=404, detail="Dev mode disabled")
    prompts = [p.stem for p in TEST_PROMPTS_DIR.glob("*.json")]
    return {"prompts": prompts}


@router.post("/test-prompts/{name}")
async def run_test_prompt(name: str, request: Request):
    if not request.app.state.config.get("dev_mode"):
        raise HTTPException(status_code=404, detail="Dev mode disabled")
    fp = TEST_PROMPTS_DIR / f"{name}.json"
    if not fp.exists():
        raise HTTPException(status_code=404, detail="Prompt not found")
    data = json.loads(fp.read_text())
    user_prompt = _last_user_message(data)
    response = llm_service.generate(user_prompt)
    return {"response": response["text"]}


# ---------------------------------------------------------------------------
# Extraction endpoint


class ExtractRequest(BaseModel):
    text: Optional[str] = None
    conversation_id: Optional[str] = None


@router.post("/extract")
async def extract(payload: ExtractRequest):
    if not payload.text and not payload.conversation_id:
        raise HTTPException(
            status_code=400,
            detail="Provide text or conversation_id",
        )

    if payload.text:
        summary = langextract_service.from_text(payload.text)
    else:
        messages = conversation_store.load_conversation(
            payload.conversation_id,
        )
        conv = [
            {
                "speaker": m.get("role", ""),
                "text": m.get("content", ""),
            }
            for m in messages
        ]
        summary = langextract_service.from_conversation(conv)

    return {"summary": summary}


# Knowledge base endpoints


class KnowledgeAdd(BaseModel):
    path: Optional[str] = None
    url: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class KnowledgeFolderIngest(BaseModel):
    path: Optional[str] = None
    recursive: bool = True
    limit: Optional[int] = None
    extensions: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None


class KnowledgeTextPayload(BaseModel):
    text: str
    source: Optional[str] = None
    kind: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class KnowledgeCleanupPayload(BaseModel):
    dry_run: bool = False
    set_relative_paths: bool = True
    mark_external_excluded: bool = True
    tag_derived_items: bool = True


def _resolve_data_files_path(path: Optional[str]) -> Path:
    files_dir = _resolve_data_files_root()
    if path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = files_dir / candidate
    else:
        candidate = files_dir / "workspace"
    try:
        resolved = candidate.resolve()
    except Exception:
        resolved = candidate
    try:
        resolved.relative_to(files_dir)
    except Exception:
        raise HTTPException(status_code=400, detail="path must be under data/files")
    return resolved


def _resolve_data_files_root() -> Path:
    cfg = app_config.load_config()
    data_dir = Path(cfg.get("data_dir") or app_config.DEFAULT_DATA_DIR)
    if not data_dir.is_absolute():
        data_dir = (app_config.REPO_ROOT / data_dir).resolve()
    else:
        try:
            data_dir = data_dir.resolve()
        except Exception:
            pass
    files_dir = (data_dir / "files").resolve()
    files_dir.mkdir(parents=True, exist_ok=True)
    for dirname in ("uploads", "screenshots", "downloaded", "workspace"):
        try:
            (files_dir / dirname).mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
    return files_dir


def _coerce_relative_files_path(value: str) -> str:
    """Return a normalized relative path under data/files, or empty string."""
    raw = str(value or "").strip().replace("\\", "/").lstrip("/")
    if not raw:
        return ""
    parts = [
        segment for segment in raw.split("/") if segment and segment not in {".", ".."}
    ]
    return "/".join(parts)


def _infer_relative_from_source(source: str, files_dir: Path) -> str:
    """Best-effort: normalize legacy/absolute source strings to data/files-relative paths."""
    source_text = str(source or "").strip()
    if "#chunk:" in source_text:
        source_text = source_text.split("#chunk:", 1)[0].strip()
    if not source_text:
        return ""
    lower = source_text.lower()
    if "://" in source_text or lower.startswith(
        ("memory:", "calendar_event:", "image:", "/api/")
    ):
        return ""

    candidate = Path(source_text).expanduser()
    if not candidate.is_absolute():
        candidate = files_dir / candidate
    try:
        resolved = candidate.resolve()
    except Exception:
        resolved = candidate
    try:
        return _coerce_relative_files_path(str(resolved.relative_to(files_dir)))
    except Exception:
        pass

    normalized = source_text.replace("\\", "/")
    patterns = [
        r"(?:^|/)data/files/(?P<rel>.+)$",
        r"(?:^|/)files/(?P<rel>.+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if not match:
            continue
        rel = _coerce_relative_files_path(match.group("rel"))
        if rel:
            return rel
    return ""


def _is_external_knowledge_source(metadata: Dict[str, Any]) -> bool:
    source = metadata.get("root_source") or metadata.get("source")
    if not isinstance(source, str) or not source.strip():
        return False
    source_text = source.strip()
    if "#chunk:" in source_text:
        source_text = source_text.split("#chunk:", 1)[0].strip()
    lower = source_text.lower()
    if "://" in source_text or lower.startswith(
        ("memory:", "calendar_event:", "image:", "/api/")
    ):
        return False
    rel = _infer_relative_from_source(source_text, _resolve_data_files_root())
    if rel:
        return False
    return Path(source_text).expanduser().is_absolute()


def _sanitize_knowledge_metadata_for_api(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Return metadata safe for UI/prompt use (no host-absolute source leakage)."""
    if not isinstance(metadata, dict):
        return {}
    cleaned = dict(metadata)
    files_dir = _resolve_data_files_root()

    rel = ""
    relative_path = cleaned.get("relative_path")
    if isinstance(relative_path, str) and relative_path.strip():
        rel = _coerce_relative_files_path(relative_path)
    if not rel:
        source = cleaned.get("source")
        if isinstance(source, str):
            rel = _infer_relative_from_source(source, files_dir)
    if rel:
        cleaned["relative_path"] = rel
        cleaned["source"] = rel
        if isinstance(cleaned.get("root_source"), str) and cleaned.get("root_source"):
            cleaned["root_source"] = rel
    elif _is_external_knowledge_source(cleaned):
        cleaned["source"] = "[external-path]"
        if isinstance(cleaned.get("root_source"), str) and cleaned.get("root_source"):
            cleaned["root_source"] = "[external-path]"
    return cleaned


def _sanitize_knowledge_source_for_api(
    source: Optional[str], metadata: Optional[Dict[str, Any]] = None
) -> str:
    candidate: Dict[str, Any] = {}
    if isinstance(metadata, dict):
        relative_path = metadata.get("relative_path")
        if isinstance(relative_path, str) and relative_path.strip():
            candidate["relative_path"] = relative_path
        meta_source = metadata.get("source")
        if isinstance(meta_source, str) and meta_source.strip():
            candidate["source"] = meta_source
    if isinstance(source, str) and source.strip():
        candidate["source"] = source
    sanitized = _sanitize_knowledge_metadata_for_api(candidate)
    sanitized_source = sanitized.get("source")
    if isinstance(sanitized_source, str) and sanitized_source.strip():
        return sanitized_source.strip()
    return str(source or "").strip()


def _resolve_knowledge_local_source(metadata: Dict[str, Any]) -> Path:
    files_dir = _resolve_data_files_root()
    candidates: List[Path] = []

    relative_path = metadata.get("relative_path")
    if isinstance(relative_path, str) and relative_path.strip():
        rel = _coerce_relative_files_path(relative_path)
        if rel:
            candidates.append(files_dir / rel)

    source = metadata.get("source")
    if isinstance(source, str):
        source_text = source.strip()
        rel = _infer_relative_from_source(source_text, files_dir)
        if rel:
            candidates.append(files_dir / rel)
        elif (
            source_text
            and "://" not in source_text
            and not source_text.startswith("/api/")
        ):
            source_path = Path(source_text).expanduser()
            if not source_path.is_absolute():
                source_path = files_dir / source_path
            candidates.append(source_path)

    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        try:
            resolved.relative_to(files_dir)
        except Exception:
            continue
        if resolved.exists():
            return resolved

    if candidates:
        raise HTTPException(
            status_code=400,
            detail="Local knowledge file path must exist under data/files",
        )
    raise HTTPException(
        status_code=400,
        detail="Knowledge item has no local file path in metadata",
    )


def _open_path_in_system_file_browser(target: Path) -> bool:
    folder = target if target.is_dir() else target.parent
    folder_str = os.path.normpath(str(folder))
    target_str = os.path.normpath(str(target))

    def _spawn_ok(command: Union[list[str], str], *, shell: bool = False) -> bool:
        try:
            proc = subprocess.Popen(
                command,
                shell=shell,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # Give explorer-style launchers a moment; non-zero immediate exit means failure.
            time.sleep(0.12)
            code = proc.poll()
            return code is None or code == 0
        except Exception:
            return False

    try:
        if sys.platform.startswith("linux"):
            subprocess.Popen(["xdg-open", str(folder)])
            return True
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
            return True
        if os.name == "nt":
            try:
                if target.is_file():
                    if _spawn_ok(["explorer.exe", "/select,", target_str]):
                        return True
                else:
                    if _spawn_ok(["explorer.exe", folder_str]):
                        return True
            except Exception:
                pass
            # Try default shell association (opens folder) before final command fallbacks.
            startfile = getattr(os, "startfile", None)
            if callable(startfile):
                try:
                    startfile(folder_str)
                    return True
                except Exception:
                    pass
            if _spawn_ok(["cmd.exe", "/c", "start", "", folder_str]):
                return True
            if _spawn_ok(f'explorer.exe "{folder_str}"', shell=True):
                return True
    except Exception:
        return False
    return False


def _path_to_file_uri(path_value: Path) -> str:
    try:
        resolved = path_value.resolve()
    except Exception:
        resolved = path_value
    normalized = resolved.as_posix()
    if os.name == "nt":
        if normalized.startswith("//"):
            return f"file:{quote(normalized, safe='/:')}"
        return f"file:///{quote(normalized, safe='/:')}"
    return f"file://{quote(normalized, safe='/:')}"


@router.post("/knowledge/add")
async def knowledge_add(payload: KnowledgeAdd):
    service = _get_rag_service()
    if payload.path:
        resolved_path = _resolve_data_files_path(payload.path)
        if not resolved_path.exists():
            raise HTTPException(
                status_code=404, detail="path not found under data/files"
            )
        if not resolved_path.is_file():
            raise HTTPException(
                status_code=400,
                detail="path must be a file under data/files (use ingest-folder for folders)",
            )
        files_dir = _resolve_data_files_root()
        relative_path = _coerce_relative_files_path(
            str(resolved_path.relative_to(files_dir))
        )
        meta = dict(payload.metadata or {})
        meta.setdefault("kind", "document")
        meta.setdefault("type", meta.get("kind"))
        meta.setdefault("filename", resolved_path.name)
        meta.setdefault("relative_path", relative_path)
        meta.setdefault("source", relative_path)
        suffix = resolved_path.suffix.lower()
        if suffix == ".pdf":
            doc_id = service.ingest_pdf(str(resolved_path), meta)
        elif suffix in {".md", ".markdown"}:
            doc_id = service.ingest_markdown(str(resolved_path), meta)
        else:
            doc_id = service.ingest_file(str(resolved_path), meta)
    elif payload.url:
        meta = dict(payload.metadata or {})
        meta.setdefault("kind", "document")
        meta.setdefault("type", meta.get("kind"))
        meta.setdefault("url", payload.url)
        meta.setdefault("source", payload.url)
        doc_id = service.ingest_url(payload.url, meta)
    else:
        raise HTTPException(status_code=400, detail="path or url required")
    return {"id": doc_id}


@router.post("/knowledge/ingest-folder")
async def knowledge_ingest_folder(payload: KnowledgeFolderIngest):
    """Ingest a folder of local files under data/files into the knowledge base."""
    service = _get_rag_service()
    files_dir = _resolve_data_files_root()
    target = _resolve_data_files_path(payload.path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="path not found")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="path must be a folder")

    default_exts = {".txt", ".md", ".markdown", ".json", ".csv", ".tsv", ".pdf"}
    if payload.extensions:
        allowed = {
            (ext if ext.startswith(".") else f".{ext}").lower()
            for ext in payload.extensions
            if ext
        }
    else:
        allowed = default_exts

    files = sorted(target.rglob("*")) if payload.recursive else sorted(target.glob("*"))
    ingested: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    limit = payload.limit or 0
    count = 0

    for path in files:
        if limit and count >= limit:
            break
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if allowed and suffix not in allowed:
            rel_path = _coerce_relative_files_path(str(path.relative_to(files_dir)))
            skipped.append({"path": rel_path or str(path), "reason": "extension"})
            continue
        try:
            size = path.stat().st_size
        except Exception:
            size = None
        if size is not None and size > MAX_UPLOAD_SIZE:
            rel_path = _coerce_relative_files_path(str(path.relative_to(files_dir)))
            skipped.append(
                {"path": rel_path or str(path), "reason": "too_large", "size": size}
            )
            continue
        meta = dict(payload.metadata or {})
        meta.setdefault("kind", "document")
        meta.setdefault("type", meta.get("kind"))
        meta.setdefault("filename", path.name)
        meta.setdefault("origin", "files")
        try:
            rel_to_files = _coerce_relative_files_path(str(path.relative_to(files_dir)))
            if rel_to_files:
                meta.setdefault("relative_path", rel_to_files)
                meta.setdefault("source", rel_to_files)
        except Exception:
            pass
        try:
            if suffix == ".pdf":
                doc_id = service.ingest_pdf(str(path), meta)
            elif suffix in {".md", ".markdown"}:
                doc_id = service.ingest_markdown(str(path), meta)
            else:
                doc_id = service.ingest_file(str(path), meta)
            rel_path = _coerce_relative_files_path(str(path.relative_to(files_dir)))
            ingested.append({"path": rel_path or str(path), "id": doc_id})
            count += 1
        except Exception as exc:
            rel_path = _coerce_relative_files_path(str(path.relative_to(files_dir)))
            errors.append({"path": rel_path or str(path), "error": str(exc)})

    return {
        "path": str(target),
        "ingested": ingested,
        "skipped": skipped,
        "errors": errors,
        "count": len(ingested),
    }


@router.post("/knowledge/upload")
async def knowledge_upload(file: UploadFile = UploadFileType(...)):
    """Upload a file and ingest it into the knowledge base."""
    if file.content_type not in ALLOWED_UPLOAD_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported file type")

    data = await file.read()
    if len(data) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=400, detail="File too large")

    ctype = (file.content_type or "").lower()
    if ctype.startswith("image/"):
        return _caption_and_index_image_bytes(
            data,
            filename=file.filename or "image",
            content_type=ctype,
        )

    service = _get_rag_service()
    files_dir = _resolve_data_files_root()
    workspace_dir = files_dir / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename or "document.txt").name
    stem = Path(safe_name).stem or "document"
    suffix = Path(safe_name).suffix
    target = workspace_dir / safe_name
    if target.exists():
        target = workspace_dir / f"{stem}-{int(time.time())}{suffix}"
    target.write_bytes(data)
    try:
        relative_path = _coerce_relative_files_path(str(target.relative_to(files_dir)))
        metadata = {
            "kind": "document",
            "type": "document",
            "source": relative_path or target.name,
            "relative_path": relative_path or target.name,
            "filename": target.name,
            "content_type": file.content_type,
        }
        if ctype == "application/pdf":
            doc_id = service.ingest_pdf(str(target), metadata)
        else:
            doc_id = service.ingest_file(str(target), metadata)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return {"id": doc_id}


@router.post("/knowledge/text")
async def knowledge_text(payload: KnowledgeTextPayload):
    """Ingest an arbitrary text snippet into the knowledge base."""
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    service = _get_rag_service()
    metadata = dict(payload.metadata or {})
    metadata.setdefault("kind", payload.kind or "note")
    if payload.source:
        metadata.setdefault("source", payload.source)
    doc_id = service.ingest_text(text, metadata)
    return {"id": doc_id}


_ALLOWED_VISION_WORKFLOWS = {"auto", "image_qa", "ocr", "compare", "caption"}


def _normalize_attachment_origin(value: Any, *, default: str = "upload") -> str:
    return normalize_asset_origin(str(value or ""), default=default)


def _infer_attachment_origin(metadata: Dict[str, Any]) -> str:
    if not isinstance(metadata, dict):
        return "upload"
    origin = _normalize_attachment_origin(metadata.get("origin"), default="")
    if origin:
        return origin
    rel_path = _coerce_relative_files_path(metadata.get("relative_path") or "")
    root = rel_path.split("/", 1)[0].lower() if rel_path else ""
    if root == "captured":
        return "captured"
    if root == "screenshots":
        return "screenshot"
    return "upload"


def _normalize_vision_workflow(value: Any, *, default: str = "auto") -> str:
    raw = str(value or "").strip().lower()
    if raw in _ALLOWED_VISION_WORKFLOWS:
        return raw
    return default


def _vision_workflow_instruction(workflow: str, image_count: int) -> str:
    mode = _normalize_vision_workflow(workflow)
    if mode == "ocr":
        return (
            "The user attached image content. Prioritize reading visible text exactly, "
            "call out uncertainty where text is unclear, and preserve important wording."
        )
    if mode == "compare":
        return (
            f"The user attached {image_count} images for comparison. Focus on meaningful "
            "differences and similarities, and note uncertainty instead of inventing details."
        )
    if mode == "caption":
        return (
            "The user wants a concise caption or description of the attached image content. "
            "Lead with a direct caption, then add a short supporting detail if helpful."
        )
    if mode == "image_qa":
        return (
            "The user attached images and is asking about them directly. Answer based on the "
            "visual content, and explicitly note uncertainty when details are unclear."
        )
    return ""


def _configured_vision_caption_model() -> str:
    cfg = app_config.load_config()
    configured = str(cfg.get("vision_model") or "").strip()
    lowered = configured.lower()
    if configured and "clip" not in lowered:
        return configured
    env_model = str(os.getenv("VISION_CAPTION_MODEL") or "").strip()
    if env_model:
        return env_model
    return "google/paligemma2-3b-pt-224"


def _attachment_status_defaults(
    metadata: Dict[str, Any],
    *,
    content_type: str,
) -> Dict[str, Any]:
    meta = dict(metadata) if isinstance(metadata, dict) else {}
    is_image = str(content_type or "").lower().startswith("image/")
    caption_status = str(meta.get("caption_status") or "").strip().lower()
    if not caption_status:
        if meta.get("caption"):
            if meta.get("caption_model") == "manual-caption":
                caption_status = "manual"
            elif bool(meta.get("placeholder_caption")):
                caption_status = "placeholder"
            else:
                caption_status = "generated"
        else:
            caption_status = "missing" if is_image else "not_applicable"
    index_status = str(meta.get("index_status") or "").strip().lower()
    if not index_status:
        if is_image and meta.get("caption"):
            index_status = "indexed"
        elif is_image:
            index_status = "missing"
        else:
            index_status = "not_applicable"
    return {
        "caption_status": caption_status,
        "index_status": index_status,
        "placeholder_caption": bool(meta.get("placeholder_caption")),
    }


def _iter_attachment_hashes() -> List[str]:
    hashes: set[str] = set()
    try:
        for entry in BLOBS_DIR.iterdir():
            if not entry.is_file():
                continue
            name = entry.name
            if name.lower() == "readme.md":
                continue
            if name.endswith(".json"):
                hashes.add(entry.stem)
                continue
            hashes.add(name)
    except FileNotFoundError:
        pass
    files_dir = _resolve_data_files_root()
    for dirname in ("uploads", "captured", "screenshots"):
        root = files_dir / dirname
        if not root.exists() or not root.is_dir():
            continue
        for child in root.iterdir():
            if child.is_dir():
                hashes.add(child.name)
    return sorted(hash for hash in hashes if hash)


def _caption_and_index_image_bytes(
    data: bytes,
    *,
    filename: str,
    content_type: str,
    url: str | None = None,
    content_hash: str | None = None,
    caption_override: str | None = None,
) -> Dict[str, Any]:
    """Caption + index an image into both text and CLIP knowledge stores.

    Persistence strategy:
    - Always store the caption text in the main (text) knowledge index so it is
      visible/auditable in the UI.
    - Best-effort: also store the image's CLIP embedding into a dedicated CLIP
      index (separate collection/class to avoid dimension conflicts).

    Note: CLIP vectors are not reversible; we keep the caption as the human/audit
    surface and for prompt injection.
    """
    service = _get_rag_service()
    safe_name = Path(filename or "image").name
    blob_hash = (content_hash or "").strip()
    if not blob_hash:
        blob_hash = put_blob(data)
    attachment_url = url or f"/api/attachments/{blob_hash}/{safe_name}"
    uploaded_at = (
        datetime.now(tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    try:
        existing_meta = _read_attachment_meta(blob_hash)
        merged_meta = dict(existing_meta)
        merged_meta.update(
            {
                "filename": safe_name,
                "content_type": content_type,
                "size": len(data),
                "uploaded_at": merged_meta.get("uploaded_at") or uploaded_at,
                "caption_status": "indexing",
                "index_status": "indexing",
            }
        )
        _write_attachment_meta(
            blob_hash,
            merged_meta,
        )
    except Exception:
        pass

    manual_caption = _sanitize_attachment_caption(caption_override or "")
    if not manual_caption:
        try:
            existing_caption_meta = _read_attachment_meta(blob_hash)
            if (
                str(existing_caption_meta.get("caption_status") or "").strip().lower()
                == "manual"
            ):
                manual_caption = _sanitize_attachment_caption(
                    existing_caption_meta.get("caption") or ""
                )
        except Exception:
            manual_caption = ""
    if manual_caption:
        caption = manual_caption
        placeholder = False
        caption_model = "manual-caption"
    else:
        caption, placeholder, caption_model = _generate_image_caption(
            data,
            content_hash=blob_hash,
        )

    source = f"image:{blob_hash}"
    caption_metadata = {
        "kind": "image_caption",
        "type": "image_caption",
        "source": source,
        "filename": safe_name,
        "content_type": content_type,
        "caption_model": caption_model,
        "placeholder": placeholder,
        "content_hash": blob_hash,
        "url": attachment_url,
    }
    doc_id = service.ingest_text(caption, caption_metadata)

    clip_service = _get_clip_rag_service(raise_http=False)
    clip_model = None
    if clip_service and isinstance(getattr(clip_service, "embedding_model", None), str):
        candidate = str(clip_service.embedding_model)
        if candidate.lower().startswith("clip:"):
            clip_model = candidate.split(":", 1)[1].strip() or None
    clip_model = clip_model or os.getenv("RAG_CLIP_MODEL", "ViT-B-32")
    clip_info: Dict[str, Any] = {
        "saved": False,
        "id": None,
        "dim": None,
        "model": clip_model,
        "error": None,
    }
    try:
        if clip_service:
            from app.services.clip_embeddings import embed_clip_image_bytes

            clip_embedding = embed_clip_image_bytes(
                data,
                model_name=str(clip_model or "ViT-B-32"),
            )
            clip_info["dim"] = len(clip_embedding)
            clip_metadata = dict(caption_metadata)
            clip_metadata.update(
                {
                    "kind": "image_embedding",
                    "type": "image_embedding",
                    "derived": True,
                    "embedding_model": f"clip:{clip_model}",
                    "caption_doc_id": doc_id,
                    "__embedding": clip_embedding,
                }
            )
            clip_doc_id = clip_service.ingest_text(caption, clip_metadata)
            clip_info["saved"] = True
            clip_info["id"] = clip_doc_id
        else:
            clip_info["error"] = "clip_index_unavailable"
    except Exception as exc:
        # Keep caption storage successful even when CLIP is unavailable.
        clip_info["error"] = str(exc)

    try:
        existing_meta = _read_attachment_meta(blob_hash)
        merged_meta = dict(existing_meta)
        merged_meta.update(
            {
                "filename": safe_name,
                "content_type": content_type,
                "size": len(data),
                "uploaded_at": merged_meta.get("uploaded_at") or uploaded_at,
                "caption": caption,
                "caption_model": caption_model,
                "placeholder_caption": placeholder,
                "caption_status": (
                    "manual"
                    if caption_model == "manual-caption"
                    else "placeholder"
                    if placeholder
                    else "generated"
                ),
                "index_status": "indexed",
                "indexed_at": uploaded_at,
            }
        )
        clip_error = clip_info.get("error")
        if isinstance(clip_error, str) and clip_error:
            merged_meta["index_warning"] = clip_error
        else:
            merged_meta.pop("index_warning", None)
        _write_attachment_meta(blob_hash, merged_meta)
    except Exception:
        logger.debug("Failed to update attachment index metadata", exc_info=True)

    return {
        "id": doc_id,
        "source": source,
        "url": attachment_url,
        "caption": caption,
        "caption_model": caption_model,
        "saved": True,
        "embedding_dim": clip_info.get("dim"),
        "placeholder": placeholder,
        "clip": clip_info,
    }


def _forget_attachment_knowledge(content_hash: str) -> None:
    source = f"image:{content_hash}"
    try:
        _get_rag_service().delete_source(source)
    except Exception:
        pass
    try:
        clip_service = _get_clip_rag_service(raise_http=False)
        if clip_service:
            clip_service.delete_source(source)
    except Exception:
        pass


def _reindex_attachment_caption(
    content_hash: str,
    *,
    caption_override: str | None = None,
) -> Optional[Dict[str, Any]]:
    normalized_hash = _normalize_attachment_hash(content_hash)
    metadata = _read_attachment_meta(normalized_hash)
    filename = (
        str(metadata.get("filename") or normalized_hash).strip() or normalized_hash
    )
    target = _resolve_attachment_target(normalized_hash, filename=filename)
    if not target or not target.exists() or not target.is_file():
        return None
    try:
        data = target.read_bytes()
    except Exception:
        return None
    content_type = (
        str(
            metadata.get("content_type")
            or mimetypes.guess_type(filename)[0]
            or "application/octet-stream"
        ).strip()
        or "application/octet-stream"
    )
    if not content_type.lower().startswith("image/"):
        return None
    return _caption_and_index_image_bytes(
        data,
        filename=filename,
        content_type=content_type,
        url=f"/api/attachments/{normalized_hash}/{filename}",
        content_hash=normalized_hash,
        caption_override=caption_override,
    )


def _generate_image_caption(
    data: bytes,
    *,
    content_hash: str | None = None,
) -> tuple[str, bool, str]:
    placeholder = False
    caption = ""
    caption_model = "vision-captioner"
    try:
        configured_model = _configured_vision_caption_model()
        try:
            captioner = VisionCaptioner(model=configured_model)
        except TypeError:
            # Test doubles and alternate captioner implementations may not
            # accept a `model=` kwarg; fall back to the default constructor.
            captioner = VisionCaptioner()
        caption_model = getattr(captioner, "model", caption_model)
        result = captioner.run(data)
        if isinstance(result, str):
            caption = result
            placeholder = is_placeholder_caption(caption)
        elif isinstance(result, dict):
            caption = str(result.get("image_caption") or "")
            placeholder = bool(
                result.get("placeholder", is_placeholder_caption(caption))
            )
    except Exception:
        caption = ""
        placeholder = False
    if not caption:
        key = (content_hash or hashlib.sha256(data).hexdigest())[:8]
        caption = placeholder_caption(key)
        placeholder = True
    return caption, placeholder, caption_model


@router.post("/knowledge/caption-image")
async def knowledge_caption_image(file: UploadFile = UploadFileType(...)):
    """Caption an image and store it in the knowledge base."""
    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="file is required")
    ctype = (file.content_type or "").lower()
    if ctype not in {
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "image/svg+xml",
    }:
        raise HTTPException(status_code=400, detail="Unsupported image type")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")

    return _caption_and_index_image_bytes(
        data,
        filename=file.filename,
        content_type=ctype,
    )


@router.post("/knowledge/caption-image-preview")
async def knowledge_caption_image_preview(file: UploadFile = UploadFileType(...)):
    """Generate an image caption without indexing it into knowledge."""
    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="file is required")
    ctype = (file.content_type or "").lower()
    if ctype not in {
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "image/svg+xml",
    }:
        raise HTTPException(status_code=400, detail="Unsupported image type")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    caption, placeholder, model = _generate_image_caption(data)
    return {
        "caption": caption,
        "placeholder": placeholder,
        "caption_model": model,
    }


@router.get("/knowledge/query")
async def knowledge_query(q: str, k: int = 5, mode: str = "text"):
    """Query the knowledge base.

    Modes:
    - text: query the main text index
    - clip: query the CLIP image index (returns caption docs with CLIP scores)
    - hybrid: prefer CLIP image matches, then fill with text matches
    """
    service = _get_rag_service()
    mode_norm = (mode or "text").strip().lower()
    warnings: list[str] = []

    matches_text: list[Dict[str, Any]] = []
    if mode_norm in {"text", "hybrid"}:
        matches_text = service.query(q, top_k=k) or []

    matches_clip: list[Dict[str, Any]] = []
    if mode_norm in {"clip", "hybrid"}:
        clip_service = _get_clip_rag_service(raise_http=False)
        if not clip_service:
            warnings.append("clip_index_unavailable")
        else:
            # If open_clip isn't installed, the clip service falls back to hash embeddings.
            if (
                str(getattr(clip_service, "embedding_model", ""))
                .lower()
                .startswith("clip:")
                and getattr(clip_service, "_embedding_encoder", None) is None
            ):
                warnings.append("clip_embeddings_unavailable")
            raw_clip = clip_service.query(q, top_k=k) or []
            for match in raw_clip:
                if not isinstance(match, dict):
                    continue
                meta = (
                    match.get("metadata")
                    if isinstance(match.get("metadata"), dict)
                    else {}
                )
                caption_id = meta.get("caption_doc_id") or match.get("id")
                trace = None
                if caption_id:
                    try:
                        trace = service.trace(str(caption_id))
                    except Exception:
                        trace = None
                trace_meta = (
                    trace.get("metadata")
                    if isinstance(trace, dict)
                    and isinstance(trace.get("metadata"), dict)
                    else {}
                )
                merged_meta = dict(trace_meta)
                for key in (
                    "source",
                    "filename",
                    "content_hash",
                    "content_type",
                    "url",
                ):
                    if key in meta and key not in merged_meta:
                        merged_meta[key] = meta[key]
                merged_meta["retrieved_via"] = "clip"
                matches_clip.append(
                    {
                        "id": str(caption_id or match.get("id") or ""),
                        "text": (trace.get("text") if isinstance(trace, dict) else None)
                        or match.get("text", ""),
                        "metadata": merged_meta,
                        "score": match.get("score"),
                    }
                )

    if mode_norm == "clip":
        matches = matches_clip
    elif mode_norm == "hybrid":
        seen: set[str] = set()
        merged: list[Dict[str, Any]] = []
        for match in matches_clip:
            match_id = str(match.get("id") or "")
            if match_id:
                seen.add(match_id)
            merged.append(match)
        for match in matches_text:
            if not isinstance(match, dict):
                continue
            match_id = str(match.get("id") or "")
            if match_id and match_id in seen:
                continue
            merged.append(match)
        matches = merged[: max(0, int(k))]
    else:
        matches = matches_text
    sanitized_matches: list[Dict[str, Any]] = []
    for match in matches:
        if not isinstance(match, dict):
            continue
        metadata = (
            match.get("metadata") if isinstance(match.get("metadata"), dict) else {}
        )
        safe_meta = _sanitize_knowledge_metadata_for_api(metadata)
        safe_source = _sanitize_knowledge_source_for_api(match.get("source"), safe_meta)
        payload = dict(match)
        payload["metadata"] = safe_meta
        if safe_source:
            payload["source"] = safe_source
        sanitized_matches.append(payload)
    return {
        "mode": mode_norm,
        "warnings": warnings,
        "matches": sanitized_matches,
        "ids": [m.get("id") for m in sanitized_matches if isinstance(m, dict)],
        "documents": [m.get("text") for m in sanitized_matches if isinstance(m, dict)],
        "metadatas": [
            m.get("metadata") for m in sanitized_matches if isinstance(m, dict)
        ],
        "scores": [m.get("score") for m in sanitized_matches if isinstance(m, dict)],
    }


@router.get("/knowledge/trace/{doc_id}")
async def knowledge_trace(doc_id: str):
    service = _get_rag_service()
    trace = service.trace(doc_id)
    if isinstance(trace, dict) and isinstance(trace.get("metadata"), dict):
        trace = dict(trace)
        trace["metadata"] = _sanitize_knowledge_metadata_for_api(trace["metadata"])
    return trace


class WeaviateImport(BaseModel):
    url: str
    class_name: str
    api_key: Optional[str] = None


@router.post("/knowledge/import/weaviate")
async def knowledge_import_weaviate(payload: WeaviateImport):
    service = _get_rag_service()
    try:
        ids = service.import_from_weaviate(
            payload.url,
            payload.class_name,
            payload.api_key,
        )
        return {"ids": ids}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Weaviate import failed: {e}")


@router.get("/weaviate/status")
async def weaviate_status(url: Optional[str] = None):
    """Return Weaviate readiness details when the backend is enabled."""
    cfg = app_config.load_config()
    backend_choice = cfg.get("rag_backend", "chroma").lower()
    if backend_choice != "weaviate":
        return {
            "backend": backend_choice,
            "reachable": False,
            "detail": "Weaviate disabled in current profile",
        }

    derived = (
        url
        or os.getenv("WEAVIATE_URL")
        or os.getenv("FLOAT_WEAVIATE_URL")
        or "http://localhost:8080"
    )
    reachable = False
    try:
        import requests

        def _looks_ready(u: str, timeout: float = 0.5) -> bool:
            try:
                r = requests.get(
                    u.rstrip("/") + "/v1/.well-known/ready", timeout=timeout
                )
                if r.status_code == 200:
                    return True
            except Exception:
                pass
            try:
                r = requests.get(u.rstrip("/") + "/v1/meta", timeout=timeout)
                return r.status_code == 200
            except Exception:
                return False

        reachable = _looks_ready(derived)
    except Exception:
        reachable = False
    return {"backend": backend_choice, "url": derived, "reachable": reachable}


class WeaviateStartRequest(BaseModel):
    url: Optional[str] = None
    wait_seconds: Optional[int] = 45


@router.post("/weaviate/start")
async def weaviate_start(req: WeaviateStartRequest):
    """Attempt to launch the Weaviate container via Docker Compose.

    Returns whether the endpoint is reachable after the attempt.
    """
    target = (
        req.url
        or os.getenv("WEAVIATE_URL")
        or os.getenv("FLOAT_WEAVIATE_URL")
        or "http://localhost:8080"
    )
    try:
        ok = autostart_weaviate(target, wait_seconds=int(req.wait_seconds or 45))
    except Exception:
        ok = False
    return {"url": target, "started": ok, "reachable": ok}


@router.get("/rag/status")
async def rag_status():
    """Return diagnostics for the configured RAG backend."""
    cfg = app_config.load_config()
    backend_choice = cfg.get("rag_backend", "chroma").lower()
    status: Dict[str, Any] = {
        "backend": backend_choice,
        "checked_at": datetime.now(tz=timezone.utc).isoformat(),
        "embedding_model": cfg.get("rag_embedding_model"),
        "aux_models": _get_aux_model_status(cfg),
    }

    if backend_choice == "chroma":
        persist_dir = Path(
            cfg.get("chroma_persist_dir", str(app_config.DEFAULT_CHROMA_DIR))
        )
        status["persist_dir"] = str(persist_dir)
        exists = persist_dir.exists()
        status["exists"] = exists
        status["writable"] = exists and os.access(persist_dir, os.W_OK)
        total_bytes: int | None = 0
        file_count: int | None = 0
        latest_mtime: float | None = None
        if exists:
            try:
                for entry in persist_dir.rglob("*"):
                    if entry.is_file():
                        file_count += 1
                        info = entry.stat()
                        total_bytes += info.st_size
                        if latest_mtime is None or info.st_mtime > latest_mtime:
                            latest_mtime = info.st_mtime
            except Exception:
                total_bytes = None
                file_count = None
        status["files"] = file_count
        status["size_bytes"] = total_bytes
        status["last_modified"] = (
            datetime.fromtimestamp(latest_mtime, tz=timezone.utc).isoformat()
            if latest_mtime is not None
            else None
        )
        try:
            docs = KnowledgeStore(cfg.get("memory_store_path")).list_items()
            status["documents"] = len(docs.get("ids", []))
        except Exception:
            status["documents"] = None
    elif backend_choice == "weaviate":
        status["url"] = (
            cfg.get("weaviate_url")
            or os.getenv("WEAVIATE_URL")
            or os.getenv("FLOAT_WEAVIATE_URL")
        )
        status["grpc_host"] = cfg.get("weaviate_grpc_host")
        status["grpc_port"] = cfg.get("weaviate_grpc_port")
        status["auto_start"] = bool(cfg.get("auto_start_weaviate"))
    else:
        status["detail"] = "In-memory fallback in use"

    try:
        celery_info = await celery_status()
        status["celery"] = {
            "online": bool(celery_info.get("online")),
            "workers": celery_info.get("workers", []),
            "timeout": bool(celery_info.get("timeout", False)),
            "details": celery_info.get("details", {}),
        }
    except Exception:
        status["celery"] = {"online": False, "error": "probe_failed"}

    return status


@router.get("/knowledge/list")
async def knowledge_list():
    service = _get_rag_service()
    payload = service.list_docs()
    metadatas = payload.get("metadatas")
    if isinstance(metadatas, list):
        payload["metadatas"] = [
            _sanitize_knowledge_metadata_for_api(meta if isinstance(meta, dict) else {})
            for meta in metadatas
        ]
    return payload


@router.post("/knowledge/cleanup")
async def knowledge_cleanup(payload: KnowledgeCleanupPayload):
    """Normalize legacy knowledge metadata and mark unsafe rows."""
    service = _get_rag_service()
    listing = service.list_docs() or {}
    ids = listing.get("ids") or []
    metadatas = listing.get("metadatas") or []
    documents = listing.get("documents") or []
    files_dir = _resolve_data_files_root()

    inspected = 0
    updated = 0
    normalized = 0
    excluded_external = 0
    tagged_derived = 0
    errors: List[Dict[str, str]] = []

    for idx, doc_id in enumerate(ids):
        if not doc_id:
            continue
        inspected += 1
        raw_meta = metadatas[idx] if idx < len(metadatas) else {}
        metadata = dict(raw_meta) if isinstance(raw_meta, dict) else {}
        next_meta = dict(metadata)
        changed = False

        if payload.set_relative_paths:
            existing_rel = _coerce_relative_files_path(
                next_meta.get("relative_path", "")
            )
            source_text = (
                next_meta.get("source")
                if isinstance(next_meta.get("source"), str)
                else str(next_meta.get("source") or "")
            )
            inferred_rel = _infer_relative_from_source(source_text, files_dir)
            rel = inferred_rel or existing_rel
            if rel:
                rel_changed = False
                if existing_rel != rel:
                    next_meta["relative_path"] = rel
                    rel_changed = True
                if str(next_meta.get("source") or "").strip() != rel:
                    next_meta["source"] = rel
                    rel_changed = True
                if rel_changed:
                    changed = True
                    normalized += 1

        if payload.tag_derived_items:
            kind = (
                str(next_meta.get("kind") or next_meta.get("type") or "")
                .strip()
                .lower()
            )
            if (
                kind in {"image_caption", "image_embedding"}
                and next_meta.get("derived") is not True
            ):
                next_meta["derived"] = True
                changed = True
                tagged_derived += 1

        if payload.mark_external_excluded:
            if _is_external_knowledge_source(next_meta) and not (
                next_meta.get("rag_excluded") or next_meta.get("excluded")
            ):
                next_meta["rag_excluded"] = True
                changed = True
                excluded_external += 1

        if not changed:
            continue
        if payload.dry_run:
            updated += 1
            continue
        try:
            text_val = documents[idx] if idx < len(documents) else ""
            if not isinstance(text_val, str):
                trace = service.trace(str(doc_id))
                if isinstance(trace, dict):
                    text_val = str(trace.get("text") or "")
                else:
                    text_val = ""
            service.update_doc(str(doc_id), text_val, next_meta)
            updated += 1
        except Exception as exc:
            errors.append({"id": str(doc_id), "error": str(exc)})

    return {
        "status": "ok",
        "dry_run": bool(payload.dry_run),
        "inspected": inspected,
        "updated": updated,
        "normalized": normalized,
        "excluded_external": excluded_external,
        "tagged_derived": tagged_derived,
        "errors": errors,
    }


class KnowledgeRagRehydrate(BaseModel):
    limit: Optional[int] = None
    dry_run: bool = False


@router.post("/knowledge/rag/rehydrate")
async def knowledge_rag_rehydrate(payload: KnowledgeRagRehydrate):
    """Refresh vector mirrors from canonical knowledge rows."""
    service = _get_rag_service()
    canonical_store = getattr(service, "canonical_store", None)
    if canonical_store is None:
        return {"scanned": 0, "reindexed": 0}
    listing = canonical_store.list_items() or {}
    ids = listing.get("ids") or []
    documents = listing.get("documents") or []
    metadatas = listing.get("metadatas") or []
    max_items = None
    if payload.limit is not None:
        try:
            max_items = max(0, int(payload.limit))
        except Exception:
            max_items = None
    scanned = 0
    updated = 0
    for idx, raw_id in enumerate(ids):
        if max_items is not None and scanned >= max_items:
            break
        text = str(documents[idx] if idx < len(documents) else "")
        if not text.strip():
            continue
        raw_meta = metadatas[idx] if idx < len(metadatas) else {}
        metadata = dict(raw_meta) if isinstance(raw_meta, dict) else {}
        source = str(metadata.get("source") or "").strip()
        if not source:
            continue
        scanned += 1
        if payload.dry_run:
            continue
        try:
            if service.rehydrate_canonical_document(
                text,
                metadata,
                knowledge_id=str(raw_id or metadata.get("knowledge_id") or source),
            ):
                updated += 1
        except Exception:
            pass
    return {"scanned": scanned, "reindexed": updated}


class KnowledgeUpdate(BaseModel):
    text: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


@router.put("/knowledge/{doc_id}")
async def knowledge_update(doc_id: str, payload: KnowledgeUpdate):
    service = _get_rag_service()
    merged: Dict[str, Any] = {}
    existing_text: str = ""
    existing: Optional[Dict[str, Any]] = None
    try:
        trace = service.trace(doc_id)
        if isinstance(trace, dict):
            existing = trace
    except Exception:
        existing = None
    if existing is None and payload.text is None:
        raise HTTPException(status_code=404, detail="not found")
    if existing is not None and isinstance(existing.get("metadata"), dict):
        merged.update(existing["metadata"])
    if existing is not None and isinstance(existing.get("text"), str):
        existing_text = existing.get("text") or ""
    if payload.metadata:
        merged.update(payload.metadata)
    merged.setdefault("updated_at", time.time())
    next_text = payload.text if payload.text is not None else existing_text
    service.update_doc(doc_id, next_text, merged)
    return {"status": "updated"}


@router.get("/knowledge/{doc_id}")
async def knowledge_get(doc_id: str):
    service = _get_rag_service()
    trace = service.trace(doc_id)
    if not trace:
        raise HTTPException(status_code=404, detail="not found")
    metadata = trace.get("metadata") if isinstance(trace, dict) else {}
    text = trace.get("text") if isinstance(trace, dict) else ""
    safe_meta = _sanitize_knowledge_metadata_for_api(
        metadata if isinstance(metadata, dict) else {}
    )
    return {"ids": [doc_id], "documents": [text], "metadatas": [safe_meta]}


@router.get("/knowledge/reveal/{doc_id}")
async def knowledge_reveal(doc_id: str):
    """Reveal a local knowledge source file (if it resolves under data/files)."""
    service = _get_rag_service()
    trace = service.trace(doc_id)
    if not trace:
        raise HTTPException(status_code=404, detail="not found")
    metadata = trace.get("metadata") if isinstance(trace, dict) else {}
    if not isinstance(metadata, dict):
        raise HTTPException(status_code=400, detail="Knowledge metadata is unavailable")

    target = _resolve_knowledge_local_source(metadata)
    opened = _open_path_in_system_file_browser(target)
    folder = target if target.is_dir() else target.parent
    return {
        "path": str(target),
        "folder": str(folder),
        "open_uri": _path_to_file_uri(folder),
        "opened": opened,
    }


@router.get("/knowledge/file/{doc_id}")
async def knowledge_file(doc_id: str):
    """Serve a local knowledge file when it resolves under data/files."""
    service = _get_rag_service()
    trace = service.trace(doc_id)
    if not trace:
        raise HTTPException(status_code=404, detail="not found")
    metadata = trace.get("metadata") if isinstance(trace, dict) else {}
    if not isinstance(metadata, dict):
        raise HTTPException(status_code=400, detail="Knowledge metadata is unavailable")

    target = _resolve_knowledge_local_source(metadata)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Knowledge file not found")

    explicit_type = metadata.get("content_type")
    guessed_type, _ = mimetypes.guess_type(target.name)
    media_type = (
        explicit_type
        if isinstance(explicit_type, str) and explicit_type.strip()
        else guessed_type or "application/octet-stream"
    )
    return FileResponse(path=str(target), media_type=media_type)


@router.delete("/knowledge/{doc_id}")
async def knowledge_delete(doc_id: str):
    service = _get_rag_service()
    service.delete_doc(doc_id)
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Attachments (chat file uploads)


def _attachment_meta_path(content_hash: str) -> Path:
    return BLOBS_DIR / f"{content_hash}.json"


def _read_attachment_meta(content_hash: str) -> Dict[str, Any]:
    meta_path = _attachment_meta_path(content_hash)
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning(
            "Failed to read attachment metadata for %s", content_hash, exc_info=True
        )
        return {}


def _write_attachment_meta(content_hash: str, metadata: Dict[str, Any]) -> None:
    meta_path = _attachment_meta_path(content_hash)
    try:
        meta_path.write_text(json.dumps(metadata), encoding="utf-8")
    except Exception:
        logger.warning(
            "Failed to write attachment metadata for %s", content_hash, exc_info=True
        )


def _normalize_attachment_hash(value: str) -> str:
    return str(value or "").strip().lower()


def _resolve_legacy_attachment_path(filename: Optional[str]) -> Optional[Path]:
    name = Path(str(filename or "")).name.strip()
    if not name:
        return None
    files_dir = _resolve_data_files_root()
    for relative_dir in (
        "uploads",
        "captured",
        "screenshots",
        "workspace",
        "downloaded",
    ):
        candidate = (files_dir / relative_dir / name).resolve()
        try:
            candidate.relative_to(files_dir)
        except Exception:
            continue
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _resolve_attachment_target(
    content_hash: str,
    *,
    filename: Optional[str] = None,
) -> Optional[Path]:
    normalized_hash = _normalize_attachment_hash(content_hash)
    metadata = _read_attachment_meta(normalized_hash) if normalized_hash else {}
    files_dir = _resolve_data_files_root()
    if isinstance(metadata, dict):
        rel_candidate = str(
            metadata.get("relative_path") or metadata.get("source_path") or ""
        ).strip()
        rel_path = _coerce_relative_files_path(rel_candidate)
        if rel_path:
            target = (files_dir / rel_path).resolve()
            try:
                target.relative_to(files_dir)
            except Exception:
                target = None
            if target and target.exists() and target.is_file():
                return target

        abs_candidate = str(
            metadata.get("path") or metadata.get("source_path") or ""
        ).strip()
        if abs_candidate:
            target = Path(abs_candidate).expanduser()
            if not target.is_absolute():
                target = (files_dir / target).resolve()
            try:
                target.relative_to(files_dir)
            except Exception:
                target = None
            if target and target.exists() and target.is_file():
                return target

    candidates = [filename]
    if isinstance(metadata, dict):
        meta_filename = metadata.get("filename")
        if isinstance(meta_filename, str):
            candidates.append(meta_filename)
    for candidate_name in candidates:
        if not normalized_hash:
            break
        target = find_asset_path(normalized_hash, filename=candidate_name)
        if target and target.exists() and target.is_file():
            return target
    for candidate_name in candidates:
        fallback = _resolve_legacy_attachment_path(candidate_name)
        if fallback:
            return fallback
    if normalized_hash:
        direct = (BLOBS_DIR / normalized_hash).resolve()
        try:
            direct.relative_to(BLOBS_DIR)
        except Exception:
            direct = None
        if direct and direct.exists() and direct.is_file():
            return direct
    return None


class AttachmentInfo(BaseModel):
    content_hash: str
    filename: str
    content_type: str
    size: int
    url: str


def _index_uploaded_attachment(
    data: bytes,
    *,
    filename: str,
    content_type: str,
    url: str,
    content_hash: str,
) -> None:
    """Best-effort: index uploaded attachments into knowledge for retrieval."""
    try:
        if (content_type or "").lower().startswith("image/"):
            _caption_and_index_image_bytes(
                data,
                filename=filename,
                content_type=(content_type or "").lower(),
                url=url,
                content_hash=content_hash,
            )
    except Exception:
        try:
            metadata = _read_attachment_meta(content_hash)
            metadata["index_status"] = "error"
            if not str(metadata.get("caption_status") or "").strip():
                metadata["caption_status"] = "error"
            _write_attachment_meta(content_hash, metadata)
        except Exception:
            pass
        logger.debug("Attachment knowledge indexing failed", exc_info=True)


@router.post("/attachments/upload")
async def attachments_upload(
    background_tasks: BackgroundTasks,
    file: UploadFile = UploadFileType(...),
    origin: str = Form(default="upload"),
    capture_source: Optional[str] = Form(default=None),
):
    """Upload a file to blob storage and return a stable URL.

    Stores as a content-addressed file under `data/files/*/<content_hash>/`
    while preserving the stable `/attachments/{content_hash}/{filename}` URL.

    Best-effort: image uploads are captioned and indexed (text + CLIP) so they
    become retrievable via the RAG system.
    """
    if file.content_type not in ALLOWED_UPLOAD_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported file type")
    data = await file.read()
    if len(data) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=400, detail="File too large")

    normalized_origin = _normalize_attachment_origin(origin)
    # Sanitize/normalize filename for URL; keep original extension
    filename = Path(file.filename or "file").name
    asset_info = put_asset(
        data,
        filename=filename,
        origin=normalized_origin,
    )
    h = asset_info["content_hash"]
    url = f"/api/attachments/{h}/{filename}"
    uploaded_at = (
        datetime.now(tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    _write_attachment_meta(
        h,
        {
            "filename": filename,
            "content_type": file.content_type,
            "size": len(data),
            "uploaded_at": uploaded_at,
            "origin": normalized_origin,
            "relative_path": asset_info.get("relative_path"),
            "path": asset_info.get("path"),
            "capture_source": (
                str(capture_source).strip() if capture_source is not None else None
            ),
            **(
                {"caption_status": "pending", "index_status": "indexing"}
                if str(file.content_type or "").lower().startswith("image/")
                else {
                    "caption_status": "not_applicable",
                    "index_status": "not_applicable",
                }
            ),
        },
    )
    background_tasks.add_task(
        _index_uploaded_attachment,
        data,
        filename=filename,
        content_type=str(file.content_type or ""),
        url=url,
        content_hash=h,
    )
    return {
        "content_hash": h,
        "filename": filename,
        "content_type": file.content_type,
        "size": len(data),
        "url": url,
        "uploaded_at": uploaded_at,
        "origin": normalized_origin,
        "relative_path": asset_info.get("relative_path"),
    }


@router.get("/attachments")
async def attachments_list():
    entries: list[Dict[str, Any]] = []
    for name in _iter_attachment_hashes():
        meta = _read_attachment_meta(name)
        filename = str(meta.get("filename") or "").strip() or name
        target = _resolve_attachment_target(name, filename=filename)
        if not target or not target.exists() or not target.is_file():
            continue
        descriptor = build_attachment_media_descriptor(
            name,
            target,
            metadata=meta,
            preferred_filename=filename,
        )
        if descriptor["metadata_changed"]:
            _write_attachment_meta(name, descriptor["metadata"])
            meta = descriptor["metadata"]
        filename = str(descriptor["filename"] or "").strip() or filename
        stat = target.stat()
        size = (
            descriptor["size"] if isinstance(descriptor["size"], int) else stat.st_size
        )
        content_type = descriptor["content_type"] or mimetypes.guess_type(filename)[0]
        uploaded_at = meta.get("uploaded_at")
        if not uploaded_at:
            uploaded_at = (
                datetime.utcfromtimestamp(stat.st_mtime)
                .replace(microsecond=0)
                .isoformat()
                + "Z"
            )
        try:
            sort_value = datetime.fromisoformat(
                uploaded_at.replace("Z", "+00:00")
            ).timestamp()
        except Exception:
            sort_value = stat.st_mtime
        entries.append(
            {
                "content_hash": name,
                "filename": filename,
                "content_type": content_type,
                "size": size,
                "uploaded_at": uploaded_at,
                "url": f"/api/attachments/{name}/{filename}",
                "origin": _infer_attachment_origin(meta),
                "relative_path": meta.get("relative_path") or "",
                "source_sync_label": str(meta.get("source_sync_label") or "").strip(),
                "source_sync_namespace": str(
                    meta.get("source_sync_namespace") or ""
                ).strip(),
                "capture_source": meta.get("capture_source") or "",
                "caption": _sanitize_attachment_caption(meta.get("caption") or ""),
                "caption_model": str(meta.get("caption_model") or "").strip(),
                "index_warning": str(meta.get("index_warning") or "").strip(),
                **_attachment_status_defaults(
                    meta,
                    content_type=str(content_type or ""),
                ),
                "_sort": sort_value,
            }
        )
    entries.sort(key=lambda item: (item["_sort"], item["content_hash"]), reverse=True)
    for item in entries:
        item.pop("_sort", None)
    return {"attachments": entries}


class AttachmentsRagRehydrate(BaseModel):
    limit: Optional[int] = None
    dry_run: bool = False


@router.post("/attachments/rag/rehydrate")
async def attachments_rag_rehydrate(payload: AttachmentsRagRehydrate):
    """Synchronously (re)index stored attachments into the knowledge base."""
    max_items = None
    if payload.limit is not None:
        try:
            max_items = max(0, int(payload.limit))
        except Exception:
            max_items = None
    scanned = 0
    updated = 0
    for name in _iter_attachment_hashes():
        meta = _read_attachment_meta(name)
        filename = meta.get("filename") or name
        target = _resolve_attachment_target(name, filename=filename)
        if not target or not target.exists() or not target.is_file():
            continue
        descriptor = build_attachment_media_descriptor(
            name,
            target,
            metadata=meta,
            preferred_filename=str(filename or ""),
        )
        if descriptor["metadata_changed"]:
            _write_attachment_meta(name, descriptor["metadata"])
            meta = descriptor["metadata"]
        filename = descriptor["filename"] or filename
        content_type = descriptor["content_type"] or ""
        if not str(content_type).lower().startswith("image/"):
            continue
        scanned += 1
        if max_items is not None and scanned > max_items:
            break
        if payload.dry_run:
            continue
        try:
            data = target.read_bytes()
        except Exception:
            continue
        url = f"/api/attachments/{name}/{filename}"
        try:
            _caption_and_index_image_bytes(
                data,
                filename=filename,
                content_type=str(content_type).lower(),
                url=url,
                content_hash=name,
            )
            updated += 1
        except Exception:
            pass
    return {"scanned": scanned, "reindexed": updated}


class AttachmentCaptionPayload(BaseModel):
    caption: str


def _sanitize_attachment_caption(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text[:2000]


@router.get("/attachments/caption/{content_hash}")
async def attachment_caption_get(content_hash: str):
    normalized_hash = _normalize_attachment_hash(content_hash)
    if not _resolve_attachment_target(normalized_hash):
        raise HTTPException(status_code=404, detail="Attachment not found")
    metadata = _read_attachment_meta(normalized_hash)
    caption = _sanitize_attachment_caption(metadata.get("caption") or "")
    return {
        "content_hash": normalized_hash,
        "caption": caption,
        "exists": bool(caption),
    }


@router.put("/attachments/caption/{content_hash}")
async def attachment_caption_put(content_hash: str, payload: AttachmentCaptionPayload):
    normalized_hash = _normalize_attachment_hash(content_hash)
    if not _resolve_attachment_target(normalized_hash):
        raise HTTPException(status_code=404, detail="Attachment not found")
    caption = _sanitize_attachment_caption(payload.caption)
    if not caption:
        raise HTTPException(status_code=400, detail="caption is required")
    metadata = _read_attachment_meta(normalized_hash)
    metadata["caption"] = caption
    metadata["caption_updated_at"] = (
        datetime.now(tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    metadata["caption_status"] = "manual"
    metadata["index_status"] = "indexing"
    metadata["placeholder_caption"] = False
    _write_attachment_meta(normalized_hash, metadata)
    try:
        _reindex_attachment_caption(normalized_hash, caption_override=caption)
    except Exception:
        metadata["index_status"] = "error"
        _write_attachment_meta(normalized_hash, metadata)
        logger.warning(
            "Failed to reindex attachment caption for %s",
            normalized_hash,
            exc_info=True,
        )
    return {"status": "saved", "content_hash": normalized_hash, "caption": caption}


@router.delete("/attachments/caption/{content_hash}")
async def attachment_caption_delete(content_hash: str):
    normalized_hash = _normalize_attachment_hash(content_hash)
    if not _resolve_attachment_target(normalized_hash):
        raise HTTPException(status_code=404, detail="Attachment not found")
    metadata = _read_attachment_meta(normalized_hash)
    had_caption = bool(_sanitize_attachment_caption(metadata.get("caption") or ""))
    metadata.pop("caption", None)
    metadata.pop("caption_updated_at", None)
    metadata["caption_status"] = "pending"
    metadata["index_status"] = "indexing"
    metadata["placeholder_caption"] = False
    _write_attachment_meta(normalized_hash, metadata)
    try:
        _reindex_attachment_caption(normalized_hash)
    except Exception:
        metadata["index_status"] = "error"
        metadata["caption_status"] = "error"
        _write_attachment_meta(normalized_hash, metadata)
        logger.warning(
            "Failed to refresh attachment caption for %s",
            normalized_hash,
            exc_info=True,
        )
    return {
        "status": "deleted",
        "content_hash": normalized_hash,
        "deleted": had_caption,
    }


@router.get("/attachments/reveal/{content_hash}")
async def attachments_reveal(
    content_hash: str,
    filename: Optional[str] = Query(default=None),
):
    """Reveal the stored blob path and best-effort open its folder."""
    target = _resolve_attachment_target(content_hash, filename=filename)
    if not target:
        raise HTTPException(status_code=404, detail="Attachment not found")
    opened = _open_path_in_system_file_browser(target)
    folder = target if target.is_dir() else target.parent
    return {
        "path": str(target),
        "folder": str(folder),
        "open_uri": _path_to_file_uri(folder),
        "opened": opened,
    }


@router.get("/attachments/{content_hash}/{filename}")
async def attachments_get(content_hash: str, filename: str):
    """Serve an attachment by content hash with a best-effort content type.

    Falls back to legacy uploads path resolution by filename when the blob hash
    file is missing (for older migrated records).
    """
    target = _resolve_attachment_target(content_hash, filename=filename)
    if not target:
        raise HTTPException(status_code=404, detail="Attachment not found")
    metadata = _read_attachment_meta(_normalize_attachment_hash(content_hash))
    descriptor = build_attachment_media_descriptor(
        _normalize_attachment_hash(content_hash),
        target,
        metadata=metadata,
        preferred_filename=filename,
    )
    if descriptor["metadata_changed"]:
        _write_attachment_meta(
            _normalize_attachment_hash(content_hash), descriptor["metadata"]
        )
    media_type = descriptor["content_type"] or "application/octet-stream"
    return FileResponse(path=str(target), media_type=media_type)


@router.delete("/attachments/{content_hash}")
async def attachments_delete(content_hash: str):
    """Delete an attachment blob by content hash."""
    normalized_hash = _normalize_attachment_hash(content_hash)
    _forget_attachment_knowledge(normalized_hash)
    ok = blob_delete(normalized_hash)
    try:
        _attachment_meta_path(normalized_hash).unlink(missing_ok=True)
    except Exception:
        pass
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to delete blob")
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Threads (semantic tags) generation and summary


class ThreadsGeneratePayload(BaseModel):
    infer_topics: bool = True
    tags: list[str] | None = None
    openai_key: str | None = None
    k_option: int | None = None
    preferred_k: int | None = 16
    max_k: int | None = 30
    cluster_backend: str | None = None
    cluster_device: str | None = None
    manual_threads: list[str] | None = None
    top_n: int | None = None
    coalesce_related: bool = True
    scope_folder: str | None = None
    scope_thread: str | None = None
    thread_signal_mode: str | None = None
    thread_signal_blend: float | None = None
    sae_model_combo: str | None = None
    sae_embeddings_fallback: bool | None = None
    sae_live_inspect_console: bool | None = None
    sae_options: dict[str, Any] | None = None


@router.post("/threads/generate")
async def threads_generate(payload: ThreadsGeneratePayload = Body(...)):
    try:
        summary = threads_service.generate_threads(
            infer_topics=payload.infer_topics,
            tags=payload.tags,
            openai_key=payload.openai_key,
            k_option=payload.k_option,
            preferred_k=payload.preferred_k,
            max_k=payload.max_k,
            cluster_backend=payload.cluster_backend,
            cluster_device=payload.cluster_device,
            manual_threads=payload.manual_threads,
            top_n=payload.top_n,
            coalesce_related=payload.coalesce_related,
            scope_folder=payload.scope_folder,
            scope_thread=payload.scope_thread,
            thread_signal_mode=payload.thread_signal_mode,
            thread_signal_blend=payload.thread_signal_blend,
            sae_model_combo=payload.sae_model_combo,
            sae_embeddings_fallback=payload.sae_embeddings_fallback,
            sae_live_inspect_console=payload.sae_live_inspect_console,
            sae_options=payload.sae_options,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"summary": summary}


@router.get("/threads/summary")
async def threads_summary():
    summary = threads_service.read_summary()
    return {"summary": summary}


class ThreadsSearchPayload(BaseModel):
    query: str
    top_k: int = 10


@router.post("/threads/search")
async def threads_search(payload: ThreadsSearchPayload = Body(...)):
    return threads_service.search_threads(payload.query, payload.top_k)


class ThreadRenamePayload(BaseModel):
    old_name: str
    new_name: str


@router.post("/threads/rename")
async def threads_rename(payload: ThreadRenamePayload = Body(...)):
    old = (payload.old_name or "").strip()
    new = (payload.new_name or "").strip()
    if not old or not new:
        raise HTTPException(status_code=400, detail="Thread names cannot be empty")
    try:
        summary = threads_service.rename_thread(old, new)
    except KeyError:
        raise HTTPException(status_code=404, detail="Thread not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"summary": summary, "renamed": {"from": old, "to": new}}


# Settings Endpoints
def _mask_secret(value: str, *, keep_start: int = 3, keep_end: int = 4) -> str:
    if not isinstance(value, str):
        return ""
    raw = value.strip()
    if not raw:
        return ""
    if len(raw) <= keep_start + keep_end:
        return "*" * len(raw)
    return f"{raw[:keep_start]}...{raw[-keep_end:]}"


def _redact_settings(cfg: dict[str, Any] | None) -> dict[str, Any]:
    """Return a response-safe copy of settings (never echo secrets)."""

    redacted = dict(cfg or {})
    api_key = str(redacted.get("api_key") or "").strip()
    redacted["api_key_set"] = bool(api_key)
    redacted["api_key_preview"] = _mask_secret(api_key) if api_key else ""
    redacted["api_key"] = ""

    hf_token = str(redacted.get("hf_token") or "").strip()
    if not hf_token:
        try:
            hf_token = str(
                os.getenv("HUGGINGFACE_HUB_TOKEN") or os.getenv("HF_TOKEN") or ""
            ).strip()
        except Exception:
            hf_token = ""
    redacted["hf_token_set"] = bool(hf_token)
    redacted["hf_token_preview"] = _mask_secret(hf_token) if hf_token else ""
    redacted["hf_token"] = ""

    mcp_token = str(redacted.get("mcp_token") or "").strip()
    redacted["mcp_token_set"] = bool(mcp_token)
    redacted["mcp_token_preview"] = _mask_secret(mcp_token) if mcp_token else ""
    redacted["mcp_token"] = ""

    provider_token = str(redacted.get("local_provider_api_token") or "").strip()
    redacted["local_provider_api_token_set"] = bool(provider_token)
    redacted["local_provider_api_token_preview"] = (
        _mask_secret(provider_token) if provider_token else ""
    )
    redacted["local_provider_api_token"] = ""

    return redacted


class SettingsRequest(BaseModel):
    mode: Optional[str] = None
    api_key: Optional[str] = None
    hf_token: Optional[str] = None
    api_url: Optional[str] = None
    local_url: Optional[str] = None
    openai_model: Optional[str] = None
    dynamic_model: Optional[str] = None
    dynamic_port: Optional[int] = None
    conv_folder: Optional[str] = None
    models_folder: Optional[str] = None
    inference_device: Optional[str] = None
    mcp_url: Optional[str] = None
    mcp_token: Optional[str] = None
    transformer_model: Optional[str] = None
    local_provider: Optional[str] = None
    local_provider_mode: Optional[str] = None
    local_provider_base_url: Optional[str] = None
    local_provider_host: Optional[str] = None
    local_provider_port: Optional[int] = None
    lmstudio_path: Optional[str] = None
    local_provider_api_token: Optional[str] = None
    local_provider_auto_start: Optional[bool] = None
    local_provider_preferred_model: Optional[str] = None
    local_provider_default_context_length: Optional[int] = None
    local_provider_show_server_logs: Optional[bool] = None
    local_provider_enable_cors: Optional[bool] = None
    local_provider_allow_lan: Optional[bool] = None
    static_model: Optional[str] = None
    harmony_format: Optional[bool] = None
    server_url: Optional[str] = None
    stt_model: Optional[str] = None
    tts_model: Optional[str] = None
    voice_model: Optional[str] = None
    stream_backend: Optional[str] = None
    realtime_model: Optional[str] = None
    realtime_voice: Optional[str] = None
    realtime_base_url: Optional[str] = None
    realtime_connect_url: Optional[str] = None
    vision_model: Optional[str] = None
    dev_mode: Optional[bool] = None
    max_context_length: Optional[int] = None
    kv_cache: Optional[bool] = None
    ram_swap: Optional[bool] = None
    device_map_strategy: Optional[str] = None
    gpu_memory_fraction: Optional[float] = None
    gpu_memory_margin_mb: Optional[int] = None
    gpu_memory_limit_gb: Optional[float] = None
    cpu_offload_fraction: Optional[float] = None
    cpu_offload_limit_gb: Optional[float] = None
    flash_attention: Optional[bool] = None
    attention_implementation: Optional[str] = None
    kv_cache_implementation: Optional[str] = None
    kv_cache_quant_backend: Optional[str] = None
    kv_cache_dtype: Optional[str] = None
    kv_cache_device: Optional[str] = None
    model_dtype: Optional[str] = None
    cpu_thread_count: Optional[int] = None
    request_timeout: Optional[float] = None
    stream_idle_timeout: Optional[float] = None
    # Weaviate (RAG) settings
    weaviate_url: Optional[str] = None
    weaviate_auto_start: Optional[bool] = None
    rag_embedding_model: Optional[str] = None
    rag_clip_model: Optional[str] = None
    rag_chat_min_similarity: Optional[float] = None
    sae_threads_signal_mode: Optional[str] = None
    sae_threads_signal_blend: Optional[float] = None
    sae_model_combo: Optional[str] = None
    sae_embeddings_fallback: Optional[bool] = None
    sae_steering_enabled: Optional[bool] = None
    sae_steering_layer: Optional[int] = None
    sae_steering_features: Optional[str] = None
    sae_steering_token_positions: Optional[str] = None
    sae_steering_dry_run: Optional[bool] = None
    sae_live_inspect_console: Optional[bool] = None


@router.get("/settings")
async def get_settings(request: Request):
    """
    Get current application settings.
    """
    cfg = request.app.state.config
    if isinstance(cfg, dict) and not str(cfg.get("hf_token") or "").strip():
        try:
            token = str(
                os.getenv("HUGGINGFACE_HUB_TOKEN") or os.getenv("HF_TOKEN") or ""
            ).strip()
            if not token:
                values = dotenv_values(app_config.get_dotenv_path())
                token = str(
                    values.get("HUGGINGFACE_HUB_TOKEN") or values.get("HF_TOKEN") or ""
                ).strip()
            if token:
                cfg["hf_token"] = token
        except Exception:
            pass
    safe_cfg = _redact_settings(cfg if isinstance(cfg, dict) else {})
    # Derive Weaviate settings from env if not present in config
    weaviate_url = (
        os.getenv("WEAVIATE_URL")
        or os.getenv("FLOAT_WEAVIATE_URL")
        or "http://localhost:8080"
    )
    weaviate_auto_start = os.getenv("FLOAT_AUTO_START_WEAVIATE", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if sys.platform.startswith("win"):
        server_platform = "windows"
    elif sys.platform == "darwin":
        server_platform = "mac"
    else:
        server_platform = "linux"
    devices = cfg.get("available_devices", [])
    cuda_diag = torch_cuda_diagnostics(devices)
    cfg["cuda_diagnostics"] = cuda_diag
    request.app.state.config = cfg
    _update_rag_config(cfg)
    local_provider = _normalize_local_provider(cfg.get("local_provider"))
    local_provider_mode = _normalize_local_provider_mode(cfg.get("local_provider_mode"))
    raw_provider_port = cfg.get("local_provider_port")
    try:
        local_provider_port = int(raw_provider_port)
    except Exception:
        local_provider_port = _default_local_provider_port(local_provider)
    if local_provider_port <= 0:
        local_provider_port = _default_local_provider_port(local_provider)
    return {
        "mode": llm_service.mode,
        "api_key": safe_cfg.get("api_key", ""),
        "api_key_set": safe_cfg.get("api_key_set", False),
        "api_key_preview": safe_cfg.get("api_key_preview", ""),
        "hf_token": safe_cfg.get("hf_token", ""),
        "hf_token_set": safe_cfg.get("hf_token_set", False),
        "hf_token_preview": safe_cfg.get("hf_token_preview", ""),
        "api_url": cfg.get("api_url") or app_config.DEFAULT_OPENAI_API_URL,
        "dotenv_path": str(app_config.get_dotenv_path()),
        "legacy_dotenv_loaded": bool(
            getattr(app_config, "LEGACY_DOTENV_LOADED", False)
        ),
        "legacy_dotenv_path": str(getattr(app_config, "LEGACY_DOTENV_PATH", "")),
        "local_url": cfg.get("local_url"),
        "model": cfg.get("api_model"),
        "dynamic_model": cfg.get("dynamic_model"),
        "dynamic_port": cfg.get("dynamic_port"),
        "conv_folder": cfg.get("conv_folder"),
        "models_folder": cfg.get("models_folder"),
        "mcp_url": cfg.get("mcp_url"),
        "mcp_token": safe_cfg.get("mcp_token", ""),
        "mcp_token_set": safe_cfg.get("mcp_token_set", False),
        "mcp_token_preview": safe_cfg.get("mcp_token_preview", ""),
        "transformer_model": cfg.get("transformer_model"),
        "local_provider": local_provider,
        "local_provider_mode": local_provider_mode,
        "local_provider_base_url": cfg.get("local_provider_base_url", ""),
        "local_provider_host": cfg.get("local_provider_host", "127.0.0.1"),
        "local_provider_port": local_provider_port,
        "lmstudio_path": cfg.get("lmstudio_path", ""),
        "local_provider_api_token": safe_cfg.get("local_provider_api_token", ""),
        "local_provider_api_token_set": safe_cfg.get(
            "local_provider_api_token_set", False
        ),
        "local_provider_api_token_preview": safe_cfg.get(
            "local_provider_api_token_preview", ""
        ),
        "local_provider_auto_start": cfg.get("local_provider_auto_start", True),
        "local_provider_preferred_model": cfg.get("local_provider_preferred_model", ""),
        "local_provider_default_context_length": cfg.get(
            "local_provider_default_context_length"
        ),
        "local_provider_show_server_logs": cfg.get(
            "local_provider_show_server_logs", True
        ),
        "local_provider_enable_cors": cfg.get("local_provider_enable_cors", False),
        "local_provider_allow_lan": cfg.get("local_provider_allow_lan", False),
        "static_model": cfg.get("static_model"),
        "harmony_format": cfg.get("harmony_format"),
        "server_url": cfg.get("server_url"),
        "stt_model": cfg.get("stt_model"),
        "tts_model": cfg.get("tts_model"),
        "voice_model": cfg.get("voice_model"),
        "stream_backend": cfg.get("stream_backend", "api"),
        "realtime_model": cfg.get("realtime_model", DEFAULT_REALTIME_MODEL),
        "realtime_voice": cfg.get("realtime_voice", DEFAULT_REALTIME_VOICE),
        "realtime_base_url": cfg.get("realtime_base_url", DEFAULT_REALTIME_SESSION_URL),
        "realtime_connect_url": cfg.get(
            "realtime_connect_url", DEFAULT_REALTIME_CONNECT_URL
        ),
        "vision_model": cfg.get("vision_model"),
        "dev_mode": cfg.get("dev_mode", False),
        "max_context_length": cfg.get("max_context_length"),
        "kv_cache": cfg.get("enable_kv_cache"),
        "ram_swap": cfg.get("enable_ram_swap"),
        "request_timeout": getattr(llm_service, "timeout", None),
        "stream_idle_timeout": getattr(llm_service, "stream_idle_timeout", None),
        "device_map_strategy": cfg.get("local_device_map_strategy"),
        "gpu_memory_fraction": cfg.get("local_max_gpu_mem_fraction"),
        "gpu_memory_margin_mb": cfg.get("local_gpu_memory_margin_mb"),
        "gpu_memory_limit_gb": cfg.get("local_gpu_mem_limit_gb"),
        "cpu_offload_fraction": cfg.get("local_cpu_offload_fraction"),
        "cpu_offload_limit_gb": cfg.get("local_cpu_offload_limit_gb"),
        "flash_attention": cfg.get("local_flash_attention"),
        "attention_implementation": cfg.get("local_attention_implementation"),
        "kv_cache_implementation": cfg.get("local_kv_cache_implementation"),
        "kv_cache_quant_backend": cfg.get("local_kv_cache_quant_backend"),
        "kv_cache_dtype": cfg.get("local_kv_cache_dtype"),
        "kv_cache_device": cfg.get("local_kv_cache_keep_on_device"),
        "model_dtype": cfg.get("local_weight_dtype"),
        "cpu_thread_count": cfg.get("local_cpu_thread_count"),
        "low_cpu_mem_usage": cfg.get("local_low_cpu_mem_usage"),
        "devices": cfg.get("available_devices", []),
        "default_device": cfg.get("default_inference_device"),
        "inference_device": cfg.get(
            "inference_device",
            (cfg.get("default_inference_device") or {}).get("id"),
        ),
        # RAG / Weaviate
        "rag_embedding_model": cfg.get("rag_embedding_model"),
        "rag_clip_model": cfg.get("rag_clip_model"),
        "rag_chat_min_similarity": cfg.get("rag_chat_min_similarity"),
        "sae_threads_signal_mode": cfg.get("sae_threads_signal_mode", "hybrid"),
        "sae_threads_signal_blend": cfg.get("sae_threads_signal_blend", 0.7),
        "sae_model_combo": cfg.get(
            "sae_model_combo", "openai/gpt-oss-20b :: future SAE pack"
        ),
        "sae_embeddings_fallback": cfg.get("sae_embeddings_fallback", True),
        "sae_steering_enabled": cfg.get("sae_steering_enabled", False),
        "sae_steering_layer": cfg.get("sae_steering_layer", 12),
        "sae_steering_features": cfg.get("sae_steering_features", ""),
        "sae_steering_token_positions": cfg.get("sae_steering_token_positions", "last"),
        "sae_steering_dry_run": cfg.get("sae_steering_dry_run", True),
        "sae_live_inspect_console": cfg.get("sae_live_inspect_console", False),
        "weaviate_url": cfg.get("weaviate_url", weaviate_url),
        "weaviate_auto_start": cfg.get("weaviate_auto_start", weaviate_auto_start),
        "default_models_dir": str(app_config.DEFAULT_MODELS_DIR),
        "default_conv_dir": str(app_config.DEFAULT_CONV_DIR),
        "server_platform": server_platform,
        "cuda_diagnostics": cuda_diag,
    }


@router.post("/settings")
async def update_settings(request: Request, settings: SettingsRequest):
    """
    Update application settings and persist to .env file.
    """
    # Persist settings to a stable dotenv path (repo-root `.env` by default).
    dotenv_path = str(app_config.get_dotenv_path())
    try:
        parent = os.path.dirname(dotenv_path)
        if parent and not os.path.exists(parent):
            os.makedirs(parent, exist_ok=True)
        if not os.path.exists(dotenv_path):
            open(dotenv_path, "a").close()
    except Exception:
        logger.exception("Failed to create .env file at %s", dotenv_path)

    def safe_set(key: str, value: str) -> None:
        try:
            set_key(dotenv_path, key, value)
        except Exception:
            try:
                open(dotenv_path, "a").close()
                set_key(dotenv_path, key, value)
            except Exception:
                logger.exception("Failed setting %s in %s", key, dotenv_path)

    def safe_unset(key: str) -> None:
        try:
            unset_key(dotenv_path, key)
        except Exception:
            try:
                lines = []
                try:
                    with open(dotenv_path, "r", encoding="utf-8") as f:
                        for line in f:
                            if line.startswith(f"{key}="):
                                continue
                            lines.append(line)
                except FileNotFoundError:
                    return
                with open(dotenv_path, "w", encoding="utf-8") as f:
                    f.writelines(lines)
            except Exception:
                logger.exception("Failed unsetting %s in %s", key, dotenv_path)

    cfg = request.app.state.config
    # Update API key
    if settings.api_key is not None:
        value = (settings.api_key or "").strip()
        if value:
            safe_set("OPENAI_API_KEY", value)
            safe_set("API_KEY", value)
            cfg["api_key"] = value
        else:
            safe_unset("OPENAI_API_KEY")
            safe_unset("API_KEY")
            cfg["api_key"] = ""
    # Update Hugging Face token
    if settings.hf_token is not None:
        value = (settings.hf_token or "").strip()
        if value:
            safe_set("HUGGINGFACE_HUB_TOKEN", value)
            safe_set("HF_TOKEN", value)
            os.environ["HUGGINGFACE_HUB_TOKEN"] = value
            os.environ["HF_TOKEN"] = value
            cfg["hf_token"] = value
        else:
            safe_unset("HUGGINGFACE_HUB_TOKEN")
            safe_unset("HF_TOKEN")
            os.environ.pop("HUGGINGFACE_HUB_TOKEN", None)
            os.environ.pop("HF_TOKEN", None)
            cfg["hf_token"] = ""
    # Update external API URL
    if settings.api_url is not None:
        value = (settings.api_url or "").strip()
        if value:
            safe_set("EXTERNAL_API_URL", value)
            cfg["api_url"] = value
        else:
            safe_unset("EXTERNAL_API_URL")
            cfg["api_url"] = app_config.DEFAULT_OPENAI_API_URL
    # Update local LLM URL
    if settings.local_url is not None:
        safe_set("LOCAL_LLM_URL", settings.local_url)
        cfg["local_url"] = settings.local_url
    # Update OpenAI model
    if settings.openai_model is not None:
        value = (settings.openai_model or "").strip()
        if value:
            safe_set("OPENAI_MODEL", value)
            cfg["api_model"] = value
        else:
            safe_unset("OPENAI_MODEL")
            cfg["api_model"] = app_config.DEFAULT_OPENAI_MODEL
    # Update dynamic model
    if settings.dynamic_model is not None:
        safe_set("DYNAMIC_MODEL", settings.dynamic_model)
        cfg["dynamic_model"] = settings.dynamic_model
    # Update dynamic port
    if settings.dynamic_port is not None:
        safe_set("DYNAMIC_PORT", str(settings.dynamic_port))
        cfg["dynamic_port"] = settings.dynamic_port
    # Update MCP settings
    if settings.mcp_url is not None:
        safe_set("MCP_SERVER_URL", settings.mcp_url)
        cfg["mcp_url"] = settings.mcp_url
    if settings.mcp_token is not None:
        safe_set("MCP_API_TOKEN", settings.mcp_token)
        cfg["mcp_token"] = settings.mcp_token
    if settings.inference_device is not None:
        value = (settings.inference_device or "").strip()
        if value:
            safe_set("LLM_DEVICE", value)
            cfg["inference_device"] = value
        else:
            safe_unset("LLM_DEVICE")
            cfg["inference_device"] = (cfg.get("default_inference_device") or {}).get(
                "id"
            )
    # Update transformer model
    if settings.transformer_model is not None:
        value = str(settings.transformer_model or "").strip()
        if value:
            safe_set("TRANSFORMER_MODEL", value)
            cfg["transformer_model"] = value
        else:
            safe_unset("TRANSFORMER_MODEL")
            cfg["transformer_model"] = ""
    # Retire legacy local backend toggle (llama.cpp path removed).
    cfg.pop("local_inference_backend", None)
    safe_unset("LOCAL_INFERENCE_BACKEND")

    provider_changed = False
    if settings.local_provider is not None:
        provider_value = _normalize_local_provider(settings.local_provider)
        safe_set("LOCAL_PROVIDER", provider_value)
        cfg["local_provider"] = provider_value
        provider_changed = True
    if settings.local_provider_mode is not None:
        mode_value = _normalize_local_provider_mode(settings.local_provider_mode)
        safe_set("LOCAL_PROVIDER_MODE", mode_value)
        cfg["local_provider_mode"] = mode_value
    if settings.local_provider_base_url is not None:
        base_url_value = str(settings.local_provider_base_url or "").strip()
        if base_url_value:
            safe_set("LOCAL_PROVIDER_BASE_URL", base_url_value)
            cfg["local_provider_base_url"] = base_url_value
        else:
            safe_unset("LOCAL_PROVIDER_BASE_URL")
            cfg["local_provider_base_url"] = ""
    if settings.local_provider_host is not None:
        host_value = str(settings.local_provider_host or "").strip() or "127.0.0.1"
        safe_set("LOCAL_PROVIDER_HOST", host_value)
        cfg["local_provider_host"] = host_value
    if settings.local_provider_port is not None:
        try:
            port_value = int(settings.local_provider_port)
        except Exception:
            port_value = 0
        if port_value > 0:
            safe_set("LOCAL_PROVIDER_PORT", str(port_value))
            cfg["local_provider_port"] = port_value
    elif provider_changed:
        provider_value = _normalize_local_provider(cfg.get("local_provider"))
        default_port = _default_local_provider_port(provider_value)
        safe_set("LOCAL_PROVIDER_PORT", str(default_port))
        cfg["local_provider_port"] = default_port
    if settings.lmstudio_path is not None:
        path_value = str(settings.lmstudio_path or "").strip()
        if path_value:
            safe_set("LMSTUDIO_PATH", path_value)
            cfg["lmstudio_path"] = path_value
        else:
            safe_unset("LMSTUDIO_PATH")
            cfg["lmstudio_path"] = ""
    if settings.local_provider_api_token is not None:
        token_value = str(settings.local_provider_api_token or "").strip()
        if token_value:
            safe_set("LOCAL_PROVIDER_API_TOKEN", token_value)
            cfg["local_provider_api_token"] = token_value
        else:
            safe_unset("LOCAL_PROVIDER_API_TOKEN")
            cfg["local_provider_api_token"] = ""
    if settings.local_provider_auto_start is not None:
        auto_start_value = bool(settings.local_provider_auto_start)
        safe_set("LOCAL_PROVIDER_AUTO_START", str(auto_start_value).lower())
        cfg["local_provider_auto_start"] = auto_start_value
    if settings.local_provider_preferred_model is not None:
        preferred_value = str(settings.local_provider_preferred_model or "").strip()
        if preferred_value:
            safe_set("LOCAL_PROVIDER_PREFERRED_MODEL", preferred_value)
            cfg["local_provider_preferred_model"] = preferred_value
        else:
            safe_unset("LOCAL_PROVIDER_PREFERRED_MODEL")
            cfg["local_provider_preferred_model"] = ""
    if settings.local_provider_default_context_length is not None:
        try:
            context_len = int(settings.local_provider_default_context_length)
        except Exception:
            context_len = 0
        if context_len > 0:
            safe_set("LOCAL_PROVIDER_DEFAULT_CONTEXT_LENGTH", str(context_len))
            cfg["local_provider_default_context_length"] = context_len
        else:
            safe_unset("LOCAL_PROVIDER_DEFAULT_CONTEXT_LENGTH")
            cfg["local_provider_default_context_length"] = 0
    if settings.local_provider_show_server_logs is not None:
        show_logs = bool(settings.local_provider_show_server_logs)
        safe_set("LOCAL_PROVIDER_SHOW_SERVER_LOGS", str(show_logs).lower())
        cfg["local_provider_show_server_logs"] = show_logs
    if settings.local_provider_enable_cors is not None:
        enable_cors = bool(settings.local_provider_enable_cors)
        safe_set("LOCAL_PROVIDER_ENABLE_CORS", str(enable_cors).lower())
        cfg["local_provider_enable_cors"] = enable_cors
    if settings.local_provider_allow_lan is not None:
        allow_lan = bool(settings.local_provider_allow_lan)
        safe_set("LOCAL_PROVIDER_ALLOW_LAN", str(allow_lan).lower())
        cfg["local_provider_allow_lan"] = allow_lan
    # Update static model
    if settings.static_model is not None:
        safe_set("STATIC_MODEL", settings.static_model)
        cfg["static_model"] = settings.static_model
    # Update harmony formatting toggle
    if settings.harmony_format is not None:
        safe_set("HARMONY_FORMAT", str(settings.harmony_format))
        cfg["harmony_format"] = settings.harmony_format
    # Update server URL
    if settings.server_url is not None:
        safe_set("SERVER_URL", settings.server_url)
        cfg["server_url"] = settings.server_url
    # Update STT model
    if settings.stt_model is not None:
        safe_set("STT_MODEL", settings.stt_model)
        cfg["stt_model"] = settings.stt_model
    # Update TTS model
    if settings.tts_model is not None:
        safe_set("TTS_MODEL", settings.tts_model)
        cfg["tts_model"] = settings.tts_model
    # Update voice model
    if settings.voice_model is not None:
        safe_set("VOICE_MODEL", settings.voice_model)
        cfg["voice_model"] = settings.voice_model
    if settings.stream_backend is not None:
        backend_value = _normalize_stream_backend(settings.stream_backend)
        safe_set("FLOAT_STREAM_BACKEND", backend_value)
        cfg["stream_backend"] = backend_value
    if settings.realtime_model is not None:
        value = str(settings.realtime_model or "").strip()
        if value:
            safe_set("OPENAI_REALTIME_MODEL", value)
            cfg["realtime_model"] = value
        else:
            safe_unset("OPENAI_REALTIME_MODEL")
            cfg["realtime_model"] = DEFAULT_REALTIME_MODEL
    if settings.realtime_voice is not None:
        value = str(settings.realtime_voice or "").strip()
        if value:
            safe_set("OPENAI_REALTIME_VOICE", value)
            cfg["realtime_voice"] = value
        else:
            safe_unset("OPENAI_REALTIME_VOICE")
            cfg["realtime_voice"] = DEFAULT_REALTIME_VOICE
    if settings.realtime_base_url is not None:
        value = str(settings.realtime_base_url or "").strip()
        if value:
            safe_set("OPENAI_REALTIME_URL", value)
            cfg["realtime_base_url"] = value
        else:
            safe_unset("OPENAI_REALTIME_URL")
            cfg["realtime_base_url"] = DEFAULT_REALTIME_SESSION_URL
    if settings.realtime_connect_url is not None:
        value = str(settings.realtime_connect_url or "").strip()
        if value:
            safe_set("OPENAI_REALTIME_CONNECT_URL", value)
            cfg["realtime_connect_url"] = value
        else:
            safe_unset("OPENAI_REALTIME_CONNECT_URL")
            cfg["realtime_connect_url"] = DEFAULT_REALTIME_CONNECT_URL
    # Update vision model
    if settings.vision_model is not None:
        safe_set("VISION_MODEL", settings.vision_model)
        safe_set("VISION_CAPTION_MODEL", settings.vision_model)
        cfg["vision_model"] = settings.vision_model
    if settings.max_context_length is not None:
        safe_set("MAX_CONTEXT_LENGTH", str(settings.max_context_length))
        cfg["max_context_length"] = settings.max_context_length
        llm_service.max_context_length = settings.max_context_length
    if settings.kv_cache is not None:
        safe_set("KV_CACHE_ENABLED", str(settings.kv_cache))
        cfg["enable_kv_cache"] = settings.kv_cache
        llm_service.use_kv_cache = settings.kv_cache
    if settings.ram_swap is not None:
        safe_set("RAM_SWAP_ENABLED", str(settings.ram_swap))
        cfg["enable_ram_swap"] = settings.ram_swap
        llm_service.enable_ram_swap = settings.ram_swap
    if settings.request_timeout is not None:
        try:
            timeout_val = float(settings.request_timeout)
        except (TypeError, ValueError):
            timeout_val = None
        if timeout_val and timeout_val > 0:
            safe_set("LLM_REQUEST_TIMEOUT", str(timeout_val))
            safe_set("FLOAT_REQUEST_TIMEOUT", str(timeout_val))
            cfg["timeout"] = timeout_val
            llm_service.timeout = timeout_val
        else:
            # Clear override to fall back to defaults
            safe_unset("LLM_REQUEST_TIMEOUT")
            safe_unset("FLOAT_REQUEST_TIMEOUT")
            default_timeout = getattr(
                llm_service, "_parse_timeout_config", lambda: llm_service.timeout
            )()
            cfg["timeout"] = default_timeout
            llm_service.timeout = default_timeout
    if settings.stream_idle_timeout is not None:
        try:
            idle_val = float(settings.stream_idle_timeout)
        except (TypeError, ValueError):
            idle_val = None
        if idle_val and idle_val > 0:
            safe_set("LLM_STREAM_IDLE_TIMEOUT", str(idle_val))
            safe_set("FLOAT_STREAM_IDLE_TIMEOUT", str(idle_val))
            cfg["stream_idle_timeout"] = idle_val
            llm_service.stream_idle_timeout = idle_val
        else:
            safe_unset("LLM_STREAM_IDLE_TIMEOUT")
            safe_unset("FLOAT_STREAM_IDLE_TIMEOUT")
            default_idle = getattr(
                llm_service,
                "_parse_stream_idle_timeout",
                lambda: llm_service.stream_idle_timeout,
            )()
            cfg["stream_idle_timeout"] = default_idle
            llm_service.stream_idle_timeout = default_idle
    if settings.device_map_strategy is not None:
        value = (settings.device_map_strategy or "").strip() or "auto"
        safe_set("LOCAL_DEVICE_MAP_STRATEGY", value)
        cfg["local_device_map_strategy"] = value
    if settings.gpu_memory_fraction is not None:
        fraction = max(0.0, min(float(settings.gpu_memory_fraction), 1.0))
        safe_set("LOCAL_MAX_GPU_MEM_FRACTION", str(fraction))
        cfg["local_max_gpu_mem_fraction"] = fraction
    if settings.gpu_memory_margin_mb is not None:
        margin = max(0, int(settings.gpu_memory_margin_mb))
        safe_set("LOCAL_GPU_MEMORY_MARGIN_MB", str(margin))
        cfg["local_gpu_memory_margin_mb"] = margin
    if settings.gpu_memory_limit_gb is not None:
        limit = max(0.0, float(settings.gpu_memory_limit_gb))
        safe_set("LOCAL_GPU_MEM_LIMIT_GB", str(limit))
        cfg["local_gpu_mem_limit_gb"] = limit
    if settings.cpu_offload_fraction is not None:
        fraction = max(0.0, min(float(settings.cpu_offload_fraction), 1.0))
        safe_set("LOCAL_CPU_OFFLOAD_FRACTION", str(fraction))
        cfg["local_cpu_offload_fraction"] = fraction
    if settings.cpu_offload_limit_gb is not None:
        limit = max(0.0, float(settings.cpu_offload_limit_gb))
        safe_set("LOCAL_CPU_OFFLOAD_LIMIT_GB", str(limit))
        cfg["local_cpu_offload_limit_gb"] = limit
    if settings.flash_attention is not None:
        safe_set("LOCAL_FLASH_ATTENTION", str(settings.flash_attention))
        cfg["local_flash_attention"] = settings.flash_attention
    if settings.attention_implementation is not None:
        impl = (settings.attention_implementation or "").strip()
        if impl:
            safe_set("LOCAL_ATTN_IMPLEMENTATION", impl)
            cfg["local_attention_implementation"] = impl
        else:
            safe_unset("LOCAL_ATTN_IMPLEMENTATION")
            cfg["local_attention_implementation"] = None
    if settings.kv_cache_implementation is not None:
        impl = (settings.kv_cache_implementation or "").strip() or None
        if impl:
            safe_set("LOCAL_KV_CACHE_IMPLEMENTATION", impl)
        else:
            safe_unset("LOCAL_KV_CACHE_IMPLEMENTATION")
        cfg["local_kv_cache_implementation"] = impl
    if settings.kv_cache_quant_backend is not None:
        backend = (settings.kv_cache_quant_backend or "").strip() or None
        if backend:
            safe_set("LOCAL_KV_CACHE_QUANT_BACKEND", backend)
        else:
            safe_unset("LOCAL_KV_CACHE_QUANT_BACKEND")
        cfg["local_kv_cache_quant_backend"] = backend
    if settings.kv_cache_dtype is not None:
        dtype = (settings.kv_cache_dtype or "").strip() or None
        if dtype:
            safe_set("LOCAL_KV_CACHE_DTYPE", dtype)
        else:
            safe_unset("LOCAL_KV_CACHE_DTYPE")
        cfg["local_kv_cache_dtype"] = dtype
    if settings.kv_cache_device is not None:
        device_target = (settings.kv_cache_device or "").strip() or None
        if device_target:
            safe_set("LOCAL_KV_CACHE_DEVICE", device_target)
        else:
            safe_unset("LOCAL_KV_CACHE_DEVICE")
        cfg["local_kv_cache_keep_on_device"] = device_target
    if settings.model_dtype is not None:
        dtype = (settings.model_dtype or "").strip() or None
        if dtype:
            safe_set("LOCAL_MODEL_DTYPE", dtype)
        else:
            safe_unset("LOCAL_MODEL_DTYPE")
        cfg["local_weight_dtype"] = dtype
    if settings.cpu_thread_count is not None:
        threads = max(0, int(settings.cpu_thread_count))
        safe_set("LOCAL_CPU_THREADS", str(threads))
        cfg["local_cpu_thread_count"] = threads
    if settings.dev_mode is not None:
        safe_set("FLOAT_DEV_MODE", str(settings.dev_mode).lower())
        cfg["dev_mode"] = settings.dev_mode
    # Update Weaviate URL
    if settings.rag_embedding_model is not None:
        value = (settings.rag_embedding_model or "").strip()
        safe_set("RAG_EMBEDDING_MODEL", value)
        cfg["rag_embedding_model"] = value
    if settings.rag_clip_model is not None:
        value = (settings.rag_clip_model or "").strip()
        if value:
            lowered = value.lower()
            if "paligemma" in lowered or "pixtral" in lowered:
                value = "ViT-B-32"
            safe_set("RAG_CLIP_MODEL", value)
            cfg["rag_clip_model"] = value
        else:
            safe_unset("RAG_CLIP_MODEL")
            cfg["rag_clip_model"] = None
    if settings.rag_chat_min_similarity is not None:
        try:
            min_sim = float(settings.rag_chat_min_similarity)
        except (TypeError, ValueError):
            min_sim = None
        if min_sim is None:
            safe_unset("RAG_CHAT_MIN_SIMILARITY")
            cfg["rag_chat_min_similarity"] = app_config.load_config().get(
                "rag_chat_min_similarity", 0.3
            )
        else:
            min_sim = max(0.0, min(min_sim, 1.0))
            safe_set("RAG_CHAT_MIN_SIMILARITY", str(min_sim))
            cfg["rag_chat_min_similarity"] = min_sim
    if settings.sae_threads_signal_mode is not None:
        mode_value = str(settings.sae_threads_signal_mode or "").strip().lower()
        if mode_value not in {"embeddings", "hybrid", "sae"}:
            mode_value = "embeddings"
        safe_set("SAE_THREADS_SIGNAL_MODE", mode_value)
        cfg["sae_threads_signal_mode"] = mode_value
    if settings.sae_threads_signal_blend is not None:
        try:
            blend_value = float(settings.sae_threads_signal_blend)
        except (TypeError, ValueError):
            blend_value = 0.7
        blend_value = max(0.0, min(blend_value, 1.0))
        safe_set("SAE_THREADS_SIGNAL_BLEND", str(blend_value))
        cfg["sae_threads_signal_blend"] = blend_value
    if settings.sae_model_combo is not None:
        combo_value = str(settings.sae_model_combo or "").strip()
        if combo_value:
            safe_set("SAE_MODEL_COMBO", combo_value)
            cfg["sae_model_combo"] = combo_value
        else:
            safe_unset("SAE_MODEL_COMBO")
            cfg["sae_model_combo"] = ""
    if settings.sae_embeddings_fallback is not None:
        fallback_value = bool(settings.sae_embeddings_fallback)
        safe_set("SAE_EMBEDDINGS_FALLBACK", str(fallback_value).lower())
        cfg["sae_embeddings_fallback"] = fallback_value
    if settings.sae_steering_enabled is not None:
        steering_enabled = bool(settings.sae_steering_enabled)
        safe_set("SAE_STEERING_ENABLED", str(steering_enabled).lower())
        cfg["sae_steering_enabled"] = steering_enabled
    if settings.sae_steering_layer is not None:
        steering_layer = max(0, int(settings.sae_steering_layer))
        safe_set("SAE_STEERING_LAYER", str(steering_layer))
        cfg["sae_steering_layer"] = steering_layer
    if settings.sae_steering_features is not None:
        features_value = str(settings.sae_steering_features or "").strip()
        if features_value:
            safe_set("SAE_STEERING_FEATURES", features_value)
            cfg["sae_steering_features"] = features_value
        else:
            safe_unset("SAE_STEERING_FEATURES")
            cfg["sae_steering_features"] = ""
    if settings.sae_steering_token_positions is not None:
        token_positions = (
            str(settings.sae_steering_token_positions or "").strip() or "last"
        )
        safe_set("SAE_STEERING_TOKEN_POSITIONS", token_positions)
        cfg["sae_steering_token_positions"] = token_positions
    if settings.sae_steering_dry_run is not None:
        dry_run_value = bool(settings.sae_steering_dry_run)
        safe_set("SAE_STEERING_DRY_RUN", str(dry_run_value).lower())
        cfg["sae_steering_dry_run"] = dry_run_value
    if settings.sae_live_inspect_console is not None:
        live_inspect = bool(settings.sae_live_inspect_console)
        safe_set("SAE_LIVE_INSPECT_CONSOLE", str(live_inspect).lower())
        cfg["sae_live_inspect_console"] = live_inspect
    if settings.weaviate_url is not None:
        safe_set("WEAVIATE_URL", settings.weaviate_url)
        # clear alternate var to avoid ambiguity
        safe_set("FLOAT_WEAVIATE_URL", settings.weaviate_url)
        cfg["weaviate_url"] = settings.weaviate_url
    # Update Weaviate auto-start flag
    if settings.weaviate_auto_start is not None:
        val = "true" if settings.weaviate_auto_start else "false"
        safe_set("FLOAT_AUTO_START_WEAVIATE", val)
        cfg["weaviate_auto_start"] = settings.weaviate_auto_start
    # Update mode
    if settings.mode is not None:
        llm_service.mode = settings.mode
    # Update conversations folder
    if settings.conv_folder is not None:
        safe_set("FLOAT_CONV_DIR", settings.conv_folder)
        cfg["conv_folder"] = settings.conv_folder
    # Update custom models folder
    if settings.models_folder is not None:
        safe_set("FLOAT_MODELS_DIR", settings.models_folder)
        cfg["models_folder"] = settings.models_folder
    cfg["cuda_diagnostics"] = torch_cuda_diagnostics(cfg.get("available_devices", []))
    # Persist updated config to app state and service
    request.app.state.config = cfg
    llm_service.config = cfg
    global livekit_service
    livekit_service = LiveKitService(cfg)
    request.app.state.livekit_service = livekit_service
    # Ensure the singleton RAG service picks up any backend/model changes.
    _update_rag_config(cfg)
    return {
        "status": "success",
        "settings": _redact_settings(cfg),
        "mode": llm_service.mode,
    }


@router.get("/vram-estimate")
async def vram_estimate(context_length: int):
    """Estimate VRAM usage for a given context length."""
    estimate = llm_service.estimate_vram(context_length)
    return {"context_length": context_length, "estimate_mb": estimate}


# Model download endpoints
@router.get("/models/supported")
async def list_supported_models(request: Request):
    """Return a list of supported model identifiers."""
    cfg = request.app.state.config
    devices = cfg.get("available_devices", [])
    filtered = filter_models_for_devices(devices)
    return {"models": sorted(filtered)}


class LocalModelRegistrationPayload(BaseModel):
    alias: Optional[str] = None
    path: str
    model_type: Optional[str] = None


@router.get("/models/registered")
async def list_registered_local_models():
    return {"models": list_local_model_entries(include_missing=True)}


@router.post("/models/registered")
async def register_local_model(payload: LocalModelRegistrationPayload):
    raw_path = str(payload.path or "").strip()
    if not raw_path:
        raise HTTPException(status_code=400, detail="path is required")
    try:
        entry = upsert_local_model_entry(
            path=raw_path,
            alias=payload.alias,
            model_type=payload.model_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"model": entry}


@router.delete("/models/registered/{alias}")
async def unregister_local_model(alias: str):
    if not remove_local_model_entry(alias):
        raise HTTPException(status_code=404, detail="Registered model not found")
    return {"status": "deleted"}


def _is_hf_cache_dir(models_dir: Path) -> bool:
    parts = [p.lower() for p in models_dir.parts]
    return "huggingface" in parts and "hub" in parts


def _hf_cache_model_allowed(name: str, *, allow_extras: bool) -> bool:
    """Filter noisy HF cache entries to keep selectors usable."""
    if allow_extras:
        return True
    lowered = name.lower()
    allowed_prefixes = (
        "gpt-oss",
        "llama",
        "qwen",
        "gemma",
        "mistral",
        "mixtral",
        "phi",
        "falcon",
        "zephyr",
    )
    allowed_exact = {
        "gpt-5.1",
        "gpt-4.1",
        "gpt-4o-mini",
    }
    return lowered in allowed_exact or lowered.startswith(allowed_prefixes)


@router.get("/transformers/models")
async def list_transformer_models(
    request: Request,
    path: Optional[str] = None,
    include_cache_unfiltered: bool = False,
):  # noqa: E501
    """List available transformer models from all search directories.

    Filters Hugging Face cache noise by default so selectors are not flooded
    with unrelated tiny checkpoints; pass include_cache_unfiltered=true to
    return every cache entry.
    """
    cfg = request.app.state.config
    dirs = app_config.model_search_dirs(
        path or cfg.get("models_folder", app_config.DEFAULT_MODELS_DIR)
    )
    models: set[str] = set()
    for models_dir in dirs:
        if not models_dir.exists():
            continue
        is_cache = _is_hf_cache_dir(models_dir)
        for item in models_dir.iterdir():
            if not item.is_dir():
                continue
            if any(
                f.suffix.lower() in {".gguf", ".bin", ".safetensors"}
                for f in item.glob("**/*")
            ):
                name = item.name
                if name.startswith("models--"):
                    parts = name.split("--")
                    if len(parts) >= 3:
                        name = parts[-1]
                if is_cache and not _hf_cache_model_allowed(
                    name, allow_extras=include_cache_unfiltered
                ):
                    continue
                models.add(name)
    for entry in list_local_model_entries(include_missing=False):
        alias = str(entry.get("alias") or "").strip()
        if alias:
            models.add(alias)
    return {"models": sorted(models)}


def _resolve_hf_snapshot(models_root: Path, model_name: str) -> Optional[Path]:
    """Best-effort resolve a Hugging Face cached snapshot dir for a model.

    Looks for hub layout: models--ORG--NAME/{refs,snapshots}/... and resolves the
    active snapshot (refs/main) or falls back to the newest snapshot folder.
    Returns a concrete path to the snapshot directory if found, else None.
    """
    try:
        # Glob any organization
        for candidate in models_root.glob(f"models--*--{model_name}"):
            if not candidate.is_dir():
                continue
            refs = candidate / "refs" / "main"
            snap_root = candidate / "snapshots"
            if refs.exists():
                try:
                    commit = refs.read_text().strip()
                    snap = snap_root / commit
                    if snap.exists() and snap.is_dir():
                        return snap
                except Exception:
                    pass
            # Fallback: pick the most recently modified snapshot
            try:
                snaps = [p for p in snap_root.iterdir() if p.is_dir()]
                if snaps:
                    snaps.sort(
                        key=lambda p: getattr(p.stat(), "st_mtime", 0), reverse=True
                    )
                    return snaps[0]
            except Exception:
                pass
    except Exception:
        pass
    return None


def _resolve_local_model_dir(
    search_roots: list[Path], model_name: str
) -> Optional[Path]:
    """Return a concrete local directory for a model if present.

    Order of checks per root:
      1) <root>/<model_name>
      2) HF cache layout: <root>/models--*--<model_name>/snapshots/<sha>
    """
    registered = resolve_registered_model_path(model_name, for_loading=False)
    if registered is not None:
        return registered
    for root in search_roots:
        direct = root / model_name
        try:
            if direct.exists() and direct.is_dir():
                return direct
        except Exception:
            # Ignore permission or transient errors
            pass
        # Also check HF hub cache structure within this root
        snap = _resolve_hf_snapshot(root, model_name)
        if snap is not None:
            return snap
    return None


@router.get("/models/exists/{model_name}")
async def model_exists(
    request: Request, model_name: str, path: Optional[str] = None
):  # noqa: E501
    cfg = request.app.state.config
    dirs = app_config.model_search_dirs(
        path or cfg.get("models_folder", app_config.DEFAULT_MODELS_DIR)
    )
    resolved = _resolve_local_model_dir(dirs, model_name)
    return {"exists": bool(resolved)}


@router.get("/models/local-size/{model_name}")
async def model_local_size(
    request: Request, model_name: str, path: Optional[str] = None
):  # noqa: E501
    """
    Return the total on-disk size in bytes for the model folder if present.
    Supports both direct folders and Hugging Face cache snapshots.
    """
    cfg = request.app.state.config
    dirs = app_config.model_search_dirs(
        path or cfg.get("models_folder", app_config.DEFAULT_MODELS_DIR)
    )
    resolved = _resolve_local_model_dir(dirs, model_name)
    if resolved is not None:
        try:
            allow_patterns = get_download_allow_patterns(model_name)
            return {
                "exists": True,
                "size": _folder_size_bytes(resolved, include_patterns=allow_patterns),
            }
        except Exception:
            return {"exists": True, "size": 0}
    return {"exists": False, "size": 0}


@router.get("/models/verify/{model_name}")
async def verify_model(
    request: Request, model_name: str, path: Optional[str] = None
):  # noqa: E501
    """
    Verify on-disk model files against the upstream repository manifest.

    Returns:
      - exists: whether a local model folder exists
      - verified: True if all upstream files are present and checks pass
      - expected_bytes: total bytes from upstream manifest
      - installed_bytes: total bytes found locally (recursive)
      - checked_files: number of files compared
    Notes:
      - Hash checks are only performed once all file sizes match; this keeps
        the common case fast and avoids hashing partial downloads.
    """
    cfg = request.app.state.config
    # Resolve local directory (direct or HF cache snapshot)
    dirs = app_config.model_search_dirs(
        path or cfg.get("models_folder", app_config.DEFAULT_MODELS_DIR)
    )
    allow_patterns = get_download_allow_patterns(model_name)
    local_dir: Optional[Path] = _resolve_local_model_dir(dirs, model_name)
    installed = 0
    if local_dir:
        try:
            installed = _folder_size_bytes(local_dir, include_patterns=allow_patterns)
        except Exception:
            installed = 0

    repo_id = MODEL_REPOS.get(model_name)
    if not local_dir:
        return {
            "exists": False,
            "verified": False,
            "expected_bytes": 0,
            "installed_bytes": 0,
            "checked_files": 0,
        }
    if not repo_id or str(repo_id).startswith("TODO"):
        # Unknown or API-only; cannot verify against upstream
        return {
            "exists": True,
            "verified": False,
            "expected_bytes": 0,
            "installed_bytes": int(installed),
            "checked_files": 0,
        }

    # Build remote manifest
    try:
        # Offload blocking HF API call
        token = cfg.get("hf_token") if isinstance(cfg, dict) else None
        manifest, expected, _commit = await asyncio.to_thread(
            _remote_manifest, repo_id, allow_patterns, token
        )
    except Exception:
        fallback = _fallback_verification_from_job(
            request, model_name, local_dir, installed
        )
        if fallback is not None:
            return fallback

        # Without manifest or job info we cannot assert verification
        return {
            "exists": True,
            "verified": False,
            "expected_bytes": 0,
            "installed_bytes": int(installed),
            "checked_files": 0,
        }

    # Quick path: if installed size is less than expected, definitely not verified
    if expected > 0 and installed < expected:
        return {
            "exists": True,
            "verified": False,
            "expected_bytes": int(expected),
            "installed_bytes": int(installed),
            "checked_files": 0,
        }

    # Compare file-by-file sizes; only check hashes if sizes match everywhere
    sizes_ok = True
    checked = 0
    for entry in manifest:
        rel = entry.get("path") or ""
        if not rel:
            continue
        local_path = local_dir / rel
        try:
            st = local_path.stat()
        except Exception:
            sizes_ok = False
            break
        if int(entry.get("size") or 0) != int(getattr(st, "st_size", 0)):
            sizes_ok = False
            break
        checked += 1

    if not sizes_ok:
        return {
            "exists": True,
            "verified": False,
            "expected_bytes": int(expected),
            "installed_bytes": int(installed),
            "checked_files": int(checked),
        }

    if expected <= 0 or not manifest:
        fallback = _fallback_verification_from_job(
            request, model_name, local_dir, installed
        )
        if fallback is not None:
            return fallback

    # Hash verification only if sizes match (avoid heavy hashing on partial downloads)
    # Only verify entries that provide a sha256 in the manifest.
    for entry in manifest:
        sha = entry.get("sha256")
        if not sha:
            continue
        rel = entry.get("path") or ""
        if not rel:
            continue
        local_path = local_dir / rel
        try:
            local_sha = await asyncio.to_thread(_sha256_file, local_path)
        except Exception:
            return {
                "exists": True,
                "verified": False,
                "expected_bytes": int(expected),
                "installed_bytes": int(installed),
                "checked_files": int(checked),
            }
        if local_sha != sha:
            return {
                "exists": True,
                "verified": False,
                "expected_bytes": int(expected),
                "installed_bytes": int(installed),
                "checked_files": int(checked),
            }

    if checked == 0 and installed > 0:
        fallback = _fallback_verification_from_job(
            request, model_name, local_dir, installed
        )
        if fallback is not None:
            return fallback

    if checked <= 0 and local_dir is not None:
        checked = _count_local_files(local_dir)
    expected_bytes = int(expected) if expected > 0 else int(installed)
    return {
        "exists": True,
        "verified": installed > 0 and expected > 0,
        "expected_bytes": expected_bytes,
        "installed_bytes": int(installed),
        "checked_files": int(checked),
    }


@router.get("/models/integrity/{model_name}")
async def model_integrity(model_name: str):
    """Return a quick summary of local model files for diagnostics."""
    summary = llm_service.verify_local_model(model_name)
    return {"integrity": summary}


# ---------------------------
# Download Job API (background)
# ---------------------------


class ModelJobRequest(BaseModel):
    model: str
    path: Optional[str] = None


def _get_jobs_state(app) -> Dict[str, dict]:
    if not hasattr(app.state, "model_jobs"):
        app.state.model_jobs = {}
    return app.state.model_jobs


def _resolve_models_dir(cfg: dict, requested_path: Optional[str]) -> Path:
    requested = requested_path or cfg.get(
        "models_folder", str(app_config.DEFAULT_MODELS_DIR)
    )
    try:
        p = Path(requested)
    except Exception:
        return Path(cfg.get("models_folder", app_config.DEFAULT_MODELS_DIR))
    if (not p.is_absolute()) and (not p.exists()):
        return Path(cfg.get("models_folder", app_config.DEFAULT_MODELS_DIR))
    return p


def _start_download_process(
    repo_id: str, target_dir: Path, model_alias: Optional[str] = None
) -> subprocess.Popen:
    cmd = [
        sys.executable,
        "-m",
        "app.download_worker",
        "--repo",
        repo_id,
        "--dir",
        str(target_dir),
    ]
    if model_alias:
        cmd.extend(["--model", model_alias])
    # Prefer fast transport if available and pass through HF token.
    env = os.environ.copy()
    try:
        import importlib.util as _importlib_util

        if _importlib_util.find_spec("hf_transfer") is not None:
            env.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "1")
    except Exception:
        # If hf_transfer isn't available, avoid forcing the flag.
        pass
    # Detach stdio; logs aren't needed here
    return subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )


def _job_progress(job: dict) -> dict:
    # Compute on-disk size; 'path' already includes the model folder
    downloaded = 0
    try:
        p = Path(job["path"])
        if p.exists():
            allow_patterns = job.get("allow_patterns")
            downloaded = _folder_size_bytes(
                p,
                include_patterns=allow_patterns
                if isinstance(allow_patterns, list)
                else None,
            )
    except Exception:
        downloaded = 0
    total = int(job.get("total", 0) or 0)
    pct = (downloaded / total) if total > 0 else 0.0
    return {
        "downloaded": downloaded,
        "total": total,
        "percent": min(1.0, pct),
    }


@router.post("/models/jobs")
async def create_model_job(request: Request, body: ModelJobRequest):
    cfg = request.app.state.config
    token = cfg.get("hf_token") if isinstance(cfg, dict) else None
    repo_id = MODEL_REPOS.get(body.model)
    if not repo_id or str(repo_id).startswith("TODO"):
        raise HTTPException(status_code=400, detail="Unsupported model")

    models_root = _resolve_models_dir(cfg, body.path)
    target_dir = models_root / body.model
    target_dir.mkdir(parents=True, exist_ok=True)
    allow_patterns = get_download_allow_patterns(body.model)

    def _norm_path(value: str) -> str:
        try:
            return str(Path(value).expanduser().resolve())
        except Exception:
            return str(Path(value).expanduser())

    # Deduplicate or resume existing jobs so repeated clicks don't spawn multiple
    # concurrent download processes for the same model.
    jobs = _get_jobs_state(request.app)
    target_key = _norm_path(str(target_dir))
    candidates = [
        j
        for j in jobs.values()
        if j.get("model") == body.model
        and _norm_path(str(j.get("path") or "")) == target_key
    ]
    if candidates:
        candidates.sort(
            key=lambda j: j.get("updated_at", j.get("started_at", 0)), reverse=True
        )
        job = candidates[0]
        _refresh_job_status(job)
        if job.get("status") == "running":
            prog = _job_progress(job)
            return {
                "job": {k: v for k, v in job.items() if not k.startswith("_")},
                **prog,
            }
        if job.get("status") in {"paused", "error"}:
            proc = _start_download_process(
                job.get("repo_id") or repo_id,
                Path(job["path"]),
                job.get("model") or body.model,
            )
            job["_proc"] = proc
            job["pid"] = proc.pid
            job["status"] = "running"
            job["error"] = None
            job["updated_at"] = time.time()
            prog = _job_progress(job)
            return {
                "job": {k: v for k, v in job.items() if not k.startswith("_")},
                **prog,
            }

    # Determine expected total size using the Hub API; fallback to 0 if unknown
    total_size = 0
    # Lazy import to keep startup fast
    from huggingface_hub import HfApi

    try:
        from huggingface_hub.utils import GatedRepoError
    except Exception:  # pragma: no cover - fallback if import path changes
        GatedRepoError = None  # type: ignore

    api = HfApi(token=token) if token else HfApi()
    try:
        info = await asyncio.to_thread(api.model_info, repo_id, files_metadata=True)
        total_size = sum(
            (
                int(getattr(s, "size", None) or 0)
                for s in getattr(info, "siblings", []) or []
                if _path_matches_any(
                    str(getattr(s, "rfilename", None) or getattr(s, "path", "") or ""),
                    allow_patterns,
                )
            )
        )
    except Exception as exc:
        if GatedRepoError is not None and isinstance(exc, GatedRepoError):
            raise HTTPException(
                status_code=403,
                detail=(
                    "Model access is gated. Set a Hugging Face token (HF_TOKEN/HUGGINGFACE_HUB_TOKEN) "
                    "and accept the model license on the repo page before retrying."
                ),
            )
        total_size = 0

    job_id = str(uuid4())
    proc = _start_download_process(repo_id, target_dir, body.model)
    job = {
        "id": job_id,
        "model": body.model,
        "repo_id": repo_id,
        "path": str(Path(target_dir).resolve()),
        "status": "running",
        "total": int(total_size),
        "error": None,
        "pid": proc.pid,
        "_proc": proc,
        "allow_patterns": allow_patterns,
        "started_at": time.time(),
        "updated_at": time.time(),
    }
    jobs[job_id] = job
    prog = _job_progress(job)
    return {"job": {k: v for k, v in job.items() if not k.startswith("_")}, **prog}


# ---------------------------
# Notifications (generic + SSE)
# ---------------------------


class NotificationPayload(BaseModel):
    title: str = "Float notification"
    body: str = ""
    category: str = "general"
    data: Dict[str, Any] = {}


def emit_notification(
    app,
    *,
    title: str,
    body: str = "",
    category: str = "general",
    data: Optional[Dict[str, Any]] = None,
) -> None:
    entry = {
        "title": title,
        "body": body,
        "category": category,
        "data": data or {},
        "ts": time.time(),
    }
    # buffer
    buf = _notifications_buffer()
    buf.append(entry)
    while len(buf) > 50:
        buf.pop(0)
    # enqueue SSE
    try:
        q: asyncio.Queue = app.state.notify_queue
        q.put_nowait(entry)
    except Exception:
        pass
    # web push
    try:
        if can_send_push():
            settings = user_settings.load_settings()
            sub = settings.get("push_subscription")
            enabled = settings.get("push_enabled", False)
            if enabled and sub:
                err = send_web_push(
                    sub,
                    {
                        "title": title,
                        "body": body,
                        "data": data or {},
                    },
                )
                if err:
                    logger.warning("web push failed: %s", err)
    except Exception:
        logger.exception("notify push failed")


@router.post("/notify")
async def create_notification(request: Request, payload: NotificationPayload):
    emit_notification(
        request.app,
        title=payload.title,
        body=payload.body,
        category=payload.category,
        data=payload.data,
    )
    return {"ok": True}


@router.get("/notifications/recent")
async def recent_notifications():
    return {"notifications": list(_notifications_buffer())}


@router.get("/stream/notifications")
async def stream_notifications(request: Request):
    """SSE stream for in-app notifications."""
    queue: asyncio.Queue = request.app.state.notify_queue

    async def generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                entry = await queue.get()
                enriched = {**entry, "request_id": get_request_id()}
                yield "event: notification\n" + f"data: {json.dumps(enriched)}\n\n"
        except asyncio.CancelledError:
            logger.info("notification SSE cancelled")
        except Exception:
            logger.exception("notification SSE error")

    return StreamingResponse(generator(), media_type="text/event-stream")


def _refresh_job_status(job: dict) -> None:
    proc: Optional[subprocess.Popen] = job.get("_proc")
    if proc is not None:
        code = proc.poll()
        if code is None:
            job["status"] = "running"
        else:
            job["_proc"] = None
            job["pid"] = None
            job["updated_at"] = time.time()
            if code == 0:
                job["status"] = "completed"
            else:
                job["status"] = "error"
                job["error"] = f"process exited with code {code}"


@router.get("/models/jobs")
async def list_model_jobs(
    request: Request,
    limit: int = 50,
    include_finished: bool = True,
):
    jobs = _get_jobs_state(request.app)
    rows: list[dict[str, Any]] = []
    safe_limit = max(1, min(int(limit or 50), 200))
    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        _refresh_job_status(job)
        status = str(job.get("status") or "")
        if not include_finished and status in {"completed", "canceled"}:
            continue
        public = {k: v for k, v in job.items() if not k.startswith("_")}
        public.update(_job_progress(job))
        rows.append(public)
    rows.sort(
        key=lambda item: item.get("updated_at", item.get("started_at", 0)) or 0,
        reverse=True,
    )
    return {"jobs": rows[:safe_limit]}


@router.get("/models/jobs/{job_id}")
async def get_model_job(request: Request, job_id: str):
    jobs = _get_jobs_state(request.app)
    job = jobs.get(job_id)
    if not job:
        return {
            "job": {
                "id": job_id,
                "status": "unknown",
                "error": "Job not found",
            },
            "downloaded": 0,
            "total": 0,
            "percent": 0.0,
        }
    _refresh_job_status(job)
    prog = _job_progress(job)
    return {"job": {k: v for k, v in job.items() if not k.startswith("_")}, **prog}


def _terminate_proc(job: dict) -> None:
    proc: Optional[subprocess.Popen] = job.get("_proc")
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        job["_proc"] = None
        job["pid"] = None


@router.post("/models/jobs/{job_id}/pause")
async def pause_model_job(request: Request, job_id: str):
    jobs = _get_jobs_state(request.app)
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _refresh_job_status(job)
    if job["status"] != "running":
        return {"job": {k: v for k, v in job.items() if not k.startswith("_")}}
    _terminate_proc(job)
    job["status"] = "paused"
    job["updated_at"] = time.time()
    prog = _job_progress(job)
    return {"job": {k: v for k, v in job.items() if not k.startswith("_")}, **prog}


@router.post("/models/jobs/{job_id}/cancel")
async def cancel_model_job(request: Request, job_id: str):
    jobs = _get_jobs_state(request.app)
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _terminate_proc(job)
    job["status"] = "canceled"
    job["updated_at"] = time.time()
    prog = _job_progress(job)
    return {"job": {k: v for k, v in job.items() if not k.startswith("_")}, **prog}


@router.post("/models/jobs/{job_id}/resume")
async def resume_model_job(request: Request, job_id: str):
    jobs = _get_jobs_state(request.app)
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _refresh_job_status(job)
    if job["status"] not in {"paused", "error"}:
        return {"job": {k: v for k, v in job.items() if not k.startswith("_")}}
    # Restart process; snapshot_download will resume remaining files
    proc = _start_download_process(job["repo_id"], Path(job["path"]), job.get("model"))
    job["_proc"] = proc
    job["pid"] = proc.pid
    job["status"] = "running"
    job["error"] = None
    job["updated_at"] = time.time()
    prog = _job_progress(job)
    return {"job": {k: v for k, v in job.items() if not k.startswith("_")}, **prog}


@router.get("/models/info/{model_name}")
async def model_info(request: Request, model_name: str):
    """Return basic metadata for a supported model.

    For API-only or unknown identifiers, return size=0 with a TODO repo tag
    rather than raising 400. This keeps the UI logic simple and avoids noisy
    errors for provider-only voices (e.g., 'alloy').
    """
    repo_id = MODEL_REPOS.get(model_name)
    if not repo_id:
        return {"repo_id": "TODO: unsupported", "size": 0}
    if str(repo_id).startswith("TODO"):
        # Placeholder link; size unknown
        return {"repo_id": repo_id, "size": 0}

    # Lazy import to avoid heavy hub import on startup
    from huggingface_hub import HfApi

    cfg = request.app.state.config if request else {}
    token = cfg.get("hf_token") if isinstance(cfg, dict) else None

    try:
        from huggingface_hub.utils import (
            HfHubHTTPError,
            RepositoryNotFoundError,
            GatedRepoError,
        )
    except Exception:  # pragma: no cover - fallback if import path changes
        HfHubHTTPError = RepositoryNotFoundError = GatedRepoError = Exception  # type: ignore
    api = HfApi(token=token) if token else HfApi()
    try:
        info = await asyncio.to_thread(api.model_info, repo_id, files_metadata=True)
        siblings = getattr(info, "siblings", []) or []
        allow_patterns = get_download_allow_patterns(model_name)
        size = sum(
            int(getattr(s, "size", None) or 0)
            for s in siblings
            if _path_matches_any(
                str(getattr(s, "rfilename", None) or getattr(s, "path", "") or ""),
                allow_patterns,
            )
        )
        return {"repo_id": repo_id, "size": int(size)}
    except GatedRepoError as e:  # gated/private repo; likely requires auth
        return {"repo_id": repo_id, "size": 0, "requires_auth": True, "error": str(e)}
    except (RepositoryNotFoundError, HfHubHTTPError) as e:
        # repo missing or API error â€” report gracefully
        return {"repo_id": repo_id, "size": 0, "error": str(e)}
    except Exception as e:
        return {"repo_id": repo_id, "size": 0, "error": str(e)}


@router.get("/models/summary/{model_name}")
async def model_summary(
    request: Request,
    model_name: str,
    verify: bool = False,
    path: Optional[str] = None,
):  # noqa: E501
    """Return a compact, aggregated status for a model.

    Includes local presence and size, upstream expected size, optional
    verification against the upstream manifest, and the most recent download
    job status if present.
    """
    cfg = request.app.state.config
    repo_id = MODEL_REPOS.get(model_name)

    # Resolve local dirs and compute installed size
    dirs = app_config.model_search_dirs(
        path or cfg.get("models_folder", app_config.DEFAULT_MODELS_DIR)
    )
    resolved = _resolve_local_model_dir(dirs, model_name)
    installed = 0
    if resolved is not None:
        try:
            allow_patterns = get_download_allow_patterns(model_name)
            installed = _folder_size_bytes(resolved, include_patterns=allow_patterns)
        except Exception:
            installed = 0

    # Expected upstream size (best-effort)
    expected = 0
    requires_auth = False
    repo_error: Optional[str] = None
    token = cfg.get("hf_token") if isinstance(cfg, dict) else None
    if repo_id and not str(repo_id).startswith("TODO"):
        # Lazy imports
        from huggingface_hub import HfApi

        try:
            from huggingface_hub.utils import GatedRepoError
        except Exception:  # pragma: no cover
            GatedRepoError = Exception  # type: ignore
        api = HfApi(token=token) if token else HfApi()
        try:
            info = await asyncio.to_thread(api.model_info, repo_id, files_metadata=True)
            siblings = getattr(info, "siblings", []) or []
            allow_patterns = get_download_allow_patterns(model_name)
            expected = int(
                sum(
                    int(getattr(s, "size", None) or 0)
                    for s in siblings
                    if _path_matches_any(
                        str(
                            getattr(s, "rfilename", None)
                            or getattr(s, "path", "")
                            or ""
                        ),
                        allow_patterns,
                    )
                )
            )
        except GatedRepoError as e:  # gated/private
            requires_auth = True
            repo_error = str(e)
        except Exception as e:
            repo_error = str(e)

    # Optional verification
    verified: Optional[bool] = None
    checked_files = 0
    if verify:
        try:
            v = await verify_model(request, model_name, path=path)  # type: ignore[arg-type]
            verified = bool(v.get("verified"))
            checked_files = int(v.get("checked_files", 0) or 0)
            # Prefer expected from verification if it produced one
            expected = int(v.get("expected_bytes", expected) or expected)
            installed = int(v.get("installed_bytes", installed) or installed)
        except Exception:
            verified = False

    # Most recent job for this model (if any)
    job_info = None
    try:
        jobs = _get_jobs_state(request.app)
        # pick latest by updated_at
        candidates = [j for j in jobs.values() if j.get("model") == model_name]
        if candidates:
            candidates.sort(
                key=lambda j: j.get("updated_at", j.get("started_at", 0)), reverse=True
            )
            job = candidates[0]
            _refresh_job_status(job)
            prog = _job_progress(job)
            job_info = {
                "id": job.get("id"),
                "status": job.get("status"),
                "pid": job.get("pid"),
                "downloaded": prog.get("downloaded"),
                "total": prog.get("total"),
                "percent": prog.get("percent"),
                "updated_at": job.get("updated_at"),
            }
    except Exception:
        job_info = None

    # Target path for convenience (first candidate root)
    target_root = dirs[0] if dirs else app_config.DEFAULT_MODELS_DIR
    target_path = target_root / model_name

    out = {
        "model": model_name,
        "repo_id": repo_id or "TODO: unsupported",
        "exists": bool(resolved),
        "path": str(resolved or target_path),
        "installed_bytes": int(installed),
        "expected_bytes": int(expected),
        "verified": verified,
        "checked_files": int(checked_files),
        "job": job_info,
        "requires_auth": requires_auth,
    }
    if repo_error:
        out["repo_error"] = repo_error
    return out


# Catalog selection and readiness with optional health checks
@router.get("/models/catalog/select")
async def catalog_select(
    capability: str,
    mode: Optional[str] = None,
    check_health: bool = False,
    timeout: float = 0.7,
):
    """Select endpoint for a capability with optional health checking."""
    catalog = load_model_catalog()
    try:
        backend, endpoint = catalog.select_endpoint(
            capability, mode=mode, check_health=check_health, timeout=timeout
        )
        return {"backend": backend, "endpoint": endpoint}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/models/catalog/readiness")
async def catalog_readiness(
    workflow: str, check_health: bool = False, timeout: float = 0.7
):
    """Aggregate readiness for a workflow with optional health checking."""
    catalog = load_model_catalog()
    status = catalog.readiness(workflow, check_health=check_health, timeout=timeout)
    return status


@router.get("/models/reveal/{model_name}")
async def reveal_model_directory(
    request: Request, model_name: str, path: Optional[str] = None
):
    """Attempt to reveal/open the model folder on the server host.

    Returns the resolved path and a best-effort 'opened' flag.
    If no GUI is available, 'opened' may be false while still returning the path.
    """
    cfg = request.app.state.config
    dirs = app_config.model_search_dirs(
        path or cfg.get("models_folder", app_config.DEFAULT_MODELS_DIR)
    )
    target = _resolve_local_model_dir(dirs, model_name)
    if not target:
        raise HTTPException(status_code=404, detail="Model not found")
    open_target = target.parent if target.is_file() else target
    opened = False
    try:
        # Best-effort: open folder via platform default handler
        if sys.platform.startswith("linux"):
            subprocess.Popen(["xdg-open", str(open_target)])
            opened = True
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(open_target)])
            opened = True
        elif os.name == "nt":
            subprocess.Popen(["explorer", str(open_target)])
            opened = True
    except Exception:
        opened = False
    return {"path": str(open_target), "opened": opened}


@router.delete("/models/{model_name}")
async def delete_model(
    request: Request, model_name: str, path: Optional[str] = None
):  # noqa: E501
    # For explicitly registered paths, delete acts as an unregister operation
    # so we do not remove arbitrary external folders/files.
    if remove_local_model_entry(model_name):
        return {"status": "unregistered"}
    cfg = request.app.state.config
    dirs = app_config.model_search_dirs(
        path or cfg.get("models_folder", app_config.DEFAULT_MODELS_DIR)
    )
    for models_dir in dirs:
        target = models_dir / model_name
        if target.exists():
            shutil.rmtree(target)
            return {"status": "deleted"}
    raise HTTPException(status_code=404, detail="Model not found")


# OpenAI Responses API Proxy Endpoints
@router.get("/responses")
async def list_responses(
    request: Request,
    model: Optional[str] = None,
    user: Optional[str] = None,
    limit: Optional[int] = None,
):
    """
    Proxy to OpenAI Responses API: list responses.
    """
    cfg = request.app.state.config
    api_key = cfg.get("api_key")
    if not api_key:
        raise HTTPException(status_code=400, detail="API key not configured")
    # Derive base URL from configured api_url (strip legacy suffixes)
    base = _responses_api_base(cfg.get("api_url"))
    url = f"{base.rstrip('/')}/responses"
    headers = {"Authorization": f"Bearer {api_key}"}
    params = {}
    if model:
        params["model"] = model
    if user:
        params["user"] = user
    if limit is not None:
        params["limit"] = limit
    resp = http_session.get(url, headers=headers, params=params, timeout=10)
    try:
        resp.raise_for_status()
    except Exception:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()


@router.get("/responses/{response_id}/completions")
async def get_response_completions(request: Request, response_id: str):
    """
    Proxy to OpenAI Responses API: get completions for a given response ID.
    """
    cfg = request.app.state.config
    api_key = cfg.get("api_key")
    if not api_key:
        raise HTTPException(status_code=400, detail="API key not configured")
    base = _responses_api_base(cfg.get("api_url"))
    url = f"{base.rstrip('/')}/responses/{response_id}/completions"
    headers = {"Authorization": f"Bearer {api_key}"}
    resp = http_session.get(url, headers=headers, timeout=10)
    try:
        resp.raise_for_status()
    except Exception:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()


@router.get("/openai/models")
async def openai_models(request: Request):
    """List models available to the configured API key (OpenAI-compatible)."""
    cfg = request.app.state.config
    api_key = cfg.get("api_key")
    if not api_key:
        raise HTTPException(status_code=400, detail="API key not configured")
    base = _responses_api_base(cfg.get("api_url"))
    headers = {"Authorization": f"Bearer {api_key}"}
    url = f"{base.rstrip('/')}/models"
    resp = http_session.get(url, headers=headers, timeout=10)
    try:
        resp.raise_for_status()
    except Exception:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    data = resp.json()
    raw = data.get("data", [])
    if not isinstance(raw, list):
        raw = []
    model_ids = sorted(
        {
            m.get("id")
            for m in raw
            if isinstance(m, dict) and isinstance(m.get("id"), str) and m.get("id")
        }
    )
    return {"models": model_ids}


# ---------------------------------------------------------------------------
# Multi-step task orchestration


class TaskStep(BaseModel):
    agent: str
    args: Optional[list[Any]] = None
    kwargs: Optional[Dict[str, Any]] = None


class TaskPlan(BaseModel):
    steps: list[TaskStep]


class ActionRevertRequest(BaseModel):
    action_ids: Optional[List[str]] = None
    response_id: Optional[str] = None
    conversation_id: Optional[str] = None
    force: bool = False


@router.get("/actions")
async def get_actions(
    request: Request,
    conversation_id: Optional[str] = Query(default=None),
    response_id: Optional[str] = Query(default=None),
    include_reverted: bool = Query(default=True),
    limit: int = Query(default=200, ge=1, le=500),
) -> dict:
    service = _get_action_history_service(request.app)
    if service is None:
        return {"actions": []}
    return {
        "actions": service.list_actions(
            conversation_id=conversation_id,
            response_id=response_id,
            include_reverted=include_reverted,
            limit=limit,
        )
    }


@router.get("/actions/{action_id}")
async def get_action_detail(request: Request, action_id: str) -> dict:
    service = _get_action_history_service(request.app)
    if service is None:
        raise HTTPException(status_code=404, detail="Action history unavailable")
    detail = service.get_action_detail(action_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Action not found")
    return {"action": detail}


@router.post("/actions/revert")
async def post_revert_actions(request: Request, payload: ActionRevertRequest) -> dict:
    service = _get_action_history_service(request.app)
    if service is None:
        raise HTTPException(status_code=404, detail="Action history unavailable")
    try:
        result = service.revert_actions(
            action_ids=payload.action_ids or None,
            response_id=payload.response_id or None,
            conversation_id=payload.conversation_id or None,
            force=payload.force,
            context=_build_action_context(
                session_id=payload.conversation_id,
                message_id=payload.response_id,
                chain_id=payload.response_id,
                request_id=_current_request_id(),
                agent_id="action-history",
                agent_label="action history",
            ),
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = 409 if "cannot revert" in detail.lower() else 400
        raise HTTPException(status_code=status_code, detail=detail)
    return result


@router.get("/agents/console")
async def get_agent_console(request: Request) -> dict:
    """Return a snapshot of the in-memory agent console state."""
    state = _ensure_agent_console_state(request.app)
    resources = state.get("resources") if isinstance(state, dict) else {}
    agents = []
    for record in state.get("agents", {}).values():
        if not isinstance(record, dict):
            continue
        events = [
            event for event in record.get("events", []) if isinstance(event, dict)
        ]
        agent_id = record.get("id")
        resource_payload = (
            resources.get(agent_id)
            if isinstance(resources, dict) and agent_id in resources
            else None
        )
        agents.append(
            {
                "id": record.get("id"),
                "label": record.get("label"),
                "status": record.get("status"),
                "summary": record.get("summary"),
                "updated_at": record.get("updated_at"),
                "events": events[-_MAX_AGENT_HISTORY:],
                "resources": resource_payload,
            }
        )
    agents.sort(key=lambda item: item.get("updated_at") or 0, reverse=True)
    actions: List[Dict[str, Any]] = []
    action_history = _get_action_history_service(request.app)
    if action_history is not None:
        try:
            actions = action_history.list_actions(limit=300)
        except Exception:
            logger.debug("Failed to load action history snapshot", exc_info=True)
    sync_reviews = _sync_reviews_snapshot(pending_limit=6, recent_limit=6)
    return {
        "agents": agents,
        "actions": actions,
        "sync_reviews": {
            "pending": sync_reviews["pending"],
            "recent": sync_reviews["recent"],
        },
    }


@router.get("/agents/resources")
async def get_agent_resources(request: Request) -> dict:
    """Return per-agent resource summaries (token usage only for now)."""
    state = _ensure_agent_console_state(request.app)
    resources = state.get("resources") if isinstance(state, dict) else {}
    payload: list[dict[str, Any]] = []
    if isinstance(resources, dict):
        for agent_id, entry in resources.items():
            if not isinstance(entry, dict):
                continue
            item = dict(entry)
            item.setdefault("agent_id", agent_id)
            payload.append(item)
    payload.sort(key=lambda item: item.get("updated_at") or 0, reverse=True)
    return {"resources": payload}


@router.get("/agents/resources/{agent_id}")
async def get_agent_resource(agent_id: str, request: Request) -> dict:
    """Return resource usage for a single agent."""
    state = _ensure_agent_console_state(request.app)
    resources = state.get("resources") if isinstance(state, dict) else {}
    entry = resources.get(agent_id) if isinstance(resources, dict) else None
    if not isinstance(entry, dict):
        return {"resource": None}
    resource = dict(entry)
    resource.setdefault("agent_id", agent_id)
    return {"resource": resource}


@router.post("/tasks/")
async def start_task(plan: TaskPlan):
    """Start a multi-step task using ``MultiAgentEngine``."""
    result = engine.plan_and_execute(
        [
            {
                "agent": step.agent,
                "args": step.args or [],
                "kwargs": step.kwargs or {},
            }
            for step in plan.steps
        ]
    )
    return {"task_id": result.id}


@router.get("/tasks/{task_id}")
async def get_task_status(task_id: str):
    """Return the status and result of a previously started task."""
    res: AsyncResult = engine.result(task_id)
    data = {"state": res.state}
    if res.ready():
        data["result"] = res.result
    return data


class VoiceConnect(BaseModel):
    identity: str
    room: str = "float"


class VoiceTtsRequest(BaseModel):
    text: str = Field(..., min_length=1)
    model: Optional[str] = None
    voice: Optional[str] = None
    audio_format: str = "wav"


@router.post("/voice/tts")
async def voice_tts(request: Request, payload: VoiceTtsRequest):
    """Synthesize speech for the supplied text."""
    cfg = request.app.state.config if request else app_config.load_config()
    try:
        result = await asyncio.to_thread(
            tts_service.synthesize,
            payload.text,
            cfg,
            model=payload.model,
            voice=payload.voice,
            audio_format=payload.audio_format or "wav",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    audio_b64 = base64.b64encode(result.audio).decode("ascii")
    return {
        "audio_b64": audio_b64,
        "content_type": result.content_type,
        "provider": result.provider,
        "model": result.model,
        "voice": result.voice,
        "sample_rate": result.sample_rate,
    }


@router.post("/voice/connect")
async def voice_connect(request: Request, payload: VoiceConnect):
    """Return connection details for the configured streaming backend."""
    svc: LiveKitService = request.app.state.livekit_service
    try:
        return svc.connect(payload.identity, payload.room)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.post("/voice/stream")
async def voice_stream(request: Request, file: UploadFile = UploadFileType(...)):
    """Process an uploaded audio chunk via the async worker (LiveKit mode)."""
    svc: LiveKitService = request.app.state.livekit_service
    if getattr(svc, "is_api_mode", False):
        raise HTTPException(
            status_code=501,
            detail="Realtime API handles streaming directly; voice uploads disabled in light mode",
        )
    data = await file.read()
    task = process_livekit_audio.delay(data)
    return {"task_id": task.id}
