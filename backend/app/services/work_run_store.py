"""Durable device-local receipts for background work.

The Calendar event store describes future work.  This ledger deliberately has
no foreign key to that store, so deleting an event cannot erase the compact
record of work that already ran.  Receipts contain only the fields already
used by Float's Activity surface; conversation or prompt bodies are never
accepted into the payload.
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from app import config as app_config

JsonDict = Dict[str, Any]

DEFAULT_BUSY_TIMEOUT_MS = 5_000
DEFAULT_LIMIT = 100
MAX_LIMIT = 500
IDENTITY_LIMIT = 512

_SHA256_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SAFE_TOOL_TARGET_RE = re.compile(r"tool:[A-Za-z0-9_.-]{1,256}\Z")
_HASHED_TARGET_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}:sha256:[0-9a-f]{64}\Z")
_SAFE_ERROR_IDENTIFIER_RE = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*\Z")

_ACTIVE_RECOVERY_STATUSES = frozenset(
    {
        "claimed",
        "authorization_approved",
        "cancel_requested",
        "followup_pending",
        "in_progress",
        "pending",
        "prompt_resume_pending",
        "queued",
        "retrying",
        "running",
    }
)
_ATTENTION_RECOVERY_STATUSES = frozenset(
    {
        "abandoned",
        "authorization_required",
        "interrupted_unknown",
        "orphaned",
        "unknown",
    }
)
_TERMINAL_STATUSES = frozenset(
    {
        "canceled",
        "cancelled",
        "complete",
        "completed",
        "error",
        "failed",
        "authorization_denied",
        "authorization_expired",
        "authorization_invalidated",
        "invoked",
        "prompted",
        "skipped",
        "success",
        "succeeded",
        "timed_out",
        "timeout",
    }
)

_EFFECT_ATTENTION_STATUSES = frozenset({"dispatched", "interrupted_unknown", "unknown"})
_EFFECT_SAFE_STATUSES = frozenset(
    {"acknowledged", "confirmed", "intent", "intended", "not_dispatched"}
)
_EFFECT_UNCERTAIN_CERTAINTIES = frozenset({"uncertain", "unconfirmed", "unknown"})

_TEXT_LIMITS = {
    "id": 512,
    "source": 64,
    "run_id": 256,
    "job_id": 512,
    "event_id": 512,
    "event_title": 300,
    "action_id": 512,
    "action_kind": 128,
    "action_name": 256,
    "occurrence_id": 768,
    "status": 64,
    "phase": 64,
    "followup_status": 64,
    "effect_status": 64,
    "effect_certainty": 64,
    "state_delta_certainty": 64,
    "reconciliation_outcome": 64,
    "summary": 1_200,
    "recovery_reason": 600,
    "recovery_reason_code": 128,
    "recovered_from_phase": 64,
    "run_conversation_id": 512,
}
_TIMESTAMP_FIELDS = {
    "occurrence_at",
    "started_at",
    "finished_at",
    "recovered_at",
    "lease_expires_at",
}
_OWNERSHIP_LIMITS = {
    "owner_kind": 64,
    "calendar_event_id": 512,
    "conversation_id": 512,
    "message_id": 512,
    "parent_job_id": 512,
    "parent_agent_id": 512,
}
_FILTER_COLUMNS = {
    "source": "source",
    "run_id": "run_id",
    "job_id": "job_id",
    "event_id": "event_id",
    "action_id": "action_id",
    "occurrence_id": "occurrence_id",
    "status": "status",
    "phase": "phase",
    "followup_status": "followup_status",
    "owner_kind": "owner_kind",
    "conversation_id": "conversation_id",
    "message_id": "message_id",
    "parent_job_id": "parent_job_id",
    "parent_agent_id": "parent_agent_id",
}

_MEANINGFUL_EVENT_FIELDS = (
    "status",
    "phase",
    "followup_status",
    "effect_status",
    "effect_certainty",
    "state_delta_certainty",
    "reconciliation_outcome",
    "reconcile_required",
    "recovery_count",
)
_LIFECYCLE_SNAPSHOT_FIELDS = (
    *_MEANINGFUL_EVENT_FIELDS,
    "recovered_at",
    "recovery_reason",
    "recovery_reason_code",
    "recovered_from_phase",
    "tool_invoked",
    "lease_expires_at",
)

_ATTEMPT_TEXT_LIMITS = {
    "id": 512,
    "receipt_id": 512,
    "run_id": 256,
    "step_id": 512,
    "status": 64,
    "retry_of_attempt_id": 512,
    "retry_reason_code": 128,
    "checkpoint_id": 512,
    "checkpoint_status": 64,
    "checkpoint_digest": 256,
    "provider": 128,
    "model": 256,
    "provider_request_id": 512,
    "provider_response_id": 512,
    "finish_reason": 128,
    "error_category": 128,
    "error_code": 256,
    "state_change_kind": 128,
    "state_delta_certainty": 64,
    "before_state_digest": 256,
    "after_state_digest": 256,
    "effect_watermark_digest": 256,
}
_ATTEMPT_TIMESTAMP_FIELDS = {
    "created_at",
    "started_at",
    "finished_at",
    "retry_scheduled_at",
}

_EFFECT_TEXT_LIMITS = {
    "id": 512,
    "receipt_id": 512,
    "run_id": 256,
    "step_id": 512,
    "attempt_id": 512,
    "tool_name": 256,
    "tool_call_id": 512,
    "tool_kind": 128,
    "tool_provider": 128,
    "tool_server": 256,
    "effect_scope": 128,
    "replay_policy": 128,
    "status": 64,
    "certainty": 64,
    "redacted_target": 600,
    "argument_digest": 256,
    "idempotency_key": 512,
    "approval_status": 64,
    "approval_id": 512,
    "approval_policy_id": 512,
    "permission_status": 64,
    "permission_scope": 256,
    "permission_policy_id": 512,
    "permission_grant_id": 512,
    "before_digest": 256,
    "after_digest": 256,
    "result_digest": 256,
    "error_category": 128,
    "error_code": 256,
    "reconciliation_decision": 64,
    "reconciled_by": 64,
}
_EFFECT_TIMESTAMP_FIELDS = {
    "intended_at",
    "dispatched_at",
    "acknowledged_at",
    "confirmed_at",
    "reconciled_at",
    "finished_at",
}

_ERROR_LIMITS = {"category": 128, "code": 256}
_REMOTE_ID_LIMITS = {
    "operation_id": 512,
    "request_id": 512,
    "resource_id": 512,
    "version_id": 512,
    "message_id": 512,
    "event_id": 512,
    "job_id": 512,
    "transaction_id": 512,
    "commit_id": 512,
    "task_id": 512,
    "thread_id": 512,
    "deployment_id": 512,
    "record_id": 512,
}


def work_run_store_path(
    config: Optional[Mapping[str, Any]] = None,
    *,
    data_dir: Optional[str | Path] = None,
) -> Path:
    """Resolve the ledger beneath Float's configured device-local data root."""

    cfg = dict(config or {})
    explicit_store = cfg.get("work_run_store_path") or cfg.get("work_runs_store_path")
    if explicit_store:
        path = Path(str(explicit_store)).expanduser()
        if not path.is_absolute():
            path = app_config.REPO_ROOT / path
    else:
        raw_root = (
            data_dir
            if data_dir is not None
            else cfg.get("data_dir")
            or os.getenv("FLOAT_DATA_DIR")
            or app_config.DEFAULT_DATA_DIR
        )
        root = Path(str(raw_root)).expanduser()
        if not root.is_absolute():
            root = app_config.REPO_ROOT / root
        path = root / "databases" / "work_runs.sqlite3"
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _bounded_text(value: Any, limit: int) -> str:
    if value is None or not isinstance(value, (str, int, float, bool)):
        return ""
    text = str(value).strip()
    if len(text) <= limit:
        return text
    return text[:limit]


def _safe_error_identifier(value: Any, limit: int) -> str:
    """Keep stable machine codes while dropping messages and secret-bearing text."""

    if not isinstance(value, str):
        return ""
    text = value.strip()
    if len(text) > limit or not _SAFE_ERROR_IDENTIFIER_RE.fullmatch(text):
        return ""
    return text


def _validated_identity(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a non-empty string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{label} must be a non-empty string")
    if len(cleaned) > IDENTITY_LIMIT:
        raise ValueError(f"{label} must be at most {IDENTITY_LIMIT} characters")
    return cleaned


def _lookup_identity(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned or len(cleaned) > IDENTITY_LIMIT:
        return None
    return cleaned


def _validated_digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_DIGEST_RE.fullmatch(value.strip()):
        raise ValueError(f"{label} must be a full sha256 digest")
    return value.strip()


def _validated_redacted_target(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("redacted_target must be a safe redacted target")
    cleaned = value.strip()
    if not (
        _SAFE_TOOL_TARGET_RE.fullmatch(cleaned) or _HASHED_TARGET_RE.fullmatch(cleaned)
    ):
        raise ValueError(
            "redacted_target must be tool:<safe-name> or "
            "<key>:sha256:<64 lowercase hex characters>"
        )
    return cleaned


def _finite_timestamp(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    return timestamp if math.isfinite(timestamp) else None


def _bounded_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _bounded_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _bounded_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return None


def _sanitize_ownership(value: Any) -> JsonDict:
    if not isinstance(value, Mapping):
        return {}
    return {
        key: _bounded_text(value[key], limit)
        for key, limit in _OWNERSHIP_LIMITS.items()
        if key in value and value[key] is not None
    }


def _sanitize_patience(value: Any) -> JsonDict:
    if not isinstance(value, Mapping):
        return {}
    cleaned: JsonDict = {}
    if "stop_condition" in value:
        cleaned["stop_condition"] = _bounded_text(value["stop_condition"], 64)
    for key in ("max_attempts", "max_runtime_seconds", "max_provider_retries"):
        if key in value:
            cleaned[key] = _bounded_int(value[key])
    if "satisfied_threshold" in value:
        cleaned["satisfied_threshold"] = _bounded_float(value["satisfied_threshold"])
    return {key: item for key, item in cleaned.items() if item is not None}


def _sanitize_execution(value: Any) -> JsonDict:
    if not isinstance(value, Mapping):
        return {}
    cleaned: JsonDict = {}
    for key in (
        "reasoning_effort",
        "model",
        "workflow",
        "provider",
        "server_provider",
        "finish_reason",
        "termination_category",
        "requested_model",
        "received_model",
    ):
        if key in value:
            cleaned[key] = _bounded_text(value[key], 256)
    for key in ("allow_subagents", "sandbox_processes"):
        if key in value:
            cleaned[key] = _bounded_bool(value[key])
    if "permissions" in value and isinstance(value["permissions"], (list, tuple)):
        cleaned["permissions"] = [
            _bounded_text(item, 128)
            for item in value["permissions"][:32]
            if _bounded_text(item, 128)
        ]
    if "thought_trace_length" in value:
        cleaned["thought_trace_length"] = _bounded_int(value["thought_trace_length"])
    if "usage" in value and isinstance(value["usage"], Mapping):
        usage_keys = {
            "input_tokens",
            "output_tokens",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
        }
        cleaned["usage"] = {
            key: count
            for key in usage_keys
            if key in value["usage"]
            and (count := _bounded_int(value["usage"][key])) is not None
        }
    return {key: item for key, item in cleaned.items() if item is not None}


def _sanitize_storage(value: Any) -> JsonDict:
    if not isinstance(value, Mapping):
        return {}
    cleaned: JsonDict = {}
    if "schema_version" in value:
        cleaned["schema_version"] = _bounded_int(value["schema_version"])
    if "backend" in value:
        cleaned["backend"] = _bounded_text(value["backend"], 64)
    if "device_local" in value:
        cleaned["device_local"] = _bounded_bool(value["device_local"])
    for key in ("inserted_at", "updated_at"):
        if key in value:
            cleaned[key] = _finite_timestamp(value[key])
    return {key: item for key, item in cleaned.items() if item is not None}


def sanitize_work_run(record: Mapping[str, Any]) -> JsonDict:
    """Return a compact allowlisted Activity receipt.

    Unknown keys are intentionally dropped.  In particular, prompt text,
    conversation bodies, tool arguments, and raw provider results cannot enter
    this ledger accidentally.
    """

    cleaned: JsonDict = {}
    for key, limit in _TEXT_LIMITS.items():
        if key in record and record[key] is not None:
            if key == "id":
                cleaned[key] = _validated_identity(record[key], "work-run receipt id")
            elif text := _bounded_text(record[key], limit):
                cleaned[key] = text
    for key in _TIMESTAMP_FIELDS:
        if key in record:
            cleaned[key] = _finite_timestamp(record[key])
    if "ownership" in record:
        cleaned["ownership"] = _sanitize_ownership(record["ownership"])
    if "patience" in record:
        cleaned["patience"] = _sanitize_patience(record["patience"])
    if "execution" in record:
        cleaned["execution"] = _sanitize_execution(record["execution"])
    if "storage" in record:
        cleaned["storage"] = _sanitize_storage(record["storage"])
    if "authorization" in record:
        authorization = _sanitize_authorization(record["authorization"])
        if authorization:
            cleaned["authorization"] = authorization
    for key in ("recovery_count", "recovery_attempt"):
        if key in record:
            count = _bounded_int(record[key])
            if count is not None:
                cleaned[key] = max(0, min(count, 1_000_000))
    for key in ("tool_invoked", "reconcile_required"):
        if key in record:
            cleaned[key] = _bounded_bool(record[key])
    return cleaned


def _sanitize_error_metadata(value: Any) -> JsonDict:
    if not isinstance(value, Mapping):
        return {}
    cleaned: JsonDict = {}
    for key, limit in _ERROR_LIMITS.items():
        if key in value and (text := _safe_error_identifier(value[key], limit)):
            cleaned[key] = text
    if "retryable" in value:
        cleaned["retryable"] = _bounded_bool(value["retryable"])
    if "http_status" in value:
        cleaned["http_status"] = _bounded_int(value["http_status"])
    return {key: item for key, item in cleaned.items() if item is not None}


def _sanitize_attempt_retry(value: Any) -> JsonDict:
    if not isinstance(value, Mapping):
        return {}
    cleaned: JsonDict = {}
    for key, limit in {
        "of_attempt_id": 512,
        "reason_code": 128,
    }.items():
        if key in value and value[key] is not None:
            if key == "of_attempt_id":
                cleaned[key] = _validated_identity(value[key], "retry.of_attempt_id")
            elif text := _bounded_text(value[key], limit):
                cleaned[key] = text
    if "number" in value:
        cleaned["number"] = _bounded_int(value["number"])
    if "is_retry" in value:
        cleaned["is_retry"] = _bounded_bool(value["is_retry"])
    if "scheduled_at" in value:
        cleaned["scheduled_at"] = _finite_timestamp(value["scheduled_at"])
    return {key: item for key, item in cleaned.items() if item is not None}


def _sanitize_checkpoint(value: Any) -> JsonDict:
    if not isinstance(value, Mapping):
        return {}
    cleaned: JsonDict = {}
    for key, limit in {"id": 512, "status": 64, "digest": 256}.items():
        if key in value and value[key] is not None:
            if key == "id":
                cleaned[key] = _validated_identity(value[key], "checkpoint.id")
            elif key == "digest":
                cleaned[key] = _validated_digest(value[key], "checkpoint.digest")
            elif text := _bounded_text(value[key], limit):
                cleaned[key] = text
    return cleaned


def _sanitize_provider_metadata(value: Any) -> JsonDict:
    if not isinstance(value, Mapping):
        return {}
    cleaned: JsonDict = {}
    for key, limit in {
        "name": 128,
        "model": 256,
        "request_id": 512,
        "response_id": 512,
        "finish_reason": 128,
    }.items():
        if key in value and value[key] is not None:
            if text := _bounded_text(value[key], limit):
                cleaned[key] = text
    return cleaned


def _sanitize_state_delta(value: Any) -> JsonDict:
    if not isinstance(value, Mapping):
        return {}
    cleaned: JsonDict = {}
    for key, limit in {
        "kind": 128,
        "certainty": 64,
        "before_digest": 256,
        "after_digest": 256,
    }.items():
        if key in value and value[key] is not None:
            if key in {"before_digest", "after_digest"}:
                cleaned[key] = _validated_digest(value[key], f"state_delta.{key}")
            elif text := _bounded_text(value[key], limit):
                cleaned[key] = text
    if "changed" in value:
        cleaned["changed"] = _bounded_bool(value["changed"])
    if "change_count" in value:
        cleaned["change_count"] = _bounded_int(value["change_count"])
    return {key: item for key, item in cleaned.items() if item is not None}


def sanitize_work_attempt(record: Mapping[str, Any]) -> JsonDict:
    """Return metadata that is safe to retain for one provider attempt.

    Error messages, prompts, provider bodies, and checkpoint contents are not
    allowlisted.  Callers can retain codes and digests without copying content
    into the durable Activity ledger.
    """

    cleaned: JsonDict = {}
    identity_fields = {"id", "receipt_id", "retry_of_attempt_id"}
    digest_fields = {
        "checkpoint_digest",
        "before_state_digest",
        "after_state_digest",
        "effect_watermark_digest",
    }
    for key, limit in _ATTEMPT_TEXT_LIMITS.items():
        value = record.get(key)
        if value is None:
            continue
        if key in identity_fields:
            cleaned[key] = _validated_identity(value, f"attempt.{key}")
        elif key in digest_fields:
            cleaned[key] = _validated_digest(value, f"attempt.{key}")
        elif key in {"error_category", "error_code"}:
            if text := _safe_error_identifier(value, limit):
                cleaned[key] = text
        elif text := _bounded_text(value, limit):
            cleaned[key] = text
    for key in _ATTEMPT_TIMESTAMP_FIELDS:
        if key in record:
            cleaned[key] = _finite_timestamp(record[key])
    for key in ("attempt_number", "retry_number", "state_change_count"):
        if key in record:
            cleaned[key] = _bounded_int(record[key])
    for key in ("is_retry", "retryable", "state_changed"):
        if key in record:
            cleaned[key] = _bounded_bool(record[key])
    nested_sanitizers = {
        "retry": _sanitize_attempt_retry,
        "checkpoint": _sanitize_checkpoint,
        "provider_metadata": _sanitize_provider_metadata,
        "error": _sanitize_error_metadata,
        "state_delta": _sanitize_state_delta,
    }
    for key, sanitizer in nested_sanitizers.items():
        if key in record and (nested := sanitizer(record[key])):
            cleaned[key] = nested
    if isinstance(record.get("provider"), Mapping):
        provider_metadata = _sanitize_provider_metadata(record["provider"])
        if provider_metadata:
            cleaned["provider_metadata"] = provider_metadata
    return {key: item for key, item in cleaned.items() if item is not None}


def _sanitize_tool_metadata(value: Any) -> JsonDict:
    if not isinstance(value, Mapping):
        return {}
    cleaned: JsonDict = {}
    for key, limit in {
        "name": 256,
        "call_id": 512,
        "kind": 128,
        "provider": 128,
        "server": 256,
    }.items():
        if key in value and value[key] is not None:
            if text := _bounded_text(value[key], limit):
                cleaned[key] = text
    return cleaned


def _sanitize_approval(value: Any) -> JsonDict:
    if not isinstance(value, Mapping):
        return {}
    cleaned: JsonDict = {}
    for key, limit in {
        "status": 64,
        "id": 512,
        "policy_id": 512,
        "method": 128,
        "actor_kind": 128,
        "scope": 256,
    }.items():
        if key in value and value[key] is not None:
            if text := _bounded_text(value[key], limit):
                cleaned[key] = text
    if "required" in value:
        cleaned["required"] = _bounded_bool(value["required"])
    if "decided_at" in value:
        cleaned["decided_at"] = _finite_timestamp(value["decided_at"])
    return {key: item for key, item in cleaned.items() if item is not None}


def _sanitize_permission(value: Any) -> JsonDict:
    if not isinstance(value, Mapping):
        return {}
    cleaned: JsonDict = {}
    for key, limit in {
        "status": 64,
        "scope": 256,
        "policy_id": 512,
        "grant_id": 512,
        "actor_kind": 128,
    }.items():
        if key in value and value[key] is not None:
            if text := _bounded_text(value[key], limit):
                cleaned[key] = text
    if isinstance(value.get("scopes"), (list, tuple)):
        cleaned["scopes"] = [
            scope
            for item in value["scopes"][:32]
            if (scope := _bounded_text(item, 128))
        ]
    if "checked_at" in value:
        cleaned["checked_at"] = _finite_timestamp(value["checked_at"])
    return {key: item for key, item in cleaned.items() if item is not None}


def _sanitize_authorization(value: Any) -> JsonDict:
    """Retain only metadata needed to review one scheduled authorization."""

    if not isinstance(value, Mapping):
        return {}
    cleaned: JsonDict = {}
    identity_fields = {"id", "decision_id"}
    digest_fields = {
        "request_digest",
        "action_definition_digest",
        "policy_digest",
    }
    for key, limit in {
        "id": 512,
        "decision_id": 512,
        "status": 64,
        "request_digest": 256,
        "action_definition_digest": 256,
        "policy_id": 256,
        "policy_digest": 256,
        "actor_kind": 128,
        "invalidation_reason": 128,
    }.items():
        item = value.get(key)
        if item is None:
            continue
        if key in identity_fields:
            cleaned[key] = _validated_identity(item, f"authorization.{key}")
        elif key in digest_fields:
            cleaned[key] = _validated_digest(item, f"authorization.{key}")
        elif text := _bounded_text(item, limit):
            cleaned[key] = text
    for key in (
        "occurrence_at",
        "requested_at",
        "decided_at",
        "consumed_at",
        "invalidated_at",
    ):
        if key in value:
            cleaned[key] = _finite_timestamp(value[key])
    for key in ("approval_required", "can_approve"):
        if key in value:
            cleaned[key] = _bounded_bool(value[key])
    if "schema_version" in value:
        cleaned["schema_version"] = _bounded_int(value["schema_version"])
    for key in ("required_scopes", "configured_scopes", "missing_scopes"):
        if isinstance(value.get(key), (list, tuple)):
            cleaned[key] = [
                scope for item in value[key][:32] if (scope := _bounded_text(item, 128))
            ]
    return {key: item for key, item in cleaned.items() if item is not None}


def _sanitize_remote_ids(value: Any) -> JsonDict:
    if not isinstance(value, Mapping):
        return {}
    cleaned: JsonDict = {}
    for key, limit in _REMOTE_ID_LIMITS.items():
        if key in value and (text := _bounded_text(value[key], limit)):
            cleaned[key] = text
    return cleaned


def _sanitize_digests(value: Any) -> JsonDict:
    if not isinstance(value, Mapping):
        return {}
    return {
        key: _validated_digest(value[key], f"digests.{key}")
        for key in ("arguments", "before", "after", "result", "state")
        if key in value and value[key] is not None
    }


def sanitize_work_effect(record: Mapping[str, Any]) -> JsonDict:
    """Return metadata that is safe to retain for one possible side effect."""

    cleaned: JsonDict = {}
    identity_fields = {"id", "receipt_id", "attempt_id"}
    digest_fields = {
        "argument_digest",
        "idempotency_key",
        "before_digest",
        "after_digest",
        "result_digest",
    }
    for key, limit in _EFFECT_TEXT_LIMITS.items():
        value = record.get(key)
        if value is None:
            continue
        if key in identity_fields:
            cleaned[key] = _validated_identity(value, f"effect.{key}")
        elif key in digest_fields:
            cleaned[key] = _validated_digest(value, f"effect.{key}")
        elif key == "redacted_target":
            cleaned[key] = _validated_redacted_target(value)
        elif key in {"error_category", "error_code"}:
            if text := _safe_error_identifier(value, limit):
                cleaned[key] = text
        elif text := _bounded_text(value, limit):
            cleaned[key] = text
    for key in _EFFECT_TIMESTAMP_FIELDS:
        if key in record:
            cleaned[key] = _finite_timestamp(record[key])
    for key in ("approval_required", "reconcile_required"):
        if key in record:
            cleaned[key] = _bounded_bool(record[key])
    if isinstance(record.get("permission_scopes"), (list, tuple)):
        cleaned["permission_scopes"] = [
            scope
            for item in record["permission_scopes"][:32]
            if (scope := _bounded_text(item, 128))
        ]
    nested_sanitizers = {
        "tool": _sanitize_tool_metadata,
        "approval": _sanitize_approval,
        "approval_snapshot": _sanitize_approval,
        "permission": _sanitize_permission,
        "permission_snapshot": _sanitize_permission,
        "remote_ids": _sanitize_remote_ids,
        "digests": _sanitize_digests,
        "error": _sanitize_error_metadata,
    }
    for key, sanitizer in nested_sanitizers.items():
        if key in record and (nested := sanitizer(record[key])):
            cleaned[key] = nested
    return {key: item for key, item in cleaned.items() if item is not None}


def recovery_state_for_status(status: Any) -> str:
    """Classify a run without treating uncertain work as safe to repeat."""

    normalized = _bounded_text(status, 64).lower()
    if normalized in _ACTIVE_RECOVERY_STATUSES:
        return "active"
    if normalized in _ATTENTION_RECOVERY_STATUSES:
        return "attention"
    if normalized in _TERMINAL_STATUSES:
        return "terminal"
    return "unknown"


def recovery_state_for_run(record: Mapping[str, Any]) -> str:
    """Classify a full receipt, including resumable phase/follow-up state."""

    certainty_values = {
        _bounded_text(record.get("effect_certainty"), 64).lower(),
        _bounded_text(record.get("state_delta_certainty"), 64).lower(),
    }
    if record.get("reconcile_required") is True or certainty_values & {
        "unknown",
        "uncertain",
        "unconfirmed",
    }:
        return "attention"
    status_state = recovery_state_for_status(record.get("status"))
    if status_state == "attention":
        return status_state
    phase = _bounded_text(record.get("phase"), 64).lower()
    followup = _bounded_text(record.get("followup_status"), 64).lower()
    if phase in _ATTENTION_RECOVERY_STATUSES or followup in (
        _ATTENTION_RECOVERY_STATUSES
    ):
        return "attention"
    if phase in _ACTIVE_RECOVERY_STATUSES or followup in _ACTIVE_RECOVERY_STATUSES:
        return "active"
    return status_state


class WorkRunTransitionConflict(RuntimeError):
    """Raised when a conditional effect transition observes another status."""

    def __init__(
        self,
        effect_id: str,
        *,
        expected_statuses: Iterable[str],
        actual_status: Optional[str],
    ) -> None:
        self.effect_id = effect_id
        self.expected_statuses = tuple(expected_statuses)
        self.actual_status = actual_status
        expected = ", ".join(self.expected_statuses)
        actual = actual_status if actual_status is not None else "missing"
        super().__init__(
            f"effect {effect_id!r} status is {actual!r}; expected one of {expected}"
        )


class WorkRunStore:
    """SQLite-backed compact receipt ledger for device-local background work."""

    def __init__(
        self,
        config: Optional[Mapping[str, Any]] = None,
        *,
        data_dir: Optional[str | Path] = None,
        path: Optional[str | Path] = None,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
        now_fn: Callable[[], float] = time.time,
    ) -> None:
        self.path = (
            Path(path).expanduser().resolve()
            if path is not None
            else work_run_store_path(config, data_dir=data_dir)
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.busy_timeout_ms = max(1, int(busy_timeout_ms))
        self.now = now_fn
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self.path), timeout=self.busy_timeout_ms / 1000.0
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _ensure_schema(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS work_runs (
                    receipt_id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    run_id TEXT,
                    job_id TEXT,
                    event_id TEXT,
                    action_id TEXT,
                    occurrence_id TEXT,
                    status TEXT NOT NULL,
                    phase TEXT,
                    followup_status TEXT,
                    recovery_state TEXT NOT NULL,
                    recovery_count INTEGER NOT NULL DEFAULT 0,
                    recovered_at REAL,
                    lease_expires_at REAL,
                    tool_invoked INTEGER,
                    sort_at REAL NOT NULL,
                    started_at REAL,
                    finished_at REAL,
                    owner_kind TEXT,
                    conversation_id TEXT,
                    message_id TEXT,
                    parent_job_id TEXT,
                    parent_agent_id TEXT,
                    inserted_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    event_count INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_work_runs_sort "
                "ON work_runs(sort_at DESC, receipt_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_work_runs_source_job "
                "ON work_runs(source, job_id, sort_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_work_runs_event_action "
                "ON work_runs(event_id, action_id, occurrence_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_work_runs_status "
                "ON work_runs(status, sort_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_work_runs_recovery "
                "ON work_runs(recovery_state, lease_expires_at, updated_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_work_runs_conversation "
                "ON work_runs(conversation_id, sort_at DESC)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS work_run_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    receipt_id TEXT NOT NULL,
                    recorded_at REAL NOT NULL,
                    status TEXT,
                    phase TEXT,
                    followup_status TEXT,
                    recovery_count INTEGER NOT NULL DEFAULT 0,
                    summary TEXT,
                    changed_fields_json TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_work_run_events_receipt "
                "ON work_run_events(receipt_id, sequence DESC)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS work_run_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    receipt_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at REAL,
                    inserted_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    transition_count INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_work_run_attempts_receipt "
                "ON work_run_attempts(receipt_id, inserted_at, attempt_id)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS work_run_attempt_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    attempt_id TEXT NOT NULL,
                    receipt_id TEXT NOT NULL,
                    recorded_at REAL NOT NULL,
                    status TEXT NOT NULL,
                    changed_fields_json TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_work_run_attempt_events_id "
                "ON work_run_attempt_events(attempt_id, sequence)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS work_run_effects (
                    effect_id TEXT PRIMARY KEY,
                    receipt_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    intended_at REAL,
                    inserted_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    transition_count INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_work_run_effects_receipt "
                "ON work_run_effects(receipt_id, inserted_at, effect_id)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS work_run_effect_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    effect_id TEXT NOT NULL,
                    receipt_id TEXT NOT NULL,
                    recorded_at REAL NOT NULL,
                    status TEXT NOT NULL,
                    changed_fields_json TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_work_run_effect_events_id "
                "ON work_run_effect_events(effect_id, sequence)"
            )

    @staticmethod
    def _decode_payload(row: sqlite3.Row) -> JsonDict:
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        payload = payload if isinstance(payload, dict) else {}
        if "event_count" in row.keys():
            payload["event_count"] = int(row["event_count"] or 0)
        if "attempt_count" in row.keys():
            payload["attempt_count"] = int(row["attempt_count"] or 0)
        if "effect_count" in row.keys():
            payload["effect_count"] = int(row["effect_count"] or 0)
        return payload

    @staticmethod
    def _effect_needs_attention(effect: Mapping[str, Any]) -> bool:
        status = _bounded_text(effect.get("status"), 64).lower()
        certainty = _bounded_text(effect.get("certainty"), 64).lower()
        return bool(
            effect.get("reconcile_required") is True
            or status in _EFFECT_ATTENTION_STATUSES
            or status not in _EFFECT_SAFE_STATUSES
            or certainty in _EFFECT_UNCERTAIN_CERTAINTIES
        )

    @classmethod
    def _effect_projection(cls, effects: Iterable[Mapping[str, Any]]) -> JsonDict:
        """Conservatively summarize authoritative current child effect states."""

        current = [dict(effect) for effect in effects]
        if not current:
            return {}

        def projection_priority(effect: Mapping[str, Any]) -> tuple[int, float, str]:
            status = _bounded_text(effect.get("status"), 64).lower()
            if cls._effect_needs_attention(effect):
                severity = 100
            elif status in {"intent", "intended"}:
                severity = 70
            elif status == "acknowledged":
                severity = 50
            elif status == "not_dispatched":
                severity = 40
            else:  # Only an explicit child confirmation reaches this branch.
                severity = 30
            return (
                severity,
                _finite_timestamp(effect.get("updated_at")) or 0.0,
                _bounded_text(effect.get("id"), IDENTITY_LIMIT),
            )

        representative = max(current, key=projection_priority)
        status = _bounded_text(representative.get("status"), 64).lower()
        certainty = _bounded_text(representative.get("certainty"), 64).lower()
        projection: JsonDict = {
            "reconcile_required": any(
                cls._effect_needs_attention(effect) for effect in current
            )
        }
        if not projection["reconcile_required"] and any(
            _bounded_text(effect.get("reconciliation_decision"), 64).lower()
            in {"confirm_applied", "confirm_no_change"}
            for effect in current
        ):
            resolutions = []
            for effect in current:
                effect_status = _bounded_text(effect.get("status"), 64).lower()
                effect_certainty = _bounded_text(effect.get("certainty"), 64).lower()
                decision = _bounded_text(
                    effect.get("reconciliation_decision"), 64
                ).lower()
                if (
                    decision == "confirm_applied"
                    or effect_certainty
                    in {
                        "changed",
                        "confirmed_changed",
                        "reported_success",
                        "user_confirmed_applied",
                    }
                    or effect_status == "acknowledged"
                ):
                    resolutions.append("applied")
                elif (
                    decision == "confirm_no_change"
                    or effect_certainty
                    in {
                        "confirmed_no_change",
                        "user_confirmed_no_change",
                    }
                    or effect_status in {"intent", "intended", "not_dispatched"}
                ):
                    resolutions.append("no_change")
            if len(resolutions) == len(current):
                projection["effect_status"] = "confirmed"
                if {"applied", "no_change"}.issubset(resolutions):
                    projection["effect_certainty"] = "mixed_user_confirmed"
                elif "applied" in resolutions:
                    projection["effect_certainty"] = "user_confirmed_applied"
                else:
                    projection["effect_certainty"] = "user_confirmed_no_change"
                return projection
        if status:
            projection["effect_status"] = status
        if certainty:
            projection["effect_certainty"] = certainty
        return projection

    def _decode_receipts_with_effects(
        self,
        connection: sqlite3.Connection,
        rows: Iterable[sqlite3.Row],
    ) -> List[JsonDict]:
        """Overlay parent receipts from child effects within the read snapshot."""

        receipts = [self._decode_payload(row) for row in rows]
        receipt_ids = [
            receipt_id
            for receipt in receipts
            if (receipt_id := _lookup_identity(receipt.get("id"))) is not None
        ]
        effects_by_receipt: Dict[str, List[JsonDict]] = {}
        # Stay below SQLite's common 999-variable limit for larger recovery scans.
        for start in range(0, len(receipt_ids), 400):
            batch = receipt_ids[start : start + 400]
            placeholders = ", ".join("?" for _ in batch)
            effect_rows = connection.execute(
                "SELECT receipt_id, payload_json FROM work_run_effects "
                f"WHERE receipt_id IN ({placeholders})",
                batch,
            ).fetchall()
            for effect_row in effect_rows:
                try:
                    payload = json.loads(str(effect_row["payload_json"] or "{}"))
                except (TypeError, ValueError, json.JSONDecodeError):
                    payload = {}
                if isinstance(payload, dict):
                    effects_by_receipt.setdefault(
                        str(effect_row["receipt_id"]), []
                    ).append(payload)

        projected: List[JsonDict] = []
        for receipt in receipts:
            effect_projection = self._effect_projection(
                effects_by_receipt.get(str(receipt.get("id") or ""), [])
            )
            if effect_projection:
                parent_reconcile_required = receipt.get("reconcile_required") is True
                # Child effect snapshots are authoritative. Clear stale parent
                # effect fields when a legacy child omitted optional certainty,
                # but do not erase independent parent-level uncertainty.
                receipt.pop("effect_status", None)
                receipt.pop("effect_certainty", None)
                receipt.update(effect_projection)
                receipt["reconcile_required"] = bool(
                    parent_reconcile_required
                    or effect_projection.get("reconcile_required") is True
                )
            receipt["recovery_state"] = recovery_state_for_run(receipt)
            projected.append(receipt)
        return projected

    @staticmethod
    def _normalize_status(value: Any, *, default: str = "unknown") -> str:
        normalized = _bounded_text(value, 64).lower()
        return normalized or default

    @staticmethod
    def _normalize_source(value: Any, *, default: str = "calendar") -> str:
        normalized = _bounded_text(value, 64).lower()
        return normalized or default

    def upsert_run(
        self,
        record: Mapping[str, Any],
        *,
        source: Optional[str] = None,
    ) -> JsonDict:
        """Append a receipt or idempotently merge an update by receipt ID."""

        incoming = sanitize_work_run(record)
        receipt_id = incoming.get("id")
        if not receipt_id:
            raise ValueError("work-run receipt id is required")
        now = float(self.now())
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            existing_row = connection.execute(
                """
                SELECT payload_json, source, inserted_at, event_count
                FROM work_runs WHERE receipt_id = ?
                """,
                (receipt_id,),
            ).fetchone()
            existing = self._decode_payload(existing_row) if existing_row else {}
            existing.pop("event_count", None)
            merged = {**existing, **incoming}
            for nested_key in ("ownership", "patience", "execution", "storage"):
                previous_nested = existing.get(nested_key)
                incoming_nested = incoming.get(nested_key)
                if isinstance(previous_nested, dict) and isinstance(
                    incoming_nested, dict
                ):
                    merged[nested_key] = {**previous_nested, **incoming_nested}
            merged["id"] = receipt_id
            merged_source = self._normalize_source(
                source
                if source is not None
                else incoming.get("source")
                or (existing_row["source"] if existing_row else None)
            )
            merged_status = self._normalize_status(merged.get("status"))
            merged["source"] = merged_source
            merged["status"] = merged_status
            merged["phase"] = self._normalize_status(merged.get("phase"), default="")
            if not merged["phase"]:
                merged.pop("phase")
            if merged.get("followup_status") is not None:
                merged["followup_status"] = self._normalize_status(
                    merged.get("followup_status"), default=""
                )
                if not merged["followup_status"]:
                    merged.pop("followup_status")
            inserted_at = float(existing_row["inserted_at"]) if existing_row else now
            storage = merged.get("storage")
            storage = dict(storage) if isinstance(storage, dict) else {}
            storage.update(
                {
                    "schema_version": 1,
                    "backend": "sqlite",
                    "device_local": True,
                    "inserted_at": inserted_at,
                    "updated_at": now,
                }
            )
            merged["storage"] = storage
            recovery_state = recovery_state_for_run(merged)
            merged["recovery_state"] = recovery_state
            ownership = merged.get("ownership")
            ownership = ownership if isinstance(ownership, dict) else {}
            sort_at = (
                _finite_timestamp(merged.get("finished_at"))
                or _finite_timestamp(merged.get("started_at"))
                or _finite_timestamp(merged.get("occurrence_at"))
                or now
            )
            payload_json = json.dumps(
                merged,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            changed_fields = [
                field
                for field in _MEANINGFUL_EVENT_FIELDS
                if existing.get(field) != merged.get(field)
            ]
            connection.execute(
                """
                INSERT INTO work_runs (
                    receipt_id, source, run_id, job_id, event_id, action_id,
                    occurrence_id, status, phase, followup_status,
                    recovery_state, recovery_count, recovered_at,
                    lease_expires_at, tool_invoked, sort_at, started_at,
                    finished_at, owner_kind, conversation_id, message_id,
                    parent_job_id, parent_agent_id, inserted_at, updated_at,
                    event_count, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(receipt_id) DO UPDATE SET
                    source=excluded.source,
                    run_id=excluded.run_id,
                    job_id=excluded.job_id,
                    event_id=excluded.event_id,
                    action_id=excluded.action_id,
                    occurrence_id=excluded.occurrence_id,
                    status=excluded.status,
                    phase=excluded.phase,
                    followup_status=excluded.followup_status,
                    recovery_state=excluded.recovery_state,
                    recovery_count=excluded.recovery_count,
                    recovered_at=excluded.recovered_at,
                    lease_expires_at=excluded.lease_expires_at,
                    tool_invoked=excluded.tool_invoked,
                    sort_at=excluded.sort_at,
                    started_at=excluded.started_at,
                    finished_at=excluded.finished_at,
                    owner_kind=excluded.owner_kind,
                    conversation_id=excluded.conversation_id,
                    message_id=excluded.message_id,
                    parent_job_id=excluded.parent_job_id,
                    parent_agent_id=excluded.parent_agent_id,
                    updated_at=excluded.updated_at,
                    payload_json=excluded.payload_json
                """,
                (
                    receipt_id,
                    merged_source,
                    merged.get("run_id"),
                    merged.get("job_id"),
                    merged.get("event_id"),
                    merged.get("action_id"),
                    merged.get("occurrence_id"),
                    merged_status,
                    merged.get("phase"),
                    merged.get("followup_status"),
                    recovery_state,
                    int(merged.get("recovery_count") or 0),
                    _finite_timestamp(merged.get("recovered_at")),
                    _finite_timestamp(merged.get("lease_expires_at")),
                    (
                        1
                        if merged.get("tool_invoked") is True
                        else 0
                        if merged.get("tool_invoked") is False
                        else None
                    ),
                    sort_at,
                    _finite_timestamp(merged.get("started_at")),
                    _finite_timestamp(merged.get("finished_at")),
                    ownership.get("owner_kind"),
                    ownership.get("conversation_id"),
                    ownership.get("message_id"),
                    ownership.get("parent_job_id"),
                    ownership.get("parent_agent_id"),
                    now,
                    now,
                    0,
                    payload_json,
                ),
            )
            if changed_fields:
                event_snapshot = {
                    key: merged[key]
                    for key in _LIFECYCLE_SNAPSHOT_FIELDS
                    if key in merged
                }
                connection.execute(
                    """
                    INSERT INTO work_run_events (
                        receipt_id, recorded_at, status, phase,
                        followup_status, recovery_count, summary,
                        changed_fields_json, snapshot_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        receipt_id,
                        now,
                        merged_status,
                        merged.get("phase"),
                        merged.get("followup_status"),
                        int(merged.get("recovery_count") or 0),
                        None,
                        json.dumps(changed_fields, separators=(",", ":")),
                        json.dumps(
                            event_snapshot,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    ),
                )
                connection.execute(
                    """
                    UPDATE work_runs SET event_count = event_count + 1
                    WHERE receipt_id = ?
                    """,
                    (receipt_id,),
                )
            count_row = connection.execute(
                """
                SELECT wr.event_count,
                       (SELECT COUNT(*) FROM work_run_attempts wa
                        WHERE wa.receipt_id = wr.receipt_id) AS attempt_count,
                       (SELECT COUNT(*) FROM work_run_effects we
                        WHERE we.receipt_id = wr.receipt_id) AS effect_count
                FROM work_runs wr WHERE wr.receipt_id = ?
                """,
                (receipt_id,),
            ).fetchone()
            connection.commit()
        result = dict(merged)
        result["event_count"] = int(count_row["event_count"] if count_row else 0)
        result["attempt_count"] = int(count_row["attempt_count"] if count_row else 0)
        result["effect_count"] = int(count_row["effect_count"] if count_row else 0)
        return result

    # ``upsert`` is intentionally terse for runner call sites.
    upsert = upsert_run

    def get_run(self, receipt_id: str) -> Optional[JsonDict]:
        safe_receipt_id = _lookup_identity(receipt_id)
        if safe_receipt_id is None:
            return None
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                """
                SELECT wr.payload_json, wr.event_count,
                       (SELECT COUNT(*) FROM work_run_attempts wa
                        WHERE wa.receipt_id = wr.receipt_id) AS attempt_count,
                       (SELECT COUNT(*) FROM work_run_effects we
                        WHERE we.receipt_id = wr.receipt_id) AS effect_count
                FROM work_runs wr WHERE wr.receipt_id = ?
                """,
                (safe_receipt_id,),
            ).fetchone()
            receipts = self._decode_receipts_with_effects(
                connection, [row] if row else []
            )
        return receipts[0] if receipts else None

    get = get_run

    def has_active_run(self, *, event_id: str) -> bool:
        """Return whether an event still owns a recoverable active receipt."""

        safe_event_id = _lookup_identity(event_id)
        if safe_event_id is None:
            return False
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                """
                SELECT 1 FROM work_runs
                WHERE event_id = ? AND recovery_state = 'active'
                LIMIT 1
                """,
                (safe_event_id,),
            ).fetchone()
        return row is not None

    @staticmethod
    def _required_journal_id(record: Mapping[str, Any], key: str) -> str:
        return _validated_identity(record.get(key), key)

    @staticmethod
    def _decode_journal_payload(row: sqlite3.Row) -> JsonDict:
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        result = payload if isinstance(payload, dict) else {}
        if "transition_count" in row.keys():
            result["transition_count"] = int(row["transition_count"] or 0)
        return result

    @staticmethod
    def _merge_journal_metadata(
        existing: Mapping[str, Any], incoming: Mapping[str, Any]
    ) -> JsonDict:
        merged = {**existing, **incoming}
        for key in (
            "retry",
            "checkpoint",
            "provider_metadata",
            "error",
            "state_delta",
            "tool",
            "approval",
            "approval_snapshot",
            "permission",
            "permission_snapshot",
            "remote_ids",
            "digests",
        ):
            previous = existing.get(key)
            update = incoming.get(key)
            if isinstance(previous, dict) and isinstance(update, dict):
                merged[key] = {**previous, **update}
        return merged

    def _record_journal_entry(
        self,
        record: Mapping[str, Any],
        *,
        kind: str,
        expected_statuses: Optional[Iterable[str]] = None,
        create_only: bool = False,
    ) -> JsonDict:
        if not isinstance(record, Mapping):
            raise ValueError(f"work-run {kind} record must be a mapping")
        if kind == "attempt":
            sanitizer = sanitize_work_attempt
            id_key = "attempt_id"
            table = "work_run_attempts"
            event_table = "work_run_attempt_events"
            time_key = "started_at"
        elif kind == "effect":
            sanitizer = sanitize_work_effect
            id_key = "effect_id"
            table = "work_run_effects"
            event_table = "work_run_effect_events"
            time_key = "intended_at"
        else:  # pragma: no cover - internal programming error
            raise ValueError(f"unsupported journal kind: {kind}")

        entry_id = self._required_journal_id(record, "id")
        receipt_id = self._required_journal_id(record, "receipt_id")
        incoming = sanitizer(record)
        incoming["id"] = entry_id
        incoming["receipt_id"] = receipt_id
        normalized_expected: Optional[tuple[str, ...]] = None
        if expected_statuses is not None:
            candidates = (
                (expected_statuses,)
                if isinstance(expected_statuses, str)
                else tuple(expected_statuses)
            )
            normalized_expected = tuple(
                sorted(
                    {
                        status
                        for item in candidates
                        if (status := self._normalize_status(item, default=""))
                    }
                )
            )
            if not normalized_expected:
                raise ValueError("expected_statuses must contain a status")
        if create_only and kind != "effect":
            raise ValueError("create_only is supported only for effect records")
        if create_only and normalized_expected is not None:
            raise ValueError("create_only and expected_statuses are mutually exclusive")

        now = float(self.now())
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            parent_row = connection.execute(
                "SELECT 1 FROM work_runs WHERE receipt_id = ?",
                (receipt_id,),
            ).fetchone()
            if parent_row is None:
                raise ValueError(
                    f"{kind} {entry_id!r} references missing receipt " f"{receipt_id!r}"
                )
            existing_row = connection.execute(
                f"SELECT payload_json, transition_count FROM {table} "
                f"WHERE {id_key} = ?",
                (entry_id,),
            ).fetchone()
            existing = (
                self._decode_journal_payload(existing_row) if existing_row else {}
            )
            existing.pop("transition_count", None)
            actual_status = (
                self._normalize_status(existing.get("status")) if existing_row else None
            )
            if normalized_expected is not None and (
                actual_status not in normalized_expected
            ):
                raise WorkRunTransitionConflict(
                    entry_id,
                    expected_statuses=normalized_expected,
                    actual_status=actual_status,
                )
            existing_receipt_id = existing.get("receipt_id")
            if existing_receipt_id and existing_receipt_id != receipt_id:
                raise ValueError(
                    f"{kind} {entry_id!r} already belongs to receipt "
                    f"{existing_receipt_id!r}"
                )

            if create_only and existing_row:
                comparable_existing = {
                    key: value
                    for key, value in existing.items()
                    if key not in {"inserted_at", "updated_at"}
                }
                comparable_incoming = dict(incoming)
                comparable_incoming["status"] = self._normalize_status(
                    comparable_incoming.get("status")
                )
                if comparable_existing == comparable_incoming:
                    result = dict(existing)
                    result["transition_count"] = int(
                        existing_row["transition_count"] or 0
                    )
                    return result
                raise WorkRunTransitionConflict(
                    entry_id,
                    expected_statuses=("missing",),
                    actual_status=actual_status,
                )

            merged = self._merge_journal_metadata(existing, incoming)
            merged["id"] = entry_id
            merged["receipt_id"] = receipt_id
            merged["status"] = self._normalize_status(
                incoming.get("status") or existing.get("status")
            )
            comparable_existing = {
                key: value
                for key, value in existing.items()
                if key not in {"inserted_at", "updated_at"}
            }
            comparable_merged = {
                key: value
                for key, value in merged.items()
                if key not in {"inserted_at", "updated_at"}
            }
            changed_fields = sorted(
                key
                for key in set(comparable_existing) | set(comparable_merged)
                if comparable_existing.get(key) != comparable_merged.get(key)
            )
            if existing_row and not changed_fields:
                result = dict(existing)
                result["transition_count"] = int(existing_row["transition_count"] or 0)
                return result

            inserted_at = (
                float(existing["inserted_at"])
                if existing_row and existing.get("inserted_at") is not None
                else now
            )
            merged["inserted_at"] = inserted_at
            merged["updated_at"] = now
            payload_json = json.dumps(
                merged,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if existing_row:
                connection.execute(
                    f"""
                    UPDATE {table}
                    SET receipt_id = ?, status = ?, {time_key} = ?,
                        updated_at = ?, payload_json = ?
                    WHERE {id_key} = ?
                    """,
                    (
                        receipt_id,
                        merged["status"],
                        _finite_timestamp(merged.get(time_key)),
                        now,
                        payload_json,
                        entry_id,
                    ),
                )
            else:
                connection.execute(
                    f"""
                    INSERT INTO {table} (
                        {id_key}, receipt_id, status, {time_key}, inserted_at,
                        updated_at, transition_count, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                    """,
                    (
                        entry_id,
                        receipt_id,
                        merged["status"],
                        _finite_timestamp(merged.get(time_key)),
                        inserted_at,
                        now,
                        payload_json,
                    ),
                )
            connection.execute(
                f"""
                INSERT INTO {event_table} (
                    {id_key}, receipt_id, recorded_at, status,
                    changed_fields_json, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_id,
                    receipt_id,
                    now,
                    merged["status"],
                    json.dumps(changed_fields, separators=(",", ":")),
                    payload_json,
                ),
            )
            connection.execute(
                f"UPDATE {table} SET transition_count = transition_count + 1 "
                f"WHERE {id_key} = ?",
                (entry_id,),
            )
            count_row = connection.execute(
                f"SELECT transition_count FROM {table} WHERE {id_key} = ?",
                (entry_id,),
            ).fetchone()
            connection.commit()
        result = dict(merged)
        result["transition_count"] = int(
            count_row["transition_count"] if count_row else 0
        )
        return result

    def record_attempt(self, record: Mapping[str, Any]) -> JsonDict:
        """Record an idempotent metadata-only provider-attempt transition."""

        return self._record_journal_entry(record, kind="attempt")

    def record_effect(
        self,
        record: Mapping[str, Any],
        *,
        expected_statuses: Optional[Iterable[str]] = None,
        create_only: bool = False,
    ) -> JsonDict:
        """Record effect metadata with optional compare-and-set semantics."""

        return self._record_journal_entry(
            record,
            kind="effect",
            expected_statuses=expected_statuses,
            create_only=create_only,
        )

    def _list_journal_entries(
        self,
        receipt_id: str,
        *,
        kind: str,
        limit: int,
        offset: int,
    ) -> List[JsonDict]:
        safe_receipt_id = _lookup_identity(receipt_id)
        if safe_receipt_id is None:
            return []
        if kind == "attempt":
            table = "work_run_attempts"
            id_key = "attempt_id"
        elif kind == "effect":
            table = "work_run_effects"
            id_key = "effect_id"
        else:  # pragma: no cover - internal programming error
            raise ValueError(f"unsupported journal kind: {kind}")
        safe_limit = max(1, min(int(limit), MAX_LIMIT))
        safe_offset = max(0, int(offset))
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                f"""
                SELECT payload_json, transition_count FROM {table}
                WHERE receipt_id = ?
                ORDER BY inserted_at ASC, {id_key} ASC LIMIT ? OFFSET ?
                """,
                (safe_receipt_id, safe_limit, safe_offset),
            ).fetchall()
        return [self._decode_journal_payload(row) for row in rows]

    def list_attempts(
        self,
        receipt_id: str,
        *,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> List[JsonDict]:
        return self._list_journal_entries(
            receipt_id,
            kind="attempt",
            limit=limit,
            offset=offset,
        )

    def list_effects(
        self,
        receipt_id: str,
        *,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> List[JsonDict]:
        return self._list_journal_entries(
            receipt_id,
            kind="effect",
            limit=limit,
            offset=offset,
        )

    def _count_journal_entries(self, receipt_id: str, *, kind: str) -> int:
        safe_receipt_id = _lookup_identity(receipt_id)
        if safe_receipt_id is None:
            return 0
        table = "work_run_attempts" if kind == "attempt" else "work_run_effects"
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                f"SELECT COUNT(*) AS count FROM {table} WHERE receipt_id = ?",
                (safe_receipt_id,),
            ).fetchone()
        return int(row["count"] if row else 0)

    def count_attempts(self, receipt_id: str) -> int:
        return self._count_journal_entries(receipt_id, kind="attempt")

    def count_effects(self, receipt_id: str) -> int:
        return self._count_journal_entries(receipt_id, kind="effect")

    @staticmethod
    def _unresolved_effect_predicate(table_alias: str) -> tuple[str, List[str]]:
        """Build the shared fail-closed predicate for current effect rows."""

        if table_alias not in {"candidate_effect", "we"}:
            raise ValueError("unsupported effect table alias")
        safe_statuses = sorted(_EFFECT_SAFE_STATUSES)
        uncertain_certainties = sorted(_EFFECT_UNCERTAIN_CERTAINTIES)
        status_placeholders = ", ".join("?" for _ in safe_statuses)
        certainty_placeholders = ", ".join("?" for _ in uncertain_certainties)
        predicate = f"""
            (
                LOWER({table_alias}.status) NOT IN ({status_placeholders})
                OR CASE
                    WHEN json_valid({table_alias}.payload_json) = 0 THEN 1
                    ELSE (
                        json_extract(
                            {table_alias}.payload_json,
                            '$.reconcile_required'
                        ) = 1
                        OR LOWER(COALESCE(
                            json_extract(
                                {table_alias}.payload_json,
                                '$.certainty'
                            ), ''
                        )) IN ({certainty_placeholders})
                    )
                END
            )
        """
        return predicate, [*safe_statuses, *uncertain_certainties]

    def has_unresolved_effects(self, *, event_id: str, action_id: str) -> bool:
        """Return whether an action has an effect that is unsafe to replay."""

        safe_event_id = _validated_identity(event_id, "event_id")
        safe_action_id = _validated_identity(action_id, "action_id")
        unresolved_predicate, unresolved_params = self._unresolved_effect_predicate(
            "we"
        )
        query = f"""
            SELECT 1
            FROM work_runs wr
            JOIN work_run_effects we ON we.receipt_id = wr.receipt_id
            WHERE wr.event_id = ? AND wr.action_id = ?
              AND {unresolved_predicate}
            LIMIT 1
        """
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                query,
                (
                    safe_event_id,
                    safe_action_id,
                    *unresolved_params,
                ),
            ).fetchone()
        return row is not None

    @staticmethod
    def _build_where(
        *,
        source: str = "",
        run_id: str = "",
        job_id: str = "",
        event_id: str = "",
        action_id: str = "",
        occurrence_id: str = "",
        status: str = "",
        phase: str = "",
        followup_status: str = "",
        statuses: Optional[Iterable[str]] = None,
        owner_kind: str = "",
        conversation_id: str = "",
        message_id: str = "",
        parent_job_id: str = "",
        parent_agent_id: str = "",
        started_after: Optional[float] = None,
        started_before: Optional[float] = None,
    ) -> tuple[str, List[Any]]:
        values = {
            "source": source,
            "run_id": run_id,
            "job_id": job_id,
            "event_id": event_id,
            "action_id": action_id,
            "occurrence_id": occurrence_id,
            "status": status,
            "phase": phase,
            "followup_status": followup_status,
            "owner_kind": owner_kind,
            "conversation_id": conversation_id,
            "message_id": message_id,
            "parent_job_id": parent_job_id,
            "parent_agent_id": parent_agent_id,
        }
        clauses: List[str] = []
        params: List[Any] = []
        for key, raw in values.items():
            cleaned = _bounded_text(raw, _TEXT_LIMITS.get(key, 512))
            if not cleaned:
                continue
            if key in {"source", "status", "phase", "followup_status"}:
                cleaned = cleaned.lower()
            clauses.append(f"{_FILTER_COLUMNS[key]} = ?")
            params.append(cleaned)
        normalized_statuses = sorted(
            {
                _bounded_text(item, 64).lower()
                for item in statuses or []
                if _bounded_text(item, 64)
            }
        )
        if normalized_statuses:
            placeholders = ", ".join("?" for _ in normalized_statuses)
            clauses.append(f"status IN ({placeholders})")
            params.extend(normalized_statuses)
        if started_after is not None:
            clauses.append("started_at >= ?")
            params.append(float(started_after))
        if started_before is not None:
            clauses.append("started_at <= ?")
            params.append(float(started_before))
        return (" WHERE " + " AND ".join(clauses) if clauses else "", params)

    def list_runs(
        self,
        *,
        source: str = "",
        run_id: str = "",
        job_id: str = "",
        event_id: str = "",
        action_id: str = "",
        occurrence_id: str = "",
        status: str = "",
        phase: str = "",
        followup_status: str = "",
        statuses: Optional[Iterable[str]] = None,
        owner_kind: str = "",
        conversation_id: str = "",
        message_id: str = "",
        parent_job_id: str = "",
        parent_agent_id: str = "",
        started_after: Optional[float] = None,
        started_before: Optional[float] = None,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> List[JsonDict]:
        """List newest receipts with indexed source, job, and lineage filters."""

        where, params = self._build_where(
            source=source,
            run_id=run_id,
            job_id=job_id,
            event_id=event_id,
            action_id=action_id,
            occurrence_id=occurrence_id,
            status=status,
            phase=phase,
            followup_status=followup_status,
            statuses=statuses,
            owner_kind=owner_kind,
            conversation_id=conversation_id,
            message_id=message_id,
            parent_job_id=parent_job_id,
            parent_agent_id=parent_agent_id,
            started_after=started_after,
            started_before=started_before,
        )
        safe_limit = max(1, min(int(limit), MAX_LIMIT))
        safe_offset = max(0, int(offset))
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                """
                SELECT wr.payload_json, wr.event_count,
                       (SELECT COUNT(*) FROM work_run_attempts wa
                        WHERE wa.receipt_id = wr.receipt_id) AS attempt_count,
                       (SELECT COUNT(*) FROM work_run_effects we
                        WHERE we.receipt_id = wr.receipt_id) AS effect_count
                FROM work_runs wr
                """
                + where
                + " ORDER BY sort_at DESC, receipt_id DESC LIMIT ? OFFSET ?",
                (*params, safe_limit, safe_offset),
            ).fetchall()
            receipts = self._decode_receipts_with_effects(connection, rows)
        return receipts

    def count_runs(self, **filters: Any) -> int:
        """Count receipts using the same filters accepted by ``list_runs``."""

        filters.pop("limit", None)
        filters.pop("offset", None)
        where, params = self._build_where(**filters)
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM work_runs" + where,
                params,
            ).fetchone()
        return int(row["count"] if row else 0)

    def list_events(
        self,
        receipt_id: str,
        *,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
        newest_first: bool = False,
    ) -> List[JsonDict]:
        """List compact append-only lifecycle transitions for one receipt."""

        safe_receipt_id = _lookup_identity(receipt_id)
        if safe_receipt_id is None:
            return []
        safe_limit = max(1, min(int(limit), MAX_LIMIT))
        safe_offset = max(0, int(offset))
        direction = "DESC" if newest_first else "ASC"
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                """
                SELECT sequence, receipt_id, recorded_at,
                       changed_fields_json, snapshot_json
                FROM work_run_events
                WHERE receipt_id = ?
                """
                + f" ORDER BY sequence {direction} LIMIT ? OFFSET ?",
                (safe_receipt_id, safe_limit, safe_offset),
            ).fetchall()
        events: List[JsonDict] = []
        for row in rows:
            try:
                snapshot = json.loads(str(row["snapshot_json"] or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                snapshot = {}
            try:
                changed_fields = json.loads(str(row["changed_fields_json"] or "[]"))
            except (TypeError, ValueError, json.JSONDecodeError):
                changed_fields = []
            event = snapshot if isinstance(snapshot, dict) else {}
            event.update(
                {
                    "sequence": int(row["sequence"]),
                    "receipt_id": str(row["receipt_id"]),
                    "recorded_at": float(row["recorded_at"]),
                    "changed_fields": (
                        changed_fields if isinstance(changed_fields, list) else []
                    ),
                }
            )
            events.append(event)
        return events

    def count_events(self, receipt_id: str) -> int:
        safe_receipt_id = _lookup_identity(receipt_id)
        if safe_receipt_id is None:
            return 0
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count FROM work_run_events
                WHERE receipt_id = ?
                """,
                (safe_receipt_id,),
            ).fetchone()
        return int(row["count"] if row else 0)

    def list_recovery_candidates(
        self,
        *,
        stale_before: Optional[float] = None,
        include_attention: bool = False,
        source: str = "",
        job_id: str = "",
        limit: int = DEFAULT_LIMIT,
    ) -> List[JsonDict]:
        """List unfinished receipts that a recovery pass should inspect.

        ``interrupted_unknown`` is excluded by default because blindly replaying
        a non-cooperative tool could duplicate an external side effect.  Callers
        can include attention-needed records for reconciliation without implying
        that they are safe to execute again.
        """

        states = {"active"}
        if include_attention:
            states.add("attention")
        persisted_candidate_states = ("active", "attention")
        state_placeholders = ", ".join("?" for _ in persisted_candidate_states)
        unresolved_predicate, unresolved_params = self._unresolved_effect_predicate(
            "candidate_effect"
        )
        clauses: List[str] = [
            "(wr.recovery_state IN ("
            + state_placeholders
            + ") OR EXISTS (SELECT 1 FROM work_run_effects candidate_effect "
            "WHERE candidate_effect.receipt_id = wr.receipt_id AND "
            + unresolved_predicate
            + "))"
        ]
        params: List[Any] = [*persisted_candidate_states, *unresolved_params]
        cleaned_source = self._normalize_source(source, default="")
        if cleaned_source:
            clauses.append("source = ?")
            params.append(cleaned_source)
        cleaned_job_id = _bounded_text(job_id, 512)
        if cleaned_job_id:
            clauses.append("job_id = ?")
            params.append(cleaned_job_id)
        if stale_before is not None:
            stale_at = float(stale_before)
            clauses.append(
                "((lease_expires_at IS NOT NULL AND lease_expires_at <= ?) "
                "OR (lease_expires_at IS NULL AND updated_at <= ?))"
            )
            params.extend((stale_at, stale_at))
        safe_limit = max(1, min(int(limit), MAX_LIMIT))
        page_size = min(MAX_LIMIT, max(100, safe_limit * 2))
        receipts: List[JsonDict] = []
        offset = 0
        with closing(self._connect()) as connection, connection:
            while len(receipts) < safe_limit:
                rows = connection.execute(
                    """
                    SELECT wr.payload_json, wr.event_count,
                           (SELECT COUNT(*) FROM work_run_attempts wa
                            WHERE wa.receipt_id = wr.receipt_id) AS attempt_count,
                           (SELECT COUNT(*) FROM work_run_effects we
                            WHERE we.receipt_id = wr.receipt_id) AS effect_count
                    FROM work_runs wr WHERE
                    """
                    + " AND ".join(clauses)
                    + " ORDER BY COALESCE(lease_expires_at, updated_at) ASC, "
                    "receipt_id LIMIT ? OFFSET ?",
                    (*params, page_size, offset),
                ).fetchall()
                receipts.extend(
                    receipt
                    for receipt in self._decode_receipts_with_effects(connection, rows)
                    if receipt.get("recovery_state") in states
                )
                if len(rows) < page_size:
                    break
                offset += len(rows)
        return receipts[:safe_limit]

    def recovery_state(self, receipt_id: str) -> Optional[str]:
        receipt = self.get_run(receipt_id)
        if receipt is None:
            return None
        return recovery_state_for_run(receipt)


def build_work_run_store(
    config: Optional[Mapping[str, Any]] = None,
    **kwargs: Any,
) -> WorkRunStore:
    return WorkRunStore(config, **kwargs)


__all__ = [
    "WorkRunStore",
    "WorkRunTransitionConflict",
    "build_work_run_store",
    "recovery_state_for_run",
    "recovery_state_for_status",
    "sanitize_work_attempt",
    "sanitize_work_effect",
    "sanitize_work_run",
    "work_run_store_path",
]
