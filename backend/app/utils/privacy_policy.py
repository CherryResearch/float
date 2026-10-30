"""Data boundaries shared by memory projections, retrieval, and tool dispatch."""

from __future__ import annotations

import ipaddress
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator, Mapping
from urllib.parse import urlsplit

SECRET_MEMORY_REFERENCE = "Secret memory: read its value through local memory access."


class PrivacyCleanupError(RuntimeError):
    """A prohibited retrieval copy could not be removed reliably."""


def sensitivity(metadata: Mapping[str, Any] | None) -> str:
    return str((metadata or {}).get("sensitivity") or "mundane").strip().lower()


def is_secret_memory(metadata: Mapping[str, Any] | None, source: str = "") -> bool:
    meta = metadata or {}
    memory_source = any(
        str(value or "").strip().lower().startswith("memory:")
        for value in (source, meta.get("source"), meta.get("root_source"))
    )
    return sensitivity(meta) == "secret" and (
        memory_source
        or bool(meta.get("memory_key"))
        or str(meta.get("kind") or meta.get("type") or "").strip().lower() == "memory"
    )


def secret_memory_metadata(metadata: Mapping[str, Any], source: str) -> dict[str, Any]:
    """A derived reference carries identity and policy, never content fields."""
    identity_fields = {
        "source",
        "root_source",
        "knowledge_id",
        "memory_key",
        "key",
        "created_at",
        "updated_at",
        "chunk_index",
        "chunk_count",
        "chunked",
        "source_sync_namespace",
        "source_sync_id",
        "sensitivity_source",
    }
    result = {key: value for key, value in metadata.items() if key in identity_fields}
    result.update(
        source=source,
        kind="memory",
        type="memory",
        sensitivity="secret",
        title="Secret memory",
        privacy_projection="reference_only",
        retrieval_mirror_status="skipped_privacy",
    )
    return result


def project_knowledge_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Apply the same reference projection to direct SQL sync records."""
    result = dict(record)
    source = str(result.get("source") or "")
    metadata = dict(result.get("metadata") or {})
    metadata.setdefault("kind", result.get("kind"))
    if any(
        is_secret_memory({**metadata, **(chunk.get("metadata") or {})}, source)
        for chunk in result.get("chunks") or []
        if isinstance(chunk, dict)
    ):
        metadata["sensitivity"] = "secret"
    if not is_secret_memory(metadata, source):
        return result
    result.update(
        text=SECRET_MEMORY_REFERENCE,
        summary_text=SECRET_MEMORY_REFERENCE,
        title="Secret memory",
        kind="memory",
        metadata=secret_memory_metadata(metadata, source),
    )
    result["chunks"] = [
        {
            **chunk,
            "text": SECRET_MEMORY_REFERENCE,
            "metadata": secret_memory_metadata(
                {**metadata, **(chunk.get("metadata") or {})},
                str(chunk.get("source") or source),
            ),
        }
        for chunk in result.get("chunks") or []
        if isinstance(chunk, dict)
    ]
    return result


def is_loopback_endpoint(endpoint: Any) -> bool:
    try:
        parsed = urlsplit(str(endpoint or "").strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        if parsed.hostname.lower() == "localhost":
            return True
        return ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class DataPolicy:
    external: bool
    allow_remote_query: bool
    # Destination classification and a local transport obligation are distinct:
    # nesting an external caller cannot release already acquired local context.
    local_only: bool = False


_CURRENT_POLICY: ContextVar[DataPolicy | None] = ContextVar(
    "float_data_policy", default=None
)


def current_data_policy() -> DataPolicy | None:
    return _CURRENT_POLICY.get()


def policy_for_destination(mode: Any, endpoint: Any = None) -> DataPolicy:
    mode = str(mode or "").strip().lower()
    local = (mode == "local" and not endpoint) or (
        mode in {"local", "server"} and is_loopback_endpoint(endpoint)
    )
    return DataPolicy(external=not local, allow_remote_query=not local)


def local_only_transport_required() -> bool:
    policy = current_data_policy()
    return policy is not None and (policy.local_only or not policy.external)


def require_allowed_endpoint(endpoint: Any) -> None:
    """Check the resolved transport, including changes after request preparation."""
    if local_only_transport_required() and not is_loopback_endpoint(endpoint):
        raise PermissionError(
            "This request contains local-only context, but its model endpoint is external. Retry with the intended local model."
        )


def transport_privacy_options() -> dict[str, bool]:
    # A loopback server must not redirect a local-only request body elsewhere.
    return {"allow_redirects": False} if local_only_transport_required() else {}


@contextmanager
def data_policy_scope(policy: DataPolicy | None) -> Iterator[None]:
    previous = _CURRENT_POLICY.get()
    # A nested tool may restrict its caller's policy, never widen it.
    if previous is not None and policy is not None:
        policy = DataPolicy(
            external=previous.external or policy.external,
            allow_remote_query=previous.allow_remote_query
            and policy.allow_remote_query,
            local_only=(
                previous.local_only
                or policy.local_only
                or not previous.external
                or not policy.external
            ),
        )
    token = _CURRENT_POLICY.set(policy if policy is not None else previous)
    try:
        yield
    finally:
        _CURRENT_POLICY.reset(token)
