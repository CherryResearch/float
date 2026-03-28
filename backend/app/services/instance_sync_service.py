from __future__ import annotations

import base64
import copy
import ipaddress
import json
import logging
import socket
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

import requests
from app.utils import blob_store, calendar_store, conversation_store
from app.utils import graph_store as graph_store_module
from app.utils import knowledge_store as knowledge_store_module
from app.utils import memory_store, user_settings
from app.utils.attachment_media import build_attachment_media_descriptor
from app.utils.blob_store import BLOBS_DIR, find_asset_path
from app.utils.workspace_registry import resolve_workspace_selection

logger = logging.getLogger(__name__)

SYNC_SECTION_LABELS: Dict[str, str] = {
    "conversations": "Conversations",
    "memories": "Memories",
    "knowledge": "Knowledge",
    "graph": "Knowledge graph",
    "attachments": "Attachments",
    "calendar": "Calendar",
    "settings": "Workspace preferences",
}
SYNC_SECTION_ORDER = list(SYNC_SECTION_LABELS.keys())
SYNC_NAMESPACE_TOKEN_SEPARATOR = "__"
SYNC_SOURCE_LINK_SECTIONS = {
    "conversations",
    "memories",
    "knowledge",
    "graph",
    "attachments",
    "calendar",
}
ROOT_ATTACHMENT_PATH_SEGMENTS = {
    "uploads",
    "captured",
    "screenshots",
    "downloaded",
    "workspace",
}
SYNCABLE_USER_SETTING_KEYS = (
    "history",
    "approval_level",
    "theme",
    "push_enabled",
    "calendar_notify_minutes",
    "tool_resolution_notifications",
    "user_timezone",
    "export_default_format",
    "export_default_include_chat",
    "export_default_include_thoughts",
    "export_default_include_tools",
    "system_prompt_base",
    "system_prompt_custom",
    "conversation_folders",
    "tool_display_mode",
    "tool_link_behavior",
    "live_transcript_enabled",
    "live_camera_default_enabled",
    "sync_link_to_source_device",
    "sync_source_namespace",
    "workspace_profiles",
    "active_workspace_id",
    "sync_selected_workspace_ids",
)
_ATTACHMENT_ORIGIN_DIRS = {
    "upload": "uploads",
    "captured": "captured",
    "screenshot": "screenshots",
}


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _safe_float(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except Exception:
        return 0.0


def _coerce_timestamp(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    try:
        return float(raw)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _json_dumps(value: Any) -> str:
    return json.dumps(
        value if value is not None else {},
        ensure_ascii=False,
        default=memory_store._default_serializer,
    )


def _format_sync_timestamp(value: Any) -> str:
    ts = _safe_float(value)
    if ts <= 0:
        return ""
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        )
    except Exception:
        return ""


def _format_byte_count(value: Any) -> str:
    try:
        size = int(value or 0)
    except Exception:
        size = 0
    if size <= 0:
        return ""
    units = ["B", "KB", "MB", "GB", "TB"]
    amount = float(size)
    unit_index = 0
    while amount >= 1024.0 and unit_index < len(units) - 1:
        amount /= 1024.0
        unit_index += 1
    if unit_index == 0:
        return f"{int(amount)} {units[unit_index]}"
    return f"{amount:.1f} {units[unit_index]}"


def _load_attachment_meta(content_hash: str) -> Dict[str, Any]:
    meta_path = BLOBS_DIR / f"{content_hash}.json"
    if not meta_path.exists():
        return {}
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_attachment_meta(content_hash: str, metadata: Dict[str, Any]) -> None:
    meta_path = BLOBS_DIR / f"{content_hash}.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")


def _resolve_attachment_updated_at(
    metadata: Dict[str, Any],
    *,
    fallback_path: Optional[Path] = None,
) -> float:
    for key in ("caption_updated_at", "indexed_at", "uploaded_at", "updated_at"):
        ts = _coerce_timestamp(metadata.get(key))
        if ts > 0:
            return ts
    if fallback_path is not None and fallback_path.exists():
        try:
            return float(fallback_path.stat().st_mtime)
        except Exception:
            return 0.0
    return 0.0


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
    files_dir = blob_store._resolve_data_files_root()
    for dirname in set(_ATTACHMENT_ORIGIN_DIRS.values()) | {"downloaded", "workspace"}:
        root = files_dir / dirname
        if not root.exists() or not root.is_dir():
            continue
        for child in root.iterdir():
            if child.is_dir():
                hashes.add(child.name)
    return sorted(hash_value for hash_value in hashes if hash_value)


def _safe_attachment_filename(value: Any, fallback: str) -> str:
    name = Path(str(value or "").strip()).name
    return name or fallback


def _coerce_relative_files_path(value: str) -> str:
    raw = str(value or "").strip().replace("\\", "/").lstrip("/")
    if not raw:
        return ""
    parts = [
        segment for segment in raw.split("/") if segment and segment not in {".", ".."}
    ]
    return "/".join(parts)


def _prefix_path(namespace: str, value: str) -> str:
    cleaned = _coerce_relative_files_path(value)
    if not cleaned:
        return namespace
    if cleaned == namespace or cleaned.startswith(f"{namespace}/"):
        return cleaned
    return f"{namespace}/{cleaned}"


def _prefix_token(namespace: str, value: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        return namespace
    token_prefix = f"{namespace}{SYNC_NAMESPACE_TOKEN_SEPARATOR}"
    if cleaned == namespace or cleaned.startswith(token_prefix):
        return cleaned
    return f"{token_prefix}{cleaned}"


def _namespace_from_prefixed_token(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw or SYNC_NAMESPACE_TOKEN_SEPARATOR not in raw:
        return ""
    namespace, _, _rest = raw.partition(SYNC_NAMESPACE_TOKEN_SEPARATOR)
    return _coerce_relative_files_path(namespace)


def _attachment_path_namespace(value: Any) -> Optional[str]:
    relative_path = _coerce_relative_files_path(value)
    if not relative_path:
        return None
    namespace, _, _rest = relative_path.partition("/")
    if namespace in ROOT_ATTACHMENT_PATH_SEGMENTS:
        return ""
    return namespace


def _namespace_matches_selection(
    namespace: str,
    *,
    include_root: bool,
    namespaces: List[str],
) -> bool:
    cleaned = _coerce_relative_files_path(namespace)
    if not cleaned:
        return include_root
    return any(
        cleaned == candidate or cleaned.startswith(f"{candidate}/")
        for candidate in namespaces
    )


def _resolve_attachment_target(
    content_hash: str,
    *,
    filename: Optional[str] = None,
) -> Optional[Path]:
    metadata = _load_attachment_meta(content_hash)
    files_dir = blob_store._resolve_data_files_root()
    rel_candidate = _coerce_relative_files_path(
        str(metadata.get("relative_path") or metadata.get("source_path") or "").strip()
    )
    if rel_candidate:
        target = (files_dir / rel_candidate).resolve()
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
    target = find_asset_path(content_hash, filename=filename)
    if target is not None and target.exists() and target.is_file():
        return target
    blob_target = BLOBS_DIR / content_hash
    if blob_target.exists() and blob_target.is_file():
        return blob_target
    return None


def _write_conversation_snapshot(
    *,
    name: str,
    messages: List[Dict[str, Any]],
    metadata: Dict[str, Any],
) -> None:
    target = conversation_store._path(name)
    target.parent.mkdir(parents=True, exist_ok=True)

    def _serialize(obj: Any) -> Any:
        if isinstance(obj, bytes):
            return obj.decode("utf-8", errors="replace")
        raise TypeError(
            f"Object of type {obj.__class__.__name__} is not JSON serializable"
        )

    with target.open("w", encoding="utf-8") as handle:
        json.dump(messages, handle, indent=2, default=_serialize)
    next_meta = dict(metadata or {})
    next_meta["name"] = name
    next_meta.setdefault("id", str(uuid.uuid4()))
    next_meta.setdefault("created_at", next_meta.get("updated_at") or _now_iso())
    next_meta.setdefault("updated_at", next_meta.get("created_at") or _now_iso())
    next_meta.setdefault("display_name", next_meta.get("display_name") or name)
    next_meta.setdefault("auto_title_applied", False)
    next_meta.setdefault("manual_title", False)
    next_meta["message_count"] = len(messages)
    conversation_store._write_meta(name, next_meta)


def _portable_settings_snapshot() -> Dict[str, Any]:
    settings = user_settings.load_settings()
    return {
        key: settings.get(key) for key in SYNCABLE_USER_SETTING_KEYS if key in settings
    }


def _settings_updated_at() -> float:
    settings = user_settings.load_settings()
    ts = _coerce_timestamp(settings.get("updated_at"))
    if ts > 0:
        return ts
    try:
        return float(user_settings.USER_SETTINGS_PATH.stat().st_mtime)
    except Exception:
        return 0.0


def _write_settings_snapshot(payload: Dict[str, Any], updated_at: Any) -> None:
    current = user_settings.load_settings()
    next_settings = dict(current)
    next_settings.update(
        {key: payload.get(key) for key in SYNCABLE_USER_SETTING_KEYS if key in payload}
    )
    timestamp = str(updated_at or "")
    next_settings["updated_at"] = timestamp or _now_iso()
    user_settings.USER_SETTINGS_PATH.write_text(
        json.dumps(next_settings, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _resolve_remote_urls(remote_url: str) -> Dict[str, str]:
    raw = str(remote_url or "").strip()
    if not raw:
        raise ValueError("Remote URL is required")
    if "://" not in raw:
        raw = f"http://{raw}"
    parsed = urlparse(raw)
    hostname = str(parsed.hostname or "").strip()
    if not parsed.netloc or not hostname:
        raise ValueError("Remote URL is invalid")
    if all(char.isdigit() or char == "." for char in hostname):
        try:
            ipaddress.ip_address(hostname)
        except ValueError as exc:
            raise ValueError(
                "Remote URL looks incomplete. Use the full private address, for example 192.168.1.25:59185."
            ) from exc
    path = parsed.path.rstrip("/")
    if path.endswith("/api"):
        api_path = path
        instance_path = path[:-4]
    else:
        api_path = f"{path}/api" if path else "/api"
        instance_path = path
    instance_base = (
        parsed._replace(path=instance_path, params="", query="", fragment="")
        .geturl()
        .rstrip("/")
    )
    api_base = (
        parsed._replace(path=api_path, params="", query="", fragment="")
        .geturl()
        .rstrip("/")
    )
    return {"instance_base": instance_base, "api_base": api_base}


class RemoteFloatClient:
    def __init__(
        self,
        remote_url: str,
        *,
        timeout: float = 45.0,
        session: Optional[requests.Session] = None,
        paired_device: Optional[Dict[str, Any]] = None,
        device_name: Optional[str] = None,
    ) -> None:
        urls = _resolve_remote_urls(remote_url)
        self.instance_base = urls["instance_base"]
        self.api_base = urls["api_base"]
        self.timeout = float(timeout)
        self.session = session or requests.Session()
        self._token: Optional[str] = None
        self._paired_device = (
            dict(paired_device) if isinstance(paired_device, dict) else {}
        )
        self._device_name = (
            str(device_name or "").strip() or f"float-sync-{socket.gethostname()}"
        )

    def get_pairing_state(self) -> Dict[str, Any]:
        return dict(self._paired_device)

    def _requested_scopes(self) -> List[str]:
        raw = self._paired_device.get("scopes")
        if not isinstance(raw, list):
            return ["sync"]
        normalized: List[str] = []
        seen: set[str] = set()
        for item in raw:
            scope = str(item or "").strip().lower()
            if scope not in {"sync", "stream", "files"} or scope in seen:
                continue
            seen.add(scope)
            normalized.append(scope)
        return normalized or ["sync"]

    def _device_capabilities(self) -> Dict[str, Any]:
        scopes = self._requested_scopes()
        return {
            "instance_sync": True,
            "requested_scopes": scopes,
            "sync": "sync" in scopes,
            "stream": "stream" in scopes,
            "files": "files" in scopes,
        }

    def _peer_log_label(self) -> str:
        return (
            str(
                self._paired_device.get("remote_device_name")
                or self._paired_device.get("label")
                or ""
            ).strip()
            or "unknown"
        )

    def _response_excerpt(self, response: Optional[requests.Response]) -> str:
        if response is None:
            return ""
        try:
            body = str(response.text or "").strip()
        except Exception:
            return ""
        if not body:
            return ""
        compact = " ".join(body.split())
        return compact[:280]

    def _log_request_failure(
        self,
        *,
        method: str,
        path: str,
        with_auth: bool,
        exc: requests.RequestException,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> None:
        response = getattr(exc, "response", None)
        status_code = response.status_code if response is not None else None
        logger.warning(
            "Remote sync request failed: method=%s path=%s api_base=%s instance_base=%s with_auth=%s peer_id=%s peer_label=%s remote_device_id=%s requested_scopes=%s body_keys=%s status=%s error=%s response=%s",
            method.upper(),
            path.lstrip("/"),
            self.api_base,
            self.instance_base,
            with_auth,
            str(self._paired_device.get("id") or "").strip() or "-",
            self._peer_log_label(),
            str(self._paired_device.get("remote_device_id") or "").strip() or "-",
            ",".join(self._requested_scopes()) or "-",
            ",".join(sorted((json_body or {}).keys())) or "-",
            status_code if status_code is not None else "-",
            exc,
            self._response_excerpt(response) or "-",
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        with_auth: bool = False,
    ) -> Dict[str, Any]:
        url = f"{self.api_base}/{path.lstrip('/')}"
        headers: Dict[str, str] = {}
        if with_auth:
            if not self._token:
                self._bootstrap_token()
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            response = self.session.request(
                method.upper(),
                url,
                json=json_body,
                headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            self._log_request_failure(
                method=method,
                path=path,
                with_auth=with_auth,
                exc=exc,
                json_body=json_body,
            )
            raise
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def get_sync_overview(self) -> Dict[str, Any]:
        try:
            response = self.session.get(
                f"{self.api_base}/sync/overview",
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            self._log_request_failure(
                method="get",
                path="sync/overview",
                with_auth=False,
                exc=exc,
            )
            raise
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def sync_device_registration(self) -> Dict[str, Any]:
        scopes = self._requested_scopes()
        device_id = str(self._paired_device.get("remote_device_id") or "").strip()
        if not device_id:
            self._bootstrap_token()
            return self.get_pairing_state()
        payload = self._request(
            "patch",
            f"devices/{device_id}",
            json_body={
                "name": self._device_name,
                "capabilities": self._device_capabilities(),
            },
            with_auth=True,
        )
        device = (
            payload.get("device") if isinstance(payload.get("device"), dict) else {}
        )
        self._paired_device.update(
            {
                "remote_url": self.instance_base,
                "remote_device_id": str(device.get("id") or device_id).strip(),
                "public_key": str(self._paired_device.get("public_key") or "").strip(),
                "scopes": scopes,
            }
        )
        return self.get_pairing_state()

    def delete_remote_device(self) -> None:
        device_id = str(self._paired_device.get("remote_device_id") or "").strip()
        if not device_id:
            raise ValueError("Paired device is missing a remote device id")
        self._request("delete", f"devices/{device_id}", with_auth=True)

    def _bootstrap_token(self) -> None:
        scopes = self._requested_scopes()
        device_id = str(self._paired_device.get("remote_device_id") or "").strip()
        if device_id:
            try:
                issued = self._request(
                    "post",
                    "devices/token",
                    json_body={
                        "device_id": device_id,
                        "scopes": scopes,
                        "ttl_seconds": 3600,
                    },
                )
                token = str(issued.get("token") or "").strip()
                if not token:
                    raise ValueError("Remote token issuance failed")
                self._token = token
                try:
                    self._request(
                        "patch",
                        f"devices/{device_id}",
                        json_body={
                            "name": self._device_name,
                            "capabilities": self._device_capabilities(),
                        },
                        with_auth=True,
                    )
                except requests.RequestException:
                    pass
                return
            except requests.HTTPError as exc:
                status_code = (
                    exc.response.status_code if exc.response is not None else None
                )
                if status_code not in {400, 404}:
                    raise
        public_key = str(self._paired_device.get("public_key") or "").strip() or str(
            uuid.uuid4()
        )
        register_payload = {
            "public_key": public_key,
            "name": self._device_name,
            "capabilities": self._device_capabilities(),
        }
        registered = self._request(
            "post", "devices/register", json_body=register_payload
        )
        device = (
            registered.get("device")
            if isinstance(registered.get("device"), dict)
            else {}
        )
        device_id = str(device.get("id") or "").strip()
        if not device_id:
            raise ValueError("Remote device registration did not return an id")
        self._paired_device.update(
            {
                "remote_url": self.instance_base,
                "remote_device_id": device_id,
                "public_key": public_key,
                "scopes": scopes,
            }
        )
        issued = self._request(
            "post",
            "devices/token",
            json_body={
                "device_id": device_id,
                "scopes": scopes,
                "ttl_seconds": 3600,
            },
        )
        token = str(issued.get("token") or "").strip()
        if not token:
            raise ValueError("Remote token issuance failed")
        self._token = token

    def get_manifest(
        self,
        sections: Optional[List[str]] = None,
        *,
        workspace_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        return self._request(
            "post",
            "sync/manifest",
            json_body={
                "sections": sections or None,
                "workspace_ids": workspace_ids or None,
            },
            with_auth=True,
        )

    def export_snapshot(
        self,
        sections: Optional[List[str]] = None,
        *,
        workspace_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        return self._request(
            "post",
            "sync/export",
            json_body={
                "sections": sections or None,
                "workspace_ids": workspace_ids or None,
            },
            with_auth=True,
        )

    def ingest_snapshot(
        self,
        snapshot: Dict[str, Any],
        *,
        link_to_source: bool = False,
        source_namespace: Optional[str] = None,
        source_label: Optional[str] = None,
        target_namespace: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._request(
            "post",
            "sync/ingest",
            json_body={
                "snapshot": snapshot,
                "link_to_source": link_to_source,
                "source_namespace": source_namespace,
                "source_label": source_label,
                "target_namespace": target_namespace,
            },
            with_auth=True,
        )


class InstanceSyncService:
    def _default_source_label(self) -> str:
        settings = user_settings.load_settings()
        display_name = str(settings.get("device_display_name") or "").strip()
        return display_name or socket.gethostname()

    def normalize_sections(self, sections: Optional[Iterable[str]]) -> List[str]:
        if not sections:
            return list(SYNC_SECTION_ORDER)
        seen: set[str] = set()
        normalized: List[str] = []
        for value in sections:
            key = str(value or "").strip().lower()
            if key not in SYNC_SECTION_LABELS or key in seen:
                continue
            seen.add(key)
            normalized.append(key)
        return normalized or list(SYNC_SECTION_ORDER)

    def resolve_source_namespace(
        self,
        *,
        link_to_source: bool = False,
        source_namespace: Optional[str] = None,
        source_label: Optional[str] = None,
    ) -> str:
        if not link_to_source:
            return ""
        candidate = _coerce_relative_files_path(source_namespace or "")
        if candidate:
            return candidate
        fallback = _coerce_relative_files_path(source_label or "")
        return fallback or "remote"

    def current_instance_identity(
        self,
        *,
        source_namespace: Optional[str] = None,
    ) -> Dict[str, Any]:
        hostname = socket.gethostname()
        display_name = self._default_source_label()
        declared_namespace = self.resolve_source_namespace(
            link_to_source=True,
            source_namespace=source_namespace
            or user_settings.load_settings().get("sync_source_namespace"),
            source_label=display_name or hostname,
        )
        return {
            "hostname": hostname,
            "display_name": display_name,
            "source_namespace": declared_namespace,
            "link_to_source_default": bool(
                user_settings.load_settings().get("sync_link_to_source_device")
            ),
        }

    def _manifest_item_namespace(self, section: str, item: Dict[str, Any]) -> str:
        if not isinstance(item, dict):
            return ""
        if section == "conversations":
            return _namespace_from_prefixed_token(item.get("sync_id")) or (
                _coerce_relative_files_path(item.get("source_sync_namespace"))
            )
        if section == "memories":
            return _namespace_from_prefixed_token(
                item.get("key") or item.get("sync_id")
            ) or (_coerce_relative_files_path(item.get("source_sync_namespace")))
        if section == "knowledge":
            return _namespace_from_prefixed_token(
                item.get("knowledge_id") or item.get("sync_id")
            ) or (_coerce_relative_files_path(item.get("source_sync_namespace")))
        if section == "graph":
            return _namespace_from_prefixed_token(item.get("sync_id")) or (
                _coerce_relative_files_path(item.get("source_sync_namespace"))
            )
        if section == "attachments":
            path_namespace = _attachment_path_namespace(
                item.get("relative_path") or item.get("source_path")
            )
            if path_namespace is not None:
                return path_namespace
            return _coerce_relative_files_path(item.get("source_sync_namespace"))
        if section == "calendar":
            return _namespace_from_prefixed_token(
                item.get("event_id") or item.get("sync_id")
            ) or (_coerce_relative_files_path(item.get("source_sync_namespace")))
        return ""

    def _snapshot_record_namespace(self, section: str, record: Dict[str, Any]) -> str:
        if not isinstance(record, dict):
            return ""
        if section == "conversations":
            metadata = (
                record.get("metadata")
                if isinstance(record.get("metadata"), dict)
                else {}
            )
            return _namespace_from_prefixed_token(
                metadata.get("id") or record.get("sync_id")
            ) or _coerce_relative_files_path(
                metadata.get("source_sync_namespace")
            )
        if section == "memories":
            payload = (
                record.get("payload") if isinstance(record.get("payload"), dict) else {}
            )
            return _namespace_from_prefixed_token(
                record.get("key") or record.get("sync_id")
            ) or _coerce_relative_files_path(
                payload.get("source_sync_namespace")
            )
        if section == "knowledge":
            metadata = (
                record.get("metadata")
                if isinstance(record.get("metadata"), dict)
                else {}
            )
            return _namespace_from_prefixed_token(
                record.get("knowledge_id") or record.get("sync_id")
            ) or _coerce_relative_files_path(
                metadata.get("source_sync_namespace")
            )
        if section == "graph":
            if "node_id" in record:
                attributes = (
                    record.get("attributes")
                    if isinstance(record.get("attributes"), dict)
                    else {}
                )
                return _namespace_from_prefixed_token(record.get("node_id")) or (
                    _coerce_relative_files_path(attributes.get("source_sync_namespace"))
                )
            metadata = (
                record.get("metadata")
                if isinstance(record.get("metadata"), dict)
                else {}
            )
            return _namespace_from_prefixed_token(record.get("claim_id")) or (
                _coerce_relative_files_path(metadata.get("source_sync_namespace"))
            )
        if section == "attachments":
            metadata = (
                record.get("metadata")
                if isinstance(record.get("metadata"), dict)
                else {}
            )
            path_namespace = _attachment_path_namespace(
                metadata.get("relative_path") or metadata.get("source_path")
            )
            if path_namespace is not None:
                return path_namespace
            return _coerce_relative_files_path(metadata.get("source_sync_namespace"))
        if section == "calendar":
            payload = (
                record.get("payload") if isinstance(record.get("payload"), dict) else {}
            )
            return _namespace_from_prefixed_token(
                record.get("event_id") or record.get("sync_id")
            ) or _coerce_relative_files_path(
                payload.get("source_sync_namespace")
            )
        return ""

    def _filter_manifest_by_workspaces(
        self, manifest: Dict[str, Any], workspace_ids: Optional[Iterable[str]]
    ) -> Dict[str, Any]:
        selection = resolve_workspace_selection(workspace_ids)
        sections = manifest.get("sections") if isinstance(manifest, dict) else {}
        if not isinstance(sections, dict):
            return manifest
        filtered = copy.deepcopy(manifest)
        filtered_sections = filtered.get("sections") or {}
        for section in self.normalize_sections(filtered_sections.keys()):
            if section == "settings":
                continue
            payload = filtered_sections.get(section)
            items = payload.get("items") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                continue
            payload["items"] = [
                item
                for item in items
                if _namespace_matches_selection(
                    self._manifest_item_namespace(section, item),
                    include_root=selection["include_root"],
                    namespaces=selection["namespaces"],
                )
            ]
            payload["count"] = len(payload["items"])
        filtered["workspace_selection"] = {
            "workspace_ids": selection["selected_workspace_ids"],
            "include_root": selection["include_root"],
        }
        return filtered

    def _filter_snapshot_by_workspaces(
        self, snapshot: Dict[str, Any], workspace_ids: Optional[Iterable[str]]
    ) -> Dict[str, Any]:
        selection = resolve_workspace_selection(workspace_ids)
        sections = snapshot.get("sections") if isinstance(snapshot, dict) else {}
        if not isinstance(sections, dict):
            return snapshot
        filtered = copy.deepcopy(snapshot)
        filtered_sections = filtered.get("sections") or {}
        for section in self.normalize_sections(filtered_sections.keys()):
            if section == "settings":
                continue
            payload = filtered_sections.get(section)
            if section == "graph" and isinstance(payload, dict):
                payload["nodes"] = [
                    node
                    for node in payload.get("nodes") or []
                    if _namespace_matches_selection(
                        self._snapshot_record_namespace("graph", node),
                        include_root=selection["include_root"],
                        namespaces=selection["namespaces"],
                    )
                ]
                payload["claims"] = [
                    claim
                    for claim in payload.get("claims") or []
                    if _namespace_matches_selection(
                        self._snapshot_record_namespace("graph", claim),
                        include_root=selection["include_root"],
                        namespaces=selection["namespaces"],
                    )
                ]
                continue
            if not isinstance(payload, list):
                continue
            filtered_sections[section] = [
                record
                for record in payload
                if _namespace_matches_selection(
                    self._snapshot_record_namespace(section, record),
                    include_root=selection["include_root"],
                    namespaces=selection["namespaces"],
                )
            ]
        filtered["workspace_selection"] = {
            "workspace_ids": selection["selected_workspace_ids"],
            "include_root": selection["include_root"],
        }
        return filtered

    def build_manifest(
        self,
        sections: Optional[Iterable[str]] = None,
        workspace_ids: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        selected = self.normalize_sections(sections)
        payload: Dict[str, Any] = {
            "version": 1,
            "generated_at": _now_iso(),
            "instance": self.current_instance_identity(),
            "sections": {},
        }
        for section in selected:
            items = self._manifest_items_for_section(section)
            payload["sections"][section] = {
                "label": SYNC_SECTION_LABELS[section],
                "count": len(items),
                "items": items,
            }
        return self._filter_manifest_by_workspaces(payload, workspace_ids)

    def build_snapshot(
        self,
        sections: Optional[Iterable[str]] = None,
        workspace_ids: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        selected = self.normalize_sections(sections)
        payload: Dict[str, Any] = {
            "version": 1,
            "generated_at": _now_iso(),
            "instance": self.current_instance_identity(),
            "sections": {},
        }
        for section in selected:
            payload["sections"][section] = self._snapshot_for_section(section)
        return self._filter_snapshot_by_workspaces(payload, workspace_ids)

    def namespace_manifest(
        self,
        manifest: Dict[str, Any],
        *,
        namespace: str,
    ) -> Dict[str, Any]:
        if not namespace or not isinstance(manifest, dict):
            return manifest
        namespaced = copy.deepcopy(manifest)
        sections = namespaced.get("sections")
        if not isinstance(sections, dict):
            return namespaced
        for section, payload in list(sections.items()):
            items = payload.get("items") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                continue
            updated_items: List[Dict[str, Any]] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                next_item = dict(item)
                sync_id = str(next_item.get("sync_id") or "").strip()
                original_sync_id = str(
                    next_item.get("original_sync_id") or sync_id
                ).strip()
                if original_sync_id:
                    next_item["original_sync_id"] = original_sync_id
                if section == "conversations":
                    if sync_id:
                        next_item["sync_id"] = _prefix_token(namespace, sync_id)
                    if next_item.get("name"):
                        next_item["name"] = _prefix_path(
                            namespace, str(next_item["name"])
                        )
                    if next_item.get("path"):
                        next_item["path"] = _prefix_path(
                            namespace, str(next_item["path"])
                        )
                elif section in {"memories", "knowledge", "graph", "calendar"}:
                    if sync_id:
                        next_item["sync_id"] = _prefix_token(namespace, sync_id)
                    if section == "calendar" and next_item.get("event_id"):
                        next_item["event_id"] = _prefix_token(
                            namespace,
                            str(next_item["event_id"]),
                        )
                updated_items.append(next_item)
            payload["items"] = updated_items
        return namespaced

    def namespace_snapshot(
        self,
        snapshot: Dict[str, Any],
        *,
        namespace: str,
        source_label: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not namespace or not isinstance(snapshot, dict):
            return snapshot
        namespaced = copy.deepcopy(snapshot)
        sections = namespaced.get("sections")
        if not isinstance(sections, dict):
            return namespaced
        for section, payload in list(sections.items()):
            if section == "conversations":
                updated = []
                for record in payload or []:
                    if not isinstance(record, dict):
                        continue
                    metadata = (
                        dict(record.get("metadata"))
                        if isinstance(record.get("metadata"), dict)
                        else {}
                    )
                    original_name = str(record.get("name") or "").strip()
                    original_id = str(
                        metadata.get("id") or record.get("sync_id") or original_name
                    ).strip()
                    metadata["id"] = _prefix_token(namespace, original_id)
                    metadata["source_sync_namespace"] = namespace
                    metadata["source_sync_label"] = source_label or namespace
                    metadata["source_sync_original_id"] = original_id
                    metadata["source_sync_original_name"] = original_name
                    updated.append(
                        {
                            **record,
                            "sync_id": metadata["id"],
                            "name": _prefix_path(namespace, original_name),
                            "metadata": metadata,
                        }
                    )
                sections[section] = updated
            elif section == "memories":
                updated = []
                for record in payload or []:
                    if not isinstance(record, dict):
                        continue
                    original_key = str(
                        record.get("key") or record.get("sync_id") or ""
                    ).strip()
                    payload_value = record.get("payload")
                    if isinstance(payload_value, dict):
                        payload_value = dict(payload_value)
                        payload_value["source_sync_namespace"] = namespace
                        payload_value["source_sync_label"] = source_label or namespace
                        payload_value["source_sync_original_key"] = original_key
                    updated.append(
                        {
                            **record,
                            "sync_id": _prefix_token(namespace, original_key),
                            "key": _prefix_token(namespace, original_key),
                            "payload": payload_value,
                        }
                    )
                sections[section] = updated
            elif section == "knowledge":
                updated = []
                for record in payload or []:
                    if not isinstance(record, dict):
                        continue
                    metadata = (
                        dict(record.get("metadata"))
                        if isinstance(record.get("metadata"), dict)
                        else {}
                    )
                    original_id = str(
                        record.get("knowledge_id") or record.get("sync_id") or ""
                    ).strip()
                    original_source = str(record.get("source") or "").strip()
                    namespaced_id = _prefix_token(namespace, original_id)
                    namespaced_source = _prefix_path(namespace, original_source)
                    metadata["source_sync_namespace"] = namespace
                    metadata["source_sync_label"] = source_label or namespace
                    metadata["source_sync_original_id"] = original_id
                    metadata["source_sync_original_source"] = original_source
                    metadata["source"] = namespaced_source
                    chunks = []
                    for chunk in record.get("chunks") or []:
                        if not isinstance(chunk, dict):
                            continue
                        chunk_metadata = (
                            dict(chunk.get("metadata"))
                            if isinstance(chunk.get("metadata"), dict)
                            else {}
                        )
                        chunk_metadata["source_sync_namespace"] = namespace
                        chunk_metadata["source"] = _prefix_path(
                            namespace,
                            str(chunk.get("source") or original_source),
                        )
                        chunk_metadata["root_source"] = namespaced_source
                        chunks.append(
                            {
                                **chunk,
                                "chunk_id": _prefix_token(
                                    namespace,
                                    str(chunk.get("chunk_id") or ""),
                                ),
                                "knowledge_id": namespaced_id,
                                "source": chunk_metadata["source"],
                                "root_source": namespaced_source,
                                "metadata": chunk_metadata,
                            }
                        )
                    updated.append(
                        {
                            **record,
                            "sync_id": namespaced_id,
                            "knowledge_id": namespaced_id,
                            "source": namespaced_source,
                            "metadata": metadata,
                            "chunks": chunks,
                        }
                    )
                sections[section] = updated
            elif section == "graph":
                if isinstance(payload, dict):
                    nodes = []
                    for node in payload.get("nodes") or []:
                        if not isinstance(node, dict):
                            continue
                        attributes = (
                            dict(node.get("attributes"))
                            if isinstance(node.get("attributes"), dict)
                            else {}
                        )
                        attributes["source_sync_namespace"] = namespace
                        nodes.append(
                            {
                                **node,
                                "node_id": _prefix_token(
                                    namespace,
                                    str(node.get("node_id") or ""),
                                ),
                                "attributes": attributes,
                            }
                        )
                    claims = []
                    for claim in payload.get("claims") or []:
                        if not isinstance(claim, dict):
                            continue
                        metadata = (
                            dict(claim.get("metadata"))
                            if isinstance(claim.get("metadata"), dict)
                            else {}
                        )
                        metadata["source_sync_namespace"] = namespace
                        roles = []
                        for role in claim.get("roles") or []:
                            if not isinstance(role, dict):
                                continue
                            roles.append(
                                {
                                    **role,
                                    "node_id": _prefix_token(
                                        namespace,
                                        str(role.get("node_id") or ""),
                                    )
                                    if role.get("node_id")
                                    else role.get("node_id"),
                                }
                            )
                        claims.append(
                            {
                                **claim,
                                "claim_id": _prefix_token(
                                    namespace,
                                    str(claim.get("claim_id") or ""),
                                ),
                                "source_ref": _prefix_path(
                                    namespace,
                                    str(claim.get("source_ref") or ""),
                                )
                                if claim.get("source_ref")
                                else claim.get("source_ref"),
                                "metadata": metadata,
                                "roles": roles,
                            }
                        )
                    sections[section] = {"nodes": nodes, "claims": claims}
            elif section == "attachments":
                updated = []
                for record in payload or []:
                    if not isinstance(record, dict):
                        continue
                    metadata = (
                        dict(record.get("metadata"))
                        if isinstance(record.get("metadata"), dict)
                        else {}
                    )
                    filename = _safe_attachment_filename(
                        record.get("filename"),
                        str(record.get("content_hash") or "file"),
                    )
                    namespaces = metadata.get("source_sync_namespaces")
                    if not isinstance(namespaces, list):
                        namespaces = []
                    if namespace not in namespaces:
                        namespaces = [*namespaces, namespace]
                    metadata["source_sync_namespaces"] = namespaces
                    metadata["source_sync_namespace"] = namespace
                    metadata["source_sync_label"] = source_label or namespace
                    original_relative_path = _coerce_relative_files_path(
                        str(
                            metadata.get("relative_path")
                            or metadata.get("source_path")
                            or ""
                        ).strip()
                    )
                    if original_relative_path:
                        metadata["source_sync_original_relative_path"] = (
                            metadata.get("source_sync_original_relative_path")
                            or original_relative_path
                        )
                        metadata["relative_path"] = _prefix_path(
                            namespace,
                            original_relative_path,
                        )
                        metadata["source_path"] = metadata["relative_path"]
                    metadata["source_sync_relative_path"] = (
                        metadata.get("source_sync_relative_path")
                        or f"workspace/sync/{namespace}/{record.get('content_hash')}/{filename}"
                    )
                    if not str(metadata.get("relative_path") or "").strip():
                        metadata["relative_path"] = metadata[
                            "source_sync_relative_path"
                        ]
                    updated.append({**record, "metadata": metadata})
                sections[section] = updated
            elif section == "calendar":
                updated = []
                for record in payload or []:
                    if not isinstance(record, dict):
                        continue
                    original_id = str(
                        record.get("event_id") or record.get("sync_id") or ""
                    ).strip()
                    event_payload = (
                        dict(record.get("payload"))
                        if isinstance(record.get("payload"), dict)
                        else {}
                    )
                    event_payload["source_sync_namespace"] = namespace
                    event_payload["source_sync_label"] = source_label or namespace
                    event_payload["source_sync_original_event_id"] = original_id
                    updated.append(
                        {
                            **record,
                            "sync_id": _prefix_token(namespace, original_id),
                            "event_id": _prefix_token(namespace, original_id),
                            "payload": event_payload,
                        }
                    )
                sections[section] = updated
        namespaced["namespace"] = {
            "namespace": namespace,
            "source_label": source_label or namespace,
        }
        return namespaced

    def annotate_snapshot_provenance(
        self,
        snapshot: Dict[str, Any],
        *,
        source_namespace: Optional[str] = None,
        source_label: Optional[str] = None,
        rewrite_attachment_paths: bool = False,
        preserve_namespace: bool = True,
    ) -> Dict[str, Any]:
        if not isinstance(snapshot, dict):
            return snapshot
        namespaced = copy.deepcopy(snapshot)
        sections = namespaced.get("sections")
        if not isinstance(sections, dict):
            return namespaced
        safe_namespace = (
            _coerce_relative_files_path(source_namespace or source_label or "")
            or "remote"
        )
        safe_label = str(source_label or safe_namespace).strip() or safe_namespace

        def _apply_namespace_marker(container: Dict[str, Any]) -> Dict[str, Any]:
            if preserve_namespace:
                container.setdefault("source_sync_namespace", safe_namespace)
            else:
                container.pop("source_sync_namespace", None)
            return container

        for section, payload in list(sections.items()):
            if section == "conversations":
                updated = []
                for record in payload or []:
                    if not isinstance(record, dict):
                        continue
                    metadata = (
                        dict(record.get("metadata"))
                        if isinstance(record.get("metadata"), dict)
                        else {}
                    )
                    metadata = _apply_namespace_marker(metadata)
                    metadata.setdefault("source_sync_label", safe_label)
                    metadata.setdefault(
                        "source_sync_original_id",
                        str(
                            metadata.get("id")
                            or record.get("sync_id")
                            or record.get("name")
                            or ""
                        ).strip(),
                    )
                    updated.append({**record, "metadata": metadata})
                sections[section] = updated
            elif section == "memories":
                updated = []
                for record in payload or []:
                    if not isinstance(record, dict):
                        continue
                    payload_value = (
                        dict(record.get("payload"))
                        if isinstance(record.get("payload"), dict)
                        else record.get("payload")
                    )
                    if isinstance(payload_value, dict):
                        payload_value = _apply_namespace_marker(payload_value)
                        payload_value.setdefault("source_sync_label", safe_label)
                        payload_value.setdefault(
                            "source_sync_original_key",
                            str(
                                record.get("key") or record.get("sync_id") or ""
                            ).strip(),
                        )
                    updated.append({**record, "payload": payload_value})
                sections[section] = updated
            elif section == "knowledge":
                updated = []
                for record in payload or []:
                    if not isinstance(record, dict):
                        continue
                    metadata = (
                        dict(record.get("metadata"))
                        if isinstance(record.get("metadata"), dict)
                        else {}
                    )
                    metadata = _apply_namespace_marker(metadata)
                    metadata.setdefault("source_sync_label", safe_label)
                    metadata.setdefault(
                        "source_sync_original_id",
                        str(
                            record.get("knowledge_id") or record.get("sync_id") or ""
                        ).strip(),
                    )
                    metadata.setdefault(
                        "source_sync_original_source",
                        str(record.get("source") or "").strip(),
                    )
                    chunks = []
                    for chunk in record.get("chunks") or []:
                        if not isinstance(chunk, dict):
                            continue
                        chunk_metadata = (
                            dict(chunk.get("metadata"))
                            if isinstance(chunk.get("metadata"), dict)
                            else {}
                        )
                        chunk_metadata = _apply_namespace_marker(chunk_metadata)
                        chunk_metadata.setdefault("source_sync_label", safe_label)
                        chunks.append({**chunk, "metadata": chunk_metadata})
                    updated.append({**record, "metadata": metadata, "chunks": chunks})
                sections[section] = updated
            elif section == "graph" and isinstance(payload, dict):
                nodes = []
                for node in payload.get("nodes") or []:
                    if not isinstance(node, dict):
                        continue
                    attributes = (
                        dict(node.get("attributes"))
                        if isinstance(node.get("attributes"), dict)
                        else {}
                    )
                    attributes = _apply_namespace_marker(attributes)
                    attributes.setdefault("source_sync_label", safe_label)
                    nodes.append({**node, "attributes": attributes})
                claims = []
                for claim in payload.get("claims") or []:
                    if not isinstance(claim, dict):
                        continue
                    metadata = (
                        dict(claim.get("metadata"))
                        if isinstance(claim.get("metadata"), dict)
                        else {}
                    )
                    metadata = _apply_namespace_marker(metadata)
                    metadata.setdefault("source_sync_label", safe_label)
                    claims.append({**claim, "metadata": metadata})
                sections[section] = {"nodes": nodes, "claims": claims}
            elif section == "attachments":
                updated = []
                for record in payload or []:
                    if not isinstance(record, dict):
                        continue
                    metadata = (
                        dict(record.get("metadata"))
                        if isinstance(record.get("metadata"), dict)
                        else {}
                    )
                    filename = _safe_attachment_filename(
                        record.get("filename"),
                        str(record.get("content_hash") or "file"),
                    )
                    metadata = _apply_namespace_marker(metadata)
                    metadata.setdefault("source_sync_label", safe_label)
                    original_relative_path = _coerce_relative_files_path(
                        str(
                            metadata.get("relative_path")
                            or metadata.get("source_path")
                            or ""
                        ).strip()
                    )
                    if original_relative_path:
                        metadata.setdefault(
                            "source_sync_original_relative_path", original_relative_path
                        )
                    custody_path = (
                        metadata.get("source_sync_relative_path")
                        or f"workspace/sync/{safe_namespace}/{record.get('content_hash')}/{filename}"
                    )
                    metadata["source_sync_relative_path"] = custody_path
                    if rewrite_attachment_paths:
                        metadata["relative_path"] = custody_path
                        metadata["source_path"] = custody_path
                    updated.append({**record, "metadata": metadata})
                sections[section] = updated
            elif section == "calendar":
                updated = []
                for record in payload or []:
                    if not isinstance(record, dict):
                        continue
                    event_payload = (
                        dict(record.get("payload"))
                        if isinstance(record.get("payload"), dict)
                        else {}
                    )
                    event_payload = _apply_namespace_marker(event_payload)
                    event_payload.setdefault("source_sync_label", safe_label)
                    event_payload.setdefault(
                        "source_sync_original_event_id",
                        str(
                            record.get("event_id") or record.get("sync_id") or ""
                        ).strip(),
                    )
                    updated.append({**record, "payload": event_payload})
                sections[section] = updated
        namespaced["source_custody"] = {
            "namespace": safe_namespace,
            "source_label": safe_label,
        }
        return namespaced

    def _snapshot_entries_for_section(
        self,
        section: str,
        payload: Any,
    ) -> Dict[str, Dict[str, Any]]:
        entries: Dict[str, Dict[str, Any]] = {}
        if section == "conversations" and isinstance(payload, list):
            for record in payload:
                if not isinstance(record, dict):
                    continue
                resource_id = str(
                    record.get("sync_id")
                    or (record.get("metadata") or {}).get("id")
                    or record.get("name")
                    or ""
                ).strip()
                if not resource_id:
                    continue
                label = (
                    (record.get("metadata") or {}).get("display_name")
                    or record.get("name")
                    or resource_id
                )
                entries[f"conversation:{resource_id}"] = {
                    "resource_type": "conversation",
                    "resource_id": resource_id,
                    "label": label,
                    "snapshot": record,
                }
        elif section == "memories" and isinstance(payload, list):
            for record in payload:
                if not isinstance(record, dict):
                    continue
                resource_id = str(
                    record.get("key") or record.get("sync_id") or ""
                ).strip()
                if not resource_id:
                    continue
                entries[f"memory:{resource_id}"] = {
                    "resource_type": "memory",
                    "resource_id": resource_id,
                    "label": resource_id,
                    "snapshot": record,
                }
        elif section == "knowledge" and isinstance(payload, list):
            for record in payload:
                if not isinstance(record, dict):
                    continue
                resource_id = str(
                    record.get("knowledge_id") or record.get("sync_id") or ""
                ).strip()
                if not resource_id:
                    continue
                label = record.get("title") or record.get("source") or resource_id
                entries[f"knowledge:{resource_id}"] = {
                    "resource_type": "knowledge",
                    "resource_id": resource_id,
                    "label": label,
                    "snapshot": record,
                }
        elif section == "graph" and isinstance(payload, dict):
            for node in payload.get("nodes") or []:
                if not isinstance(node, dict):
                    continue
                node_id = str(node.get("node_id") or "").strip()
                if not node_id:
                    continue
                resource_id = f"node:{node_id}"
                label = node.get("canonical_name") or node.get("node_type") or node_id
                entries[f"graph_node:{resource_id}"] = {
                    "resource_type": "graph_node",
                    "resource_id": resource_id,
                    "label": label,
                    "snapshot": node,
                }
            for claim in payload.get("claims") or []:
                if not isinstance(claim, dict):
                    continue
                claim_id = str(claim.get("claim_id") or "").strip()
                if not claim_id:
                    continue
                resource_id = f"claim:{claim_id}"
                label = claim.get("predicate") or claim_id
                entries[f"graph_claim:{resource_id}"] = {
                    "resource_type": "graph_claim",
                    "resource_id": resource_id,
                    "label": label,
                    "snapshot": claim,
                }
        elif section == "attachments" and isinstance(payload, list):
            for record in payload:
                if not isinstance(record, dict):
                    continue
                resource_id = str(
                    record.get("content_hash") or record.get("sync_id") or ""
                ).strip()
                if not resource_id:
                    continue
                label = record.get("filename") or resource_id
                entries[f"attachment:{resource_id}"] = {
                    "resource_type": "attachment",
                    "resource_id": resource_id,
                    "label": label,
                    "snapshot": record,
                }
        elif section == "calendar" and isinstance(payload, list):
            for record in payload:
                if not isinstance(record, dict):
                    continue
                resource_id = str(
                    record.get("event_id") or record.get("sync_id") or ""
                ).strip()
                if not resource_id:
                    continue
                payload_value = (
                    dict(record.get("payload"))
                    if isinstance(record.get("payload"), dict)
                    else {}
                )
                label = payload_value.get("title") or record.get("title") or resource_id
                entries[f"calendar_event:{resource_id}"] = {
                    "resource_type": "calendar_event",
                    "resource_id": resource_id,
                    "label": label,
                    "snapshot": record,
                }
        elif section == "settings" and isinstance(payload, dict):
            entries["settings:settings"] = {
                "resource_type": "settings",
                "resource_id": "settings",
                "label": "Workspace preferences",
                "snapshot": payload,
            }
        return entries

    def diff_snapshots(
        self,
        before_snapshot: Dict[str, Any],
        after_snapshot: Dict[str, Any],
        sections: Optional[Iterable[str]] = None,
    ) -> List[Dict[str, Any]]:
        selected = self.normalize_sections(sections)
        before_sections = (
            before_snapshot.get("sections") if isinstance(before_snapshot, dict) else {}
        )
        after_sections = (
            after_snapshot.get("sections") if isinstance(after_snapshot, dict) else {}
        )
        if not isinstance(before_sections, dict):
            before_sections = {}
        if not isinstance(after_sections, dict):
            after_sections = {}
        items: List[Dict[str, Any]] = []
        for section in selected:
            before_entries = self._snapshot_entries_for_section(
                section,
                before_sections.get(section),
            )
            after_entries = self._snapshot_entries_for_section(
                section,
                after_sections.get(section),
            )
            for resource_key in sorted(set(before_entries) | set(after_entries)):
                before_entry = before_entries.get(resource_key)
                after_entry = after_entries.get(resource_key)
                before_value = (
                    copy.deepcopy(before_entry["snapshot"]) if before_entry else None
                )
                after_value = (
                    copy.deepcopy(after_entry["snapshot"]) if after_entry else None
                )
                if before_value == after_value:
                    continue
                operation = (
                    "create"
                    if before_entry is None
                    else "delete"
                    if after_entry is None
                    else "update"
                )
                entry = after_entry or before_entry or {}
                items.append(
                    {
                        "id": f"{section}:{resource_key}",
                        "section": section,
                        "resource_type": entry.get("resource_type"),
                        "resource_id": entry.get("resource_id"),
                        "resource_key": resource_key,
                        "label": entry.get("label") or entry.get("resource_id"),
                        "operation": operation,
                        "before": before_value,
                        "after": after_value,
                        "revertible": True,
                    }
                )
        return items

    def _manifest_resource_type(self, section: str, item: Any) -> str:
        if not isinstance(item, dict):
            return section.rstrip("s")
        if section == "graph":
            kind = str(item.get("kind") or "").strip().lower()
            if kind == "claim":
                return "graph_claim"
            return "graph_node"
        if section == "calendar":
            return "calendar_event"
        if section == "conversations":
            return "conversation"
        if section == "memories":
            return "memory"
        if section == "attachments":
            return "attachment"
        return section.rstrip("s")

    def _manifest_item_label(self, section: str, item: Any, *, fallback: str) -> str:
        if not isinstance(item, dict):
            return fallback
        if section == "conversations":
            return str(item.get("display_name") or item.get("name") or fallback)
        if section == "memories":
            return str(item.get("key") or item.get("sync_id") or fallback)
        if section == "knowledge":
            return str(
                item.get("title")
                or item.get("source")
                or item.get("knowledge_id")
                or fallback
            )
        if section == "graph":
            return str(item.get("label") or item.get("predicate") or fallback)
        if section == "attachments":
            return str(item.get("filename") or item.get("content_hash") or fallback)
        if section == "calendar":
            return str(item.get("title") or item.get("event_id") or fallback)
        return fallback

    def _manifest_selection_id(self, item: Any, *, fallback: str) -> str:
        if not isinstance(item, dict):
            return fallback
        return str(item.get("original_sync_id") or item.get("sync_id") or fallback)

    def _manifest_item_detail(self, section: str, item: Any) -> str:
        if not isinstance(item, dict):
            return ""
        details: List[str] = []
        if section == "conversations":
            name = str(item.get("name") or "").strip()
            display_name = str(item.get("display_name") or "").strip()
            if name and name != display_name:
                details.append(name)
            message_count = int(item.get("message_count") or 0)
            if message_count > 0:
                details.append(
                    f"{message_count} message{'s' if message_count != 1 else ''}"
                )
        elif section == "memories":
            if item.get("archived"):
                details.append("archived")
        elif section == "knowledge":
            kind = str(item.get("kind") or "").strip()
            source = str(item.get("source") or "").strip()
            if kind:
                details.append(kind)
            if source:
                details.append(source)
        elif section == "graph":
            kind = str(item.get("kind") or "").strip()
            if kind:
                details.append(kind)
        elif section == "attachments":
            size_label = _format_byte_count(item.get("size"))
            if size_label:
                details.append(size_label)
        elif section == "calendar":
            event_id = str(item.get("event_id") or "").strip()
            title = str(item.get("title") or "").strip()
            if event_id and event_id != title:
                details.append(event_id)
        return " | ".join(detail for detail in details if detail)

    def _preview_item(
        self,
        section: str,
        *,
        status: str,
        sync_id: str,
        primary_item: Any,
        local_item: Any = None,
        remote_item: Any = None,
    ) -> Dict[str, Any]:
        label_item = primary_item if isinstance(primary_item, dict) else {}
        local_ts = _safe_float((local_item or {}).get("updated_at"))
        remote_ts = _safe_float((remote_item or {}).get("updated_at"))
        return {
            "resource_id": sync_id,
            "selection_id": self._manifest_selection_id(
                primary_item, fallback=sync_id
            ),
            "resource_type": self._manifest_resource_type(section, label_item),
            "label": self._manifest_item_label(
                section,
                label_item,
                fallback=sync_id,
            ),
            "detail": self._manifest_item_detail(section, label_item),
            "status": status,
            "local_label": self._manifest_item_label(
                section,
                local_item,
                fallback=sync_id,
            )
            if isinstance(local_item, dict)
            else "",
            "remote_label": self._manifest_item_label(
                section,
                remote_item,
                fallback=sync_id,
            )
            if isinstance(remote_item, dict)
            else "",
            "local_updated_at": local_ts or None,
            "remote_updated_at": remote_ts or None,
            "local_updated_at_label": _format_sync_timestamp(local_ts),
            "remote_updated_at_label": _format_sync_timestamp(remote_ts),
        }

    def compare_manifests(
        self,
        local_manifest: Dict[str, Any],
        remote_manifest: Dict[str, Any],
        sections: Optional[Iterable[str]] = None,
    ) -> List[Dict[str, Any]]:
        selected = self.normalize_sections(sections)
        comparison: List[Dict[str, Any]] = []
        local_sections = (
            local_manifest.get("sections") if isinstance(local_manifest, dict) else {}
        )
        remote_sections = (
            remote_manifest.get("sections") if isinstance(remote_manifest, dict) else {}
        )
        if not isinstance(local_sections, dict):
            local_sections = {}
        if not isinstance(remote_sections, dict):
            remote_sections = {}
        for section in selected:
            local_section = (
                local_sections.get(section)
                if isinstance(local_sections.get(section), dict)
                else {}
            )
            remote_section = (
                remote_sections.get(section)
                if isinstance(remote_sections.get(section), dict)
                else {}
            )
            local_items = (
                local_section.get("items") if isinstance(local_section, dict) else []
            )
            remote_items = (
                remote_section.get("items") if isinstance(remote_section, dict) else []
            )
            local_map = {
                str(item.get("sync_id")): item
                for item in local_items or []
                if isinstance(item, dict) and item.get("sync_id")
            }
            remote_map = {
                str(item.get("sync_id")): item
                for item in remote_items or []
                if isinstance(item, dict) and item.get("sync_id")
            }
            only_local = 0
            only_remote = 0
            local_newer = 0
            remote_newer = 0
            identical = 0
            preview_items: List[Dict[str, Any]] = []
            for sync_id in sorted(set(local_map) | set(remote_map)):
                local_item = local_map.get(sync_id)
                remote_item = remote_map.get(sync_id)
                if local_item is None:
                    only_remote += 1
                    preview_items.append(
                        self._preview_item(
                            section,
                            status="only_remote",
                            sync_id=sync_id,
                            primary_item=remote_item,
                            remote_item=remote_item,
                        )
                    )
                    continue
                if remote_item is None:
                    only_local += 1
                    preview_items.append(
                        self._preview_item(
                            section,
                            status="only_local",
                            sync_id=sync_id,
                            primary_item=local_item,
                            local_item=local_item,
                        )
                    )
                    continue
                local_ts = _safe_float(local_item.get("updated_at"))
                remote_ts = _safe_float(remote_item.get("updated_at"))
                if abs(local_ts - remote_ts) < 0.000001:
                    identical += 1
                elif local_ts > remote_ts:
                    local_newer += 1
                    preview_items.append(
                        self._preview_item(
                            section,
                            status="local_newer",
                            sync_id=sync_id,
                            primary_item=local_item,
                            local_item=local_item,
                            remote_item=remote_item,
                        )
                    )
                else:
                    remote_newer += 1
                    preview_items.append(
                        self._preview_item(
                            section,
                            status="remote_newer",
                            sync_id=sync_id,
                            primary_item=remote_item,
                            local_item=local_item,
                            remote_item=remote_item,
                        )
                    )
            comparison.append(
                {
                    "key": section,
                    "label": SYNC_SECTION_LABELS[section],
                    "local_count": len(local_map),
                    "remote_count": len(remote_map),
                    "only_local": only_local,
                    "only_remote": only_remote,
                    "local_newer": local_newer,
                    "remote_newer": remote_newer,
                    "identical": identical,
                    "change_count": len(preview_items),
                    "selected_by_default": bool(preview_items),
                    "items": preview_items[:12],
                    "all_items": preview_items,
                }
            )
        return comparison

    def normalize_item_selections(
        self,
        sections: Optional[Iterable[str]],
        selections: Any,
    ) -> Dict[str, List[str]]:
        selected_sections = self.normalize_sections(sections)
        if not isinstance(selections, dict):
            return {}
        normalized: Dict[str, List[str]] = {}
        for section in selected_sections:
            raw_items = selections.get(section)
            if not isinstance(raw_items, Iterable) or isinstance(
                raw_items, (str, bytes)
            ):
                continue
            seen: set[str] = set()
            chosen: List[str] = []
            for item in raw_items:
                sync_id = str(item or "").strip()
                if not sync_id or sync_id in seen:
                    continue
                seen.add(sync_id)
                chosen.append(sync_id)
            if chosen:
                normalized[section] = chosen
        return normalized

    def _snapshot_record_sync_id(self, section: str, record: Any) -> str:
        if not isinstance(record, dict):
            return ""
        if section == "graph":
            node_id = str(record.get("node_id") or "").strip()
            if node_id:
                return f"node:{node_id}"
            claim_id = str(record.get("claim_id") or "").strip()
            if claim_id:
                return f"claim:{claim_id}"
            return ""
        return str(
            record.get("sync_id")
            or record.get("knowledge_id")
            or record.get("content_hash")
            or record.get("event_id")
            or record.get("key")
            or ""
        ).strip()

    def filter_snapshot_by_item_selections(
        self,
        snapshot: Dict[str, Any],
        item_selections: Optional[Dict[str, List[str]]] = None,
    ) -> Dict[str, Any]:
        if not item_selections or not isinstance(snapshot, dict):
            return snapshot
        filtered = copy.deepcopy(snapshot)
        sections = filtered.get("sections")
        if not isinstance(sections, dict):
            return filtered
        for section, selected_items in item_selections.items():
            payload = sections.get(section)
            if payload is None:
                continue
            selected_ids = {
                str(item_id or "").strip()
                for item_id in selected_items
                if str(item_id or "").strip()
            }
            if section == "graph" and isinstance(payload, dict):
                selected_node_ids = {
                    item_id.partition(":")[2]
                    for item_id in selected_ids
                    if item_id.startswith("node:")
                }
                selected_claim_ids = {
                    item_id.partition(":")[2]
                    for item_id in selected_ids
                    if item_id.startswith("claim:")
                }
                next_claims: List[Dict[str, Any]] = []
                for claim in payload.get("claims") or []:
                    if not isinstance(claim, dict):
                        continue
                    claim_id = str(claim.get("claim_id") or "").strip()
                    if not claim_id or claim_id not in selected_claim_ids:
                        continue
                    next_claims.append(claim)
                    for role in claim.get("roles") or []:
                        if not isinstance(role, dict):
                            continue
                        node_id = str(role.get("node_id") or "").strip()
                        if node_id:
                            selected_node_ids.add(node_id)
                payload["claims"] = next_claims
                payload["nodes"] = [
                    node
                    for node in payload.get("nodes") or []
                    if isinstance(node, dict)
                    and str(node.get("node_id") or "").strip() in selected_node_ids
                ]
                continue
            if isinstance(payload, list):
                sections[section] = [
                    record
                    for record in payload
                    if self._snapshot_record_sync_id(section, record) in selected_ids
                ]
                continue
            if isinstance(payload, dict):
                record_id = self._snapshot_record_sync_id(section, payload)
                sections[section] = (
                    payload if record_id and record_id in selected_ids else {}
                )
        return filtered

    def merge_snapshot(
        self,
        snapshot: Dict[str, Any],
        *,
        link_to_source: bool = False,
        source_namespace: Optional[str] = None,
        source_label: Optional[str] = None,
        target_namespace: Optional[str] = None,
    ) -> Dict[str, Any]:
        sections = snapshot.get("sections") if isinstance(snapshot, dict) else {}
        if not isinstance(sections, dict):
            raise ValueError("Snapshot payload is invalid")
        snapshot_instance = (
            snapshot.get("instance")
            if isinstance(snapshot.get("instance"), dict)
            else {}
        )
        source_label_value = (
            source_label
            or snapshot_instance.get("display_name")
            or snapshot_instance.get("hostname")
        )
        source_namespace_value = source_namespace or snapshot_instance.get(
            "source_namespace"
        )
        effective_namespace = _coerce_relative_files_path(target_namespace or "")
        if not effective_namespace and any(
            section in SYNC_SOURCE_LINK_SECTIONS for section in sections
        ):
            effective_namespace = self.resolve_source_namespace(
                link_to_source=link_to_source,
                source_namespace=source_namespace_value,
                source_label=source_label_value,
            )
        working_snapshot = self.annotate_snapshot_provenance(
            snapshot,
            source_namespace=source_namespace_value,
            source_label=source_label_value,
            rewrite_attachment_paths=not bool(effective_namespace),
            preserve_namespace=bool(effective_namespace),
        )
        sections = working_snapshot.get("sections") or {}
        snapshot_instance = (
            working_snapshot.get("instance")
            if isinstance(working_snapshot.get("instance"), dict)
            else snapshot_instance
        )
        if effective_namespace:
            working_snapshot = self.namespace_snapshot(
                working_snapshot,
                namespace=effective_namespace,
                source_label=source_label_value,
            )
            sections = working_snapshot.get("sections") or {}
        result: Dict[str, Any] = {
            "applied_at": _now_iso(),
            "sections": {},
            "notes": [],
            "effective_namespace": effective_namespace or None,
        }
        for section in self.normalize_sections(sections.keys()):
            payload = sections.get(section)
            if payload is None:
                continue
            merged = self._merge_section(section, payload)
            result["sections"][section] = merged
            notes = merged.get("notes")
            if isinstance(notes, list):
                result["notes"].extend(str(note) for note in notes if note)
        return result

    def _manifest_items_for_section(self, section: str) -> List[Dict[str, Any]]:
        if section == "conversations":
            return self._conversation_manifest()
        if section == "memories":
            return self._memory_manifest()
        if section == "knowledge":
            return self._knowledge_manifest()
        if section == "graph":
            return self._graph_manifest()
        if section == "attachments":
            return self._attachment_manifest()
        if section == "calendar":
            return self._calendar_manifest()
        if section == "settings":
            return self._settings_manifest()
        return []

    def _snapshot_for_section(self, section: str) -> Any:
        if section == "conversations":
            return self._conversation_snapshot()
        if section == "memories":
            return self._memory_snapshot()
        if section == "knowledge":
            return self._knowledge_snapshot()
        if section == "graph":
            return self._graph_snapshot()
        if section == "attachments":
            return self._attachment_snapshot()
        if section == "calendar":
            return self._calendar_snapshot()
        if section == "settings":
            return self._settings_snapshot()
        return []

    def _merge_section(self, section: str, payload: Any) -> Dict[str, Any]:
        if section == "conversations":
            return self._merge_conversations(payload)
        if section == "memories":
            return self._merge_memories(payload)
        if section == "knowledge":
            return self._merge_knowledge(payload)
        if section == "graph":
            return self._merge_graph(payload)
        if section == "attachments":
            return self._merge_attachments(payload)
        if section == "calendar":
            return self._merge_calendar(payload)
        if section == "settings":
            return self._merge_settings(payload)
        return {"applied": 0, "skipped": 0}

    def _conversation_manifest(self) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for entry in conversation_store.list_conversations(include_metadata=True):
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            if not name:
                continue
            sync_id = str(entry.get("id") or f"name:{name}")
            items.append(
                {
                    "sync_id": sync_id,
                    "name": name,
                    "display_name": entry.get("display_name") or name,
                    "source_sync_namespace": entry.get("source_sync_namespace") or "",
                    "updated_at": _coerce_timestamp(entry.get("updated_at")),
                    "message_count": int(entry.get("message_count") or 0),
                }
            )
        return items

    def _conversation_snapshot(self) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        for entry in conversation_store.list_conversations(include_metadata=True):
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            if not name:
                continue
            metadata = conversation_store.get_metadata(name)
            records.append(
                {
                    "sync_id": str(metadata.get("id") or f"name:{name}"),
                    "name": name,
                    "metadata": metadata,
                    "messages": conversation_store.load_conversation(name),
                }
            )
        return records

    def _merge_conversations(self, payload: Any) -> Dict[str, Any]:
        if not isinstance(payload, list):
            return {"applied": 0, "skipped": 0}
        local_entries = conversation_store.list_conversations(include_metadata=True)
        local_by_id: Dict[str, Dict[str, Any]] = {}
        local_by_name: Dict[str, Dict[str, Any]] = {}
        for entry in local_entries:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            if not name:
                continue
            local_by_name[name] = entry
            if entry.get("id"):
                local_by_id[str(entry["id"])] = entry
        applied = 0
        skipped = 0
        renamed = 0
        conflicts = 0
        for record in payload:
            if not isinstance(record, dict):
                continue
            name = str(record.get("name") or "").strip()
            metadata = (
                dict(record.get("metadata"))
                if isinstance(record.get("metadata"), dict)
                else {}
            )
            messages = (
                list(record.get("messages"))
                if isinstance(record.get("messages"), list)
                else []
            )
            if not name:
                continue
            sync_id = str(metadata.get("id") or record.get("sync_id") or f"name:{name}")
            incoming_ts = _coerce_timestamp(metadata.get("updated_at"))
            current = local_by_id.get(sync_id) or local_by_name.get(name)
            current_ts = _coerce_timestamp((current or {}).get("updated_at"))
            if current and current_ts > incoming_ts and incoming_ts > 0:
                skipped += 1
                continue
            current_name = str((current or {}).get("name") or "").strip()
            target_name = name
            occupying = local_by_name.get(target_name)
            if (
                occupying
                and str(occupying.get("id") or "") != sync_id
                and target_name != current_name
            ):
                target_name = f"{target_name} ({sync_id[:8]})"
                conflicts += 1
            if current_name and current_name != target_name:
                conversation_store.rename_conversation(current_name, target_name)
                local_by_name.pop(current_name, None)
                renamed += 1
            _write_conversation_snapshot(
                name=target_name,
                messages=messages,
                metadata=metadata,
            )
            refreshed = conversation_store.get_metadata(target_name)
            local_by_name[target_name] = {
                "name": target_name,
                "id": refreshed.get("id"),
                "updated_at": refreshed.get("updated_at"),
            }
            local_by_id[str(refreshed.get("id") or sync_id)] = local_by_name[
                target_name
            ]
            applied += 1
        notes: List[str] = []
        if conflicts:
            notes.append(
                "Some conversation titles already existed locally, so synced copies were renamed with a short id suffix."
            )
        return {
            "applied": applied,
            "skipped": skipped,
            "renamed": renamed,
            "conflicts": conflicts,
            "notes": notes,
        }

    def _memory_manifest(self) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for key, payload in memory_store.load().items():
            item = payload if isinstance(payload, dict) else {"value": payload}
            items.append(
                {
                    "sync_id": str(key),
                    "key": str(key),
                    "source_sync_namespace": item.get("source_sync_namespace") or "",
                    "updated_at": _coerce_timestamp(item.get("updated_at")),
                    "archived": bool(item.get("archived")),
                }
            )
        return items

    def _memory_snapshot(self) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        for key, payload in memory_store.load().items():
            records.append(
                {
                    "sync_id": str(key),
                    "key": str(key),
                    "payload": payload,
                }
            )
        return records

    def _merge_memories(self, payload: Any) -> Dict[str, Any]:
        if not isinstance(payload, list):
            return {"applied": 0, "skipped": 0}
        store = memory_store.load()
        applied = 0
        skipped = 0
        for record in payload:
            if not isinstance(record, dict):
                continue
            key = str(record.get("key") or record.get("sync_id") or "").strip()
            if not key:
                continue
            incoming = record.get("payload")
            current = store.get(key)
            current_item = current if isinstance(current, dict) else {"value": current}
            incoming_item = (
                incoming if isinstance(incoming, dict) else {"value": incoming}
            )
            current_ts = _coerce_timestamp(current_item.get("updated_at"))
            incoming_ts = _coerce_timestamp(incoming_item.get("updated_at"))
            if current is not None and current_ts > incoming_ts and incoming_ts > 0:
                skipped += 1
                continue
            store[key] = incoming
            applied += 1
        memory_store.save(store)
        return {"applied": applied, "skipped": skipped}

    def _knowledge_manifest(self) -> List[Dict[str, Any]]:
        records = self._knowledge_records()
        return [
            {
                "sync_id": item["knowledge_id"],
                "knowledge_id": item["knowledge_id"],
                "source": item["source"],
                "kind": item["kind"],
                "source_sync_namespace": str(
                    (item.get("metadata") or {}).get("source_sync_namespace") or ""
                ).strip(),
                "updated_at": _safe_float(item["updated_at"]),
            }
            for item in records
        ]

    def _knowledge_snapshot(self) -> List[Dict[str, Any]]:
        return self._knowledge_records()

    def _knowledge_records(self) -> List[Dict[str, Any]]:
        knowledge_store_module.KnowledgeStore()
        target = knowledge_store_module.resolve_path()
        with sqlite3.connect(str(target)) as conn:
            conn.row_factory = sqlite3.Row
            item_rows = conn.execute(
                """
                SELECT knowledge_id, source, kind, title, text, summary_text,
                       metadata_json, version, created_at, updated_at
                FROM knowledge_items
                ORDER BY updated_at DESC, knowledge_id ASC
                """
            ).fetchall()
            chunk_rows = conn.execute(
                """
                SELECT chunk_id, knowledge_id, chunk_index, chunk_count, source,
                       root_source, text, metadata_json, embedding_model,
                       created_at, updated_at
                FROM knowledge_chunks
                ORDER BY knowledge_id ASC, chunk_index ASC
                """
            ).fetchall()
        chunks_by_item: Dict[str, List[Dict[str, Any]]] = {}
        for row in chunk_rows:
            metadata = knowledge_store_module._safe_json_loads(row["metadata_json"])
            payload = {
                "chunk_id": row["chunk_id"],
                "knowledge_id": row["knowledge_id"],
                "chunk_index": int(row["chunk_index"]),
                "chunk_count": int(row["chunk_count"]),
                "source": row["source"],
                "root_source": row["root_source"],
                "text": row["text"],
                "metadata": metadata,
                "embedding_model": row["embedding_model"],
                "created_at": float(row["created_at"]),
                "updated_at": float(row["updated_at"]),
            }
            chunks_by_item.setdefault(str(row["knowledge_id"]), []).append(payload)
        records: List[Dict[str, Any]] = []
        for row in item_rows:
            metadata = knowledge_store_module._safe_json_loads(row["metadata_json"])
            records.append(
                {
                    "sync_id": row["knowledge_id"],
                    "knowledge_id": row["knowledge_id"],
                    "source": row["source"],
                    "kind": row["kind"],
                    "title": row["title"],
                    "text": row["text"],
                    "summary_text": row["summary_text"],
                    "metadata": metadata,
                    "version": int(row["version"] or 1),
                    "created_at": float(row["created_at"]),
                    "updated_at": float(row["updated_at"]),
                    "chunks": chunks_by_item.get(str(row["knowledge_id"]), []),
                }
            )
        return records

    def _merge_knowledge(self, payload: Any) -> Dict[str, Any]:
        if not isinstance(payload, list):
            return {"applied": 0, "skipped": 0}
        knowledge_store_module.KnowledgeStore()
        target = knowledge_store_module.resolve_path()
        applied = 0
        skipped = 0
        with sqlite3.connect(str(target)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            for record in payload:
                if not isinstance(record, dict):
                    continue
                knowledge_id = str(
                    record.get("knowledge_id") or record.get("sync_id") or ""
                ).strip()
                source = str(record.get("source") or "").strip()
                if not knowledge_id or not source:
                    continue
                incoming_ts = _safe_float(record.get("updated_at"))
                current = conn.execute(
                    """
                    SELECT knowledge_id, updated_at
                    FROM knowledge_items
                    WHERE knowledge_id = ?
                    """,
                    (knowledge_id,),
                ).fetchone()
                current_ts = _safe_float(current["updated_at"]) if current else 0.0
                source_row = conn.execute(
                    """
                    SELECT knowledge_id, updated_at
                    FROM knowledge_items
                    WHERE source = ?
                    """,
                    (source,),
                ).fetchone()
                if current is not None and current_ts > incoming_ts and incoming_ts > 0:
                    skipped += 1
                    continue
                if (
                    source_row is not None
                    and str(source_row["knowledge_id"]) != knowledge_id
                ):
                    source_ts = _safe_float(source_row["updated_at"])
                    if source_ts > incoming_ts and incoming_ts > 0:
                        skipped += 1
                        continue
                    conn.execute(
                        "DELETE FROM knowledge_items WHERE knowledge_id = ?",
                        (str(source_row["knowledge_id"]),),
                    )
                conn.execute(
                    """
                    INSERT INTO knowledge_items (
                        knowledge_id, source, kind, title, text, summary_text,
                        metadata_json, version, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(knowledge_id) DO UPDATE SET
                        source=excluded.source,
                        kind=excluded.kind,
                        title=excluded.title,
                        text=excluded.text,
                        summary_text=excluded.summary_text,
                        metadata_json=excluded.metadata_json,
                        version=excluded.version,
                        created_at=excluded.created_at,
                        updated_at=excluded.updated_at
                    """,
                    (
                        knowledge_id,
                        source,
                        str(record.get("kind") or "document"),
                        record.get("title"),
                        str(record.get("text") or ""),
                        record.get("summary_text"),
                        _json_dumps(record.get("metadata") or {}),
                        int(record.get("version") or 1),
                        _safe_float(record.get("created_at")),
                        incoming_ts,
                    ),
                )
                conn.execute(
                    "DELETE FROM knowledge_chunks WHERE knowledge_id = ?",
                    (knowledge_id,),
                )
                for chunk in record.get("chunks") or []:
                    if not isinstance(chunk, dict):
                        continue
                    conn.execute(
                        """
                        INSERT INTO knowledge_chunks (
                            chunk_id, knowledge_id, chunk_index, chunk_count, source,
                            root_source, text, metadata_json, embedding_model,
                            created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(chunk.get("chunk_id") or ""),
                            knowledge_id,
                            int(chunk.get("chunk_index") or 0),
                            int(chunk.get("chunk_count") or 1),
                            str(chunk.get("source") or source),
                            str(chunk.get("root_source") or source),
                            str(chunk.get("text") or ""),
                            _json_dumps(chunk.get("metadata") or {}),
                            chunk.get("embedding_model"),
                            _safe_float(chunk.get("created_at")),
                            _safe_float(chunk.get("updated_at")),
                        ),
                    )
                applied += 1
            conn.commit()
        notes: List[str] = []
        if applied:
            notes.append(
                "Knowledge rows were synced into the canonical SQLite store."
            )
        return {"applied": applied, "skipped": skipped, "notes": notes}

    def _graph_manifest(self) -> List[Dict[str, Any]]:
        snapshot = self._graph_snapshot()
        items: List[Dict[str, Any]] = []
        for node in snapshot.get("nodes", []):
            if not isinstance(node, dict):
                continue
            items.append(
                {
                    "sync_id": f"node:{node.get('node_id')}",
                    "kind": "node",
                    "source_sync_namespace": str(
                        (node.get("attributes") or {}).get("source_sync_namespace")
                        or ""
                    ).strip(),
                    "updated_at": _safe_float(node.get("updated_at")),
                    "label": node.get("canonical_name")
                    or node.get("node_type")
                    or node.get("node_id"),
                }
            )
        for claim in snapshot.get("claims", []):
            if not isinstance(claim, dict):
                continue
            items.append(
                {
                    "sync_id": f"claim:{claim.get('claim_id')}",
                    "kind": "claim",
                    "source_sync_namespace": str(
                        (claim.get("metadata") or {}).get("source_sync_namespace") or ""
                    ).strip(),
                    "updated_at": _safe_float(claim.get("updated_at")),
                    "label": claim.get("predicate") or claim.get("claim_id"),
                }
            )
        return items

    def _graph_snapshot(self) -> Dict[str, Any]:
        graph_store_module.GraphStore()
        target = graph_store_module.resolve_path()
        with sqlite3.connect(str(target)) as conn:
            conn.row_factory = sqlite3.Row
            node_rows = conn.execute(
                """
                SELECT node_id, node_kind, node_type, canonical_name, summary_text,
                       attributes_json, status, created_at, updated_at
                FROM graph_nodes
                ORDER BY updated_at DESC, node_id ASC
                """
            ).fetchall()
            claim_rows = conn.execute(
                """
                SELECT claim_id, claim_type, predicate, status, epistemic_status,
                       confidence, valid_from, valid_to, occurred_at, source_kind,
                       source_ref, metadata_json, created_at, updated_at
                FROM graph_claims
                ORDER BY updated_at DESC, claim_id ASC
                """
            ).fetchall()
            role_rows = conn.execute(
                """
                SELECT claim_id, role_name, ordinal, node_id, value_json, metadata_json
                FROM graph_claim_roles
                ORDER BY claim_id ASC, ordinal ASC, role_name ASC
                """
            ).fetchall()
        roles_by_claim: Dict[str, List[Dict[str, Any]]] = {}
        for role in role_rows:
            roles_by_claim.setdefault(str(role["claim_id"]), []).append(
                {
                    "role_name": role["role_name"],
                    "ordinal": int(role["ordinal"]),
                    "node_id": role["node_id"],
                    "value": graph_store_module._json_loads_any(role["value_json"]),
                    "metadata": graph_store_module._json_loads(role["metadata_json"]),
                }
            )
        return {
            "nodes": [
                {
                    "node_id": row["node_id"],
                    "node_kind": row["node_kind"],
                    "node_type": row["node_type"],
                    "canonical_name": row["canonical_name"],
                    "summary_text": row["summary_text"],
                    "attributes": graph_store_module._json_loads(
                        row["attributes_json"]
                    ),
                    "status": row["status"],
                    "created_at": float(row["created_at"]),
                    "updated_at": float(row["updated_at"]),
                }
                for row in node_rows
            ],
            "claims": [
                {
                    "claim_id": row["claim_id"],
                    "claim_type": row["claim_type"],
                    "predicate": row["predicate"],
                    "status": row["status"],
                    "epistemic_status": row["epistemic_status"],
                    "confidence": float(row["confidence"]),
                    "valid_from": row["valid_from"],
                    "valid_to": row["valid_to"],
                    "occurred_at": row["occurred_at"],
                    "source_kind": row["source_kind"],
                    "source_ref": row["source_ref"],
                    "metadata": graph_store_module._json_loads(row["metadata_json"]),
                    "created_at": float(row["created_at"]),
                    "updated_at": float(row["updated_at"]),
                    "roles": roles_by_claim.get(str(row["claim_id"]), []),
                }
                for row in claim_rows
            ],
        }

    def _merge_graph(self, payload: Any) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return {"applied": 0, "skipped": 0}
        graph_store_module.GraphStore()
        target = graph_store_module.resolve_path()
        applied = 0
        skipped = 0
        with sqlite3.connect(str(target)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            for node in payload.get("nodes") or []:
                if not isinstance(node, dict):
                    continue
                node_id = str(node.get("node_id") or "").strip()
                if not node_id:
                    continue
                incoming_ts = _safe_float(node.get("updated_at"))
                current = conn.execute(
                    "SELECT updated_at FROM graph_nodes WHERE node_id = ?",
                    (node_id,),
                ).fetchone()
                current_ts = _safe_float(current["updated_at"]) if current else 0.0
                if current is not None and current_ts > incoming_ts and incoming_ts > 0:
                    skipped += 1
                    continue
                conn.execute(
                    """
                    INSERT INTO graph_nodes (
                        node_id, node_kind, node_type, canonical_name, summary_text,
                        attributes_json, status, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(node_id) DO UPDATE SET
                        node_kind=excluded.node_kind,
                        node_type=excluded.node_type,
                        canonical_name=excluded.canonical_name,
                        summary_text=excluded.summary_text,
                        attributes_json=excluded.attributes_json,
                        status=excluded.status,
                        created_at=excluded.created_at,
                        updated_at=excluded.updated_at
                    """,
                    (
                        node_id,
                        str(node.get("node_kind") or "entity"),
                        str(node.get("node_type") or "unknown"),
                        node.get("canonical_name"),
                        node.get("summary_text"),
                        _json_dumps(node.get("attributes") or {}),
                        str(node.get("status") or "active"),
                        _safe_float(node.get("created_at")),
                        incoming_ts,
                    ),
                )
                applied += 1
            for claim in payload.get("claims") or []:
                if not isinstance(claim, dict):
                    continue
                claim_id = str(claim.get("claim_id") or "").strip()
                if not claim_id:
                    continue
                incoming_ts = _safe_float(claim.get("updated_at"))
                current = conn.execute(
                    "SELECT updated_at FROM graph_claims WHERE claim_id = ?",
                    (claim_id,),
                ).fetchone()
                current_ts = _safe_float(current["updated_at"]) if current else 0.0
                if current is not None and current_ts > incoming_ts and incoming_ts > 0:
                    skipped += 1
                    continue
                conn.execute(
                    """
                    INSERT INTO graph_claims (
                        claim_id, claim_type, predicate, status, epistemic_status,
                        confidence, valid_from, valid_to, occurred_at, source_kind,
                        source_ref, metadata_json, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(claim_id) DO UPDATE SET
                        claim_type=excluded.claim_type,
                        predicate=excluded.predicate,
                        status=excluded.status,
                        epistemic_status=excluded.epistemic_status,
                        confidence=excluded.confidence,
                        valid_from=excluded.valid_from,
                        valid_to=excluded.valid_to,
                        occurred_at=excluded.occurred_at,
                        source_kind=excluded.source_kind,
                        source_ref=excluded.source_ref,
                        metadata_json=excluded.metadata_json,
                        created_at=excluded.created_at,
                        updated_at=excluded.updated_at
                    """,
                    (
                        claim_id,
                        str(claim.get("claim_type") or "relation"),
                        str(claim.get("predicate") or ""),
                        str(claim.get("status") or "active"),
                        str(claim.get("epistemic_status") or "asserted"),
                        float(claim.get("confidence") or 1.0),
                        claim.get("valid_from"),
                        claim.get("valid_to"),
                        claim.get("occurred_at"),
                        claim.get("source_kind"),
                        claim.get("source_ref"),
                        _json_dumps(claim.get("metadata") or {}),
                        _safe_float(claim.get("created_at")),
                        incoming_ts,
                    ),
                )
                conn.execute(
                    "DELETE FROM graph_claim_roles WHERE claim_id = ?",
                    (claim_id,),
                )
                for role in claim.get("roles") or []:
                    if not isinstance(role, dict):
                        continue
                    conn.execute(
                        """
                        INSERT INTO graph_claim_roles (
                            claim_id, role_name, ordinal, node_id, value_json, metadata_json
                        )
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            claim_id,
                            str(role.get("role_name") or ""),
                            int(role.get("ordinal") or 0),
                            role.get("node_id"),
                            _json_dumps(role.get("value"))
                            if role.get("value") is not None
                            else None,
                            _json_dumps(role.get("metadata") or {}),
                        ),
                    )
                applied += 1
            conn.commit()
        return {"applied": applied, "skipped": skipped}

    def _attachment_manifest(self) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for content_hash in _iter_attachment_hashes():
            meta = _load_attachment_meta(content_hash)
            filename = _safe_attachment_filename(meta.get("filename"), content_hash)
            target = _resolve_attachment_target(content_hash, filename=filename)
            if target is None or not target.exists():
                continue
            descriptor = build_attachment_media_descriptor(
                content_hash,
                target,
                metadata=meta,
                preferred_filename=filename,
            )
            if descriptor["metadata_changed"]:
                _write_attachment_meta(content_hash, descriptor["metadata"])
                meta = descriptor["metadata"]
            filename = str(descriptor["filename"] or "").strip() or filename
            path_namespace = _attachment_path_namespace(
                meta.get("relative_path") or meta.get("source_path")
            )
            manifest_namespace = (
                path_namespace
                if path_namespace is not None
                else str(meta.get("source_sync_namespace") or "").strip()
            )
            items.append(
                {
                    "sync_id": content_hash,
                    "content_hash": content_hash,
                    "filename": filename,
                    "source_sync_namespace": manifest_namespace,
                    "relative_path": str(meta.get("relative_path") or "").strip(),
                    "source_path": str(meta.get("source_path") or "").strip(),
                    "updated_at": _resolve_attachment_updated_at(
                        meta, fallback_path=target
                    ),
                    "size": int(meta.get("size") or target.stat().st_size),
                }
            )
        return items

    def _attachment_snapshot(self) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        for content_hash in _iter_attachment_hashes():
            meta = _load_attachment_meta(content_hash)
            filename = _safe_attachment_filename(meta.get("filename"), content_hash)
            target = _resolve_attachment_target(content_hash, filename=filename)
            if target is None or not target.exists():
                continue
            descriptor = build_attachment_media_descriptor(
                content_hash,
                target,
                metadata=meta,
                preferred_filename=filename,
            )
            if descriptor["metadata_changed"]:
                _write_attachment_meta(content_hash, descriptor["metadata"])
                meta = descriptor["metadata"]
            filename = str(descriptor["filename"] or "").strip() or filename
            records.append(
                {
                    "sync_id": content_hash,
                    "content_hash": content_hash,
                    "filename": filename,
                    "updated_at": _resolve_attachment_updated_at(
                        meta, fallback_path=target
                    ),
                    "metadata": meta,
                    "content_b64": base64.b64encode(target.read_bytes()).decode(
                        "ascii"
                    ),
                }
            )
        return records

    def _write_attachment_file(
        self,
        *,
        content_hash: str,
        filename: str,
        metadata: Dict[str, Any],
        data: bytes,
    ) -> None:
        files_dir = blob_store._resolve_data_files_root()
        rel_candidate = _coerce_relative_files_path(
            str(
                metadata.get("relative_path") or metadata.get("source_path") or ""
            ).strip()
        )
        if rel_candidate:
            target = (files_dir / rel_candidate).resolve()
            try:
                target.relative_to(files_dir)
            except Exception as exc:
                raise ValueError(
                    "Attachment relative_path escaped data/files root"
                ) from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            normalized_meta = dict(metadata)
            normalized_meta["filename"] = target.name
            normalized_meta["relative_path"] = target.relative_to(files_dir).as_posix()
            _write_attachment_meta(content_hash, normalized_meta)
            return
        origin = str(metadata.get("origin") or "upload").strip().lower()
        dirname = _ATTACHMENT_ORIGIN_DIRS.get(origin)
        if dirname:
            target = (files_dir / dirname / content_hash / filename).resolve()
            try:
                target.relative_to(files_dir)
            except Exception as exc:
                raise ValueError("Attachment target escaped data/files root") from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            normalized_meta = dict(metadata)
            normalized_meta["filename"] = filename
            normalized_meta["origin"] = origin
            normalized_meta["relative_path"] = target.relative_to(files_dir).as_posix()
            _write_attachment_meta(content_hash, normalized_meta)
            return
        target = BLOBS_DIR / content_hash
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        normalized_meta = dict(metadata)
        normalized_meta["filename"] = filename
        _write_attachment_meta(content_hash, normalized_meta)

    def _merge_attachments(self, payload: Any) -> Dict[str, Any]:
        if not isinstance(payload, list):
            return {"applied": 0, "skipped": 0}
        local_items = {
            str(item["sync_id"]): item
            for item in self._attachment_manifest()
            if isinstance(item, dict) and item.get("sync_id")
        }
        applied = 0
        skipped = 0
        for record in payload:
            if not isinstance(record, dict):
                continue
            content_hash = (
                str(record.get("content_hash") or record.get("sync_id") or "")
                .strip()
                .lower()
            )
            if not content_hash:
                continue
            incoming_ts = _safe_float(record.get("updated_at"))
            current = local_items.get(content_hash)
            current_ts = _safe_float((current or {}).get("updated_at"))
            if current and current_ts > incoming_ts and incoming_ts > 0:
                skipped += 1
                continue
            try:
                data = base64.b64decode(str(record.get("content_b64") or ""))
            except Exception:
                skipped += 1
                continue
            metadata = (
                dict(record.get("metadata"))
                if isinstance(record.get("metadata"), dict)
                else {}
            )
            filename = _safe_attachment_filename(record.get("filename"), content_hash)
            self._write_attachment_file(
                content_hash=content_hash,
                filename=filename,
                metadata=metadata,
                data=data,
            )
            local_items[content_hash] = {
                "sync_id": content_hash,
                "updated_at": incoming_ts,
            }
            applied += 1
        notes: List[str] = []
        if applied:
            notes.append(
                "Attachment files and captions were synced."
            )
        return {"applied": applied, "skipped": skipped, "notes": notes}

    def _calendar_manifest(self) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for event_id in calendar_store.list_events():
            event = calendar_store.load_event(event_id)
            path = calendar_store._path(event_id)
            ts = _coerce_timestamp(
                event.get("updated_at")
                or event.get("modified_at")
                or event.get("updatedAt")
            )
            if ts <= 0 and path.exists():
                ts = float(path.stat().st_mtime)
            items.append(
                {
                    "sync_id": event_id,
                    "event_id": event_id,
                    "source_sync_namespace": str(
                        event.get("source_sync_namespace") or ""
                    ).strip(),
                    "updated_at": ts,
                    "title": event.get("title") or event.get("summary") or event_id,
                }
            )
        return items

    def _calendar_snapshot(self) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        for event_id in calendar_store.list_events():
            event = calendar_store.load_event(event_id)
            records.append(
                {
                    "sync_id": event_id,
                    "event_id": event_id,
                    "payload": event,
                }
            )
        return records

    def _merge_calendar(self, payload: Any) -> Dict[str, Any]:
        if not isinstance(payload, list):
            return {"applied": 0, "skipped": 0}
        local_items = {
            str(item["sync_id"]): item
            for item in self._calendar_manifest()
            if isinstance(item, dict) and item.get("sync_id")
        }
        applied = 0
        skipped = 0
        for record in payload:
            if not isinstance(record, dict):
                continue
            event_id = str(
                record.get("event_id") or record.get("sync_id") or ""
            ).strip()
            payload_value = record.get("payload")
            if not event_id or not isinstance(payload_value, dict):
                continue
            incoming_ts = _coerce_timestamp(
                payload_value.get("updated_at")
                or payload_value.get("modified_at")
                or payload_value.get("updatedAt")
            )
            current_ts = _safe_float(
                (local_items.get(event_id) or {}).get("updated_at")
            )
            if (
                local_items.get(event_id)
                and current_ts > incoming_ts
                and incoming_ts > 0
            ):
                skipped += 1
                continue
            calendar_store.save_event(event_id, payload_value)
            applied += 1
        notes: List[str] = []
        if applied:
            notes.append(
                "Calendar files were synced."
            )
        return {"applied": applied, "skipped": skipped, "notes": notes}

    def _settings_manifest(self) -> List[Dict[str, Any]]:
        return [
            {
                "sync_id": "settings",
                "updated_at": _settings_updated_at(),
                "keys": len(_portable_settings_snapshot()),
            }
        ]

    def _settings_snapshot(self) -> Dict[str, Any]:
        return {
            "sync_id": "settings",
            "updated_at": user_settings.load_settings().get("updated_at") or _now_iso(),
            "data": _portable_settings_snapshot(),
        }

    def _merge_settings(self, payload: Any) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return {"applied": 0, "skipped": 0}
        incoming_data = payload.get("data")
        if not isinstance(incoming_data, dict):
            return {"applied": 0, "skipped": 0}
        current_ts = _settings_updated_at()
        incoming_ts = _coerce_timestamp(payload.get("updated_at"))
        if current_ts > incoming_ts and incoming_ts > 0:
            return {"applied": 0, "skipped": 1}
        _write_settings_snapshot(incoming_data, payload.get("updated_at"))
        return {"applied": 1, "skipped": 0}


__all__ = [
    "InstanceSyncService",
    "RemoteFloatClient",
    "SYNC_SECTION_LABELS",
    "SYNC_SECTION_ORDER",
]
