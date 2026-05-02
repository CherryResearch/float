from __future__ import annotations

import re
from fnmatch import fnmatch
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.utils import user_settings
from app.utils.sync_paths import (
    clean_relative_path,
    is_default_workspace_source,
    join_relative_path,
    path_token,
    sync_workspace_root_path_from_namespace,
    synced_workspace_namespace,
)

DEFAULT_WORKSPACE_ID = "root"
DEFAULT_WORKSPACE_NAME = "Main workspace"
DEFAULT_WORKSPACE_SLUG = "main"
DEFAULT_WORKSPACE_ROOT = "data/files/workspace"
WORKSPACE_PRIVACY_MODES = {"default", "protected", "secret"}


def normalize_workspace_privacy_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in WORKSPACE_PRIVACY_MODES else "default"


def normalize_workspace_private_patterns(value: Any) -> List[str]:
    if isinstance(value, str):
        raw_items = value.splitlines()
    elif isinstance(value, Iterable) and not isinstance(value, (str, bytes, dict)):
        raw_items = list(value)
    else:
        return []
    patterns: List[str] = []
    seen: set[str] = set()
    for raw_item in raw_items:
        text = str(raw_item or "").strip().replace("\\", "/")
        text = text.lstrip("./").strip()
        if not text or text.startswith("#"):
            continue
        lowered = text.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        patterns.append(text)
    return patterns


def _slugify(value: Any, fallback: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return text or fallback


def resolve_synced_workspace_location(
    *,
    parent_profile: Optional[Dict[str, Any]],
    source_device_name: str,
    source_workspace_id: str,
    source_workspace_name: str,
    source_workspace_slug: str = "",
) -> Dict[str, Any]:
    _ = parent_profile
    namespace = synced_workspace_namespace(
        source_device_name,
        source_workspace_id,
        source_workspace_name,
        source_workspace_slug,
    )
    is_default_workspace = is_default_workspace_source(
        source_workspace_id,
        source_workspace_name,
        source_workspace_slug,
    )
    path_parts = namespace.split("/") if namespace else [path_token("Remote", "Remote")]
    return {
        "is_default_workspace": is_default_workspace,
        "path_parts": path_parts,
        "namespace": namespace,
        "root_path": sync_workspace_root_path_from_namespace(namespace),
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
    namespace = clean_relative_path(entry.get("namespace"))
    if workspace_id == DEFAULT_WORKSPACE_ID:
        namespace = ""
        slug = DEFAULT_WORKSPACE_SLUG
    root_path = clean_relative_path(entry.get("root_path"))
    if not root_path:
        root_path = (
            DEFAULT_WORKSPACE_ROOT
            if workspace_id == DEFAULT_WORKSPACE_ID
            else join_relative_path(DEFAULT_WORKSPACE_ROOT, slug)
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
        "privacy_mode": normalize_workspace_privacy_mode(entry.get("privacy_mode")),
        "private_patterns": normalize_workspace_private_patterns(
            entry.get("private_patterns")
        ),
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
    root_profile = default_workspace_profile()

    def _append(profile: Dict[str, Any]) -> None:
        profile_id = str(profile.get("id") or "").strip()
        if not profile_id or profile_id in seen_ids:
            return
        seen_ids.add(profile_id)
        profiles.append(profile)

    if isinstance(raw_profiles, list):
        for index, entry in enumerate(raw_profiles):
            profile = normalize_workspace_profile(entry, index)
            if profile["id"] == DEFAULT_WORKSPACE_ID:
                root_profile = profile
                continue
    _append(root_profile)
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
        "privacy_mode": normalize_workspace_privacy_mode(profile.get("privacy_mode")),
        "private_patterns": normalize_workspace_private_patterns(
            profile.get("private_patterns")
        ),
    }


def workspace_profile_for_namespace(
    profiles: Optional[List[Dict[str, Any]]], namespace: Any
) -> Optional[Dict[str, Any]]:
    available = profiles or [default_workspace_profile()]
    cleaned = clean_relative_path(namespace)
    root_profile: Optional[Dict[str, Any]] = None
    best_match: Optional[Dict[str, Any]] = None
    best_length = -1
    for profile in available:
        profile_namespace = clean_relative_path(profile.get("namespace"))
        if not profile_namespace:
            if root_profile is None:
                root_profile = profile
            continue
        if cleaned == profile_namespace or cleaned.startswith(f"{profile_namespace}/"):
            if len(profile_namespace) > best_length:
                best_match = profile
                best_length = len(profile_namespace)
    return best_match or root_profile


def workspace_profile_blocks_sync(profile: Optional[Dict[str, Any]]) -> bool:
    return normalize_workspace_privacy_mode((profile or {}).get("privacy_mode")) in {
        "protected",
        "secret",
    }


def workspace_profile_blocks_default_recall(profile: Optional[Dict[str, Any]]) -> bool:
    return normalize_workspace_privacy_mode((profile or {}).get("privacy_mode")) in {
        "protected",
        "secret",
    }


def workspace_match_candidates_for_profile(
    profile: Optional[Dict[str, Any]], values: Optional[Iterable[Any]]
) -> List[str]:
    namespace = clean_relative_path((profile or {}).get("namespace"))
    candidates: List[str] = []
    seen: set[str] = set()
    for raw_value in values or []:
        text = str(raw_value or "").strip().replace("\\", "/")
        text = text.lstrip("./").strip().strip("/")
        if not text:
            continue
        variants = [text]
        if namespace and text.startswith(f"{namespace}/"):
            relative = text[len(namespace) + 1 :].strip("/")
            if relative:
                variants.append(relative)
        for variant in variants:
            lowered = variant.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            candidates.append(variant)
    return candidates


def workspace_matches_private_patterns(
    profile: Optional[Dict[str, Any]], values: Optional[Iterable[Any]]
) -> bool:
    patterns = normalize_workspace_private_patterns(
        (profile or {}).get("private_patterns")
    )
    if not patterns:
        return False
    candidates = workspace_match_candidates_for_profile(profile, values)
    if not candidates:
        return False
    lowered_patterns = [pattern.lower() for pattern in patterns]
    for candidate in candidates:
        lowered_candidate = candidate.lower()
        basename = lowered_candidate.rsplit("/", 1)[-1]
        for pattern in lowered_patterns:
            if fnmatch(lowered_candidate, pattern) or fnmatch(basename, pattern):
                return True
    return False


def workspace_item_exclusion_reason(
    *,
    namespace: Any,
    values: Optional[Iterable[Any]] = None,
    profiles: Optional[List[Dict[str, Any]]] = None,
    settings: Optional[Dict[str, Any]] = None,
    purpose: str = "sync",
) -> Optional[str]:
    available_profiles = (
        profiles if profiles is not None else load_workspace_state(settings)[0]
    )
    profile = workspace_profile_for_namespace(available_profiles, namespace)
    if profile is None:
        return None
    if purpose == "sync" and workspace_profile_blocks_sync(profile):
        return "privacy_mode"
    if purpose in {
        "default_recall",
        "recall",
    } and workspace_profile_blocks_default_recall(profile):
        return "privacy_mode"
    if workspace_matches_private_patterns(profile, values):
        return "private_pattern"
    return None


def filter_workspace_ids_for_sync(
    workspace_ids: Optional[Iterable[Any]],
    profiles: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    available_profiles = profiles or [default_workspace_profile()]
    requested_ids = normalize_workspace_ids(workspace_ids, available_profiles)
    profile_by_id = workspace_profile_map(available_profiles)
    allowed_ids: List[str] = []
    blocked_ids: List[str] = []
    for workspace_id in requested_ids:
        profile = profile_by_id.get(workspace_id)
        if workspace_profile_blocks_sync(profile):
            blocked_ids.append(workspace_id)
            continue
        allowed_ids.append(workspace_id)
    return {
        "requested_workspace_ids": requested_ids,
        "workspace_ids": allowed_ids,
        "privacy_ignored_workspace_ids": blocked_ids,
    }


def resolve_workspace_selection(
    requested_ids: Optional[Iterable[Any]],
    *,
    settings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    profiles, active_workspace_id, default_selected = load_workspace_state(settings)
    requested_workspace_ids = (
        normalize_workspace_ids(requested_ids, profiles) or default_selected
    )
    sync_filter = filter_workspace_ids_for_sync(requested_workspace_ids, profiles)
    selected_ids = list(sync_filter["workspace_ids"])
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
        "requested_workspace_ids": requested_workspace_ids,
        "selected_workspace_ids": selected_ids,
        "privacy_ignored_workspace_ids": list(
            sync_filter["privacy_ignored_workspace_ids"]
        ),
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
        source_workspace_slug
        or source_workspace_name
        or source_workspace_id
        or "workspace",
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
