from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

try:
    import tomllib
except ImportError:  # pragma: no cover - Python 3.11+ in supported environments
    tomllib = None


STATUS_SCHEMA_VERSION = 1
DEPLOYMENT_DESCRIPTOR_SCHEMA_VERSION = 1
BUILD_RECEIPT_SCHEMA_VERSION = 1
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_BUILD_RECEIPT_PATH = REPO_ROOT / ".float-build.json"
DEFAULT_RELEASE_VERSION = "0.1.0a1"
logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def resolve_data_root(value: Optional[Path | str] = None) -> Path:
    raw = value if value is not None else os.getenv("FLOAT_DATA_DIR")
    path = Path(raw).expanduser() if raw else REPO_ROOT / "data"
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


def deployment_descriptor_path(data_root: Optional[Path | str] = None) -> Path:
    return resolve_data_root(data_root) / "deployment.json"


def ensure_deployment_descriptor(
    data_root: Optional[Path | str] = None,
) -> Dict[str, Any]:
    root = resolve_data_root(data_root)
    path = deployment_descriptor_path(root)
    existing = _read_json(path)
    deployment_id = str(existing.get("deployment_id") or "").strip()
    if deployment_id:
        return {
            **existing,
            "schema_version": DEPLOYMENT_DESCRIPTOR_SCHEMA_VERSION,
            "deployment_id": deployment_id,
            "data_root": str(root),
        }

    payload = {
        "schema_version": DEPLOYMENT_DESCRIPTOR_SCHEMA_VERSION,
        "deployment_id": str(uuid4()),
        "created_at": _now_iso(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except FileExistsError:
        winner = _read_json(path)
        winner_id = str(winner.get("deployment_id") or "").strip()
        if winner_id:
            payload = winner
        else:
            _atomic_write_json(path, payload)
    return {
        **payload,
        "schema_version": DEPLOYMENT_DESCRIPTOR_SCHEMA_VERSION,
        "data_root": str(root),
    }


def observe_data_revision(
    revision: Optional[Dict[str, Any]],
    *,
    data_root: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """Attach a deployment-local observation time to a deterministic revision."""
    normalized = dict(revision or {})
    digest = str(normalized.get("digest") or "").strip().lower()
    if not digest:
        return normalized

    root = resolve_data_root(data_root)
    path = deployment_descriptor_path(root)
    descriptor = ensure_deployment_descriptor(root)
    previous_digest = str(descriptor.get("data_revision_digest") or "").strip().lower()
    observed_at = str(descriptor.get("data_updated_at") or "").strip()
    if digest != previous_digest or not observed_at:
        observed_at = _now_iso()

    persisted = {key: value for key, value in descriptor.items() if key != "data_root"}
    persisted.update(
        {
            "schema_version": DEPLOYMENT_DESCRIPTOR_SCHEMA_VERSION,
            "data_revision_digest": digest,
            "data_revision_code": str(normalized.get("code") or "").strip(),
            "data_updated_at": observed_at,
        }
    )
    if (
        digest != previous_digest
        or str(descriptor.get("data_updated_at") or "").strip() != observed_at
    ):
        _atomic_write_json(path, persisted)
    if digest != previous_digest:
        try:
            from app.utils.deployment_event_store import record_event

            record_event(
                event_type="data.revision",
                data_root=root,
                local_revision_before={
                    key: value
                    for key, value in {
                        "digest": previous_digest,
                        "code": str(descriptor.get("data_revision_code") or "").strip(),
                    }.items()
                    if value
                },
                local_revision_after={
                    "digest": digest,
                    "code": str(normalized.get("code") or "").strip(),
                },
            )
        except Exception:
            logger.warning(
                "Failed to record deployment data revision event",
                exc_info=True,
            )

    return {
        **normalized,
        "observed_at_iso": observed_at,
    }


def _release_version_from_pyproject() -> str:
    if tomllib is None:
        return DEFAULT_RELEASE_VERSION
    try:
        payload = tomllib.loads(
            (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        version = str(
            (payload.get("tool") or {}).get("poetry", {}).get("version") or ""
        )
    except Exception:
        return DEFAULT_RELEASE_VERSION
    return version.strip() or DEFAULT_RELEASE_VERSION


def build_receipt_path(value: Optional[Path | str] = None) -> Path:
    raw = value if value is not None else os.getenv("FLOAT_BUILD_RECEIPT")
    path = Path(raw).expanduser() if raw else DEFAULT_BUILD_RECEIPT_PATH
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


def load_build_receipt(
    path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    payload = _read_json(build_receipt_path(path))
    if not payload:
        return {}
    return {
        **payload,
        "schema_version": int(
            payload.get("schema_version") or BUILD_RECEIPT_SCHEMA_VERSION
        ),
    }


def software_status(
    receipt: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    build = dict(receipt) if isinstance(receipt, dict) else load_build_receipt()
    release_version = str(
        os.getenv("FLOAT_SERVICE_VERSION")
        or build.get("release_version")
        or _release_version_from_pyproject()
    ).strip()
    build_code = str(
        os.getenv("FLOAT_BUILD_CODE") or build.get("build_code") or ""
    ).strip()
    source_revision = str(
        os.getenv("FLOAT_SOURCE_REVISION") or build.get("source_revision") or ""
    ).strip()
    snapshot_digest = str(build.get("snapshot_digest") or "").strip().lower()
    label = release_version
    if build_code:
        label = f"{release_version} // {build_code}"
    return {
        "release_version": release_version or DEFAULT_RELEASE_VERSION,
        "build_code": build_code,
        "label": label,
        "state": "built" if build_code else "unassigned",
        "source_revision": source_revision,
        "source_dirty": bool(build.get("source_dirty")),
        "snapshot_digest": snapshot_digest,
        "built_at": str(build.get("built_at") or "").strip(),
        "compatibility": {
            "deployment_status_schema": STATUS_SCHEMA_VERSION,
            "sync_manifest_schema": 1,
        },
    }


def compare_software_status(
    local: Optional[Dict[str, Any]], remote: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    left = local if isinstance(local, dict) else {}
    right = remote if isinstance(remote, dict) else {}
    left_version = str(left.get("release_version") or "").strip()
    right_version = str(right.get("release_version") or "").strip()
    left_build = str(left.get("build_code") or "").strip()
    right_build = str(right.get("build_code") or "").strip()
    left_digest = str(left.get("snapshot_digest") or "").strip().lower()
    right_digest = str(right.get("snapshot_digest") or "").strip().lower()
    version_match = bool(
        left_version and right_version and left_version == right_version
    )
    build_match = bool(left_build and right_build and left_build == right_build)
    digest_match = bool(left_digest and right_digest and left_digest == right_digest)

    if digest_match:
        state = "exact"
        summary = "Exact software snapshot"
    elif not left_version or not right_version:
        state = "unknown"
        summary = "Software version unavailable"
    elif not version_match:
        state = "version_mismatch"
        summary = "Different release versions"
    elif build_match:
        state = "same_build"
        summary = "Same build checkpoint"
    else:
        state = "compatible"
        summary = "Same release version; build differs or is unassigned"
    return {
        "state": state,
        "summary": summary,
        "version_match": version_match,
        "build_match": build_match,
        "digest_match": digest_match,
    }


def machine_registry_path(value: Optional[Path | str] = None) -> Path:
    raw = value if value is not None else os.getenv("FLOAT_DEPLOYMENT_REGISTRY_PATH")
    if raw:
        path = Path(raw).expanduser()
    else:
        local_app_data = str(os.getenv("LOCALAPPDATA") or "").strip()
        base = (
            Path(local_app_data)
            if local_app_data
            else Path.home() / "AppData" / "Local"
        )
        path = base / "Float" / "deployments.json"
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


def register_machine_deployment(
    *,
    descriptor: Dict[str, Any],
    software: Dict[str, Any],
    data: Optional[Dict[str, Any]] = None,
    display_name: str,
    registry_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    deployment_id = str(descriptor.get("deployment_id") or "").strip()
    if not deployment_id:
        raise ValueError("Deployment descriptor is missing deployment_id")
    path = machine_registry_path(registry_path)
    payload = _read_json(path)
    deployments = payload.get("deployments")
    if not isinstance(deployments, dict):
        deployments = {}
    entry = {
        "deployment_id": deployment_id,
        "display_name": str(display_name or "").strip(),
        "repo_root": str(REPO_ROOT),
        "data_root": str(descriptor.get("data_root") or "").strip(),
        "release_version": str(software.get("release_version") or "").strip(),
        "build_code": str(software.get("build_code") or "").strip(),
        "snapshot_digest": str(software.get("snapshot_digest") or "").strip(),
        "data_revision": str(
            ((data or {}).get("revision") or {}).get("code") or ""
        ).strip(),
        "data_updated_at": (data or {}).get("last_updated_at"),
        "last_synced_at": ((data or {}).get("sync_checkpoint") or {}).get(
            "last_synced_at"
        ),
        "last_synced_peer_deployment_id": str(
            ((data or {}).get("sync_checkpoint") or {}).get("peer_deployment_id") or ""
        ).strip(),
        "workspace_lineages": {
            str(workspace.get("id") or "")
            .strip(): str(workspace.get("lineage_id") or "")
            .strip()
            for workspace in ((data or {}).get("workspaces") or [])
            if isinstance(workspace, dict)
            and str(workspace.get("id") or "").strip()
            and str(workspace.get("lineage_id") or "").strip()
        },
        "upstream_deployment_ids": sorted(
            {
                str(workspace.get("upstream_deployment_id") or "").strip()
                for workspace in ((data or {}).get("workspaces") or [])
                if isinstance(workspace, dict)
                and str(workspace.get("upstream_deployment_id") or "").strip()
            }
        ),
        "last_seen_at": _now_iso(),
    }
    deployments[deployment_id] = entry
    next_payload = {
        "schema_version": STATUS_SCHEMA_VERSION,
        "deployments": deployments,
    }
    _atomic_write_json(path, next_payload)
    return entry


def data_status(
    *,
    settings: Optional[Dict[str, Any]] = None,
    data_root: Optional[Path | str] = None,
    data_revision: Optional[Dict[str, Any]] = None,
    sync_checkpoint: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    from app.utils import user_settings
    from app.utils.workspace_registry import (
        load_workspace_state,
        summarize_workspace_profile,
    )

    root = resolve_data_root(data_root)
    descriptor = ensure_deployment_descriptor(root)
    saved_settings = (
        settings if isinstance(settings, dict) else user_settings.load_settings()
    )
    profiles, active_workspace_id, selected_workspace_ids = load_workspace_state(
        saved_settings
    )
    workspace_summaries = []
    for profile in profiles:
        summary = summarize_workspace_profile(
            profile,
            deployment_id=str(descriptor.get("deployment_id") or "").strip(),
        )
        imported = bool(summary.get("imported")) or summary.get("kind") == "synced"
        summary["custody_role"] = "replica" if imported else "primary"
        summary["upstream_peer_id"] = str(summary.get("source_peer_id") or "").strip()
        workspace_summaries.append(summary)
    display_name = str(saved_settings.get("device_display_name") or "").strip()
    revision = dict(data_revision or {})
    checkpoint = dict(sync_checkpoint or {})
    state = str(checkpoint.get("state") or "").strip() or "ready"
    return {
        "deployment_id": descriptor["deployment_id"],
        "display_name": display_name,
        "data_root": str(root),
        "state": state,
        "revision": revision,
        "last_updated_at": (
            revision.get("observed_at_iso")
            or revision.get("updated_at_iso")
            or revision.get("updated_at")
        ),
        "sync_checkpoint": checkpoint,
        "active_workspace_id": active_workspace_id,
        "selected_workspace_ids": selected_workspace_ids,
        "workspaces": workspace_summaries,
        "workspace_count": len(workspace_summaries),
    }


def build_instance_status(
    *,
    settings: Optional[Dict[str, Any]] = None,
    data_root: Optional[Path | str] = None,
    register_machine: bool = False,
    registry_path: Optional[Path | str] = None,
    data_revision: Optional[Dict[str, Any]] = None,
    sync_checkpoint: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    software = software_status()
    data = data_status(
        settings=settings,
        data_root=data_root,
        data_revision=data_revision,
        sync_checkpoint=sync_checkpoint,
    )
    resolved_data_root = str(data.pop("data_root", "") or "")
    if register_machine:
        descriptor = {
            "deployment_id": data["deployment_id"],
            "data_root": resolved_data_root,
        }
        register_machine_deployment(
            descriptor=descriptor,
            software=software,
            data=data,
            display_name=data.get("display_name") or "",
            registry_path=registry_path,
        )
    return {
        "schema_version": STATUS_SCHEMA_VERSION,
        "software": software,
        "data": data,
    }


def deployment_identity_summary(
    *,
    settings: Optional[Dict[str, Any]] = None,
    data_root: Optional[Path | str] = None,
) -> Dict[str, Any]:
    status = build_instance_status(
        settings=settings,
        data_root=data_root,
        register_machine=False,
    )
    return {
        "deployment_id": status["data"]["deployment_id"],
        "software": status["software"],
    }


__all__ = [
    "STATUS_SCHEMA_VERSION",
    "build_instance_status",
    "compare_software_status",
    "data_status",
    "deployment_descriptor_path",
    "deployment_identity_summary",
    "ensure_deployment_descriptor",
    "load_build_receipt",
    "machine_registry_path",
    "observe_data_revision",
    "register_machine_deployment",
    "resolve_data_root",
    "software_status",
]
