#!/usr/bin/env python3
"""Safely install a manifest-driven Float release snapshot without Git."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

try:
    from scripts import create_release_snapshot as release
except ImportError:  # Running directly from the scripts directory.
    import create_release_snapshot as release  # type: ignore


INSTALL_SCHEMA_VERSION = 1

# Paths that were managed by an older release snapshot but are intentionally no
# longer shipped. Keep this list explicit so upgrades can prune the exact retired
# file while unknown or runtime-owned manifest entries still fail closed.
RETIRED_SHIPPED_PATHS = frozenset(
    {
        "backend/app/services/tests/test_threads_embedding_bakeoff_eval.py",
    }
)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
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


def _normalized_relative(value: Any) -> str:
    text = str(value or "").replace("\\", "/").strip().strip("/")
    if not text or text in {".", ".."}:
        raise ValueError("Deployment manifest contains an empty or invalid path")
    parts = Path(text).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"Deployment manifest path is unsafe: {text}")
    return "/".join(parts)


def _safe_destination(target: Path, relative: str) -> Path:
    normalized = _normalized_relative(relative)
    destination = (target / Path(normalized)).resolve()
    try:
        destination.relative_to(target)
    except ValueError as exc:
        raise ValueError(f"Deployment path escapes the target: {relative}") from exc
    return destination


def _snapshot_digest(snapshot: Path, relatives: Iterable[str]) -> str:
    manifest = hashlib.sha256()
    for relative in sorted(relatives):
        path = _safe_destination(snapshot, relative)
        content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest.update(f"{relative}\t{content_hash}\n".encode("utf-8"))
    return manifest.hexdigest()


def inspect_snapshot(snapshot: Path) -> dict[str, Any]:
    receipt_path = snapshot / release.BUILD_RECEIPT_NAME
    receipt = _read_json(receipt_path)
    if not receipt:
        raise ValueError(
            f"Snapshot is missing a valid {release.BUILD_RECEIPT_NAME} receipt"
        )
    shipped: list[str] = []
    all_files: list[str] = []
    for path in sorted(snapshot.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(snapshot).as_posix()
        all_files.append(relative)
        if relative == release.BUILD_RECEIPT_NAME:
            continue
        if not release.is_manifest_relative_path(relative):
            raise ValueError(f"Snapshot contains a non-shipped path: {relative}")
        shipped.append(relative)
    expected_digest = str(receipt.get("snapshot_digest") or "").strip().lower()
    actual_digest = _snapshot_digest(snapshot, shipped)
    if not expected_digest or expected_digest != actual_digest:
        raise ValueError(
            "Snapshot receipt digest does not match the staged file contents"
        )
    return {
        "receipt": receipt,
        "shipped_files": shipped,
        "installed_files": all_files,
        "snapshot_digest": actual_digest,
    }


def _installed_manifest_path(target: Path) -> Path:
    return target / release.DEPLOYMENT_MANIFEST_NAME


def _manifest_files(payload: dict[str, Any]) -> set[str]:
    raw = payload.get("installed_files")
    if not isinstance(raw, list):
        return set()
    installed: set[str] = set()
    for value in raw:
        relative = _normalized_relative(value)
        if (
            relative != release.BUILD_RECEIPT_NAME
            and not release.is_manifest_relative_path(relative)
            and relative not in RETIRED_SHIPPED_PATHS
        ):
            raise ValueError(
                "Installed deployment manifest contains a non-shipped path: "
                f"{relative}"
            )
        installed.add(relative)
    return installed


def discover_shipped_files(target: Path) -> set[str]:
    discovered: set[str] = set()
    for item in [*release.INCLUDE_PATHS, *sorted(release.OPTIONAL_PATHS)]:
        candidate = target / item
        if not candidate.exists():
            continue
        paths = [candidate] if candidate.is_file() else candidate.rglob("*")
        for path in paths:
            if not path.is_file():
                continue
            relative = path.relative_to(target).as_posix()
            if release.is_manifest_relative_path(relative):
                discovered.add(relative)
    receipt = target / release.BUILD_RECEIPT_NAME
    if receipt.is_file():
        discovered.add(release.BUILD_RECEIPT_NAME)
    return discovered


def build_deployment_plan(
    *,
    snapshot: Path,
    target: Path,
    bootstrap_prune: bool = False,
) -> dict[str, Any]:
    snapshot = snapshot.expanduser().resolve()
    target = target.expanduser().resolve()
    if not snapshot.is_dir():
        raise ValueError(f"Snapshot directory does not exist: {snapshot}")
    if not target.is_dir():
        raise ValueError(f"Target directory does not exist: {target}")
    if snapshot == target or snapshot in target.parents or target in snapshot.parents:
        raise ValueError("Snapshot and target directories must be independent")

    inspected = inspect_snapshot(snapshot)
    incoming = set(inspected["installed_files"])
    installed_manifest = _read_json(_installed_manifest_path(target))
    previous = _manifest_files(installed_manifest)
    baseline = previous
    baseline_source = "installed_manifest"
    if not previous and bootstrap_prune:
        baseline = discover_shipped_files(target)
        baseline_source = "bootstrap_discovery"
    elif not previous:
        baseline_source = "none"

    changed: list[str] = []
    unchanged: list[str] = []
    for relative in sorted(incoming):
        source = _safe_destination(snapshot, relative)
        destination = _safe_destination(target, relative)
        if (
            destination.is_file()
            and hashlib.sha256(source.read_bytes()).digest()
            == hashlib.sha256(destination.read_bytes()).digest()
        ):
            unchanged.append(relative)
        else:
            changed.append(relative)
    stale = sorted(baseline - incoming)
    return {
        **inspected,
        "snapshot": snapshot,
        "target": target,
        "changed_files": changed,
        "unchanged_files": unchanged,
        "stale_files": stale,
        "baseline_source": baseline_source,
    }


def _copy_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _remove_empty_parents(path: Path, target: Path) -> None:
    parent = path.parent
    while parent != target:
        try:
            parent.rmdir()
        except OSError:
            return
        parent = parent.parent


def _software_metadata(payload: dict[str, Any]) -> dict[str, str]:
    return {
        key: str(payload.get(key) or "").strip()
        for key in (
            "release_version",
            "build_code",
            "snapshot_digest",
            "source_revision",
        )
        if str(payload.get(key) or "").strip()
    }


def _record_software_install_event(
    *,
    target: Path,
    previous_manifest: dict[str, Any],
    manifest: dict[str, Any],
    changed_count: int,
    unchanged_count: int,
    stale_count: int,
) -> dict[str, Any]:
    try:
        from app.utils.deployment_event_store import record_event
    except ModuleNotFoundError:
        backend_root = Path(__file__).resolve().parents[1] / "backend"
        if str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))
        from app.utils.deployment_event_store import record_event

    return record_event(
        event_type="software.install",
        data_root=target / "data",
        counts={
            "changed_file_count": changed_count,
            "unchanged_file_count": unchanged_count,
            "removed_file_count": stale_count,
        },
        software_before=_software_metadata(previous_manifest),
        software_after=_software_metadata(manifest),
    )


def apply_deployment_plan(plan: dict[str, Any]) -> dict[str, Any]:
    snapshot = Path(plan["snapshot"]).resolve()
    target = Path(plan["target"]).resolve()
    changed = list(plan.get("changed_files") or [])
    stale = list(plan.get("stale_files") or [])
    previous_manifest = _read_json(_installed_manifest_path(target))

    for relative in changed:
        _copy_atomic(
            _safe_destination(snapshot, relative),
            _safe_destination(target, relative),
        )
    for relative in stale:
        destination = _safe_destination(target, relative)
        if destination.is_file():
            destination.unlink()
            _remove_empty_parents(destination, target)

    for relative in plan["installed_files"]:
        source = _safe_destination(snapshot, relative)
        destination = _safe_destination(target, relative)
        if not destination.is_file():
            raise RuntimeError(f"Deployed file is missing: {relative}")
        if (
            hashlib.sha256(source.read_bytes()).digest()
            != hashlib.sha256(destination.read_bytes()).digest()
        ):
            raise RuntimeError(f"Deployed file hash mismatch: {relative}")

    receipt = dict(plan.get("receipt") or {})
    manifest = {
        "schema_version": INSTALL_SCHEMA_VERSION,
        "deployed_at": _now_iso(),
        "snapshot_digest": str(plan.get("snapshot_digest") or ""),
        "release_version": str(receipt.get("release_version") or ""),
        "build_code": str(receipt.get("build_code") or ""),
        "source_revision": str(receipt.get("source_revision") or ""),
        "installed_files": sorted(plan["installed_files"]),
    }
    _atomic_write_json(_installed_manifest_path(target), manifest)
    _record_software_install_event(
        target=target,
        previous_manifest=previous_manifest,
        manifest=manifest,
        changed_count=len(changed),
        unchanged_count=len(list(plan.get("unchanged_files") or [])),
        stale_count=len(stale),
    )
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Install a verified Float release snapshot into an existing deployment "
            "while preserving runtime-owned data, settings, logs, and dependencies."
        )
    )
    parser.add_argument("snapshot", type=Path, help="Release snapshot directory.")
    parser.add_argument("target", type=Path, help="Existing Float deployment root.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the plan. Without this flag the command is a dry run.",
    )
    parser.add_argument(
        "--bootstrap-prune",
        action="store_true",
        help=(
            "On the first managed deployment, discover and remove stale files only "
            "inside the release allowlist. Later runs use the installed manifest."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        plan = build_deployment_plan(
            snapshot=args.snapshot,
            target=args.target,
            bootstrap_prune=bool(args.bootstrap_prune),
        )
        action = "Applied" if args.apply else "Dry run"
        if args.apply:
            manifest = apply_deployment_plan(plan)
            build_label = (
                f"{manifest['release_version']} // {manifest['build_code']}"
                if manifest["build_code"]
                else manifest["release_version"]
            )
        else:
            receipt = plan["receipt"]
            build_label = (
                f"{receipt.get('release_version')} // {receipt.get('build_code')}"
                if receipt.get("build_code")
                else str(receipt.get("release_version") or "")
            )
        print(
            f"{action}: {build_label or 'unassigned build'}; "
            f"copy={len(plan['changed_files'])}, "
            f"unchanged={len(plan['unchanged_files'])}, "
            f"stale={len(plan['stale_files'])}, "
            f"baseline={plan['baseline_source']}."
        )
        if not args.apply:
            print("Re-run with --apply to install this plan.")
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Deployment failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
