from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.utils import user_settings

DEFAULT_WORKSPACE_ID = "root"
DEFAULT_WORKSPACE_NAME = "Main workspace"
DEFAULT_WORKSPACE_SLUG = "main"
DEFAULT_WORKSPACE_ROOT = "data/files/workspace"


def _slugify(value: Any, fallback: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return text or fallback


def _path_token(value: Any, fallback: str) -> str:
    text = re.sub(r"[^0-9A-Za-z]+", "-", str(value or "").strip()).strip("-")
    return text or fallback


def _clean_relative_path(value: Any) -> str:
    raw = str(value or "").strip().replace("\\", "/").lstrip("/")
    if not raw:
        return ""
    parts = [
        segment for segment in raw.split("/") if segment and segment not in {".", ".."}
    ]
    return "/".join(parts)


def _join_relative_path(*parts: str) -> str:
    cleaned = [
        _clean_relative_path(part) for part in parts if _clean_relative_path(part)
    ]
    return "/".join(cleaned)


def _is_default_workspace_source(
    source_workspace_id: Any,
    source_workspace_name: Any,
    source_workspace_slug: Any = None,
) -> bool:
    workspace_id = str(source_workspace_id or "").strip().lower()
    workspace_name = str(source_workspace_name or "").strip().lower()
    workspace_slug = str(source_workspace_slug or "").strip().lower()
    return workspace_id in {"", DEFAULT_WORKSPACE_ID} or workspace_slug in {
        "",
        DEFAULT_WORKSPACE_SLUG,
    } or workspace_name in {
        DEFAULT_WORKSPACE_NAME.lower(),
        "main workspace",
        "main",
        "root",
    }


def resolve_synced_workspace_location(
    *,
    parent_profile: Optional[Dict[str, Any]],
    source_device_name: str,
    source_workspace_id: str,
    source_workspace_name: str,
    source_workspace_slug: str = "",
) -> Dict[str, Any]:
    parent_namespace = _clean_relative_path(
        (parent_profile or {}).get("namespace")
        if isinstance(parent_profile, dict)
        else ""
    )
    parent_root = _clean_relative_path(
        (parent_profile or {}).get("root_path")
        if isinstance(parent_profile, dict)
        else ""
    )
    is_default_workspace = _is_default_workspace_source(
        source_workspace_id,
        source_workspace_name,
        source_workspace_slug,
    )
    device_segment = _path_token(source_device_name or "Remote", "Remote")
    workspace_segment = _path_token(
        source_workspace_name or source_workspace_id or "Workspace",
        "Workspace",
    )
    path_parts = [device_segment]
    if not is_default_workspace:
        path_parts.append(workspace_segment)
    return {
        "is_default_workspace": is_default_workspace,
        "path_parts": path_parts,
        "namespace": _join_relative_path(parent_namespace, *path_parts),
        "root_path": _join_relative_path(
            parent_root or DEFAULT_WORKSPACE_ROOT,
            *path_parts,
        ),
        "display_name": (
            source_device_name or "Remote"
            if is_default_workspace
            else f"{source_device_name or 'Remote'} / {source_workspace_name or 'Workspace'}"
        ),
    }


def normalize_workspace_profile(entry: Any, index: int = 0) -> Dict[str, Any]:
    if not isinstance(entry, dict):
        entry = {}
    workspace_id = str(entry.get("id") or "").strip() or f"workspace-{index + 1}"
    raw_name = str(entry.get("name") or "").strip()
    source_device_name = str(entry.get("source_device_name") or "").strip()
    source_workspace_name = str(entry.get("source_workspace_name") or "").strip()
    name = raw_name or source_workspace_name or f"Workspace {index + 1}"
    if workspace_id == DEFAULT_WORKSPACE_ID:
        name = raw_name or DEFAULT_WORKSPACE_NAME
    slug = _slugify(entry.get("slug") or name, DEFAULT_WORKSPACE_SLUG)
    namespace = _clean_relative_path(entry.get("namespace"))
    if workspace_id == DEFAULT_WORKSPACE_ID:
        namespace = ""
        slug = DEFAULT_WORKSPACE_SLUG
    root_path = _clean_relative_path(entry.get("root_path"))
    if not root_path:
        root_path = (
            DEFAULT_WORKSPACE_ROOT
            if workspace_id == DEFAULT_WORKSPACE_ID
            else _join_relative_path(DEFAULT_WORKSPACE_ROOT, slug)
        )
    kind = str(entry.get("kind") or "").strip().lower() or (
        "root" if workspace_id == DEFAULT_WORKSPACE_ID else "local"
    )
    return {
        "id": workspace_id,
        "name": name,
        "slug": slug,
        "namespace": namespace,
        "root_path": root_path,
        "kind": kind,
        "imported": kind == "synced" or bool(entry.get("imported")),
        "source_peer_id": str(entry.get("source_peer_id") or "").strip(),
        "source_device_name": source_device_name,
        "source_workspace_id": str(entry.get("source_workspace_id") or "").strip(),
        "source_workspace_name": source_workspace_name,
    }


def default_workspace_profile() -> Dict[str, Any]:
    return normalize_workspace_profile({"id": DEFAULT_WORKSPACE_ID}, 0)


def load_workspace_state(
    settings: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], str, List[str]]:
    settings_payload = (
        settings if isinstance(settings, dict) else user_settings.load_settings()
    )
    raw_profiles = settings_payload.get("workspace_profiles")
    profiles: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()

    def _append(profile: Dict[str, Any]) -> None:
        profile_id = str(profile.get("id") or "").strip()
        if not profile_id or profile_id in seen_ids:
            return
        seen_ids.add(profile_id)
        profiles.append(profile)

    _append(default_workspace_profile())
    if isinstance(raw_profiles, list):
        for index, entry in enumerate(raw_profiles):
            profile = normalize_workspace_profile(entry, index)
            if profile["id"] == DEFAULT_WORKSPACE_ID:
                continue
            _append(profile)

    active_workspace_id = str(settings_payload.get("active_workspace_id") or "").strip()
    if active_workspace_id not in seen_ids:
        active_workspace_id = DEFAULT_WORKSPACE_ID

    requested_selection = settings_payload.get("sync_selected_workspace_ids")
    selected_workspace_ids = normalize_workspace_ids(requested_selection, profiles)
    if not selected_workspace_ids:
        selected_workspace_ids = [active_workspace_id]

    return profiles, active_workspace_id, selected_workspace_ids


def normalize_workspace_ids(
    value: Optional[Iterable[Any]],
    profiles: Optional[List[Dict[str, Any]]] = None,
) -> List[str]:
    available = {
        str(profile.get("id") or "").strip()
        for profile in (profiles or [default_workspace_profile()])
        if str(profile.get("id") or "").strip()
    }
    if DEFAULT_WORKSPACE_ID not in available:
        available.add(DEFAULT_WORKSPACE_ID)
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes)):
        return []
    seen: set[str] = set()
    normalized: List[str] = []
    for item in value:
        workspace_id = str(item or "").strip()
        if not workspace_id or workspace_id not in available or workspace_id in seen:
            continue
        seen.add(workspace_id)
        normalized.append(workspace_id)
    return normalized


def workspace_profile_map(
    profiles: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Dict[str, Any]]:
    available = profiles or [default_workspace_profile()]
    return {
        str(profile.get("id") or "").strip(): profile
        for profile in available
        if str(profile.get("id") or "").strip()
    }


def summarize_workspace_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    workspace_id = str(profile.get("id") or "").strip()
    return {
        "id": workspace_id,
        "name": str(profile.get("name") or "").strip() or workspace_id or "workspace",
        "slug": str(profile.get("slug") or "").strip() or DEFAULT_WORKSPACE_SLUG,
        "namespace": str(profile.get("namespace") or "").strip(),
        "root_path": str(profile.get("root_path") or "").strip()
        or DEFAULT_WORKSPACE_ROOT,
        "kind": str(profile.get("kind") or "").strip() or "local",
        "imported": bool(profile.get("imported")),
        "source_peer_id": str(profile.get("source_peer_id") or "").strip(),
        "source_device_name": str(profile.get("source_device_name") or "").strip(),
        "source_workspace_id": str(profile.get("source_workspace_id") or "").strip(),
        "source_workspace_name": str(
            profile.get("source_workspace_name") or ""
        ).strip(),
        "is_root": workspace_id == DEFAULT_WORKSPACE_ID,
    }


def resolve_workspace_selection(
    requested_ids: Optional[Iterable[Any]],
    *,
    settings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    profiles, active_workspace_id, default_selected = load_workspace_state(settings)
    selected_ids = normalize_workspace_ids(requested_ids, profiles) or default_selected
    profile_by_id = workspace_profile_map(profiles)
    selected_profiles = [
        profile_by_id[workspace_id]
        for workspace_id in selected_ids
        if workspace_id in profile_by_id
    ]
    namespaces = [
        str(profile.get("namespace") or "").strip()
        for profile in selected_profiles
        if str(profile.get("namespace") or "").strip()
    ]
    return {
        "profiles": profiles,
        "profile_by_id": profile_by_id,
        "active_workspace_id": active_workspace_id,
        "selected_workspace_ids": selected_ids,
        "selected_profiles": selected_profiles,
        "include_root": DEFAULT_WORKSPACE_ID in selected_ids,
        "namespaces": namespaces,
    }


def build_synced_workspace_profile(
    *,
    parent_profile: Optional[Dict[str, Any]],
    source_peer_id: str,
    source_device_name: str,
    source_workspace_id: str,
    source_workspace_name: str,
    source_workspace_slug: str = "",
) -> Dict[str, Any]:
    location = resolve_synced_workspace_location(
        parent_profile=parent_profile,
        source_device_name=source_device_name,
        source_workspace_id=source_workspace_id,
        source_workspace_name=source_workspace_name,
        source_workspace_slug=source_workspace_slug,
    )
    source_device_slug = _slugify(source_device_name or "remote", "remote")
    source_workspace_slug = _slugify(
        source_workspace_slug or source_workspace_name or source_workspace_id or "workspace",
        "workspace",
    )
    return normalize_workspace_profile(
        {
            "id": f"sync-{_slugify(source_peer_id or source_device_name, 'peer')}-{source_workspace_slug}",
            "name": location["display_name"],
            "slug": f"{source_device_slug}-{source_workspace_slug}",
            "namespace": location["namespace"],
            "root_path": location["root_path"],
            "kind": "synced",
            "imported": True,
            "source_peer_id": source_peer_id,
            "source_device_name": source_device_name,
            "source_workspace_id": source_workspace_id,
            "source_workspace_name": source_workspace_name,
        }
    )
