from __future__ import annotations

import re
from pathlib import Path
from typing import Any, List

DEFAULT_SYNC_ROOT = "data/sync"
SYNC_WORKSPACE_SEGMENT = "workspace"
SYNC_ATTACHMENTS_SEGMENT = "attachments"


def clean_relative_path(value: Any) -> str:
    raw = str(value or "").strip().replace("\\", "/").lstrip("/")
    if not raw:
        return ""
    parts = [
        segment for segment in raw.split("/") if segment and segment not in {".", ".."}
    ]
    return "/".join(parts)


def join_relative_path(*parts: Any) -> str:
    cleaned = [clean_relative_path(part) for part in parts if clean_relative_path(part)]
    return "/".join(cleaned)


def path_token(value: Any, fallback: str) -> str:
    text = re.sub(r"[^0-9A-Za-z]+", "-", str(value or "").strip()).strip("-")
    return text or fallback


def is_default_workspace_source(
    source_workspace_id: Any,
    source_workspace_name: Any,
    source_workspace_slug: Any = None,
) -> bool:
    workspace_id = str(source_workspace_id or "").strip().lower()
    workspace_name = str(source_workspace_name or "").strip().lower()
    workspace_slug = str(source_workspace_slug or "").strip().lower()
    return (
        workspace_id in {"", "root"}
        or workspace_slug in {"", "main"}
        or workspace_name
        in {
            "main workspace",
            "main",
            "root",
        }
    )


def sync_namespace_parts(namespace: Any) -> List[str]:
    cleaned = clean_relative_path(namespace)
    if not cleaned:
        return ["Remote"]
    return [path_token(part, "Workspace") for part in cleaned.split("/")]


def sync_workspace_relative_path_from_namespace(namespace: Any) -> str:
    parts = sync_namespace_parts(namespace)
    device = parts[0] if parts else "Remote"
    workspaces = parts[1:]
    return join_relative_path("sync", device, SYNC_WORKSPACE_SEGMENT, *workspaces)


def sync_workspace_root_path_from_namespace(namespace: Any) -> str:
    return join_relative_path(
        "data", sync_workspace_relative_path_from_namespace(namespace)
    )


def sync_attachment_relative_path(
    namespace: Any, content_hash: Any, filename: Any
) -> str:
    safe_filename = Path(str(filename or "file")).name or "file"
    safe_hash = str(content_hash or "").strip().lower() or "attachment"
    workspace_rel = sync_workspace_relative_path_from_namespace(namespace)
    return join_relative_path(
        workspace_rel,
        SYNC_ATTACHMENTS_SEGMENT,
        safe_hash,
        safe_filename,
    )


def synced_workspace_namespace(
    source_device_name: Any,
    source_workspace_id: Any,
    source_workspace_name: Any,
    source_workspace_slug: Any = None,
) -> str:
    device_segment = path_token(source_device_name or "Remote", "Remote")
    workspace_segment = path_token(
        source_workspace_name or source_workspace_id or "Workspace",
        "Workspace",
    )
    parts = [device_segment]
    if not is_default_workspace_source(
        source_workspace_id,
        source_workspace_name,
        source_workspace_slug,
    ):
        parts.append(workspace_segment)
    return join_relative_path(*parts)
