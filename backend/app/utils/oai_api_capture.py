"""Best-effort OpenAI API capture logging for local debugging.

Responses API stream snapshots echo the complete tool catalog at several lifecycle
stages. Captures keep the outbound catalog once and replace those response echoes
with deterministic references so the log stays inspectable without multiplying a
large schema payload.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# Raw content is sensitive: never capture unless deliberately enabled locally.
CAPTURE_OPT_IN = "FLOAT_OAI_CAPTURE_CONTENT"
MAX_CAPTURE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
MAX_CAPTURE_FILES = 50
MAX_AGE_SECONDS = 7 * 24 * 60 * 60
_CAPTURE_NAME = re.compile(r"capture-[0-9a-f]{32}\.json\Z")


def _capture_directory() -> Path:
    from app.config import load_config

    root = Path(load_config()["data_dir"]).resolve()
    directory = root
    for component in ("diagnostics", "oai_api"):
        directory = directory / component
        if directory.is_symlink():
            raise ValueError("Capture directory cannot be a symlink")
        directory.mkdir(exist_ok=True)
    return directory


def _prune_captures(directory: Path, incoming_bytes: int) -> None:
    """Only remove files owned by this capture format; never legacy/user files."""
    retained = []
    now = time.time()
    for path in directory.iterdir():
        if not _CAPTURE_NAME.fullmatch(path.name) or path.is_symlink():
            continue
        if not path.is_file():
            continue
        stat = path.stat()
        if now - stat.st_mtime > MAX_AGE_SECONDS:
            path.unlink()
        else:
            retained.append((stat.st_mtime, path.name, stat.st_size, path))
    retained.sort()
    total = sum(entry[2] for entry in retained)
    while retained and (
        len(retained) >= MAX_CAPTURE_FILES or total + incoming_bytes > MAX_TOTAL_BYTES
    ):
        _, _, size, path = retained.pop(0)
        path.unlink()
        total -= size


_REQUEST_TOOL_CATALOG_REF = "#/request_payload/tools"
_RESPONSE_SNAPSHOT_TYPES = {
    "response.created",
    "response.queued",
    "response.in_progress",
    "response.completed",
    "response.incomplete",
    "response.failed",
    "response.done",
}


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _response_id(payload: Dict[str, Any]) -> str:
    direct = payload.get("id")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    nested = payload.get("response")
    if isinstance(nested, dict):
        nested_id = nested.get("id")
        if isinstance(nested_id, str) and nested_id.strip():
            return nested_id.strip()
    return ""


def _stable_json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _json_pointer_part(value: Any) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def _normalization_patch(
    canonical: Any,
    observed: Any,
    *,
    path: str = "",
    catalog_root: bool = True,
) -> list[Dict[str, Any]]:
    """Return a deterministic, reconstructable difference from canonical to observed."""

    if type(canonical) is not type(observed):
        return [{"op": "replace", "path": path, "value": copy.deepcopy(observed)}]
    if isinstance(canonical, dict):
        operations: list[Dict[str, Any]] = []
        canonical_keys = set(canonical)
        observed_keys = set(observed)
        for key in sorted(canonical_keys - observed_keys):
            operations.append(
                {"op": "remove", "path": f"{path}/{_json_pointer_part(key)}"}
            )
        for key in sorted(observed_keys - canonical_keys):
            operations.append(
                {
                    "op": "add",
                    "path": f"{path}/{_json_pointer_part(key)}",
                    "value": copy.deepcopy(observed[key]),
                }
            )
        for key in sorted(canonical_keys & observed_keys):
            operations.extend(
                _normalization_patch(
                    canonical[key],
                    observed[key],
                    path=f"{path}/{_json_pointer_part(key)}",
                    catalog_root=False,
                )
            )
        return operations
    if isinstance(canonical, list):
        if not catalog_root and len(canonical) != len(observed):
            return [{"op": "replace", "path": path, "value": copy.deepcopy(observed)}]
        operations = []
        common_length = min(len(canonical), len(observed))
        for index in range(common_length):
            operations.extend(
                _normalization_patch(
                    canonical[index],
                    observed[index],
                    path=f"{path}/{index}",
                    catalog_root=False,
                )
            )
        for index in range(len(canonical) - 1, len(observed) - 1, -1):
            operations.append({"op": "remove", "path": f"{path}/{index}"})
        for index in range(len(canonical), len(observed)):
            operations.append(
                {
                    "op": "add",
                    "path": f"{path}/{index}",
                    "value": copy.deepcopy(observed[index]),
                }
            )
        return operations
    if canonical != observed:
        return [{"op": "replace", "path": path, "value": copy.deepcopy(observed)}]
    return []


def _catalog_reference(
    observed_catalog: list[Any],
    *,
    canonical_catalog: list[Any],
    canonical_sha256: str,
    variants: Dict[str, Any],
) -> Dict[str, Any]:
    observed_sha256 = _stable_json_sha256(observed_catalog)
    if observed_sha256 == canonical_sha256:
        return {"$ref": _REQUEST_TOOL_CATALOG_REF}

    variant_id = observed_sha256.removeprefix("sha256:")
    variants.setdefault(
        variant_id,
        {
            "base_ref": _REQUEST_TOOL_CATALOG_REF,
            "base_sha256": canonical_sha256,
            "observed_sha256": observed_sha256,
            "observed_count": len(observed_catalog),
            "normalization_patch": _normalization_patch(
                canonical_catalog,
                observed_catalog,
            ),
        },
    )
    return {
        "$ref": _REQUEST_TOOL_CATALOG_REF,
        "normalization_ref": f"#/tool_catalog/variants/{variant_id}",
    }


def _replace_tool_catalog(
    container: Any,
    *,
    canonical_catalog: list[Any],
    canonical_sha256: str,
    variants: Dict[str, Any],
) -> bool:
    if not isinstance(container, dict):
        return False
    observed_catalog = container.get("tools")
    if not isinstance(observed_catalog, list):
        return False
    container["tools"] = _catalog_reference(
        observed_catalog,
        canonical_catalog=canonical_catalog,
        canonical_sha256=canonical_sha256,
        variants=variants,
    )
    return True


def _deduplicate_response_tool_catalogs(
    request_payload: Dict[str, Any],
    response_payload: Dict[str, Any],
) -> tuple[Dict[str, Any], Dict[str, Any], Optional[Dict[str, Any]]]:
    """Copy capture payloads and replace provider-echoed tool catalogs."""

    request_copy = copy.deepcopy(request_payload)
    response_copy = copy.deepcopy(response_payload)
    canonical_catalog = request_copy.get("tools")
    if not isinstance(canonical_catalog, list) or not canonical_catalog:
        return request_copy, response_copy, None

    canonical_sha256 = _stable_json_sha256(canonical_catalog)
    reference_count = 0
    variants: Dict[str, Any] = {}
    reference_count += int(
        _replace_tool_catalog(
            response_copy,
            canonical_catalog=canonical_catalog,
            canonical_sha256=canonical_sha256,
            variants=variants,
        )
    )
    reference_count += int(
        _replace_tool_catalog(
            response_copy.get("response"),
            canonical_catalog=canonical_catalog,
            canonical_sha256=canonical_sha256,
            variants=variants,
        )
    )

    stream_events = response_copy.get("stream_events")
    if isinstance(stream_events, list):
        for event in stream_events:
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("type") or "").strip().lower()
            if event_type not in _RESPONSE_SNAPSHOT_TYPES:
                continue
            reference_count += int(
                _replace_tool_catalog(
                    event,
                    canonical_catalog=canonical_catalog,
                    canonical_sha256=canonical_sha256,
                    variants=variants,
                )
            )
            reference_count += int(
                _replace_tool_catalog(
                    event.get("response"),
                    canonical_catalog=canonical_catalog,
                    canonical_sha256=canonical_sha256,
                    variants=variants,
                )
            )

    catalog_metadata = {
        "ref": _REQUEST_TOOL_CATALOG_REF,
        "sha256": canonical_sha256,
        "count": len(canonical_catalog),
        "response_reference_count": reference_count,
    }
    if variants:
        catalog_metadata["variants"] = dict(sorted(variants.items()))
    return request_copy, response_copy, catalog_metadata


def write_capture(
    *,
    endpoint: str,
    request_payload: Dict[str, Any],
    response_payload: Dict[str, Any],
    session_id: Optional[str] = None,
    message_id: Optional[str] = None,
) -> Optional[str]:
    # Check before copying/serializing inputs or creating any capture directory.
    if os.getenv(CAPTURE_OPT_IN, "").strip().lower() != "true":
        return None
    try:
        (
            request_capture,
            response_capture,
            tool_catalog,
        ) = _deduplicate_response_tool_catalogs(request_payload, response_payload)
        record = {
            "captured_at": _now_iso(),
            "endpoint": endpoint,
            "session_id": str(session_id or "").strip() or None,
            "message_id": str(message_id or "").strip() or None,
            "request_payload": request_capture,
            "response_payload": response_capture,
        }
        if tool_catalog is not None:
            record["tool_catalog"] = tool_catalog
        encoded = json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8")
        if len(encoded) > min(MAX_CAPTURE_BYTES, MAX_TOTAL_BYTES):
            return None
        directory = _capture_directory()
        # Exclusive creation serializes retention across processes. Busy/stale locks
        # disable capture rather than interfering with an inference request.
        lock = directory / ".capture.lock"
        try:
            lock_handle = lock.open("x")
        except FileExistsError:
            return None
        try:
            lock_handle.close()
            _prune_captures(directory, len(encoded))
            # Provider IDs are untrusted and must never become filesystem paths.
            target = directory / f"capture-{uuid.uuid4().hex}.json"
            created = False
            try:
                with target.open("xb") as handle:
                    created = True
                    handle.write(encoded)
            except Exception:
                if created:
                    target.unlink(missing_ok=True)
                raise
            return str(target)
        finally:
            lock.unlink(missing_ok=True)
    except Exception:
        # Diagnostics must not fail the user's original request, even for malformed
        # payloads, full disks, unsafe destinations or retention failures.
        return None
