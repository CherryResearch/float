from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional
from uuid import uuid4

from app import config as app_config

SCHEMA_VERSION = 1
CHECKPOINTS_PATH = app_config.DEFAULT_DATABASES_DIR / "sync_checkpoints.json"


def _now() -> float:
    return time.time()


def _iso_timestamp(value: float) -> str:
    if value <= 0:
        return ""
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def _coerce_timestamp(value: Any) -> float:
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, parsed.timestamp())


def digest_value(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read() -> Dict[str, Any]:
    try:
        payload = json.loads(CHECKPOINTS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"schema_version": SCHEMA_VERSION, "checkpoints": {}}
    if not isinstance(payload, dict):
        return {"schema_version": SCHEMA_VERSION, "checkpoints": {}}
    checkpoints = payload.get("checkpoints")
    if not isinstance(checkpoints, dict):
        checkpoints = {}
    return {
        **payload,
        "schema_version": SCHEMA_VERSION,
        "checkpoints": checkpoints,
    }


def _write(payload: Dict[str, Any]) -> None:
    CHECKPOINTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = CHECKPOINTS_PATH.with_name(
        f".{CHECKPOINTS_PATH.name}.{uuid4().hex}.tmp"
    )
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, CHECKPOINTS_PATH)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _normalized_sections(
    manifest: Optional[Dict[str, Any]],
    sections: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    section_map = manifest.get("sections") if isinstance(manifest, dict) else {}
    if not isinstance(section_map, dict):
        return {}
    requested = {
        str(section or "").strip()
        for section in sections or section_map.keys()
        if str(section or "").strip()
    }
    return {
        key: section_map.get(key) for key in sorted(requested) if key in section_map
    }


def manifest_revision(
    manifest: Optional[Dict[str, Any]],
    sections: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    normalized = _normalized_sections(manifest, sections)
    latest = 0.0
    item_count = 0
    section_digests: Dict[str, str] = {}
    for section, payload in normalized.items():
        items = payload.get("items") if isinstance(payload, dict) else []
        if not isinstance(items, list):
            items = []
        ordered_items = sorted(
            items,
            key=lambda item: (
                _selection_id(item) if isinstance(item, dict) else "",
                digest_value(item),
            ),
        )
        item_count += len(items)
        for item in items:
            if isinstance(item, dict):
                latest = max(latest, _coerce_timestamp(item.get("updated_at")))
        section_digests[section] = digest_value(ordered_items)
    digest = digest_value(
        {section: section_digests[section] for section in sorted(section_digests)}
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "digest": digest,
        "code": f"d-{digest[:12]}",
        "updated_at": latest or None,
        "updated_at_iso": _iso_timestamp(latest),
        "item_count": item_count,
        "section_digests": section_digests,
    }


def scope_key(scope: Optional[Dict[str, Any]]) -> str:
    return digest_value(scope if isinstance(scope, dict) else {})[:24]


def _peer_key(peer_deployment_id: str, peer_id: str) -> str:
    deployment_id = str(peer_deployment_id or "").strip()
    saved_peer_id = str(peer_id or "").strip()
    if deployment_id:
        return deployment_id
    return f"pair:{saved_peer_id}" if saved_peer_id else ""


def checkpoint_key(
    *,
    peer_deployment_id: str,
    peer_id: str,
    scope: Optional[Dict[str, Any]],
) -> str:
    peer = _peer_key(peer_deployment_id, peer_id)
    if not peer:
        return ""
    return f"{peer}:{scope_key(scope)}"


def _manifest_items(
    manifest: Optional[Dict[str, Any]], section: str
) -> List[Dict[str, Any]]:
    section_map = manifest.get("sections") if isinstance(manifest, dict) else {}
    section_payload = section_map.get(section) if isinstance(section_map, dict) else {}
    items = section_payload.get("items") if isinstance(section_payload, dict) else []
    return [item for item in items or [] if isinstance(item, dict)]


def _selection_id(item: Dict[str, Any]) -> str:
    return str(item.get("original_sync_id") or item.get("sync_id") or "").strip()


def _items_by_selection_id(
    manifest: Optional[Dict[str, Any]], section: str
) -> Dict[str, Dict[str, Any]]:
    mapped: Dict[str, Dict[str, Any]] = {}
    for item in _manifest_items(manifest, section):
        selection_id = _selection_id(item)
        if selection_id:
            mapped[selection_id] = item
    return mapped


def build_view_baseline(
    *,
    local_manifest: Optional[Dict[str, Any]],
    remote_manifest: Optional[Dict[str, Any]],
    item_selections: Dict[str, List[str]],
) -> Dict[str, Any]:
    section_baselines: Dict[str, Any] = {}
    for section, raw_ids in sorted((item_selections or {}).items()):
        selected_ids = [
            str(item_id or "").strip()
            for item_id in raw_ids or []
            if str(item_id or "").strip()
        ]
        if not selected_ids:
            continue
        local_items = _items_by_selection_id(local_manifest, section)
        remote_items = _items_by_selection_id(remote_manifest, section)
        items: Dict[str, Any] = {}
        for selection_id in selected_ids:
            local_item = local_items.get(selection_id)
            remote_item = remote_items.get(selection_id)
            items[selection_id] = {
                "local_digest": digest_value(local_item) if local_item else "",
                "remote_digest": digest_value(remote_item) if remote_item else "",
                "local_present": local_item is not None,
                "remote_present": remote_item is not None,
                "local_updated_at": _coerce_timestamp(
                    (local_item or {}).get("updated_at")
                )
                or None,
                "remote_updated_at": _coerce_timestamp(
                    (remote_item or {}).get("updated_at")
                )
                or None,
            }
        section_baselines[section] = {"items": items}
    return {"sections": section_baselines}


def build_equal_view_baseline(
    *,
    local_manifest: Optional[Dict[str, Any]],
    remote_manifest: Optional[Dict[str, Any]],
    sections: Iterable[str],
) -> tuple[Dict[str, Any], Dict[str, List[str]]]:
    """Capture items that are currently identical as a safe common ancestor."""
    section_baselines: Dict[str, Any] = {}
    selections: Dict[str, List[str]] = {}
    for section in sorted(
        {str(value or "").strip() for value in sections if str(value or "").strip()}
    ):
        local_items = _items_by_selection_id(local_manifest, section)
        remote_items = _items_by_selection_id(remote_manifest, section)
        equal_ids = [
            selection_id
            for selection_id in sorted(set(local_items) & set(remote_items))
            if digest_value(local_items[selection_id])
            == digest_value(remote_items[selection_id])
        ]
        if not equal_ids:
            continue
        selections[section] = equal_ids
        section_baselines[section] = build_view_baseline(
            local_manifest=local_manifest,
            remote_manifest=remote_manifest,
            item_selections={section: equal_ids},
        )["sections"][section]
    return {"sections": section_baselines}, selections


def build_observed_view_baseline(
    *,
    local_manifest: Optional[Dict[str, Any]],
    remote_manifest: Optional[Dict[str, Any]],
    sections: Iterable[str],
) -> Dict[str, Any]:
    """Capture a complete observed comparison without claiming a successful sync."""
    section_baselines: Dict[str, Any] = {}
    for section in sorted(
        {str(value or "").strip() for value in sections if str(value or "").strip()}
    ):
        local_items = _items_by_selection_id(local_manifest, section)
        remote_items = _items_by_selection_id(remote_manifest, section)
        observed_ids = sorted(set(local_items) | set(remote_items))
        section_payload = build_view_baseline(
            local_manifest=local_manifest,
            remote_manifest=remote_manifest,
            item_selections={section: observed_ids},
        )["sections"].get(section, {"items": {}})
        section_baselines[section] = {
            **section_payload,
            "complete": True,
        }
    return {"sections": section_baselines}


def load_checkpoint(
    *,
    peer_deployment_id: str,
    peer_id: str,
    scope: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    key = checkpoint_key(
        peer_deployment_id=peer_deployment_id,
        peer_id=peer_id,
        scope=scope,
    )
    if not key:
        return {}
    checkpoint = _read()["checkpoints"].get(key)
    return dict(checkpoint) if isinstance(checkpoint, dict) else {}


def _merge_view(
    existing: Any,
    incoming: Any,
    *,
    preserve_complete_sections: bool = False,
) -> Dict[str, Any]:
    current = dict(existing) if isinstance(existing, dict) else {}
    current_sections = current.get("sections")
    if not isinstance(current_sections, dict):
        current_sections = {}
    next_sections = incoming.get("sections") if isinstance(incoming, dict) else {}
    if not isinstance(next_sections, dict):
        next_sections = {}
    for section, section_payload in next_sections.items():
        existing_section = current_sections.get(section)
        if (
            preserve_complete_sections
            and isinstance(existing_section, dict)
            and bool(existing_section.get("complete"))
        ):
            continue
        incoming_items = (
            section_payload.get("items") if isinstance(section_payload, dict) else {}
        )
        if not isinstance(incoming_items, dict):
            continue
        existing_items = (
            existing_section.get("items") if isinstance(existing_section, dict) else {}
        )
        if not isinstance(existing_items, dict):
            existing_items = {}
        current_sections[section] = {
            "items": {**existing_items, **incoming_items},
            "complete": bool(
                (
                    existing_section.get("complete")
                    if isinstance(existing_section, dict)
                    else False
                )
                or (
                    section_payload.get("complete")
                    if isinstance(section_payload, dict)
                    else False
                )
            ),
        }
    return {"sections": current_sections}


def record_checkpoint(
    *,
    peer_deployment_id: str,
    peer_id: str,
    peer_label: str,
    direction: str,
    sections: List[str],
    item_selections: Optional[Dict[str, List[str]]],
    local_revision: Optional[Dict[str, Any]],
    remote_revision: Optional[Dict[str, Any]],
    scope: Optional[Dict[str, Any]],
    views: Optional[Dict[str, Dict[str, Any]]] = None,
    synced_at: Optional[float] = None,
    successful_sync: bool = True,
) -> Dict[str, Any]:
    key = checkpoint_key(
        peer_deployment_id=peer_deployment_id,
        peer_id=peer_id,
        scope=scope,
    )
    if not key:
        return {}
    payload = _read()
    checkpoints = payload["checkpoints"]
    existing = checkpoints.get(key)
    existing = dict(existing) if isinstance(existing, dict) else {}
    existing_views = existing.get("views")
    if not isinstance(existing_views, dict):
        existing_views = {}
    merged_views = dict(existing_views)
    for view_name, view_payload in (views or {}).items():
        merged_views[str(view_name)] = _merge_view(
            merged_views.get(str(view_name)),
            view_payload,
            preserve_complete_sections=not successful_sync,
        )
    timestamp = float(synced_at or _now())
    merged_selections: Dict[str, List[str]] = {}
    existing_selections = existing.get("item_selections")
    if not isinstance(existing_selections, dict):
        existing_selections = {}
    for section in set(existing_selections) | set(item_selections or {}):
        merged_selections[str(section)] = sorted(
            {
                str(item_id or "").strip()
                for item_id in [
                    *(existing_selections.get(section) or []),
                    *((item_selections or {}).get(section) or []),
                ]
                if str(item_id or "").strip()
            }
        )
    checkpoint = {
        **existing,
        "schema_version": SCHEMA_VERSION,
        "peer_deployment_id": str(peer_deployment_id or "").strip(),
        "peer_id": str(peer_id or "").strip(),
        "peer_label": str(peer_label or "").strip(),
        "scope_key": scope_key(scope),
        "scope": dict(scope or {}),
        "last_verified_at": timestamp,
        "last_verified_at_iso": _iso_timestamp(timestamp),
        "last_verified_direction": str(direction or "").strip(),
        "sections": sorted(
            {
                str(section or "").strip()
                for section in [*(existing.get("sections") or []), *sections]
                if str(section or "").strip()
            }
        ),
        "item_selections": merged_selections,
        "last_verified_local_revision": dict(local_revision or {}),
        "last_verified_remote_revision": dict(remote_revision or {}),
        "views": merged_views,
    }
    if successful_sync:
        checkpoint.update(
            {
                "last_synced_at": timestamp,
                "last_synced_at_iso": _iso_timestamp(timestamp),
                "last_direction": str(direction or "").strip(),
                "local_revision": dict(local_revision or {}),
                "remote_revision": dict(remote_revision or {}),
            }
        )
    checkpoints[key] = checkpoint
    payload["checkpoints"] = checkpoints
    _write(payload)
    return checkpoint


def checkpoint_summary(
    checkpoint: Optional[Dict[str, Any]],
    *,
    current_revision: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    saved = checkpoint if isinstance(checkpoint, dict) else {}
    if not saved:
        return {
            "state": "never_synced",
            "summary": "No successful sync checkpoint",
        }
    last_synced_at = float(saved.get("last_synced_at") or 0)
    if last_synced_at <= 0:
        views = saved.get("views") if isinstance(saved.get("views"), dict) else {}
        observed_complete = any(
            bool(section_payload.get("complete"))
            for view in views.values()
            if isinstance(view, dict)
            for section_payload in (
                view.get("sections").values()
                if isinstance(view.get("sections"), dict)
                else []
            )
            if isinstance(section_payload, dict)
        )
        return {
            "state": "observed" if observed_complete else "verified_common",
            "summary": (
                "Peer data state observed; no successful apply checkpoint yet"
                if observed_complete
                else "Common data state verified; no successful apply checkpoint yet"
            ),
            "peer_deployment_id": str(saved.get("peer_deployment_id") or "").strip(),
            "peer_id": str(saved.get("peer_id") or "").strip(),
            "peer_label": str(saved.get("peer_label") or "").strip(),
            "last_verified_at": saved.get("last_verified_at"),
            "last_verified_at_iso": str(
                saved.get("last_verified_at_iso") or ""
            ).strip(),
            "last_verified_direction": str(
                saved.get("last_verified_direction") or ""
            ).strip(),
            "sections": list(saved.get("sections") or []),
            "scope_key": str(saved.get("scope_key") or "").strip(),
        }
    stored_local = (
        saved.get("local_revision")
        if isinstance(saved.get("local_revision"), dict)
        else {}
    )
    current = current_revision if isinstance(current_revision, dict) else {}
    current_digest = str(current.get("digest") or "").strip()
    stored_digest = str(stored_local.get("digest") or "").strip()
    if current_digest and stored_digest and current_digest == stored_digest:
        state = "synced"
        summary = "Local data matches the last successful sync checkpoint"
    elif current_digest and stored_digest:
        state = "local_changes"
        summary = "Local data changed after the last successful sync"
    else:
        state = "checkpointed"
        summary = "Successful sync checkpoint recorded"
    return {
        "state": state,
        "summary": summary,
        "peer_deployment_id": str(saved.get("peer_deployment_id") or "").strip(),
        "peer_id": str(saved.get("peer_id") or "").strip(),
        "peer_label": str(saved.get("peer_label") or "").strip(),
        "last_synced_at": saved.get("last_synced_at"),
        "last_synced_at_iso": str(saved.get("last_synced_at_iso") or "").strip(),
        "last_direction": str(saved.get("last_direction") or "").strip(),
        "last_verified_at": saved.get("last_verified_at"),
        "last_verified_at_iso": str(saved.get("last_verified_at_iso") or "").strip(),
        "sections": list(saved.get("sections") or []),
        "local_revision": dict(stored_local),
        "remote_revision": dict(saved.get("remote_revision") or {}),
        "scope_key": str(saved.get("scope_key") or "").strip(),
    }


def latest_checkpoint(
    *,
    peer_deployment_id: str = "",
    peer_id: str = "",
) -> Dict[str, Any]:
    candidates = []
    for checkpoint in _read()["checkpoints"].values():
        if not isinstance(checkpoint, dict):
            continue
        if (
            peer_deployment_id
            and str(checkpoint.get("peer_deployment_id") or "").strip()
            != str(peer_deployment_id).strip()
        ):
            continue
        if (
            peer_id
            and str(checkpoint.get("peer_id") or "").strip() != str(peer_id).strip()
        ):
            continue
        candidates.append(checkpoint)
    if not candidates:
        return {}
    return dict(
        max(
            candidates,
            key=lambda item: max(
                float(item.get("last_synced_at") or 0),
                float(item.get("last_verified_at") or 0),
            ),
        )
    )


def comparison_baseline(
    checkpoint: Optional[Dict[str, Any]], direction: str
) -> Dict[str, Any]:
    views = checkpoint.get("views") if isinstance(checkpoint, dict) else {}
    view = views.get(direction) if isinstance(views, dict) else {}
    return dict(view) if isinstance(view, dict) else {}


__all__ = [
    "CHECKPOINTS_PATH",
    "SCHEMA_VERSION",
    "build_equal_view_baseline",
    "build_observed_view_baseline",
    "build_view_baseline",
    "checkpoint_key",
    "checkpoint_summary",
    "comparison_baseline",
    "digest_value",
    "latest_checkpoint",
    "load_checkpoint",
    "manifest_revision",
    "record_checkpoint",
    "scope_key",
]
