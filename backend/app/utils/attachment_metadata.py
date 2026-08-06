"""Concurrency-safe persistence helpers for attachment sidecar metadata."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
from urllib.parse import parse_qsl, unquote, urlparse

_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.RLock] = {}
ATTACHMENT_INDEX_LEASE_SECONDS = 30 * 60
_SOURCE_URL_SECRET_QUERY_NAMES = {
    "token",
    "access_token",
    "api_key",
    "x_api_key",
    "apikey",
    "key",
    "signature",
    "sig",
    "credential",
    "auth",
    "authorization",
    "password",
    "passwd",
    "pwd",
    "secret",
    "client_secret",
    "bearer",
    "bearer_token",
    "session",
    "session_id",
    "jwt",
    "jwt_token",
    "id_token",
    "refresh_token",
    "client_assertion",
    "assertion",
    "googleaccessid",
    "awsaccesskeyid",
    "policy",
    # Azure shared-access-signature fields.
    "se",
    "sp",
    "sr",
    "st",
    "sv",
    "sip",
    "spr",
    "skoid",
    "sktid",
    "skt",
    "ske",
    "sks",
    "skv",
}
_SOURCE_URL_NORMALIZED_SECRET_NAMES = {
    re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    for value in _SOURCE_URL_SECRET_QUERY_NAMES
}
_SOURCE_URL_COMPACT_SECRET_NAMES = {
    value.replace("_", "") for value in _SOURCE_URL_NORMALIZED_SECRET_NAMES
}


def attachment_index_generation_is_active(
    metadata: Dict[str, Any],
    *,
    now: datetime | None = None,
) -> bool:
    """Return whether a queued/running attachment index generation is fresh."""

    if not isinstance(metadata, dict):
        return False
    if str(metadata.get("index_status") or "").strip().lower() != "indexing":
        return False
    generation = str(metadata.get("index_generation") or "").strip()
    started_at = str(metadata.get("index_generation_started_at") or "").strip()
    if not generation or not started_at:
        return False
    try:
        parsed = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return False
    current = now or datetime.now(tz=timezone.utc)
    age_seconds = (current.astimezone(timezone.utc) - parsed).total_seconds()
    return -60 <= age_seconds <= ATTACHMENT_INDEX_LEASE_SECONDS


def sanitize_attachment_source_url(value: Any, *, max_length: int = 2048) -> str:
    """Validate passive web provenance without retaining obvious credentials."""

    source_url = str(value or "").strip()
    if not source_url:
        return ""
    if len(source_url) > max_length:
        raise ValueError("source_url is too long")
    if any(character.isspace() or ord(character) < 32 for character in source_url):
        raise ValueError("source_url contains unsafe characters")
    parsed = urlparse(source_url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("source_url must be an http or https URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("source_url must not contain credentials")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("source_url has an invalid port") from exc

    def _is_secret_name(name: str) -> bool:
        decoded_name = str(name or "")
        for _ in range(2):
            next_value = unquote(decoded_name)
            if next_value == decoded_name:
                break
            decoded_name = next_value
        decoded_name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", decoded_name)
        normalized_name = re.sub(
            r"[^a-z0-9]+",
            "_",
            decoded_name.strip().casefold(),
        ).strip("_")
        compact_name = normalized_name.replace("_", "")
        return bool(
            normalized_name.startswith("x_amz_")
            or normalized_name.startswith("x_goog_")
            or normalized_name.startswith(("jwt_", "bearer_"))
            or normalized_name.endswith(
                (
                    "_password",
                    "_secret",
                    "_credential",
                    "_signature",
                    "_token",
                    "_jwt",
                    "_session",
                )
            )
            or normalized_name in _SOURCE_URL_NORMALIZED_SECRET_NAMES
            or compact_name in _SOURCE_URL_COMPACT_SECRET_NAMES
        )

    for name, _value in parse_qsl(parsed.query, keep_blank_values=True):
        if _is_secret_name(name):
            raise ValueError("source_url query contains credentials")
    decoded_fragment = unquote(parsed.fragment or "")
    fragment_parameter_groups = [decoded_fragment]
    if "?" in decoded_fragment:
        fragment_parameter_groups.append(decoded_fragment.rsplit("?", 1)[1])
    for group in fragment_parameter_groups:
        for name, _value in parse_qsl(
            group.lstrip("#?/"),
            keep_blank_values=True,
        ):
            if _is_secret_name(name):
                raise ValueError("source_url fragment contains credentials")
    return source_url


def _metadata_path(blobs_dir: Path, content_hash: str) -> Path:
    return Path(blobs_dir) / f"{content_hash}.json"


def _lock_for(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


@contextmanager
def attachment_metadata_lock(blobs_dir: Path, content_hash: str):
    """Serialize compound file/metadata operations for one attachment hash."""

    path = _metadata_path(blobs_dir, content_hash)
    with _lock_for(path):
        yield


def _read_unlocked(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _write_unlocked(path: Path, metadata: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(metadata, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def read_attachment_metadata(blobs_dir: Path, content_hash: str) -> Dict[str, Any]:
    path = _metadata_path(blobs_dir, content_hash)
    with _lock_for(path):
        return _read_unlocked(path)


def write_attachment_metadata(
    blobs_dir: Path,
    content_hash: str,
    metadata: Dict[str, Any],
) -> None:
    path = _metadata_path(blobs_dir, content_hash)
    with _lock_for(path):
        _write_unlocked(path, dict(metadata))


def mutate_attachment_metadata(
    blobs_dir: Path,
    content_hash: str,
    mutate: Callable[[Dict[str, Any]], Dict[str, Any] | None],
) -> Dict[str, Any]:
    """Atomically read, mutate, and replace one attachment sidecar."""

    path = _metadata_path(blobs_dir, content_hash)
    with _lock_for(path):
        current = _read_unlocked(path)
        updated = mutate(dict(current))
        next_value = current if updated is None else dict(updated)
        _write_unlocked(path, next_value)
        return dict(next_value)


def delete_attachment_metadata(blobs_dir: Path, content_hash: str) -> None:
    path = _metadata_path(blobs_dir, content_hash)
    with _lock_for(path):
        path.unlink(missing_ok=True)
