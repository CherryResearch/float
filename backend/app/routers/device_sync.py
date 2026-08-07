from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import shutil
import signal
import socket
import subprocess
import textwrap
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional
from uuid import uuid4

import jwt
import requests
from app import config as app_config
from app.services.instance_sync_service import (
    SYNC_SECTION_LABELS,
    InstanceSyncService,
    RemoteFloatClient,
    _resolve_remote_urls,
)
from app.utils import deployment_event_store, sync_checkpoint_store, user_settings
from app.utils.blob_store import exists as blob_exists
from app.utils.blob_store import get_blob, put_blob
from app.utils.deployment_status import (
    build_instance_status,
    compare_software_status,
    ensure_deployment_descriptor,
    observe_data_revision,
)
from app.utils.device_registry import (
    delete_device,
    get_device,
    issue_device_token,
    list_devices,
    register_or_update_device,
    touch_device,
    update_device,
)
from app.utils.device_visibility import (
    advertised_device_access,
    candidate_device_urls,
    is_lan_binding_host,
)
from app.utils.http_client import http_session
from app.utils.rendezvous_store import accept_offer as accept_rendezvous_offer
from app.utils.rendezvous_store import create_offer as create_rendezvous_offer
from app.utils.rendezvous_store import create_session as create_rendezvous_session
from app.utils.rendezvous_store import get_offer_by_code as get_rendezvous_offer_by_code
from app.utils.sync_plan_receipt import (
    assert_sync_plan_authorized,
    decode_sync_plan_receipt,
    issue_sync_plan_receipt,
)
from app.utils.sync_review_store import create_review as create_sync_review
from app.utils.sync_review_store import get_review as get_sync_review
from app.utils.sync_review_store import list_reviews as list_sync_reviews
from app.utils.sync_review_store import update_review as update_sync_review
from app.utils.sync_store import cancel_operation as sync_cancel_operation
from app.utils.sync_store import finish_operation as sync_finish_operation
from app.utils.sync_store import get_changes_since as sync_get_changes_since
from app.utils.sync_store import get_cursor as sync_get_cursor
from app.utils.sync_store import operations_snapshot as sync_operations_snapshot
from app.utils.sync_store import record_changes as sync_record_changes
from app.utils.sync_store import (
    retire_pending_pushes_after_pull as sync_retire_pending_pushes_after_pull,
)
from app.utils.sync_store import start_operation as sync_start_operation
from app.utils.workspace_registry import (
    DEFAULT_WORKSPACE_ID,
    build_synced_workspace_profile,
    filter_workspace_ids_for_sync,
    load_workspace_state,
    normalize_workspace_ids,
    resolve_synced_workspace_location,
    summarize_workspace_profile,
    workspace_profile_map,
)
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()


@dataclass(frozen=True)
class DeviceSyncRuntime:
    """Routes-owned behavior needed by the extracted device and sync domain."""

    current_request_id: Callable[[], Optional[str]]
    get_or_create_device_public_key: Callable[[], str]
    is_local_control_request: Callable[[Request], bool]
    local_control_or_device_claims: Callable[[Request, str], Optional[Dict[str, Any]]]
    optional_device_claims: Callable[[Request, Optional[str]], Optional[Dict[str, Any]]]
    record_sync_action: Callable[..., None]
    require_local_control: Callable[[Request], None]
    require_scope: Callable[[Request, str], Dict[str, Any]]
    knowledge_rag_rehydrate: Callable[[Any], Awaitable[Dict[str, Any]]]
    knowledge_rag_payload_factory: Callable[[], Any]
    attachments_rag_rehydrate: Callable[[Any], Awaitable[Dict[str, Any]]]
    attachments_rag_payload_factory: Callable[[Optional[List[str]]], Any]
    calendar_rag_rehydrate: Callable[[Any], Awaitable[Dict[str, Any]]]
    calendar_rag_payload_factory: Callable[[], Any]


_runtime: Optional[DeviceSyncRuntime] = None


def configure_device_sync_runtime(runtime: DeviceSyncRuntime) -> None:
    """Bind aggregate-route services without importing ``app.routes`` back."""

    global _runtime
    _runtime = runtime


def _runtime_services() -> DeviceSyncRuntime:
    if _runtime is None:
        raise RuntimeError("Device and sync runtime is not configured")
    return _runtime


def _current_request_id() -> Optional[str]:
    return _runtime_services().current_request_id()


def _get_or_create_device_public_key() -> str:
    return _runtime_services().get_or_create_device_public_key()


def _is_local_control_request(request: Request) -> bool:
    return _runtime_services().is_local_control_request(request)


def _local_control_or_device_claims(
    request: Request, scope: str = "sync"
) -> Optional[Dict[str, Any]]:
    return _runtime_services().local_control_or_device_claims(request, scope)


def _optional_device_claims(
    request: Request, scope: Optional[str] = "sync"
) -> Optional[Dict[str, Any]]:
    return _runtime_services().optional_device_claims(request, scope)


def _record_sync_action(*args: Any, **kwargs: Any) -> None:
    _runtime_services().record_sync_action(*args, **kwargs)


def _require_local_control(request: Request) -> None:
    _runtime_services().require_local_control(request)


def _require_scope(request: Request, scope: str) -> Dict[str, Any]:
    return _runtime_services().require_scope(request, scope)


async def _run_knowledge_rag_rehydrate() -> Dict[str, Any]:
    runtime = _runtime_services()
    return await runtime.knowledge_rag_rehydrate(
        runtime.knowledge_rag_payload_factory()
    )


async def _run_attachments_rag_rehydrate(
    content_hashes: Optional[List[str]],
) -> Dict[str, Any]:
    runtime = _runtime_services()
    return await runtime.attachments_rag_rehydrate(
        runtime.attachments_rag_payload_factory(content_hashes)
    )


async def _run_calendar_rag_rehydrate() -> Dict[str, Any]:
    runtime = _runtime_services()
    return await runtime.calendar_rag_rehydrate(runtime.calendar_rag_payload_factory())


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


def _registered_device_scopes(record: Dict[str, Any]) -> List[str]:
    capabilities = record.get("capabilities") if isinstance(record, dict) else {}
    if not isinstance(capabilities, dict):
        return []
    requested = _normalize_sync_scopes(capabilities.get("requested_scopes"))
    if requested:
        return requested
    allowed = [
        scope for scope in SYNC_DEVICE_SCOPE_ORDER if bool(capabilities.get(scope))
    ]
    if allowed:
        return allowed
    if capabilities.get("instance_sync") or capabilities.get("paired_via_offer"):
        return ["sync"]
    return []


def _coerce_observed_software(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        key: str(value.get(key) or "").strip()
        for key in (
            "release_version",
            "build_code",
            "label",
            "source_revision",
            "snapshot_digest",
            "built_at",
        )
        if str(value.get(key) or "").strip()
    }


def _coerce_observed_data(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    try:
        workspace_count = max(0, int(value.get("workspace_count") or 0))
    except (TypeError, ValueError):
        workspace_count = 0
    result = {
        key: str(value.get(key) or "").strip()
        for key in ("deployment_id", "display_name", "state", "last_updated_at")
        if str(value.get(key) or "").strip()
    }
    result["workspace_count"] = workspace_count
    if isinstance(value.get("revision"), dict) and value.get("revision"):
        result["revision"] = dict(value["revision"])
    if isinstance(value.get("sync_checkpoint"), dict) and value.get("sync_checkpoint"):
        result["sync_checkpoint"] = dict(value["sync_checkpoint"])
    return result


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
        "remote_deployment_id": str(entry.get("remote_deployment_id") or "").strip(),
        "remote_software": _coerce_observed_software(entry.get("remote_software")),
        "remote_data": _coerce_observed_data(entry.get("remote_data")),
        "last_status_at": str(entry.get("last_status_at") or "").strip(),
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


def _saved_peer_by_id(peer_id: Any) -> Optional[Dict[str, Any]]:
    needle = str(peer_id or "").strip()
    if not needle:
        return None
    return next(
        (
            peer
            for peer in _load_saved_peers()
            if str(peer.get("id") or "").strip() == needle
        ),
        None,
    )


def _sync_ownership_summary(
    settings: Dict[str, Any],
    access: Dict[str, Any],
    saved_peers: List[Dict[str, Any]],
    operations: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    remote_url = str(settings.get("sync_remote_url") or "").strip()
    default_peer = next(
        (
            peer
            for peer in saved_peers
            if str(peer.get("remote_url") or "").strip() == remote_url
        ),
        None,
    )
    visibility = access.get("visibility") if isinstance(access, dict) else {}
    visibility = visibility if isinstance(visibility, dict) else {}
    lan_enabled = bool(
        visibility.get("lan_enabled") or settings.get("sync_visible_on_lan")
    )
    online_supported = bool(visibility.get("online_supported"))
    online_requested = bool(settings.get("sync_visible_online"))
    auto_accept_push = bool(settings.get("sync_auto_accept_push"))
    if remote_url:
        outbound_mode = "saved_peer" if default_peer else "manual_url"
    else:
        outbound_mode = "none"
    operations = operations if isinstance(operations, dict) else {}
    active_operation = operations.get("active_operation")
    if not isinstance(active_operation, dict):
        active_operation = None
    last_operation = operations.get("last_attempt")
    if not isinstance(last_operation, dict):
        last_operation = None
    background_owner = {
        "mode": "active" if active_operation else "idle",
        "active_operation_id": str((active_operation or {}).get("id") or "").strip(),
        "active_kind": str((active_operation or {}).get("kind") or "").strip(),
        "active_status": str((active_operation or {}).get("status") or "").strip(),
        "active_owner": str((active_operation or {}).get("owner") or "").strip(),
        "active_remote_url": str(
            (active_operation or {}).get("remote_url") or ""
        ).strip(),
        "cancel_requested": bool((active_operation or {}).get("cancel_requested")),
        "can_request_stop": bool(active_operation)
        and not bool((active_operation or {}).get("cancel_requested")),
        "last_operation_id": str((last_operation or {}).get("id") or "").strip(),
        "last_kind": str((last_operation or {}).get("kind") or "").strip(),
        "last_status": str((last_operation or {}).get("status") or "").strip(),
        "last_owner": str((last_operation or {}).get("owner") or "").strip(),
    }
    return {
        "private_network_only": True,
        "inbound_visibility": {
            "lan_enabled": lan_enabled,
            "lan_listening": bool(visibility.get("lan_listening")),
            "lan_state": str(visibility.get("lan_state") or "unknown"),
            "online_requested": online_requested,
            "online_supported": online_supported,
        },
        "outbound_target": {
            "mode": outbound_mode,
            "remote_url": remote_url,
            "peer_id": str(default_peer.get("id") or "").strip()
            if isinstance(default_peer, dict)
            else "",
            "peer_label": str(default_peer.get("label") or "").strip()
            if isinstance(default_peer, dict)
            else "",
        },
        "push_review_mode": "auto_accept" if auto_accept_push else "review_required",
        "saved_peer_count": len(saved_peers),
        "background_owner": background_owner,
        "auto_sync": {
            "enabled": False,
            "available": False,
            "mode": "manual_review_only",
            "reason": (
                "Automatic sync is not enabled. This device can suggest "
                "manual Check remote and Preview sync steps, but it will not "
                "schedule or apply sync automatically."
            ),
        },
        "unfinished_notice": (
            "Automatic stop-kill safeguards are future work. Stop records cancel "
            "intent and aborts the current local request where possible, but it "
            "does not kill remote work that another device already accepted."
        ),
    }


def _sync_operation_remote_label(paired_device: Optional[Dict[str, Any]]) -> str:
    pair = _coerce_saved_peer(paired_device or {}) or {}
    return str(
        pair.get("label")
        or pair.get("remote_device_name")
        or pair.get("remote_device_id")
        or ""
    ).strip()


def _begin_sync_operation(
    *,
    kind: str,
    remote_url: str,
    operation_id: Optional[str] = None,
    operation_owner: Optional[str] = None,
    paired_device: Optional[Dict[str, Any]] = None,
    sections: Optional[List[str]] = None,
    workspace_mode: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    try:
        return sync_start_operation(
            kind=kind,
            operation_id=operation_id,
            owner=operation_owner,
            remote_url=remote_url,
            remote_label=_sync_operation_remote_label(paired_device),
            sections=sections or [],
            workspace_mode=workspace_mode or "",
            request_id=_current_request_id(),
            metadata=metadata,
        )
    except Exception:
        logger.debug("Failed to record sync operation start", exc_info=True)
        return None


def _finish_sync_operation(
    operation: Optional[Dict[str, Any]],
    *,
    status: str,
    error: Optional[str] = None,
    result: Optional[Dict[str, Any]] = None,
) -> None:
    op_id = str((operation or {}).get("id") or "").strip()
    if not op_id:
        return
    try:
        sync_finish_operation(op_id, status=status, error=error, result=result)
    except Exception:
        logger.debug("Failed to record sync operation finish", exc_info=True)


def _retire_pending_pushes_after_pull(
    operation: Optional[Dict[str, Any]],
    *,
    remote_url: str,
) -> Dict[str, Any]:
    op_id = str((operation or {}).get("id") or "").strip()
    try:
        return sync_retire_pending_pushes_after_pull(
            remote_url=remote_url,
            pull_operation_id=op_id or None,
        )
    except Exception:
        logger.debug(
            "Failed to retire pending push operations after pull", exc_info=True
        )
        return {"count": 0, "operation_ids": []}


def _sync_operations_overview() -> Dict[str, Any]:
    try:
        return sync_operations_snapshot()
    except Exception:
        logger.debug("Failed to read sync operation telemetry", exc_info=True)
        return {"active_operation": None, "last_attempt": None, "recent": []}


def _sync_suggestion_explanation(
    *,
    title: str,
    summary: str,
    rows: List[Dict[str, str]],
) -> Dict[str, Any]:
    return {
        "title": title,
        "summary": summary,
        "rows": [
            {
                "label": str(row.get("label") or "").strip(),
                "value": str(row.get("value") or "").strip(),
            }
            for row in rows
            if str(row.get("label") or "").strip()
            and str(row.get("value") or "").strip()
        ],
    }


def _sync_suggestion(
    *,
    suggestion_id: str,
    title: str,
    summary: str,
    action: str,
    action_label: str,
    priority: int,
    severity: str = "info",
    peer: Optional[Dict[str, Any]] = None,
    next_step: str = "",
    requirements: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    peer = peer if isinstance(peer, dict) else {}
    remote_url = str(peer.get("remote_url") or "").strip()
    peer_label = str(peer.get("label") or peer.get("remote_device_name") or "").strip()
    rows = [
        {"label": "Source", "value": "/api/sync/overview.sync_suggestions"},
        {"label": "Action", "value": action_label},
        {"label": "Automatic sync", "value": "Off"},
        {"label": "Review", "value": "Manual approval required"},
        {"label": "Peer", "value": peer_label},
        {"label": "Remote", "value": remote_url},
        {"label": "Next", "value": next_step or summary},
    ]
    return {
        "id": suggestion_id,
        "title": title,
        "summary": summary,
        "severity": severity,
        "priority": priority,
        "action": action,
        "action_label": action_label,
        "next_step": next_step or summary,
        "peer_id": str(peer.get("id") or "").strip(),
        "peer_label": peer_label,
        "remote_url": remote_url,
        "manual_review_required": True,
        "auto_sync_enabled": False,
        "auto_sync_available": False,
        "requirements": requirements or [],
        "state_explanation": _sync_suggestion_explanation(
            title=f"Why {title} is suggested",
            summary=summary,
            rows=rows,
        ),
    }


def _sync_saved_peer_ready(peer: Dict[str, Any]) -> bool:
    scopes = _normalize_sync_scopes(peer.get("scopes"))
    has_sync_scope = "sync" in scopes or not scopes
    has_identity = bool(str(peer.get("remote_public_key") or "").strip())
    local_workspaces = normalize_workspace_ids(peer.get("local_workspace_ids")) or []
    remote_workspaces = [
        str(item).strip()
        for item in (peer.get("remote_workspace_ids") or [])
        if str(item or "").strip()
    ]
    workspace_mode = str(peer.get("workspace_mode") or "").strip().lower()
    if workspace_mode == "import":
        has_workspace_mapping = bool(local_workspaces and remote_workspaces)
    else:
        has_workspace_mapping = bool(local_workspaces)
    return has_sync_scope and has_identity and has_workspace_mapping


def _sync_saved_peer_requirements(peer: Dict[str, Any]) -> List[Dict[str, str]]:
    scopes = _normalize_sync_scopes(peer.get("scopes"))
    has_sync_scope = "sync" in scopes or not scopes
    has_identity = bool(str(peer.get("remote_public_key") or "").strip())
    local_workspaces = normalize_workspace_ids(peer.get("local_workspace_ids")) or []
    remote_workspaces = [
        str(item).strip()
        for item in (peer.get("remote_workspace_ids") or [])
        if str(item or "").strip()
    ]
    workspace_mode = str(peer.get("workspace_mode") or "").strip().lower()
    if workspace_mode == "import":
        workspace_ready = bool(local_workspaces and remote_workspaces)
        workspace_detail = (
            "Local and remote targets selected"
            if workspace_ready
            else "Choose one local and one remote import target"
        )
    else:
        workspace_ready = bool(local_workspaces)
        workspace_detail = (
            "Local sync workspaces selected"
            if workspace_ready
            else "Choose local workspaces for preview/apply"
        )
    return [
        {
            "label": "Sync scope",
            "status": "ready" if has_sync_scope else "missing",
            "detail": "Sync allowed" if has_sync_scope else "Add the sync scope",
        },
        {
            "label": "Stable identity",
            "status": "ready" if has_identity else "needs_check",
            "detail": (
                "Remote fingerprint saved"
                if has_identity
                else "Run Check remote or Refresh trust to save the fingerprint"
            ),
        },
        {
            "label": "Workspace mapping",
            "status": "ready" if workspace_ready else "missing",
            "detail": workspace_detail,
        },
        {
            "label": "Automatic sync",
            "status": "off",
            "detail": "Only suggestions are shown; nothing runs automatically",
        },
    ]


def _sync_overview_suggestions(
    *,
    access: Dict[str, Any],
    saved_peers: List[Dict[str, Any]],
    operations: Dict[str, Any],
    sync_reviews: Dict[str, Any],
) -> List[Dict[str, Any]]:
    suggestions: List[Dict[str, Any]] = []
    active_operation = operations.get("active_operation")
    if isinstance(active_operation, dict):
        suggestions.append(
            _sync_suggestion(
                suggestion_id=(
                    f"active-sync-{str(active_operation.get('id') or 'operation')}"
                ),
                title="Sync request is still active",
                summary=(
                    "A local sync request is still owned by this device. Stop "
                    "records cancel intent; it does not claim remote rollback."
                ),
                action="wait_or_stop",
                action_label="Wait or stop",
                priority=5,
                severity="warning",
                peer={
                    "label": active_operation.get("remote_label") or "",
                    "remote_url": active_operation.get("remote_url") or "",
                },
                next_step=(
                    "Wait for it to finish, or use Stop if you want to abort "
                    "the current local request wait."
                ),
            )
        )

    pending_reviews = (
        sync_reviews.get("pending") if isinstance(sync_reviews, dict) else []
    )
    pending_count = len(pending_reviews) if isinstance(pending_reviews, list) else 0
    if pending_count:
        suggestions.append(
            _sync_suggestion(
                suggestion_id="review-inbound-sync",
                title="Review inbound sync",
                summary=(
                    f"{pending_count} inbound push review"
                    f"{'' if pending_count == 1 else 's'} need a decision."
                ),
                action="review_inbound",
                action_label="Review pending push",
                priority=10,
                severity="warning",
                next_step=(
                    "Open the pending sync review and approve only the sections "
                    "you want this device to accept."
                ),
            )
        )

    for peer in saved_peers:
        if not isinstance(peer, dict):
            continue
        requirements = _sync_saved_peer_requirements(peer)
        peer_id = str(peer.get("id") or "peer").strip()
        peer_label = str(peer.get("label") or peer.get("remote_url") or "peer").strip()
        if _sync_saved_peer_ready(peer):
            suggestions.append(
                _sync_suggestion(
                    suggestion_id=f"ready-reviewed-sync-{peer_id}",
                    title=f"{peer_label} is ready for reviewed sync",
                    summary=(
                        "Auto-sync is still off. This pair has sync scope, a "
                        "saved fingerprint, and workspace mapping, so the next "
                        "safe step is Check remote, then Preview."
                    ),
                    action="check_then_preview",
                    action_label="Check remote, then preview",
                    priority=30,
                    severity="info",
                    peer=peer,
                    requirements=requirements,
                    next_step=(
                        "Check remote to confirm reachability, then Preview "
                        "sync before pulling or pushing selected sections."
                    ),
                )
            )
        else:
            suggestions.append(
                _sync_suggestion(
                    suggestion_id=f"prepare-reviewed-sync-{peer_id}",
                    title=f"Prepare {peer_label} for reviewed sync",
                    summary=(
                        "This saved pair is not ready for low-friction manual "
                        "sync yet. Complete the missing trust, scope, or "
                        "workspace requirement first."
                    ),
                    action="complete_pair_setup",
                    action_label="Complete pair setup",
                    priority=35,
                    severity="warning",
                    peer=peer,
                    requirements=requirements,
                    next_step=(
                        "Refresh trust or edit the pair until sync scope, "
                        "fingerprint, and workspace mapping are ready."
                    ),
                )
            )

    if not saved_peers:
        suggestions.append(
            _sync_suggestion(
                suggestion_id="pair-a-device",
                title="Pair a device before syncing",
                summary=(
                    "No saved sync peers exist. Pairing stores a stable remote "
                    "identity so later URL changes can be verified safely."
                ),
                action="pair_device",
                action_label="Pair a device",
                priority=40,
                severity="info",
                next_step=(
                    "Enable private-network visibility if needed, then pair the "
                    "other Float instance before previewing data movement."
                ),
            )
        )

    visibility = access.get("visibility") if isinstance(access, dict) else {}
    visibility = visibility if isinstance(visibility, dict) else {}
    if not bool(visibility.get("lan_enabled")):
        suggestions.append(
            _sync_suggestion(
                suggestion_id="enable-lan-visibility",
                title="LAN visibility is off",
                summary=(
                    "This device is local-only right now. Pairing and inbound "
                    "private-network sync are easier after LAN visibility is on."
                ),
                action="enable_lan_visibility",
                action_label="Enable LAN visibility",
                priority=50,
                severity="info",
                next_step=(
                    "Turn on LAN visibility when you want another local device "
                    "to discover or reach this Float instance."
                ),
            )
        )

    return sorted(
        suggestions,
        key=lambda item: (
            int(item.get("priority") or 100),
            str(item.get("id") or ""),
        ),
    )


def _workspace_state_summary(
    settings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    profiles, active_workspace_id, selected_workspace_ids = load_workspace_state(
        settings
    )
    deployment_id = str(
        ensure_deployment_descriptor().get("deployment_id") or ""
    ).strip()
    return {
        "profiles": [
            summarize_workspace_profile(profile, deployment_id=deployment_id)
            for profile in profiles
        ],
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
    remote_deployment_id = str(
        (paired_device or {}).get("remote_deployment_id") or ""
    ).strip()
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
        upstream_deployment_id = str(
            profile.get("upstream_deployment_id") or ""
        ).strip()
        if (
            (
                remote_deployment_id
                and upstream_deployment_id
                and upstream_deployment_id == remote_deployment_id
            )
            or (peer_id and source_peer_id and source_peer_id == peer_id)
            or (
                remote_name and source_device_name and source_device_name == remote_name
            )
        ):
            ignored.append(workspace_id)
            continue
        filtered.append(workspace_id)
    return filtered, ignored


def _ignored_local_workspace_detail(
    recursive_ignored_ids: List[str], privacy_ignored_ids: List[str]
) -> str:
    if recursive_ignored_ids and privacy_ignored_ids:
        return (
            "All selected local workspaces were ignored to avoid syncing a "
            "workspace back to its source device or because of workspace privacy "
            "settings."
        )
    if recursive_ignored_ids:
        return (
            "All selected local workspaces were ignored to avoid syncing a "
            "workspace back to its source device."
        )
    return "All selected local workspaces were ignored by workspace privacy settings."


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
        else:
            next_profiles.append(existing)
    if not replaced and str(profile.get("id") or "").strip():
        next_profiles.append(profile)
    user_settings.save_settings(
        {
            "workspace_profiles": next_profiles,
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
    update_default_remote = False
    settings = user_settings.load_settings()
    current_default_remote = str(settings.get("sync_remote_url") or "").strip()
    next_peers: List[Dict[str, Any]] = []
    for peer in peers:
        if str(peer.get("id") or "").strip() == peer_id:
            next_peers.append({**peer, **next_peer})
            if current_default_remote == str(peer.get("remote_url") or "").strip():
                update_default_remote = True
            updated = True
        else:
            next_peers.append(peer)
    if not updated:
        return None
    updates: Dict[str, Any] = {"sync_saved_peers": next_peers}
    if update_default_remote:
        updates["sync_remote_url"] = next_peer["remote_url"]
    user_settings.save_settings(updates)
    return next_peer


def _remove_saved_peer_state(peer_id: str) -> None:
    needle = str(peer_id or "").strip()
    if not needle:
        return
    existing_peers = _load_saved_peers()
    removed = next(
        (
            peer
            for peer in existing_peers
            if str(peer.get("id") or "").strip() == needle
        ),
        None,
    )
    peers = [
        peer for peer in existing_peers if str(peer.get("id") or "").strip() != needle
    ]
    updates: Dict[str, Any] = {"sync_saved_peers": peers}
    settings = user_settings.load_settings()
    if (
        removed
        and str(settings.get("sync_remote_url") or "").strip()
        == str(removed.get("remote_url") or "").strip()
    ):
        updates["sync_remote_url"] = ""
    user_settings.save_settings(updates)


def _candidate_urls_for_request(request: Optional[Request]) -> List[str]:
    return candidate_device_urls(request)


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
    browser_shaped = _looks_like_legacy_browser_name(name)
    paired_via_offer = capabilities.get("paired_via_offer") is True
    legacy_record = not paired_via_offer
    status = "unverified_legacy_record" if legacy_record else "paired_device"
    status_label = "Unverified legacy record" if legacy_record else "Paired device"
    last_seen = float(record.get("last_seen") or 0)
    return {
        "id": str(device_id),
        "name": name,
        "public_key": record.get("public_key"),
        "capabilities": capabilities,
        "created_at": float(record.get("created_at") or 0),
        "last_seen": last_seen,
        "status": status,
        "status_label": status_label,
        "legacy_browser_record": browser_shaped,
        "legacy_record": legacy_record,
        "trust_provenance": "pairing_offer"
        if paired_via_offer
        else "legacy_unverified",
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


def _peer_connectivity_status(
    remote_url: str, paired_device: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    pairing = _coerce_saved_peer(paired_device or {})
    if pairing is not None:
        settings = user_settings.load_settings()
        remote = RemoteFloatClient(
            remote_url,
            paired_device=pairing,
            device_name=str(settings.get("device_display_name") or "").strip()
            or socket.gethostname(),
            timeout=8,
        )
        overview = remote.get_sync_overview()
        instance_base = remote.instance_base
    else:
        urls = _resolve_remote_urls(remote_url)
        response = http_session.get(
            f"{urls['instance_base']}/health", timeout=8, allow_redirects=False
        )
        if 300 <= int(getattr(response, "status_code", 0) or 0) < 400:
            raise requests.HTTPError(
                "Remote redirects are not allowed", response=response
            )
        response.raise_for_status()
        overview = {}
        instance_base = urls["instance_base"]
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
    public_key = str(current.get("public_key") or "").strip()
    remote_deployment_status = (
        overview.get("deployment_status")
        if isinstance(overview.get("deployment_status"), dict)
        else {}
    )
    remote_software = (
        remote_deployment_status.get("software")
        if isinstance(remote_deployment_status.get("software"), dict)
        else current.get("software")
        if isinstance(current.get("software"), dict)
        else {}
    )
    local_software = build_instance_status(
        settings=user_settings.load_settings(), register_machine=False
    )["software"]
    return {
        "reachable": True,
        "instance_base": instance_base,
        "display_name": str(current.get("display_name") or "").strip(),
        "hostname": str(current.get("hostname") or "").strip(),
        "public_key": public_key,
        "source_namespace": str(current.get("source_namespace") or "").strip(),
        "deployment_status": remote_deployment_status,
        "software_comparison": compare_software_status(local_software, remote_software),
        "identity": {
            "public_key": public_key,
            "deployment_id": str(current.get("deployment_id") or "").strip(),
            "display_name": str(current.get("display_name") or "").strip(),
            "hostname": str(current.get("hostname") or "").strip(),
            "source_namespace": str(current.get("source_namespace") or "").strip(),
            "software": remote_software,
        },
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
        "inbound_devices": (
            overview.get("inbound_devices")
            if isinstance(overview.get("inbound_devices"), list)
            else []
        ),
    }


def _remote_identity_from_overview(overview: Dict[str, Any]) -> Dict[str, Any]:
    current = (
        overview.get("current_device")
        if isinstance(overview.get("current_device"), dict)
        else {}
    )
    return {
        "public_key": str(current.get("public_key") or "").strip(),
        "deployment_id": str(current.get("deployment_id") or "").strip(),
        "display_name": str(current.get("display_name") or "").strip(),
        "hostname": str(current.get("hostname") or "").strip(),
        "source_namespace": str(current.get("source_namespace") or "").strip(),
        "software": current.get("software")
        if isinstance(current.get("software"), dict)
        else {},
    }


def _annotate_peer_identity(
    status: Dict[str, Any],
    pairing: Optional[Dict[str, Any]],
    *,
    strict: bool = False,
) -> Dict[str, Any]:
    pair = _coerce_saved_peer(pairing or {}) if isinstance(pairing, dict) else None
    identity = (
        status.get("identity") if isinstance(status.get("identity"), dict) else {}
    )
    observed_key = str(
        identity.get("public_key") or status.get("public_key") or ""
    ).strip()
    expected_key = str((pair or {}).get("remote_public_key") or "").strip()
    annotated = {
        **status,
        "identity": {
            **identity,
            "public_key": observed_key,
        },
        "identity_verified": False,
        "identity_state": "unpaired",
        "identity_warning": "",
    }
    if pair is None:
        return annotated
    if not expected_key:
        remote_device_id = str(pair.get("remote_device_id") or "").strip()
        local_public_key = str(pair.get("public_key") or "").strip()
        # A friendly local label is editable and is not remote identity evidence.
        expected_label = str(pair.get("remote_device_name") or "").strip().lower()
        observed_labels = {
            str(identity.get("display_name") or status.get("display_name") or "")
            .strip()
            .lower(),
            str(identity.get("hostname") or status.get("hostname") or "")
            .strip()
            .lower(),
            str(
                identity.get("source_namespace") or status.get("source_namespace") or ""
            )
            .strip()
            .lower(),
        }
        label_matches = not expected_label or expected_label in observed_labels
        inbound_devices = (
            status.get("inbound_devices")
            if isinstance(status.get("inbound_devices"), list)
            else []
        )
        for device in inbound_devices:
            if not isinstance(device, dict):
                continue
            if str(device.get("id") or "").strip() != remote_device_id:
                continue
            if not label_matches:
                annotated["identity_state"] = "label_mismatch"
                annotated[
                    "identity_warning"
                ] = "The remote recognized this saved pair's local device, but its advertised identity label does not match the saved peer. Pair it as a separate Float instance."
                return annotated
            if local_public_key and secrets.compare_digest(
                str(device.get("public_key") or "").strip(), local_public_key
            ):
                annotated["identity_verified"] = bool(observed_key)
                annotated["identity_state"] = (
                    "verified" if observed_key else "missing_remote_identity"
                )
                annotated["identity_anchor_source"] = "remote_registered_device"
                if not observed_key:
                    annotated[
                        "identity_warning"
                    ] = "The remote recognized this saved pair, but did not report its own stable device identity."
                    if strict:
                        raise HTTPException(
                            status_code=409,
                            detail=annotated["identity_warning"],
                        )
                return annotated
        annotated["identity_state"] = "unanchored"
        annotated[
            "identity_warning"
        ] = "This saved peer does not have a stable remote identity yet. Refresh trust or pair again before treating a moved URL as the same device."
        return annotated
    if not observed_key:
        annotated["identity_state"] = "missing_remote_identity"
        annotated[
            "identity_warning"
        ] = "The remote responded, but it did not report a stable device identity."
        if strict:
            raise HTTPException(
                status_code=409,
                detail=annotated["identity_warning"],
            )
        return annotated
    if secrets.compare_digest(expected_key, observed_key):
        annotated["identity_verified"] = True
        annotated["identity_state"] = "verified"
        return annotated
    annotated["identity_state"] = "mismatch"
    annotated[
        "identity_warning"
    ] = "The remote URL responded, but its stable device identity does not match this saved pair. Pair it as a separate Float instance."
    if strict:
        raise HTTPException(
            status_code=409,
            detail=annotated["identity_warning"],
        )
    return annotated


def _annotate_peer_identity_from_overview(
    overview: Dict[str, Any],
    pairing: Optional[Dict[str, Any]],
    *,
    strict: bool = False,
) -> Dict[str, Any]:
    return _annotate_peer_identity(
        {
            "identity": _remote_identity_from_overview(overview),
            "inbound_devices": (
                overview.get("inbound_devices")
                if isinstance(overview.get("inbound_devices"), list)
                else []
            ),
        },
        pairing,
        strict=strict,
    )


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
async def devices_register(request: Request, payload: DeviceRegisterPayload):
    _require_local_control(request)
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
    public_key: Optional[str] = None


@router.post("/devices/token")
async def devices_token(payload: DeviceTokenRequest, request: Request):
    record = get_device(payload.device_id)
    if not record:
        raise HTTPException(status_code=404, detail="Device not found")
    claims = _optional_device_claims(request, scope=None)
    if claims is not None:
        if str(claims.get("sub") or "").strip() != str(payload.device_id).strip():
            raise HTTPException(
                status_code=403, detail="Device token can only refresh itself"
            )
    else:
        expected_key = str(record.get("public_key") or "").strip()
        supplied_key = str(payload.public_key or "").strip()
        if not expected_key or not secrets.compare_digest(expected_key, supplied_key):
            raise HTTPException(
                status_code=403, detail="Device proof required for token issuance"
            )
    requested_scopes = _normalize_sync_scopes(payload.scopes) or ["sync"]
    allowed_scopes = _registered_device_scopes(record)
    scopes = [scope for scope in requested_scopes if scope in allowed_scopes]
    if not scopes:
        raise HTTPException(status_code=403, detail="Requested scopes are not allowed")
    touch_device(payload.device_id)
    token = issue_device_token(payload.device_id, scopes, payload.ttl_seconds or 3600)
    return {"token": token}


@router.get("/devices")
async def devices_list(request: Request):
    _require_local_control(request)
    return {"devices": list_devices()}


class DeviceUpdatePayload(BaseModel):
    name: Optional[str] = None
    capabilities: Optional[Dict[str, Any]] = None


@router.patch("/devices/{device_id}")
async def devices_update(
    device_id: str, payload: DeviceUpdatePayload, request: Request
):
    claims = _local_control_or_device_claims(request, "sync")
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
    claims = _local_control_or_device_claims(request, "sync")
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
async def devices_prune_legacy(request: Request):
    _require_local_control(request)
    removed = 0
    for device_id, record in (list_devices() or {}).items():
        if not isinstance(record, dict):
            continue
        summary = _summarize_inbound_device(str(device_id), record)
        if not summary["legacy_record"]:
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
    paired_device: Optional[Dict[str, Any]] = None
    update_saved_peer: bool = False
    operation_id: Optional[str] = None
    operation_owner: Optional[str] = None


MOBILE_FLOAT_DEFAULT_SERVE_PORT = 64345


class MobileFloatServePayload(BaseModel):
    serve_port: int = MOBILE_FLOAT_DEFAULT_SERVE_PORT


class LanVisibilityPayload(BaseModel):
    enabled: bool
    restart: bool = True


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
    _require_local_control(request)
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
    pending_offer = get_rendezvous_offer_by_code(payload.code)
    if pending_offer is None:
        raise HTTPException(
            status_code=400, detail="Pairing offer was not found or has expired"
        )
    offered_scopes = _normalize_sync_scopes(pending_offer.get("requested_scopes")) or [
        "sync"
    ]
    requested_scopes = _normalize_sync_scopes(payload.requested_scopes)
    scopes = [
        scope
        for scope in (requested_scopes or offered_scopes)
        if scope in offered_scopes
    ]
    if not scopes:
        raise HTTPException(
            status_code=403,
            detail="Requested scopes are not included in this pairing offer",
        )
    try:
        offer = accept_rendezvous_offer(
            payload.code,
            device_name=payload.device_name,
            public_key=payload.public_key,
            candidate_urls=payload.candidate_urls
            or _candidate_urls_for_request(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
            allow_redirects=False,
        )
        if 300 <= int(getattr(response, "status_code", 0) or 0) < 400:
            raise requests.HTTPError(
                "Remote redirects are not allowed", response=response
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
    pairing = None
    saved_pairing = None
    raw_paired_device = (
        payload.paired_device if isinstance(payload.paired_device, dict) else None
    )
    explicit_peer_id = str((raw_paired_device or {}).get("id") or "").strip()
    if payload.update_saved_peer and not explicit_peer_id:
        raise HTTPException(
            status_code=400,
            detail="Saved peer id is required to update a moved address.",
        )
    if raw_paired_device is not None:
        saved_pairing = _saved_peer_by_id(explicit_peer_id)
        if saved_pairing is not None:
            pairing = saved_pairing
        elif payload.update_saved_peer:
            raise HTTPException(
                status_code=404,
                detail="Saved peer no longer exists. Refresh the device list and try again.",
            )
        else:
            requested_pairing = _coerce_saved_peer(raw_paired_device)
            if requested_pairing is None:
                raise HTTPException(
                    status_code=400, detail="Saved peer payload is invalid."
                )
            pairing = requested_pairing
    operation = _begin_sync_operation(
        kind="check",
        remote_url=payload.remote_url,
        operation_id=payload.operation_id,
        operation_owner=payload.operation_owner,
        paired_device=pairing,
        sections=[],
        workspace_mode="",
        metadata={"update_saved_peer": bool(payload.update_saved_peer)},
    )
    try:
        status = _peer_connectivity_status(payload.remote_url, pairing)
        status = _annotate_peer_identity(status, pairing)
        observed_pair = None
        if saved_pairing is not None and status.get("identity_verified"):
            remote_status = (
                status.get("deployment_status")
                if isinstance(status.get("deployment_status"), dict)
                else {}
            )
            remote_data = (
                remote_status.get("data")
                if isinstance(remote_status.get("data"), dict)
                else {}
            )
            remote_software = (
                remote_status.get("software")
                if isinstance(remote_status.get("software"), dict)
                else (status.get("identity") or {}).get("software")
            )
            observed_pair = {
                **saved_pairing,
                "remote_public_key": (
                    (status.get("identity") or {}).get("public_key")
                    or saved_pairing.get("remote_public_key")
                    or ""
                ),
                "remote_device_name": status.get("display_name")
                or status.get("hostname")
                or saved_pairing.get("remote_device_name")
                or "",
                "remote_deployment_id": (
                    remote_data.get("deployment_id")
                    or (status.get("identity") or {}).get("deployment_id")
                    or ""
                ),
                "remote_software": _coerce_observed_software(remote_software),
                "remote_data": _coerce_observed_data(remote_data),
                "last_status_at": datetime.now(tz=timezone.utc).isoformat(),
            }
        if payload.update_saved_peer:
            if pairing is None:
                raise HTTPException(
                    status_code=400,
                    detail="Saved peer payload is required to update a moved URL.",
                )
            if not status.get("identity_verified"):
                raise HTTPException(
                    status_code=409,
                    detail=status.get("identity_warning")
                    or "Remote identity was not verified.",
                )
            next_pair = {
                **(observed_pair or pairing),
                "remote_url": status["instance_base"],
                "remote_public_key": (
                    (status.get("identity") or {}).get("public_key")
                    or pairing.get("remote_public_key")
                    or ""
                ),
                "remote_device_name": status.get("display_name")
                or status.get("hostname")
                or pairing.get("remote_device_name")
                or "",
            }
            persisted = _persist_saved_peer_state(
                next_pair,
                remote_label=next_pair.get("remote_device_name"),
            )
            if persisted is None:
                raise HTTPException(
                    status_code=409,
                    detail="Saved peer was removed while its address was being verified. Refresh and try again.",
                )
            status["paired_device"] = persisted
        elif observed_pair and (
            str(status.get("instance_base") or "").strip()
            == str(saved_pairing.get("remote_url") or "").strip()
        ):
            persisted_observation = _persist_saved_peer_state(
                observed_pair,
                remote_label=observed_pair.get("remote_device_name"),
            )
            if persisted_observation:
                status["observed_peer"] = persisted_observation
        _finish_sync_operation(
            operation,
            status="completed",
            result={
                "reachable": bool(status.get("reachable")),
                "identity_verified": bool(status.get("identity_verified")),
            },
        )
        return status
    except HTTPException as exc:
        _finish_sync_operation(operation, status="failed", error=str(exc.detail))
        raise
    except requests.RequestException as exc:
        _finish_sync_operation(operation, status="failed", error=str(exc))
        _log_remote_sync_failure(
            "sync_peer_status",
            remote_url=payload.remote_url,
            exc=exc,
        )
        raise HTTPException(
            status_code=502, detail=f"Remote status check failed: {exc}"
        )
    except ValueError as exc:
        _finish_sync_operation(operation, status="failed", error=str(exc))
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
        remote_overview = remote.get_sync_overview()
        identity_status = _annotate_peer_identity_from_overview(
            remote_overview,
            pairing,
            strict=True,
        )
        updated_pair = remote.sync_device_registration()
        remote_identity = identity_status.get("identity") or {}
        if remote_identity.get("public_key"):
            updated_pair["remote_public_key"] = str(
                remote_identity.get("public_key") or ""
            ).strip()
        if remote_identity.get("display_name") or remote_identity.get("hostname"):
            updated_pair["remote_device_name"] = str(
                remote_identity.get("display_name")
                or remote_identity.get("hostname")
                or ""
            ).strip()
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
    operation_id: Optional[str] = None
    operation_owner: Optional[str] = None


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
    operation_id: Optional[str] = None
    operation_owner: Optional[str] = None
    plan_receipt: Optional[str] = None


def _sync_service() -> InstanceSyncService:
    return InstanceSyncService()


def _sync_manifest_section_digests(
    manifest: Dict[str, Any], sections: List[str]
) -> Dict[str, str]:
    section_map = manifest.get("sections") if isinstance(manifest, dict) else {}
    section_map = section_map if isinstance(section_map, dict) else {}
    return {
        section: hashlib.sha256(
            json.dumps(
                section_map.get(section),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        for section in sections
    }


def _sync_plan_allowed_selections(
    sections: List[Dict[str, Any]], direction: str
) -> Dict[str, List[str]]:
    actionable = (
        {
            "only_remote",
            "remote_new",
            "remote_newer",
            "only_local",
            "local_new",
            "local_deleted",
            "remote_deleted",
            "known_difference",
        }
        if direction == "pull"
        else {
            "only_local",
            "local_new",
            "local_newer",
            "only_remote",
            "remote_new",
            "local_deleted",
            "remote_deleted",
            "known_difference",
        }
    )
    allowed: Dict[str, List[str]] = {}
    for section in sections:
        section_key = str(section.get("key") or "").strip()
        if not section_key:
            continue
        raw_items = section.get("all_items") or section.get("items") or []
        item_ids = sorted(
            {
                str(item.get("selection_id") or item.get("resource_id") or "").strip()
                for item in raw_items
                if isinstance(item, dict)
                and str(item.get("status") or "").strip().lower() in actionable
                and str(
                    item.get("selection_id") or item.get("resource_id") or ""
                ).strip()
            }
        )
        if item_ids:
            allowed[section_key] = item_ids
    return allowed


def _manifest_data_revision(
    manifest: Optional[Dict[str, Any]],
    sections: Optional[List[str]] = None,
) -> Dict[str, Any]:
    payload = manifest if isinstance(manifest, dict) else {}
    revision = payload.get("data_revision")
    if isinstance(revision, dict) and revision.get("digest"):
        return dict(revision)
    return sync_checkpoint_store.manifest_revision(payload, sections)


def _observe_current_data_revision(service: InstanceSyncService) -> Dict[str, Any]:
    manifest = service.build_manifest(None)
    return observe_data_revision(_manifest_data_revision(manifest))


def _content_free_revision(manifest: Optional[Dict[str, Any]]) -> Dict[str, str]:
    if not isinstance(manifest, dict) or not manifest:
        return {}
    revision = _manifest_data_revision(manifest)
    return {
        key: str(revision.get(key) or "").strip()
        for key in ("code", "digest")
        if str(revision.get(key) or "").strip()
    }


def _manifest_section_count(manifest: Optional[Dict[str, Any]], section: str) -> int:
    payload = manifest if isinstance(manifest, dict) else {}
    section_map = payload.get("sections")
    section_map = section_map if isinstance(section_map, dict) else {}
    section_payload = section_map.get(section)
    section_payload = section_payload if isinstance(section_payload, dict) else {}
    try:
        return max(0, int(section_payload.get("count") or 0))
    except (TypeError, ValueError):
        return 0


def _sync_event_counts(
    *,
    sections: List[str],
    local_before_manifest: Optional[Dict[str, Any]],
    local_after_manifest: Optional[Dict[str, Any]],
    peer_before_manifest: Optional[Dict[str, Any]],
    peer_after_manifest: Optional[Dict[str, Any]],
    item_selections: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, int]:
    counts: Dict[str, int] = {"section_count": len(sections)}
    local_before_total = 0
    local_after_total = 0
    peer_before_total = 0
    peer_after_total = 0
    for section in sections:
        local_before = _manifest_section_count(local_before_manifest, section)
        local_after = _manifest_section_count(local_after_manifest, section)
        peer_before = _manifest_section_count(peer_before_manifest, section)
        peer_after = _manifest_section_count(peer_after_manifest, section)
        counts[f"local_before_{section}"] = local_before
        counts[f"local_after_{section}"] = local_after
        counts[f"peer_before_{section}"] = peer_before
        counts[f"peer_after_{section}"] = peer_after
        local_before_total += local_before
        local_after_total += local_after
        peer_before_total += peer_before
        peer_after_total += peer_after
    counts.update(
        {
            "before_count": local_before_total,
            "after_count": local_after_total,
            "peer_before_count": peer_before_total,
            "peer_after_count": peer_after_total,
        }
    )
    selected_count = sum(
        len(values or []) for values in (item_selections or {}).values()
    )
    if selected_count:
        counts["selected_item_count"] = selected_count
        counts["changed_count"] = selected_count
    return counts


def _workspace_lineages_for_event(
    *, deployment_id: str, workspace_ids: List[str]
) -> List[str]:
    try:
        profiles, _active_id, _selected_ids = load_workspace_state(
            user_settings.load_settings()
        )
        requested = set(workspace_ids)
        return sorted(
            {
                str(
                    summarize_workspace_profile(
                        profile,
                        deployment_id=deployment_id,
                    ).get("lineage_id")
                    or ""
                ).strip()
                for profile in profiles
                if not requested or str(profile.get("id") or "").strip() in requested
            }
            - {""}
        )
    except Exception:
        logger.debug(
            "Failed to resolve workspace lineages for sync event", exc_info=True
        )
        return []


def _record_deployment_sync_event(
    *,
    service: InstanceSyncService,
    direction: str,
    sections: List[str],
    peer_deployment_id: str,
    operation_id: str = "",
    event_id: str = "",
    origin_event_id: str = "",
    status: str = "completed",
    workspace_ids: Optional[List[str]] = None,
    item_selections: Optional[Dict[str, List[str]]] = None,
    local_before_manifest: Optional[Dict[str, Any]] = None,
    local_after_manifest: Optional[Dict[str, Any]] = None,
    peer_before_manifest: Optional[Dict[str, Any]] = None,
    peer_after_manifest: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    try:
        deployment_id = str(
            service.current_instance_identity().get("deployment_id") or ""
        ).strip()
        event = deployment_event_store.record_event(
            event_type="data.sync",
            status=status,
            deployment_id=deployment_id,
            event_id=event_id or None,
            peer_deployment_id=peer_deployment_id,
            workspace_lineage_ids=_workspace_lineages_for_event(
                deployment_id=deployment_id,
                workspace_ids=list(workspace_ids or []),
            ),
            direction=direction,
            operation_id=operation_id,
            origin_event_id=origin_event_id,
            sections=sections,
            counts=_sync_event_counts(
                sections=sections,
                local_before_manifest=local_before_manifest,
                local_after_manifest=local_after_manifest,
                peer_before_manifest=peer_before_manifest,
                peer_after_manifest=peer_after_manifest,
                item_selections=item_selections,
            ),
            local_revision_before=_content_free_revision(local_before_manifest),
            local_revision_after=_content_free_revision(local_after_manifest),
            peer_revision_before=_content_free_revision(peer_before_manifest),
            peer_revision_after=_content_free_revision(peer_after_manifest),
        )
        return {
            "status": "recorded",
            "event_id": event["event_id"],
            "event_hash": event["event_hash"],
        }
    except Exception:
        logger.warning(
            "Sync completed but its deployment event was not recorded", exc_info=True
        )
        return {"status": "unavailable"}


def _sync_checkpoint_scope(
    *,
    local_workspace_ids: List[str],
    remote_workspace_ids: List[str],
    workspace_mode: str,
    local_target_workspace_id: str,
    remote_target_workspace_id: str,
    link_to_source: bool,
    source_namespace: str,
    pull_target_namespace: str,
    push_target_namespace: str,
) -> Dict[str, Any]:
    return {
        "local_workspace_ids": sorted(local_workspace_ids),
        "remote_workspace_ids": sorted(remote_workspace_ids),
        "workspace_mode": str(workspace_mode or "").strip(),
        "local_target_workspace_id": str(local_target_workspace_id or "").strip(),
        "remote_target_workspace_id": str(remote_target_workspace_id or "").strip(),
        "link_to_source": bool(link_to_source),
        "source_namespace": str(source_namespace or "").strip(),
        "pull_target_namespace": str(pull_target_namespace or "").strip(),
        "push_target_namespace": str(push_target_namespace or "").strip(),
    }


def _sync_peer_deployment_id(
    pairing: Optional[Dict[str, Any]],
    *identities: Optional[Dict[str, Any]],
) -> str:
    for identity in identities:
        if not isinstance(identity, dict):
            continue
        deployment_id = str(identity.get("deployment_id") or "").strip()
        if deployment_id:
            return deployment_id
        data = identity.get("data")
        if isinstance(data, dict):
            deployment_id = str(data.get("deployment_id") or "").strip()
            if deployment_id:
                return deployment_id
    return str((pairing or {}).get("remote_deployment_id") or "").strip()


def _sync_peer_label(
    pairing: Optional[Dict[str, Any]],
    *identities: Optional[Dict[str, Any]],
) -> str:
    for identity in identities:
        if not isinstance(identity, dict):
            continue
        label = str(
            identity.get("display_name")
            or identity.get("hostname")
            or identity.get("source_namespace")
            or ""
        ).strip()
        if label:
            return label
    return str(
        (pairing or {}).get("remote_device_name") or (pairing or {}).get("label") or ""
    ).strip()


def _compare_sync_manifests(
    service: InstanceSyncService,
    local_manifest: Dict[str, Any],
    remote_manifest: Dict[str, Any],
    sections: Optional[List[str]],
    baseline: Dict[str, Any],
) -> List[Dict[str, Any]]:
    try:
        return service.compare_manifests(
            local_manifest,
            remote_manifest,
            sections,
            baseline=baseline,
        )
    except TypeError as exc:
        if "baseline" not in str(exc):
            raise
        return service.compare_manifests(local_manifest, remote_manifest, sections)


def _namespace_sync_manifest(
    service: InstanceSyncService,
    manifest: Dict[str, Any],
    namespace: str,
) -> Dict[str, Any]:
    if not namespace:
        return manifest
    namespace_manifest = getattr(service, "namespace_manifest", None)
    if not callable(namespace_manifest):
        return manifest
    return namespace_manifest(manifest, namespace=namespace)


def _record_observed_peer_state(
    *,
    peer_deployment_id: str,
    peer_id: str,
    peer_label: str,
    scope: Dict[str, Any],
    sections: List[str],
    local_revision: Dict[str, Any],
    remote_revision: Dict[str, Any],
    pull_local_manifest: Dict[str, Any],
    pull_remote_manifest: Dict[str, Any],
    push_local_manifest: Dict[str, Any],
    push_remote_manifest: Dict[str, Any],
) -> Dict[str, Any]:
    if not peer_deployment_id and not peer_id:
        return {}
    pull_view = sync_checkpoint_store.build_observed_view_baseline(
        local_manifest=pull_local_manifest,
        remote_manifest=pull_remote_manifest,
        sections=sections,
    )
    push_view = sync_checkpoint_store.build_observed_view_baseline(
        local_manifest=push_local_manifest,
        remote_manifest=push_remote_manifest,
        sections=sections,
    )
    if not sections:
        return {}
    return sync_checkpoint_store.record_checkpoint(
        peer_deployment_id=peer_deployment_id,
        peer_id=peer_id,
        peer_label=peer_label,
        direction="preview_observed",
        sections=sections,
        item_selections={},
        local_revision=local_revision,
        remote_revision=remote_revision,
        scope=scope,
        views={"pull": pull_view, "push": push_view},
        successful_sync=False,
    )


def _record_successful_sync_checkpoint(
    *,
    service: InstanceSyncService,
    peer_deployment_id: str,
    peer_id: str,
    peer_label: str,
    direction: str,
    sections: List[str],
    item_selections: Dict[str, List[str]],
    scope: Dict[str, Any],
    remote_revision: Dict[str, Any],
    views: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    if not peer_deployment_id and not peer_id:
        return {
            "state": "unavailable",
            "summary": "Peer deployment identity unavailable",
        }
    local_revision = _observe_current_data_revision(service)
    checkpoint = sync_checkpoint_store.record_checkpoint(
        peer_deployment_id=peer_deployment_id,
        peer_id=peer_id,
        peer_label=peer_label,
        direction=direction,
        sections=sections,
        item_selections=item_selections,
        local_revision=local_revision,
        remote_revision=remote_revision,
        scope=scope,
        views=views,
    )
    return sync_checkpoint_store.checkpoint_summary(
        checkpoint,
        current_revision=local_revision,
    )


def _sync_plan_context(
    *,
    pairing: Optional[Dict[str, Any]],
    remote_base_url: str,
    remote_identity_key: str,
    previewed_sections: List[str],
    local_workspace_ids: List[str],
    remote_workspace_ids: List[str],
    workspace_mode: str,
    local_target_workspace_id: str,
    remote_target_workspace_id: str,
    link_to_source: bool,
    source_namespace: str,
    pull_target_namespace: str,
    push_target_namespace: str,
) -> Dict[str, Any]:
    pair = pairing or {}
    identity_hash = hashlib.sha256(
        str(remote_identity_key or "").strip().encode("utf-8")
    ).hexdigest()
    return {
        "peer_id": str(pair.get("id") or "").strip(),
        "remote_base_url": str(remote_base_url or "").strip(),
        "remote_device_id": str(pair.get("remote_device_id") or "").strip(),
        "remote_identity_sha256": identity_hash,
        "scopes": sorted(_normalize_sync_scopes(pair.get("scopes"))),
        "previewed_sections": sorted(previewed_sections),
        "local_workspace_ids": sorted(local_workspace_ids),
        "remote_workspace_ids": sorted(remote_workspace_ids),
        "workspace_mode": workspace_mode,
        "local_target_workspace_id": local_target_workspace_id,
        "remote_target_workspace_id": remote_target_workspace_id,
        "link_to_source": bool(link_to_source),
        "source_namespace": str(source_namespace or "").strip(),
        "pull_target_namespace": str(pull_target_namespace or "").strip(),
        "push_target_namespace": str(push_target_namespace or "").strip(),
    }


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


def _reload_memory_manager_from_store(request: Request) -> None:
    state = getattr(getattr(request, "app", None), "state", None)
    mgr = getattr(state, "memory_manager", None)
    reload_store = getattr(mgr, "_load_persisted_store", None)
    if not callable(reload_store):
        return
    try:
        reload_store()
    except Exception:
        logger.debug("Failed to reload memory manager after sync", exc_info=True)


async def _refresh_sync_result_indexes(result: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    section_results = result.get("sections")
    if not isinstance(section_results, dict):
        return {}
    refresh: Dict[str, Any] = {}
    if _sync_section_applied(section_results.get("knowledge")):
        try:
            refresh["knowledge"] = await _run_knowledge_rag_rehydrate()
        except Exception as exc:
            refresh["knowledge"] = {"error": str(exc)}
    if _sync_section_applied(section_results.get("attachments")):
        try:
            attachment_result = section_results.get("attachments")
            applied_ids = []
            if isinstance(attachment_result, dict):
                applied_ids = [
                    str(item or "").strip()
                    for item in attachment_result.get("applied_ids") or []
                    if str(item or "").strip()
                ]
            refresh["attachments"] = await _run_attachments_rag_rehydrate(
                applied_ids or None
            )
        except Exception as exc:
            refresh["attachments"] = {"error": str(exc)}
    if _sync_section_applied(section_results.get("calendar")):
        try:
            refresh["calendar"] = await _run_calendar_rag_rehydrate()
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
            if any(snippet in lower for snippet in _SYNC_MANUAL_REFRESH_NOTE_SNIPPETS):
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
    before_manifest = service.build_manifest(sections) if sections else {}
    merged = service.merge_snapshot(
        payload.snapshot,
        link_to_source=payload.link_to_source,
        source_namespace=payload.source_namespace,
        source_label=payload.source_label,
        target_namespace=payload.target_namespace,
    )
    if _sync_section_applied((merged.get("sections") or {}).get("memories")):
        _reload_memory_manager_from_store(request)
    await _refresh_sync_result_indexes(merged)
    after_snapshot = service.build_snapshot(sections) if sections else None
    after_manifest = service.build_manifest(sections) if sections else {}
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
        sync_context = (
            payload.snapshot.get("sync_context")
            if isinstance(payload.snapshot.get("sync_context"), dict)
            else {}
        )
        source_manifest = (
            sync_context.get("source_manifest")
            if isinstance(sync_context.get("source_manifest"), dict)
            else {}
        )
        local_workspace_ids: List[str] = []
        peer_deployment_id = ""
        try:
            sync_context = (
                payload.snapshot.get("sync_context")
                if isinstance(payload.snapshot.get("sync_context"), dict)
                else {}
            )
            source_scope = (
                sync_context.get("scope")
                if isinstance(sync_context.get("scope"), dict)
                else {}
            )
            source_manifest = (
                sync_context.get("source_manifest")
                if isinstance(sync_context.get("source_manifest"), dict)
                else {}
            )
            item_selections = (
                sync_context.get("item_selections")
                if isinstance(sync_context.get("item_selections"), dict)
                else {}
            )
            local_workspace_ids = [
                str(value or "").strip()
                for value in (source_scope.get("remote_workspace_ids") or [])
                if str(value or "").strip()
            ]
            local_identity = service.current_instance_identity()
            checkpoint_scope = _sync_checkpoint_scope(
                local_workspace_ids=local_workspace_ids,
                remote_workspace_ids=[
                    str(value or "").strip()
                    for value in (source_scope.get("local_workspace_ids") or [])
                    if str(value or "").strip()
                ],
                workspace_mode=str(source_scope.get("workspace_mode") or "merge"),
                local_target_workspace_id=str(
                    source_scope.get("remote_target_workspace_id") or ""
                ),
                remote_target_workspace_id=str(
                    source_scope.get("local_target_workspace_id") or ""
                ),
                link_to_source=bool(source_scope.get("link_to_source")),
                source_namespace=str(local_identity.get("source_namespace") or ""),
                pull_target_namespace=str(
                    source_scope.get("push_target_namespace")
                    or payload.target_namespace
                    or ""
                ),
                push_target_namespace=str(
                    source_scope.get("pull_target_namespace") or ""
                ),
            )
            local_manifest = service.build_manifest(
                sections,
                workspace_ids=local_workspace_ids or None,
            )
            pull_remote_manifest = _namespace_sync_manifest(
                service,
                source_manifest,
                str(checkpoint_scope.get("pull_target_namespace") or ""),
            )
            push_local_manifest = _namespace_sync_manifest(
                service,
                local_manifest,
                str(checkpoint_scope.get("push_target_namespace") or ""),
            )
            peer_deployment_id = _sync_peer_deployment_id(
                None,
                remote_instance,
            )
            claims = _optional_device_claims(request, "sync") or {}
            merged["data_checkpoint"] = _record_successful_sync_checkpoint(
                service=service,
                peer_deployment_id=peer_deployment_id,
                peer_id=str(claims.get("sub") or "").strip(),
                peer_label=str(source_label or "").strip(),
                direction="incoming_push",
                sections=sections,
                item_selections={
                    str(section): [
                        str(item_id or "").strip()
                        for item_id in item_ids or []
                        if str(item_id or "").strip()
                    ]
                    for section, item_ids in item_selections.items()
                },
                scope=checkpoint_scope,
                remote_revision=_manifest_data_revision(
                    source_manifest,
                    sections,
                ),
                views={
                    "pull": sync_checkpoint_store.build_view_baseline(
                        local_manifest=local_manifest,
                        remote_manifest=pull_remote_manifest,
                        item_selections=item_selections,
                    ),
                    "push": sync_checkpoint_store.build_view_baseline(
                        local_manifest=push_local_manifest,
                        remote_manifest=source_manifest,
                        item_selections=item_selections,
                    ),
                },
            )
        except Exception:
            logger.warning(
                "Incoming sync applied but its checkpoint could not be recorded",
                exc_info=True,
            )
            merged["data_checkpoint"] = {
                "state": "unavailable",
                "summary": (
                    "Incoming sync applied, but its checkpoint could not be recorded"
                ),
            }
        merged["deployment_event"] = _record_deployment_sync_event(
            service=service,
            direction="incoming_push",
            sections=sections,
            peer_deployment_id=peer_deployment_id,
            operation_id=str(sync_context.get("operation_id") or "").strip(),
            origin_event_id=str(sync_context.get("origin_event_id") or "").strip(),
            workspace_ids=local_workspace_ids,
            item_selections=(
                sync_context.get("item_selections")
                if isinstance(sync_context.get("item_selections"), dict)
                else {}
            ),
            local_before_manifest=before_manifest,
            local_after_manifest=after_manifest,
            peer_before_manifest=source_manifest,
            peer_after_manifest=source_manifest,
        )
    return merged


def _mobile_float_state_path() -> Path:
    override = str(os.getenv("FLOAT_DEV_STATE_PATH") or "").strip()
    if override:
        return Path(override)
    return app_config.REPO_ROOT / ".dev_state.json"


def _load_mobile_float_state() -> Dict[str, Any]:
    state_path = _mobile_float_state_path()
    if not state_path.exists():
        return {}
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        logger.debug("Failed to read Mobile Float launcher state", exc_info=True)
        return {}
    return payload if isinstance(payload, dict) else {}


def _launcher_backend_binding(state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = state if isinstance(state, dict) else _load_mobile_float_state()
    processes = payload.get("processes")
    processes = processes if isinstance(processes, dict) else {}
    backend = processes.get("backend")
    backend = backend if isinstance(backend, dict) else {}
    # The current process environment is authoritative; launcher state is only
    # a fallback for status reads made outside a managed backend process.
    bind_host = str(
        os.getenv("FLOAT_BACKEND_HOST") or payload.get("backend_host") or ""
    ).strip()
    normalized_host = bind_host.strip("[]").lower()
    binding_known = bool(normalized_host)
    lan_listening = is_lan_binding_host(normalized_host)
    backend_pid = backend.get("pid")
    try:
        backend_pid = int(backend_pid)
    except (TypeError, ValueError):
        backend_pid = 0
    launcher_running = bool(payload.get("launcher_running"))
    backend_running = bool(backend.get("running"))
    current_pid = os.getpid()
    current_parent_pid = os.getppid()
    # Uvicorn's reload worker is a child of the launcher-owned process. The
    # parent match is useful diagnostic evidence, but only the current API
    # process is safe for this endpoint to terminate directly.
    backend_pid_is_current = backend_pid > 0 and backend_pid == current_pid
    backend_pid_is_parent = backend_pid > 0 and backend_pid == current_parent_pid
    backend_pid_matches_current = backend_pid_is_current or backend_pid_is_parent
    binding_locked = bool(payload.get("backend_host_locked"))
    reload_enabled = bool(payload.get("backend_reload_enabled")) or (
        backend_pid_is_parent and "backend_reload_enabled" not in payload
    )
    restart_supported = (
        launcher_running
        and backend_running
        and backend_pid_is_current
        and not binding_locked
        and not reload_enabled
    )
    current_process_binding_known = bool(os.getenv("FLOAT_BACKEND_HOST"))
    return {
        "bind_host": bind_host,
        "binding_known": binding_known,
        "lan_listening": lan_listening
        and (backend_running or current_process_binding_known),
        "launcher_running": launcher_running,
        "backend_running": backend_running,
        "backend_pid": backend_pid,
        "current_pid": current_pid,
        "current_parent_pid": current_parent_pid,
        "backend_pid_matches_current": backend_pid_matches_current,
        "binding_locked": binding_locked,
        "reload_enabled": reload_enabled,
        "restart_supported": restart_supported,
    }


def _terminate_launcher_backend(backend_pid: int) -> None:
    os.kill(int(backend_pid), signal.SIGTERM)


def _schedule_launcher_backend_restart(binding: Dict[str, Any]) -> bool:
    backend_pid = int(binding.get("backend_pid") or 0)
    if not binding.get("restart_supported") or backend_pid <= 0:
        return False

    def terminate() -> None:
        try:
            _terminate_launcher_backend(backend_pid)
        except Exception:
            logger.warning(
                "Failed to restart the launcher-managed backend for LAN binding",
                exc_info=True,
            )

    timer = threading.Timer(0.35, terminate)
    timer.daemon = True
    timer.start()
    return True


def _coerce_port(value: Any) -> Optional[int]:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    if 1 <= port <= 65535:
        return port
    return None


def _mobile_float_serve_port(value: Any = None) -> int:
    port = _coerce_port(value)
    if port is not None:
        return port
    if value is None:
        return MOBILE_FLOAT_DEFAULT_SERVE_PORT
    raise HTTPException(
        status_code=400, detail="Serve port must be between 1 and 65535."
    )


def _run_tailscale(
    args: List[str], *, timeout: int = 15
) -> subprocess.CompletedProcess:
    tailscale_bin = shutil.which("tailscale")
    if not tailscale_bin:
        raise FileNotFoundError("tailscale executable was not found on PATH.")
    return subprocess.run(
        [tailscale_bin, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def _tailscale_command_error(action: str, result: subprocess.CompletedProcess) -> str:
    detail = (result.stderr or result.stdout or "").strip()
    return detail or f"tailscale {action} failed with exit code {result.returncode}."


def _tailscale_self_status() -> Dict[str, Any]:
    try:
        result = _run_tailscale(["status", "--self", "--json"], timeout=12)
    except FileNotFoundError:
        return {
            "installed": False,
            "ok": False,
            "error": "Tailscale is not installed or not on PATH.",
        }
    except subprocess.TimeoutExpired:
        return {"installed": True, "ok": False, "error": "Tailscale status timed out."}
    except Exception as exc:
        logger.debug("Failed to inspect Tailscale status", exc_info=True)
        return {"installed": True, "ok": False, "error": str(exc)}
    if result.returncode != 0:
        return {
            "installed": True,
            "ok": False,
            "error": _tailscale_command_error("status", result),
        }
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return {
            "installed": True,
            "ok": False,
            "error": "Tailscale returned invalid JSON.",
        }
    self_info = payload.get("Self") if isinstance(payload, dict) else None
    self_info = self_info if isinstance(self_info, dict) else {}
    dns_name = str(self_info.get("DNSName") or "").strip().rstrip(".")
    tailscale_ips = self_info.get("TailscaleIPs")
    tailscale_ip = ""
    if isinstance(tailscale_ips, list):
        tailscale_ip = str(next((ip for ip in tailscale_ips if ip), "") or "").strip()
    host = dns_name or tailscale_ip or str(self_info.get("HostName") or "").strip()
    return {
        "installed": True,
        "ok": bool(host),
        "host": host,
        "dns_name": dns_name,
        "tailscale_ip": tailscale_ip,
        "hostname": str(self_info.get("HostName") or "").strip(),
        "error": ""
        if host
        else "Tailscale is running, but no tailnet host was reported.",
    }


def _format_mobile_float_url_host(host: str) -> str:
    value = str(host or "").strip()
    if ":" in value and not value.startswith("["):
        return f"[{value}]"
    return value


def _tailscale_serve_status_text() -> Dict[str, Any]:
    try:
        result = _run_tailscale(["serve", "status"], timeout=12)
    except FileNotFoundError:
        return {
            "ok": False,
            "text": "",
            "error": "Tailscale is not installed or not on PATH.",
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "text": "", "error": "Tailscale Serve status timed out."}
    except Exception as exc:
        logger.debug("Failed to inspect Tailscale Serve status", exc_info=True)
        return {"ok": False, "text": "", "error": str(exc)}
    text = (result.stdout or result.stderr or "").strip()
    if result.returncode != 0:
        return {
            "ok": False,
            "text": text,
            "error": _tailscale_command_error("serve status", result),
        }
    return {"ok": True, "text": text, "error": ""}


def _mobile_float_serve_status(
    serve_port: int = MOBILE_FLOAT_DEFAULT_SERVE_PORT,
) -> Dict[str, Any]:
    state = _load_mobile_float_state()
    frontend_port = _coerce_port(state.get("frontend_port"))
    backend_port = _coerce_port(state.get("backend_port"))
    tailscale_status = _tailscale_self_status()
    serve_status = (
        _tailscale_serve_status_text()
        if tailscale_status.get("installed")
        else {"text": ""}
    )
    serve_text = str(serve_status.get("text") or "").strip()
    target = f"localhost:{frontend_port}" if frontend_port else ""
    host = str(tailscale_status.get("host") or "").strip()
    configured_markers = [
        f":{serve_port}",
        f":{frontend_port}" if frontend_port else "",
    ]
    running = bool(serve_text) and all(
        marker in serve_text for marker in configured_markers if marker
    )
    url = ""
    if host:
        url = f"http://{_format_mobile_float_url_host(host)}:{serve_port}/"
    warning = ""
    if not tailscale_status.get("installed"):
        warning = "Tailscale is not installed or not on PATH."
    elif not tailscale_status.get("ok"):
        warning = str(tailscale_status.get("error") or "Tailscale is not ready.")
    elif not frontend_port:
        warning = "Float frontend port was not found in .dev_state.json."
    elif not running:
        warning = "Tailscale Serve is not currently pointing at this Float frontend."
    return {
        "ok": bool(tailscale_status.get("ok")) and bool(frontend_port),
        "installed": bool(tailscale_status.get("installed")),
        "running": running,
        "serve_port": serve_port,
        "frontend_port": frontend_port,
        "backend_port": backend_port,
        "tailnet_host": host,
        "url": url,
        "target": target,
        "serve_status": serve_text,
        "status_text": "running"
        if running
        else ("ready" if host and frontend_port else "not ready"),
        "warning": warning,
        "state_path": str(_mobile_float_state_path()),
    }


@router.get("/sync/mobile-serve/status")
async def mobile_float_serve_status(serve_port: Optional[int] = None):
    return _mobile_float_serve_status(_mobile_float_serve_port(serve_port))


@router.post("/sync/lan-visibility")
async def set_lan_visibility(request: Request, payload: LanVisibilityPayload):
    """Persist LAN access and restart the launcher-managed backend if needed."""

    _require_local_control(request)
    enabled = bool(payload.enabled)
    user_settings.save_settings({"sync_visible_on_lan": enabled})
    before = _launcher_backend_binding()
    binding_matches = bool(before.get("lan_listening")) == enabled
    restart_scheduled = False
    if payload.restart and not binding_matches:
        restart_scheduled = _schedule_launcher_backend_restart(before)
    restart_required = not binding_matches and not restart_scheduled
    if binding_matches:
        message = (
            "LAN visibility is on and Float is listening on the private network."
            if enabled
            else "LAN visibility is off and Float is listening on this device only."
        )
    elif restart_scheduled:
        message = (
            "Restarting Float's backend with LAN listening on."
            if enabled
            else "Restarting Float's backend in device-only mode."
        )
    else:
        if before.get("binding_locked"):
            message = (
                "LAN visibility was saved, but this launcher has an explicit bind-host "
                "override. Restart Float without --backend-host, --lan, or --no-lan "
                "to let this control manage the listener."
            )
        elif before.get("reload_enabled"):
            message = (
                "LAN visibility was saved, but backend auto-reload is active. "
                "Restart Float without --dev to let this control restart the listener."
            )
        else:
            message = (
                "LAN visibility was saved. Restart Float to begin listening on the private network."
                if enabled
                else "LAN visibility was saved. Restart Float to stop the private-network listener."
            )
    return {
        "enabled": enabled,
        "active": binding_matches,
        "restart_scheduled": restart_scheduled,
        "restart_required": restart_required,
        "binding_before": before,
        "message": message,
    }


@router.post("/sync/mobile-serve/start")
async def mobile_float_serve_start(payload: MobileFloatServePayload):
    serve_port = _mobile_float_serve_port(payload.serve_port)
    before = _mobile_float_serve_status(serve_port)
    frontend_port = _coerce_port(before.get("frontend_port"))
    if not before.get("installed"):
        raise HTTPException(
            status_code=409,
            detail=before.get("warning") or "Tailscale is not available.",
        )
    if not before.get("tailnet_host"):
        raise HTTPException(
            status_code=409, detail=before.get("warning") or "Tailscale is not ready."
        )
    if frontend_port is None:
        raise HTTPException(
            status_code=409,
            detail="Float frontend port was not found in .dev_state.json. Start Float first.",
        )
    try:
        result = _run_tailscale(
            [
                "serve",
                f"--http={serve_port}",
                "--bg",
                "--yes",
                f"localhost:{frontend_port}",
            ],
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Tailscale Serve start timed out.")
    except Exception as exc:
        logger.debug("Failed to start Tailscale Serve for Mobile Float", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))
    if result.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=_tailscale_command_error("serve start", result),
        )
    status = _mobile_float_serve_status(serve_port)
    status["message"] = (
        f"Mobile Float available at {status.get('url')}"
        if status.get("url")
        else "Mobile Float Serve started."
    )
    return status


@router.post("/sync/mobile-serve/stop")
async def mobile_float_serve_stop(payload: MobileFloatServePayload):
    serve_port = _mobile_float_serve_port(payload.serve_port)
    try:
        result = _run_tailscale(["serve", f"--http={serve_port}", "off"], timeout=20)
    except FileNotFoundError:
        raise HTTPException(
            status_code=409, detail="Tailscale is not installed or not on PATH."
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Tailscale Serve stop timed out.")
    except Exception as exc:
        logger.debug("Failed to stop Tailscale Serve for Mobile Float", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))
    if result.returncode != 0:
        detail = _tailscale_command_error("serve stop", result)
        if "no serve config" not in detail.lower():
            raise HTTPException(status_code=500, detail=detail)
    status = _mobile_float_serve_status(serve_port)
    status["message"] = "Mobile Float Serve stopped."
    return status


@router.get("/sync/overview")
async def sync_overview(request: Request):
    if not _is_local_control_request(request):
        _require_scope(request, "sync")
    service = _sync_service()
    settings = user_settings.load_settings()
    identity = service.current_instance_identity(
        source_namespace=settings.get("sync_source_namespace"),
    )
    current_revision = _observe_current_data_revision(service)
    local_checkpoint = sync_checkpoint_store.checkpoint_summary(
        sync_checkpoint_store.latest_checkpoint(),
        current_revision=current_revision,
    )
    deployment_status = build_instance_status(
        settings=settings,
        register_machine=True,
        data_revision=current_revision,
        sync_checkpoint=local_checkpoint,
    )
    workspace_state = _workspace_state_summary(settings)
    access = advertised_device_access(request)
    launcher_binding = _launcher_backend_binding()
    access["listener"] = {
        "bind_host": launcher_binding.get("bind_host") or "",
        "binding_known": bool(launcher_binding.get("binding_known")),
        "lan_listening": bool(launcher_binding.get("lan_listening")),
        "launcher_running": bool(launcher_binding.get("launcher_running")),
        "restart_supported": bool(launcher_binding.get("restart_supported")),
        "binding_locked": bool(launcher_binding.get("binding_locked")),
        "reload_enabled": bool(launcher_binding.get("reload_enabled")),
    }
    saved_peers = []
    for peer in _load_saved_peers():
        remote_deployment_id = str(peer.get("remote_deployment_id") or "").strip()
        checkpoint = (
            sync_checkpoint_store.latest_checkpoint(
                peer_deployment_id=remote_deployment_id
            )
            if remote_deployment_id
            else sync_checkpoint_store.latest_checkpoint(
                peer_id=str(peer.get("id") or "").strip()
            )
        )
        saved_peers.append(
            {
                **peer,
                "data_checkpoint": sync_checkpoint_store.checkpoint_summary(
                    checkpoint,
                    current_revision=current_revision,
                ),
            }
        )
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
        item for item in inbound_devices if not item.get("legacy_record")
    ]
    legacy_inbound_devices = [
        item for item in inbound_devices if item.get("legacy_record")
    ]
    sync_reviews = _sync_reviews_snapshot(pending_limit=12, recent_limit=8)
    sync_operations = _sync_operations_overview()
    try:
        deployment_events = deployment_event_store.list_events(limit=12)
        deployment_event_chain = deployment_event_store.verify_chain()
    except Exception:
        logger.warning("Failed to read deployment event ledger", exc_info=True)
        deployment_events = []
        deployment_event_chain = {
            "valid": False,
            "event_count": 0,
            "broken_sequence": None,
            "state": "unavailable",
        }
    return {
        "current_device": {
            "display_name": display_name or identity.get("hostname") or "This device",
            "deployment_id": identity.get("deployment_id"),
            "hostname": identity.get("hostname"),
            "public_key": _get_or_create_device_public_key(),
            "source_namespace": identity.get("source_namespace"),
            "link_to_source_default": bool(identity.get("link_to_source_default")),
            "software": identity.get("software") or {},
            "data_revision": current_revision,
        },
        "deployment_status": deployment_status,
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
            "saved_peers": saved_peers,
        },
        "egress_summary": _sync_ownership_summary(
            settings, access, saved_peers, sync_operations
        ),
        "sync_operations": sync_operations,
        "deployment_events": {
            "recent": deployment_events,
            "chain": deployment_event_chain,
        },
        "sync_suggestions": _sync_overview_suggestions(
            access=access,
            saved_peers=saved_peers,
            operations=sync_operations,
            sync_reviews=sync_reviews,
        ),
        "workspaces": workspace_state,
        "inbound_devices": trusted_devices,
        "legacy_inbound_devices": legacy_inbound_devices,
        "sync_reviews": {
            "pending": sync_reviews["pending"],
            "recent": sync_reviews["recent"],
        },
        "device_counts": {
            "paired": len(saved_peers),
            "trusted": len(trusted_devices),
            "legacy": len(legacy_inbound_devices),
            "pending_push_reviews": sync_reviews["counts"]["pending"],
        },
    }


@router.get("/sync/events")
async def sync_events(
    request: Request,
    limit: int = Query(default=200, ge=1, le=1000),
    event_type: Optional[str] = Query(default=None),
):
    if not _is_local_control_request(request):
        _require_scope(request, "sync")
    try:
        events = deployment_event_store.list_events(
            limit=limit,
            event_type=event_type,
        )
        chain = deployment_event_store.verify_chain()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"events": events, "chain": chain}


@router.post("/sync/operations/{operation_id}/cancel")
async def sync_operation_cancel(operation_id: str):
    try:
        operation = sync_cancel_operation(operation_id)
    except Exception as exc:
        logger.debug("Failed to mark sync operation cancellation", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Unable to record sync cancellation: {exc}",
        )
    return {"status": "cancel_requested", "operation": operation}


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
    operation = _begin_sync_operation(
        kind="preview",
        remote_url=payload.remote_url,
        operation_id=payload.operation_id,
        operation_owner=payload.operation_owner,
        paired_device=payload.paired_device,
        sections=service.normalize_sections(payload.sections),
        workspace_mode=payload.workspace_mode,
        metadata={
            "local_workspace_ids": payload.local_workspace_ids or [],
            "remote_workspace_ids": payload.remote_workspace_ids or [],
        },
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
        local_sync_filter = filter_workspace_ids_for_sync(
            local_workspace_ids, local_profiles
        )
        local_workspace_ids = list(local_sync_filter["workspace_ids"])
        privacy_ignored_local_workspace_ids = list(
            local_sync_filter["privacy_ignored_workspace_ids"]
        )
        if not local_workspace_ids:
            raise HTTPException(
                status_code=400,
                detail=_ignored_local_workspace_detail(
                    ignored_local_workspace_ids, privacy_ignored_local_workspace_ids
                ),
            )
        remote = RemoteFloatClient(
            payload.remote_url,
            paired_device=pairing,
            device_name=str(settings.get("device_display_name") or "").strip()
            or socket.gethostname(),
        )
        remote_overview = remote.get_sync_overview()
        identity_status = _annotate_peer_identity_from_overview(
            remote_overview,
            pairing,
            strict=True,
        )
        remote_workspace_state = (
            remote_overview.get("workspaces")
            if isinstance(remote_overview.get("workspaces"), dict)
            else _workspace_state_summary({})
        )
        remote_profiles = (
            remote_workspace_state.get("profiles")
            if isinstance(remote_workspace_state.get("profiles"), list)
            else []
        )
        remote_workspace_ids = normalize_workspace_ids(
            payload.remote_workspace_ids
            or (pairing or {}).get("remote_workspace_ids")
            or remote_workspace_state.get("selected_workspace_ids")
            or [
                remote_workspace_state.get("active_workspace_id")
                or DEFAULT_WORKSPACE_ID
            ],
            remote_profiles,
        ) or [remote_workspace_state.get("active_workspace_id") or DEFAULT_WORKSPACE_ID]
        remote_sync_filter = filter_workspace_ids_for_sync(
            remote_workspace_ids, remote_profiles
        )
        remote_workspace_ids = list(remote_sync_filter["workspace_ids"])
        privacy_ignored_remote_workspace_ids = list(
            remote_sync_filter["privacy_ignored_workspace_ids"]
        )
        if not remote_workspace_ids:
            raise HTTPException(
                status_code=400,
                detail="All selected remote workspaces were ignored by workspace privacy settings.",
            )
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
        previewed_sections = service.normalize_sections(payload.sections)
        checkpoint_scope = _sync_checkpoint_scope(
            local_workspace_ids=local_workspace_ids,
            remote_workspace_ids=remote_workspace_ids,
            workspace_mode=workspace_mode,
            local_target_workspace_id=local_target_workspace_id,
            remote_target_workspace_id=remote_target_workspace_id,
            link_to_source=payload.link_to_source,
            source_namespace=str(local_instance.get("source_namespace") or ""),
            pull_target_namespace=pull_namespace,
            push_target_namespace=push_namespace,
        )
        peer_deployment_id = _sync_peer_deployment_id(
            pairing,
            remote_instance,
            identity_status.get("identity"),
        )
        peer_id = str((pairing or {}).get("id") or "").strip()
        peer_label = _sync_peer_label(
            pairing,
            remote_instance,
            identity_status.get("identity"),
        )
        local_revision = _observe_current_data_revision(service)
        remote_revision = _manifest_data_revision(
            remote_manifest,
            previewed_sections,
        )
        checkpoint = sync_checkpoint_store.load_checkpoint(
            peer_deployment_id=peer_deployment_id,
            peer_id=peer_id,
            scope=checkpoint_scope,
        )
        pull_comparison = _compare_sync_manifests(
            service,
            local_manifest,
            pull_manifest,
            payload.sections,
            sync_checkpoint_store.comparison_baseline(checkpoint, "pull"),
        )
        push_comparison = _compare_sync_manifests(
            service,
            push_manifest,
            remote_manifest,
            payload.sections,
            sync_checkpoint_store.comparison_baseline(checkpoint, "push"),
        )
        try:
            observed_checkpoint = _record_observed_peer_state(
                peer_deployment_id=peer_deployment_id,
                peer_id=peer_id,
                peer_label=peer_label,
                scope=checkpoint_scope,
                sections=previewed_sections,
                local_revision=local_revision,
                remote_revision=remote_revision,
                pull_local_manifest=local_manifest,
                pull_remote_manifest=pull_manifest,
                push_local_manifest=push_manifest,
                push_remote_manifest=remote_manifest,
            )
            if observed_checkpoint:
                checkpoint = observed_checkpoint
        except Exception:
            logger.debug(
                "Failed to record observed peer sync state",
                exc_info=True,
            )
        plan_context = _sync_plan_context(
            pairing=pairing,
            remote_base_url=remote.instance_base,
            remote_identity_key=str(
                (identity_status.get("identity") or {}).get("public_key")
                or remote_instance.get("public_key")
                or ""
            ),
            previewed_sections=previewed_sections,
            local_workspace_ids=local_workspace_ids,
            remote_workspace_ids=remote_workspace_ids,
            workspace_mode=workspace_mode,
            local_target_workspace_id=local_target_workspace_id,
            remote_target_workspace_id=remote_target_workspace_id,
            link_to_source=payload.link_to_source,
            source_namespace=str(local_instance.get("source_namespace") or ""),
            pull_target_namespace=pull_namespace,
            push_target_namespace=push_namespace,
        )
        plan_receipt = issue_sync_plan_receipt(
            context=plan_context,
            allowed={
                "pull": _sync_plan_allowed_selections(pull_comparison, "pull"),
                "push": _sync_plan_allowed_selections(push_comparison, "push"),
            },
            freshness={
                "local": _sync_manifest_section_digests(
                    local_manifest, previewed_sections
                ),
                "remote": _sync_manifest_section_digests(
                    remote_manifest, previewed_sections
                ),
            },
        )
        pair_state = remote.get_pairing_state()
        if pairing is not None:
            pair_state.update(
                {
                    "remote_url": remote.instance_base,
                    "remote_public_key": (
                        (identity_status.get("identity") or {}).get("public_key")
                        or pairing.get("remote_public_key")
                        or ""
                    ),
                    "local_workspace_ids": local_workspace_ids,
                    "remote_workspace_ids": remote_workspace_ids,
                    "workspace_mode": workspace_mode,
                    "local_target_workspace_id": local_target_workspace_id,
                    "remote_target_workspace_id": remote_target_workspace_id,
                }
            )
        response_payload = {
            "plan_receipt": plan_receipt,
            "link_to_source": payload.link_to_source,
            "workspace_mode": workspace_mode,
            "local": local_instance,
            "remote": {
                **remote_instance,
                "base_url": remote.instance_base,
            },
            "paired_device": pair_state,
            "effective_namespaces": {
                "pull": pull_namespace or None,
                "push": push_namespace or None,
            },
            "data_checkpoint": sync_checkpoint_store.checkpoint_summary(
                checkpoint,
                current_revision=local_revision,
            ),
            "data_revisions": {
                "local": local_revision,
                "remote": remote_revision,
            },
            "workspaces": {
                "local": {
                    **local_workspace_state,
                    "selected_workspace_ids": local_workspace_ids,
                    "target_workspace_id": local_target_workspace_id,
                    "ignored_workspace_ids": ignored_local_workspace_ids,
                    "privacy_ignored_workspace_ids": privacy_ignored_local_workspace_ids,
                },
                "remote": {
                    **remote_workspace_state,
                    "selected_workspace_ids": remote_workspace_ids,
                    "target_workspace_id": remote_target_workspace_id,
                    "privacy_ignored_workspace_ids": privacy_ignored_remote_workspace_ids,
                },
            },
            "sections": pull_comparison,
            "pull_sections": pull_comparison,
            "push_sections": push_comparison,
        }
        _finish_sync_operation(
            operation,
            status="completed",
            result={
                "workspace_mode": workspace_mode,
                "pull_sections": len(pull_comparison),
                "push_sections": len(push_comparison),
            },
        )
        return response_payload
    except HTTPException as exc:
        _finish_sync_operation(operation, status="failed", error=str(exc.detail))
        raise
    except requests.RequestException as exc:
        _finish_sync_operation(operation, status="failed", error=str(exc))
        _log_remote_sync_failure(
            "sync_plan",
            remote_url=payload.remote_url,
            paired_device=payload.paired_device,
            context={
                "sections": service.normalize_sections(payload.sections),
                "workspace_mode": payload.workspace_mode,
                "local_workspace_ids": payload.local_workspace_ids or [],
                "remote_workspace_ids": payload.remote_workspace_ids or [],
                "local_target_workspace_id": payload.local_target_workspace_id or "",
                "remote_target_workspace_id": payload.remote_target_workspace_id or "",
            },
            exc=exc,
        )
        raise HTTPException(status_code=502, detail=f"Remote sync probe failed: {exc}")
    except ValueError as exc:
        _finish_sync_operation(operation, status="failed", error=str(exc))
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/sync/apply")
async def sync_apply(request: Request, payload: SyncApplyRequest):
    if not str(payload.plan_receipt or "").strip():
        raise HTTPException(
            status_code=409,
            detail="Preview required. Preview changes again before applying.",
        )
    try:
        plan_claims = decode_sync_plan_receipt(str(payload.plan_receipt))
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=409,
            detail="Preview expired. Preview changes again before applying.",
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=403, detail="Invalid sync plan receipt."
        ) from exc
    service = _sync_service()
    sections = service.normalize_sections(payload.sections)
    item_selections = service.normalize_item_selections(
        sections, payload.item_selections
    )
    operation = _begin_sync_operation(
        kind=payload.direction,
        remote_url=payload.remote_url,
        operation_id=payload.operation_id,
        operation_owner=payload.operation_owner,
        paired_device=payload.paired_device,
        sections=sections,
        workspace_mode=payload.workspace_mode,
        metadata={"item_selection_sections": sorted(item_selections.keys())},
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
        local_sync_filter = filter_workspace_ids_for_sync(
            local_workspace_ids, local_profiles
        )
        local_workspace_ids = list(local_sync_filter["workspace_ids"])
        privacy_ignored_local_workspace_ids = list(
            local_sync_filter["privacy_ignored_workspace_ids"]
        )
        if not local_workspace_ids:
            raise HTTPException(
                status_code=400,
                detail=_ignored_local_workspace_detail(
                    ignored_local_workspace_ids, privacy_ignored_local_workspace_ids
                ),
            )
        remote = RemoteFloatClient(
            payload.remote_url,
            paired_device=pairing,
            device_name=str(settings.get("device_display_name") or "").strip()
            or socket.gethostname(),
        )
        remote_overview = remote.get_sync_overview()
        identity_status = _annotate_peer_identity_from_overview(
            remote_overview,
            pairing,
            strict=True,
        )
        remote_workspace_state = (
            remote_overview.get("workspaces")
            if isinstance(remote_overview.get("workspaces"), dict)
            else _workspace_state_summary({})
        )
        remote_profiles = (
            remote_workspace_state.get("profiles")
            if isinstance(remote_workspace_state.get("profiles"), list)
            else []
        )
        remote_workspace_ids = normalize_workspace_ids(
            payload.remote_workspace_ids
            or (pairing or {}).get("remote_workspace_ids")
            or remote_workspace_state.get("selected_workspace_ids")
            or [
                remote_workspace_state.get("active_workspace_id")
                or DEFAULT_WORKSPACE_ID
            ],
            remote_profiles,
        ) or [remote_workspace_state.get("active_workspace_id") or DEFAULT_WORKSPACE_ID]
        remote_sync_filter = filter_workspace_ids_for_sync(
            remote_workspace_ids, remote_profiles
        )
        remote_workspace_ids = list(remote_sync_filter["workspace_ids"])
        privacy_ignored_remote_workspace_ids = list(
            remote_sync_filter["privacy_ignored_workspace_ids"]
        )
        if not remote_workspace_ids:
            raise HTTPException(
                status_code=400,
                detail="All selected remote workspaces were ignored by workspace privacy settings.",
            )
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
        remote_identity = _remote_identity_from_overview(remote_overview)
        pull_target_namespace = _workspace_target_namespace(
            mode=workspace_mode,
            target_profile=local_target_workspace,
            source_device_name=remote_identity.get("display_name")
            or remote_identity.get("hostname"),
            source_workspace_profile=remote_source_workspace,
        )
        if not pull_target_namespace and payload.link_to_source:
            pull_target_namespace = service.resolve_source_namespace(
                link_to_source=True,
                source_namespace=remote_identity.get("source_namespace"),
                source_label=remote_identity.get("display_name")
                or remote_identity.get("hostname"),
            )
        effective_push_namespace = push_target_namespace
        if not effective_push_namespace and payload.link_to_source:
            effective_push_namespace = service.resolve_source_namespace(
                link_to_source=True,
                source_namespace=local_identity.get("source_namespace"),
                source_label=local_identity.get("display_name")
                or local_identity.get("hostname"),
            )
        checkpoint_scope = _sync_checkpoint_scope(
            local_workspace_ids=local_workspace_ids,
            remote_workspace_ids=remote_workspace_ids,
            workspace_mode=workspace_mode,
            local_target_workspace_id=local_target_workspace_id,
            remote_target_workspace_id=remote_target_workspace_id,
            link_to_source=payload.link_to_source,
            source_namespace=str(local_identity.get("source_namespace") or ""),
            pull_target_namespace=pull_target_namespace,
            push_target_namespace=effective_push_namespace,
        )
        peer_deployment_id = _sync_peer_deployment_id(
            pairing,
            remote_identity,
            (remote_overview.get("deployment_status") or {}).get("data")
            if isinstance(remote_overview.get("deployment_status"), dict)
            else {},
        )
        peer_id = str((pairing or {}).get("id") or "").strip()
        peer_label = _sync_peer_label(pairing, remote_identity)
        receipt_context = plan_claims.get("context")
        previewed_sections = (
            receipt_context.get("previewed_sections")
            if isinstance(receipt_context, dict)
            and isinstance(receipt_context.get("previewed_sections"), list)
            else []
        )
        current_context = _sync_plan_context(
            pairing=pairing,
            remote_base_url=remote.instance_base,
            remote_identity_key=str(
                (identity_status.get("identity") or {}).get("public_key") or ""
            ),
            previewed_sections=previewed_sections,
            local_workspace_ids=local_workspace_ids,
            remote_workspace_ids=remote_workspace_ids,
            workspace_mode=workspace_mode,
            local_target_workspace_id=local_target_workspace_id,
            remote_target_workspace_id=remote_target_workspace_id,
            link_to_source=payload.link_to_source,
            source_namespace=str(local_identity.get("source_namespace") or ""),
            pull_target_namespace=pull_target_namespace,
            push_target_namespace=effective_push_namespace,
        )
        current_local_manifest = service.build_manifest(
            sections, workspace_ids=local_workspace_ids
        )
        current_remote_manifest = remote.get_manifest(
            sections, workspace_ids=remote_workspace_ids
        )
        try:
            assert_sync_plan_authorized(
                plan_claims,
                context=current_context,
                direction=payload.direction,
                sections=sections,
                item_selections=item_selections,
                freshness={
                    "local": _sync_manifest_section_digests(
                        current_local_manifest, sections
                    ),
                    "remote": _sync_manifest_section_digests(
                        current_remote_manifest, sections
                    ),
                },
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if payload.direction == "push":
            outbound_event_id = str(uuid4())
            snapshot = service.build_snapshot(
                sections, workspace_ids=local_workspace_ids
            )
            snapshot = service.filter_snapshot_by_item_selections(
                snapshot, item_selections
            )
            snapshot["instance"] = local_identity
            snapshot["sync_context"] = {
                "schema_version": 1,
                "scope": checkpoint_scope,
                "item_selections": item_selections,
                "source_manifest": current_local_manifest,
                "operation_id": str((operation or {}).get("id") or "").strip(),
                "origin_event_id": outbound_event_id,
            }
            remote_result = remote.ingest_snapshot(
                snapshot,
                link_to_source=payload.link_to_source or workspace_mode == "import",
                source_namespace=local_identity.get("source_namespace"),
                source_label=local_identity.get("display_name")
                or local_identity.get("hostname"),
                target_namespace=effective_push_namespace or None,
            )
            pair_state = remote.get_pairing_state()
            if pairing is not None:
                pair_state.update(
                    {
                        "remote_url": remote.instance_base,
                        "remote_public_key": (
                            (identity_status.get("identity") or {}).get("public_key")
                            or pairing.get("remote_public_key")
                            or ""
                        ),
                        "local_workspace_ids": local_workspace_ids,
                        "remote_workspace_ids": remote_workspace_ids,
                        "workspace_mode": workspace_mode,
                        "local_target_workspace_id": local_target_workspace_id,
                        "remote_target_workspace_id": remote_target_workspace_id,
                    }
                )
            persisted_pair = _persist_saved_peer_state(
                pair_state,
                remote_label=peer_label,
            )
            remote_status = str(remote_result.get("status") or "").strip().lower()
            data_checkpoint = {
                "state": "pending_review",
                "summary": "Remote approval is required before this becomes a sync checkpoint",
            }
            post_local_manifest = current_local_manifest
            post_remote_manifest = current_remote_manifest
            if remote_status != "pending_review":
                try:
                    post_local_manifest = service.build_manifest(
                        sections,
                        workspace_ids=local_workspace_ids,
                    )
                    post_remote_manifest = remote.get_manifest(
                        sections,
                        workspace_ids=remote_workspace_ids,
                    )
                    push_local_view = _namespace_sync_manifest(
                        service,
                        post_local_manifest,
                        effective_push_namespace,
                    )
                    pull_remote_view = _namespace_sync_manifest(
                        service,
                        post_remote_manifest,
                        pull_target_namespace,
                    )
                    data_checkpoint = _record_successful_sync_checkpoint(
                        service=service,
                        peer_deployment_id=peer_deployment_id,
                        peer_id=peer_id,
                        peer_label=peer_label,
                        direction="push",
                        sections=sections,
                        item_selections=item_selections,
                        scope=checkpoint_scope,
                        remote_revision=_manifest_data_revision(
                            post_remote_manifest,
                            sections,
                        ),
                        views={
                            "pull": sync_checkpoint_store.build_view_baseline(
                                local_manifest=post_local_manifest,
                                remote_manifest=pull_remote_view,
                                item_selections=item_selections,
                            ),
                            "push": sync_checkpoint_store.build_view_baseline(
                                local_manifest=push_local_view,
                                remote_manifest=post_remote_manifest,
                                item_selections=item_selections,
                            ),
                        },
                    )
                except Exception:
                    logger.warning(
                        "Push applied but its local sync checkpoint could not be recorded",
                        exc_info=True,
                    )
                    data_checkpoint = {
                        "state": "unavailable",
                        "summary": (
                            "Push applied, but the local sync checkpoint could not be recorded"
                        ),
                    }
            deployment_event = _record_deployment_sync_event(
                service=service,
                direction="push",
                sections=sections,
                peer_deployment_id=peer_deployment_id,
                operation_id=str((operation or {}).get("id") or "").strip(),
                event_id=outbound_event_id,
                status="pending" if remote_status == "pending_review" else "completed",
                workspace_ids=local_workspace_ids,
                item_selections=item_selections,
                local_before_manifest=current_local_manifest,
                local_after_manifest=post_local_manifest,
                peer_before_manifest=current_remote_manifest,
                peer_after_manifest=post_remote_manifest,
            )
            response_payload = {
                "direction": "push",
                "sections": sections,
                "remote": remote.instance_base,
                "paired_device": persisted_pair or pair_state,
                "ignored_local_workspace_ids": ignored_local_workspace_ids,
                "privacy_ignored_local_workspace_ids": privacy_ignored_local_workspace_ids,
                "privacy_ignored_remote_workspace_ids": privacy_ignored_remote_workspace_ids,
                "workspace_mode": workspace_mode,
                "effective_namespace": remote_result.get("effective_namespace"),
                "item_selections": item_selections,
                "data_checkpoint": data_checkpoint,
                "deployment_event": deployment_event,
                "result": remote_result,
            }
            _finish_sync_operation(
                operation,
                status="completed",
                result={
                    "direction": "push",
                    "remote": remote.instance_base,
                    "workspace_mode": workspace_mode,
                    "remote_status": remote_status,
                    "data_checkpoint_state": data_checkpoint.get("state"),
                },
            )
            return response_payload
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
        if not pull_target_namespace and payload.link_to_source:
            pull_target_namespace = service.resolve_source_namespace(
                link_to_source=True,
                source_namespace=remote_identity.get("source_namespace"),
                source_label=remote_identity.get("display_name")
                or remote_identity.get("hostname"),
            )
        pair_state = remote.get_pairing_state()
        if pairing is not None:
            pair_state.update(
                {
                    "remote_url": remote.instance_base,
                    "remote_public_key": (
                        (identity_status.get("identity") or {}).get("public_key")
                        or pairing.get("remote_public_key")
                        or ""
                    ),
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
        if _sync_section_applied((local_result.get("sections") or {}).get("memories")):
            _reload_memory_manager_from_store(request)
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
                source_deployment_id=peer_deployment_id,
                source_lineage_id=str(
                    remote_source_workspace.get("lineage_id")
                    if remote_source_workspace
                    else ""
                ).strip(),
                source_origin_deployment_id=str(
                    remote_source_workspace.get("origin_deployment_id")
                    if remote_source_workspace
                    else ""
                ).strip(),
            )
            _upsert_workspace_profile(imported_profile)
        post_local_manifest = current_local_manifest
        try:
            post_local_manifest = service.build_manifest(
                sections,
                workspace_ids=local_workspace_ids,
            )
            pull_remote_view = _namespace_sync_manifest(
                service,
                current_remote_manifest,
                pull_target_namespace,
            )
            push_local_view = _namespace_sync_manifest(
                service,
                post_local_manifest,
                effective_push_namespace,
            )
            data_checkpoint = _record_successful_sync_checkpoint(
                service=service,
                peer_deployment_id=peer_deployment_id,
                peer_id=peer_id,
                peer_label=peer_label,
                direction="pull",
                sections=sections,
                item_selections=item_selections,
                scope=checkpoint_scope,
                remote_revision=_manifest_data_revision(
                    current_remote_manifest,
                    sections,
                ),
                views={
                    "pull": sync_checkpoint_store.build_view_baseline(
                        local_manifest=post_local_manifest,
                        remote_manifest=pull_remote_view,
                        item_selections=item_selections,
                    ),
                    "push": sync_checkpoint_store.build_view_baseline(
                        local_manifest=push_local_view,
                        remote_manifest=current_remote_manifest,
                        item_selections=item_selections,
                    ),
                },
            )
        except Exception:
            logger.warning(
                "Pull applied but its local sync checkpoint could not be recorded",
                exc_info=True,
            )
            data_checkpoint = {
                "state": "unavailable",
                "summary": (
                    "Pull applied, but the local sync checkpoint could not be recorded"
                ),
            }
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
        deployment_event = _record_deployment_sync_event(
            service=service,
            direction="pull",
            sections=sections,
            peer_deployment_id=peer_deployment_id,
            operation_id=str((operation or {}).get("id") or "").strip(),
            workspace_ids=local_workspace_ids,
            item_selections=item_selections,
            local_before_manifest=current_local_manifest,
            local_after_manifest=post_local_manifest,
            peer_before_manifest=current_remote_manifest,
            peer_after_manifest=current_remote_manifest,
        )
        response_payload = {
            "direction": "pull",
            "sections": sections,
            "remote": remote.instance_base,
            "paired_device": persisted_pair or pair_state,
            "ignored_local_workspace_ids": ignored_local_workspace_ids,
            "privacy_ignored_local_workspace_ids": privacy_ignored_local_workspace_ids,
            "privacy_ignored_remote_workspace_ids": privacy_ignored_remote_workspace_ids,
            "workspace_mode": workspace_mode,
            "effective_namespace": local_result.get("effective_namespace"),
            "item_selections": item_selections,
            "data_checkpoint": data_checkpoint,
            "deployment_event": deployment_event,
            "result": local_result,
        }
        retired_pushes = _retire_pending_pushes_after_pull(
            operation,
            remote_url=remote.instance_base,
        )
        response_payload["superseded_pending_pushes"] = retired_pushes
        _finish_sync_operation(
            operation,
            status="completed",
            result={
                "direction": "pull",
                "remote": remote.instance_base,
                "workspace_mode": workspace_mode,
                "data_checkpoint_state": data_checkpoint.get("state"),
                "superseded_pending_pushes": retired_pushes.get("count", 0),
                "superseded_pending_push_ids": retired_pushes.get("operation_ids", []),
            },
        )
        return response_payload
    except HTTPException as exc:
        _finish_sync_operation(operation, status="failed", error=str(exc.detail))
        raise
    except requests.RequestException as exc:
        _finish_sync_operation(operation, status="failed", error=str(exc))
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
                "local_target_workspace_id": payload.local_target_workspace_id or "",
                "remote_target_workspace_id": payload.remote_target_workspace_id or "",
            },
            exc=exc,
        )
        raise HTTPException(status_code=502, detail=f"Remote sync failed: {exc}")
    except ValueError as exc:
        _finish_sync_operation(operation, status="failed", error=str(exc))
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
