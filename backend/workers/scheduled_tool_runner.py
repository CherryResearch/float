import asyncio
import hashlib
import json
import logging
import os
import re
import time
from typing import Any, Dict, Mapping, Optional
from uuid import uuid4

from app.agent_workflows import build_agent_provenance, build_handoff_artifact
from app.services.calendar_jobs import (
    coerce_epoch_seconds as _shared_coerce_epoch_seconds,
)
from app.services.calendar_jobs import due_occurrence_time
from app.services.calendar_jobs import event_start_time as _shared_event_start_time
from app.services.calendar_jobs import (
    external_control_revision,
    merge_runner_action_state,
    run_control_revision,
    runner_snapshot_control_revisions_match,
)
from app.services.scheduled_action_authorization import (
    AUTHORIZATION_APPROVED_ONCE,
    AUTHORIZATION_CONSUMED,
    AUTHORIZATION_REQUIRED,
    authorization_allows_dispatch,
    build_authorization_request,
    consume_authorization,
    expire_authorization_for_occurrence,
    mark_authorization_required,
)
from app.services.scheduled_action_cancellation import cancellation_requested
from app.services.work_run_projection import project_calendar_event
from app.services.work_run_store import WorkRunStore
from app.utils import calendar_store
from app.utils.security import generate_signature, sanitize_args
from fastapi import FastAPI

logger = logging.getLogger(__name__)


class _ProviderOutputCheckpointError(RuntimeError):
    """Generation finished, but its canonical output is not fully durable."""


class _WorkRunProjectionError(RuntimeError):
    """Calendar saved the claim, but its initial Activity receipt is unavailable."""


class _ScheduledDispatchBlocked(RuntimeError):
    """An exact durable checkpoint blocked a provider or tool dispatch."""

    def __init__(self, result: Dict[str, Any]):
        super().__init__(str(result.get("status") or "dispatch_blocked"))
        self.result = result


_EVENT_RUN_LOCKS: Dict[str, asyncio.Lock] = {}
_ACTIVE_EVENT_TASKS: Dict[str, asyncio.Task] = {}
_SAFE_CONVERSATION_NAME_RE = re.compile(r"[^a-zA-Z0-9_.-]+")
_PROMPT_CHECKPOINT_SCHEMA_VERSION = 1
_PROMPT_CHECKPOINT_KEYS = frozenset(
    {
        "schema_version",
        "checkpoint_id",
        "checkpoint_digest",
        "run_id",
        "receipt_id",
        "event_id",
        "action_id",
        "occurrence_at",
        "session_id",
        "chain_id",
        "output_message_id",
        "user_message_id",
        "prompt_digest",
    }
)
_ACTION_KIND_ALIASES = {
    "continue_prompt": "prompt",
    "followup_prompt": "prompt",
    "follow_up_prompt": "prompt",
}
_CONVERSATION_MODE_ALIASES = {
    "current_chat": "inline",
    "current_thread": "inline",
    "inline": "inline",
    "inline_chat": "inline",
    "same_chat": "inline",
    "same_thread": "inline",
    "new": "new_chat",
    "new_chat": "new_chat",
    "new_thread": "new_chat",
    "separate_chat": "new_chat",
    "separate_thread": "new_chat",
    "task_chat": "new_chat",
}


def _coerce_epoch_seconds(value: Any) -> Optional[float]:
    return _shared_coerce_epoch_seconds(value)


def _event_start_time(event: Dict[str, Any]) -> Optional[float]:
    return _shared_event_start_time(event)


def _normalize_conversation_mode(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        raw = str(value).strip().lower()
    except Exception:
        return None
    if not raw:
        return None
    key = raw.replace("-", "_").replace(" ", "_")
    return _CONVERSATION_MODE_ALIASES.get(key)


def _normalize_action(action: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    normalized = dict(action)
    try:
        raw_kind = (
            str(normalized.get("kind") or normalized.get("type") or "").strip().lower()
        )
    except Exception:
        raw_kind = ""
    kind = _ACTION_KIND_ALIASES.get(
        raw_kind.replace("-", "_").replace(" ", "_"),
        raw_kind.replace("-", "_").replace(" ", "_"),
    )
    if not kind:
        if _normalize_prompt(normalized.get("prompt")) and not normalized.get("name"):
            kind = "prompt"
        elif normalized.get("name"):
            kind = "tool"
    if kind not in {"tool", "prompt"}:
        return None
    normalized["kind"] = kind
    normalized.pop("type", None)
    if kind == "prompt" and "prompt" not in normalized:
        prompt = _normalize_prompt(normalized.get("text") or normalized.get("message"))
        if prompt:
            normalized["prompt"] = prompt
    mode = _normalize_conversation_mode(
        normalized.get("conversation_mode")
        or normalized.get("run_target")
        or normalized.get("target")
    )
    if not mode:
        mode = "inline" if normalized.get("session_id") else "new_chat"
    normalized["conversation_mode"] = mode
    return normalized


def _resolve_action_conversation(
    action: Dict[str, Any],
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    mode = _normalize_conversation_mode(action.get("conversation_mode"))
    if mode == "new_chat":
        return None, None, None
    session_id = action.get("session_id")
    message_id = action.get("message_id")
    chain_id = action.get("chain_id") or message_id or session_id
    return session_id, message_id, chain_id


def _origin_session_id(action: Dict[str, Any]) -> Optional[str]:
    return action.get("origin_session_id") or action.get("session_id")


def _origin_message_id(action: Dict[str, Any]) -> Optional[str]:
    return action.get("origin_message_id") or action.get("message_id")


def _preserve_action_origin(action: Dict[str, Any]) -> None:
    """Keep source-chat lineage before a new-chat run replaces mutable ids."""

    if _normalize_conversation_mode(action.get("conversation_mode")) != "new_chat":
        return
    origin_session_id = _origin_session_id(action)
    origin_message_id = _origin_message_id(action)
    if origin_session_id:
        action.setdefault("origin_session_id", origin_session_id)
    if origin_message_id:
        action.setdefault("origin_message_id", origin_message_id)


def _iter_actions(event: Dict[str, Any]) -> list[Dict[str, Any]]:
    actions = event.get("actions")
    if not isinstance(actions, list):
        return []
    out: list[Dict[str, Any]] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        normalized = _normalize_action(action)
        if normalized is None:
            continue
        action.clear()
        action.update(normalized)
        out.append(action)
    return out


def _fallback_tool_from_description(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Try to recover a scheduled tool payload from the legacy description field."""
    desc = event.get("description")
    if not isinstance(desc, str) or not desc.strip():
        return None
    try:
        parsed = json.loads(desc)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    tool = parsed.get("tool")
    args = parsed.get("args")
    if not isinstance(tool, str) or not tool.strip():
        return None
    if not isinstance(args, dict):
        args = {}
    return {"kind": "tool", "name": tool.strip(), "args": args, "status": "scheduled"}


def _normalize_prompt(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        try:
            value = str(value)
        except Exception:
            return None
    text = value.strip()
    return text or None


def _ensure_task_conversation(
    *,
    session_id: Optional[str],
    event: Dict[str, Any],
    event_id: str,
    parent_session_id: Optional[str] = None,
    parent_message_id: Optional[str] = None,
) -> str:
    """Ensure prompt-only scheduled work is readable as its own conversation thread."""
    if session_id:
        return session_id
    raw_title = event.get("title") or event.get("summary") or ""
    title = str(raw_title).strip() if raw_title is not None else ""
    safe_event_id = _SAFE_CONVERSATION_NAME_RE.sub("-", str(event_id)).strip("-")
    conv_name = (
        f"task-{safe_event_id}" if safe_event_id else f"task-{int(time.time() * 1000)}"
    )
    try:
        from app.utils import conversation_store

        if title:
            conversation_store.set_display_name(
                conv_name, title, auto_generated=True, manual=False
            )
        conversation_store.merge_metadata(
            conv_name,
            {
                "provenance": build_agent_provenance(
                    kind="subchat",
                    parent_session_id=parent_session_id,
                    parent_message_id=parent_message_id,
                    source_event_id=event_id,
                    branch_session_id=conv_name,
                    label=title or f"Task conversation {event_id}",
                ),
                "handoff": build_handoff_artifact(
                    summary=title or "Scheduled follow-up conversation."
                ),
            },
        )
    except Exception:
        pass
    return conv_name


def _mark_event_prompted(event: Dict[str, Any]) -> None:
    raw = event.get("status")
    try:
        status = str(raw or "").strip().lower()
    except Exception:
        status = ""
    if status in {"acknowledged", "skipped", "cancelled", "paused"}:
        return
    # A recurring job remains scheduled after one occurrence completes.
    event["status"] = "scheduled" if event.get("rrule") else "prompted"


def _action_has_run_for_occurrence(
    event: Dict[str, Any], action: Dict[str, Any], occurrence_time: Optional[float]
) -> bool:
    if occurrence_time is None:
        return False
    if event.get("rrule"):
        last_occurrence = _coerce_epoch_seconds(action.get("last_occurrence_at"))
        return bool(last_occurrence and last_occurrence >= occurrence_time - 0.5)
    return _coerce_epoch_seconds(action.get("executed_at")) is not None


def _action_requires_reconciliation(
    event: Dict[str, Any], action: Dict[str, Any], action_id: str
) -> bool:
    uncertain_statuses = {
        "interrupted_unknown",
        "orphaned",
        "reconcile_required",
        "unknown",
    }
    if (
        bool(action.get("reconcile_required"))
        or str(action.get("effect_certainty") or "").lower() == "unknown"
        or str(action.get("status") or "").lower() in uncertain_statuses
    ):
        return True
    history = event.get("run_history")
    history = history if isinstance(history, list) else []
    for receipt in reversed(history):
        if not isinstance(receipt, dict):
            continue
        if str(receipt.get("action_id") or "") != str(action_id):
            continue
        return bool(
            receipt.get("reconcile_required")
            or str(receipt.get("effect_certainty") or "").lower() == "unknown"
            or str(receipt.get("state_delta_certainty") or "").lower() == "unknown"
            or str(receipt.get("status") or "").lower() in uncertain_statuses
        )
    return False


def _latest_action_status(event_id: str, action_id: str) -> str:
    """Read the persisted action status before interpreting an active effect."""

    latest = calendar_store.load_event(event_id)
    if not isinstance(latest, dict):
        return ""
    for index, candidate in enumerate(_iter_actions(latest)):
        candidate_id = str(
            candidate.get("request_id")
            or candidate.get("id")
            or f"{event_id}:tool:{index}"
        )
        if candidate_id == action_id:
            return str(candidate.get("status") or "").strip().lower()
    return ""


async def _ledger_requires_effect_reconciliation(
    app: FastAPI, *, event_id: str, action_id: str
) -> bool:
    """Consult the authoritative effect journal before any fresh tool claim."""

    store = _work_run_store(app)
    return bool(
        await asyncio.to_thread(
            store.has_unresolved_effects,
            event_id=event_id,
            action_id=action_id,
        )
    )


def _event_runtime_limit_seconds(event: Dict[str, Any]) -> float:
    policy = event.get("background_job")
    policy = policy if isinstance(policy, dict) else {}
    patience = policy.get("patience")
    patience = patience if isinstance(patience, dict) else {}
    try:
        runtime = float(patience.get("max_runtime_seconds") or 900)
    except (TypeError, ValueError):
        runtime = 900.0
    return max(30.0, min(runtime, 86400.0))


def _provider_retry_limit(event: Dict[str, Any]) -> int:
    """Return provider retries without conflating them with workflow attempts."""

    policy = event.get("background_job")
    policy = policy if isinstance(policy, dict) else {}
    patience = policy.get("patience")
    patience = patience if isinstance(patience, dict) else {}
    try:
        retries = int(patience.get("max_provider_retries", 2))
    except (TypeError, ValueError, OverflowError):
        retries = 2
    return max(0, min(retries, 10))


def _canonical_digest(value: Any) -> str:
    """Hash structured state without copying its raw contents into the ledger."""

    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    except Exception:
        encoded = repr(type(value)).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _stable_composite_id(kind: str, *parts: Any) -> str:
    """Build an unambiguous, bounded ID from a canonical ordered tuple."""

    encoded = json.dumps(
        [str(kind), *(str(part) for part in parts)],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return f"{kind}:sha256:{digest}"


def _receipt_id(event_id: str, action_id: str, action: Dict[str, Any]) -> str:
    stored_receipt_id = str(action.get("work_run_receipt_id") or "").strip()
    if stored_receipt_id:
        return stored_receipt_id
    run_id = str(action.get("run_id") or "").strip()
    if run_id:
        return _stable_composite_id("receipt", event_id, action_id, run_id)
    started_at = _coerce_epoch_seconds(action.get("started_at")) or 0.0
    return _stable_composite_id("receipt", event_id, action_id, int(started_at * 1000))


def _prompt_receipt_id(
    event: Mapping[str, Any], *, event_id: str, action_id: str, run_id: str
) -> str:
    history = event.get("run_history")
    history = history if isinstance(history, list) else []
    for item in reversed(history):
        if not isinstance(item, Mapping):
            continue
        if bool(
            str(item.get("run_id") or "").strip() == run_id
            and str(item.get("action_id") or "") == action_id
            and str(item.get("id") or "").strip()
        ):
            return str(item["id"])
    return _stable_composite_id("receipt", event_id, action_id, run_id)


def _build_prompt_checkpoint(
    event: Mapping[str, Any],
    *,
    event_id: str,
    action_id: str,
    run_id: str,
    occurrence_time: float,
    session_id: str,
    chain_id: str,
    prompt: str,
) -> Dict[str, Any]:
    """Build a bounded, content-free prompt restart checkpoint."""

    occurrence = _coerce_epoch_seconds(occurrence_time)
    prompt_text = _normalize_prompt(prompt)
    identifiers = {
        "event_id": str(event_id or "").strip(),
        "action_id": str(action_id or "").strip(),
        "run_id": str(run_id or "").strip(),
        "session_id": str(session_id or "").strip(),
        "chain_id": str(chain_id or "").strip(),
    }
    if occurrence is None or prompt_text is None:
        raise ValueError("prompt checkpoint inputs are incomplete")
    if any(not value or len(value) > 512 for value in identifiers.values()):
        raise ValueError("prompt checkpoint identifiers are invalid")
    receipt_id = _prompt_receipt_id(
        event,
        event_id=identifiers["event_id"],
        action_id=identifiers["action_id"],
        run_id=identifiers["run_id"],
    )
    output_message_id = _stable_composite_id(
        "scheduled-message",
        identifiers["event_id"],
        identifiers["action_id"],
        identifiers["run_id"],
        "prompt",
    )
    checkpoint_id = _stable_composite_id("checkpoint", receipt_id, "prompt-checkpoint")
    payload: Dict[str, Any] = {
        "schema_version": _PROMPT_CHECKPOINT_SCHEMA_VERSION,
        "checkpoint_id": checkpoint_id,
        "run_id": identifiers["run_id"],
        "receipt_id": receipt_id,
        "event_id": identifiers["event_id"],
        "action_id": identifiers["action_id"],
        "occurrence_at": occurrence,
        "session_id": identifiers["session_id"],
        "chain_id": identifiers["chain_id"],
        "output_message_id": output_message_id,
        "user_message_id": f"{output_message_id}:user",
        "prompt_digest": _canonical_digest(prompt_text),
    }
    payload["checkpoint_digest"] = _canonical_digest(payload)
    return payload


def _prompt_checkpoint_matches(
    event: Mapping[str, Any],
    action: Mapping[str, Any],
    *,
    event_id: str,
    action_id: str,
    occurrence_time: float,
    prompt: str,
) -> bool:
    checkpoint = action.get("prompt_checkpoint")
    if (
        not isinstance(checkpoint, Mapping)
        or set(checkpoint) != _PROMPT_CHECKPOINT_KEYS
    ):
        return False
    try:
        expected = _build_prompt_checkpoint(
            event,
            event_id=event_id,
            action_id=action_id,
            run_id=str(action.get("run_id") or ""),
            occurrence_time=occurrence_time,
            session_id=str(action.get("session_id") or ""),
            chain_id=str(action.get("chain_id") or action.get("session_id") or ""),
            prompt=prompt,
        )
    except (TypeError, ValueError):
        return False
    return dict(checkpoint) == expected


def _work_run_store(app: FastAPI) -> WorkRunStore:
    store = getattr(app.state, "work_run_store", None)
    if store is None:
        raise RuntimeError("work-run ledger not available")
    return store


def _safe_error_code(exc: BaseException) -> str:
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    try:
        if status is not None:
            return f"http_{int(status)}"
    except (TypeError, ValueError, OverflowError):
        pass
    name = re.sub(r"[^a-z0-9]+", "_", type(exc).__name__.lower()).strip("_")
    return name[:128] or "provider_error"


def _provider_error_category(exc: BaseException) -> tuple[str, str, bool]:
    """Classify only unambiguous transport/provider failures as retryable."""

    code = _safe_error_code(exc)
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    try:
        status_value = int(status) if status is not None else None
    except (TypeError, ValueError, OverflowError):
        status_value = None
    error_type = type(exc).__name__.lower()
    if (
        isinstance(exc, (TimeoutError, asyncio.TimeoutError))
        or "timeout" in error_type
        or status_value == 408
    ):
        return "provider_timeout", code, True
    if status_value == 429:
        return "provider_rate_limited", code, True
    if status_value in {425, 502, 503, 504} or (
        status_value is not None and 500 <= status_value <= 599
    ):
        return "provider_unavailable", code, True
    if isinstance(exc, ConnectionError) or error_type in {
        "connectionerror",
        "connecterror",
    }:
        return "provider_transport_error", code, True
    return "provider_error", code, False


def _provider_response_error(
    response: Any,
) -> Optional[tuple[str, str, bool]]:
    """Classify provider error returns using metadata only, never response bodies."""

    if not isinstance(response, dict):
        return None
    metadata = response.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    status = str(response.get("status") or "").strip().lower()
    raw_category = str(metadata.get("category") or "").strip().lower()
    has_error = bool(
        response.get("error")
        or metadata.get("error")
        or metadata.get("provider_error")
        or metadata.get("provider_error_text")
        or status in {"error", "failed", "failure"}
    )
    if not has_error:
        return None
    status_code = metadata.get("status_code")
    try:
        status_value = int(status_code) if status_code is not None else None
    except (TypeError, ValueError, OverflowError):
        status_value = None
    category_map = {
        "timeout": "provider_timeout",
        "connection_error": "provider_transport_error",
        "rate_limited": "provider_rate_limited",
        "server_error": "provider_unavailable",
    }
    known_categories = {
        "connection_error",
        "context_token_limit",
        "endpoint_not_found",
        "http_error",
        "output_token_limit",
        "rate_limited",
        "reasoning_control_unsupported",
        "server_error",
        "timeout",
        "unauthorized",
    }
    category = category_map.get(raw_category, "provider_error")
    retryable = raw_category in category_map
    if status_value == 408:
        category, retryable = "provider_timeout", True
    elif status_value == 429:
        category, retryable = "provider_rate_limited", True
    elif status_value is not None and 500 <= status_value <= 599:
        category, retryable = "provider_unavailable", True
    code = (
        f"http_{status_value}"
        if status_value is not None
        else (
            raw_category
            if raw_category in known_categories
            else "provider_error_return"
        )
    )
    return category, code, retryable


def _attempt_state_certainty(action: Dict[str, Any]) -> str:
    if str(action.get("effect_certainty") or "").lower() == "unknown":
        return "unknown"
    return "no_change_since_checkpoint"


def _effect_watermark(action: Dict[str, Any]) -> str:
    return _canonical_digest(
        {
            "effect_id": action.get("effect_id"),
            "status": action.get("effect_status"),
            "certainty": action.get("effect_certainty"),
            "tool_executed_at": action.get("executed_at"),
        }
    )


def _effect_recovery_lines(action: Dict[str, Any], *, tool_name: str) -> list[str]:
    certainty = str(action.get("effect_certainty") or "").strip().lower()
    effect_id = str(action.get("effect_id") or "").strip()
    if certainty == "unknown":
        return [
            f"- Effects: UNKNOWN for {effect_id or tool_name}; do not issue writes.",
            "- Reconciliation is required before any further external mutation.",
        ]
    if certainty == "reported_success":
        return [
            "- Effects: the tool reported success for "
            f"{effect_id or tool_name}; this is not independent verification.",
            "- Do not repeat the tool; reconcile first if stronger certainty is needed.",
        ]
    return [
        f"- Tool outcome: {tool_name} is already durable; do not repeat the tool.",
        "- No mutating effect was journaled for this tool.",
    ]


def _provider_recovery_envelope(
    *,
    attempt_number: int,
    max_attempts: int,
    prior_error_category: str,
    prior_error_code: str,
    action: Dict[str, Any],
    tool_name: str,
    output_message_id: str,
) -> str:
    prior_line = (
        "- Prior generation completed, but canonical output persistence is "
        f"missing or unknown ({prior_error_code})."
        if prior_error_category == "provider_output_checkpoint_missing"
        else f"- Prior provider error: {prior_error_category} ({prior_error_code})."
    )
    lines = [
        "Scheduled provider recovery:",
        f"- Retry {attempt_number} of {max_attempts} provider attempt(s).",
        prior_line,
        *_effect_recovery_lines(action, tool_name=tool_name),
        "- Durable local state: the scheduled user entry and pending assistant "
        f"entry {output_message_id} already exist and will be updated in place.",
        "- No other durable state changes were recorded beyond the listed "
        "tool/effect and conversation checkpoint.",
        "- Resume the provider follow-up from the durable checkpoint; "
        "do not invoke the tool.",
    ]
    return "\n".join(lines)


def _prompt_provider_recovery_envelope(
    *,
    attempt_number: int,
    max_attempts: int,
    prior_error_category: str,
    prior_error_code: str,
    output_message_id: str,
) -> str:
    prior_line = (
        "- Prior generation completed, but canonical output persistence is "
        f"missing or unknown ({prior_error_code})."
        if prior_error_category == "provider_output_checkpoint_missing"
        else f"- Prior provider error: {prior_error_category} ({prior_error_code})."
    )
    return "\n".join(
        [
            "Scheduled provider recovery:",
            f"- Retry {attempt_number} of {max_attempts} provider attempt(s).",
            prior_line,
            "- This prompt-only step dispatched no tool or external effect.",
            "- Durable local state: the scheduled user entry and pending assistant "
            f"entry {output_message_id} already exist and will be updated in place.",
            "- No other durable state changes were recorded beyond those "
            "conversation placeholders.",
            "- Resume generation from the durable prompt checkpoint.",
        ]
    )


async def _close_interrupted_provider_attempt(
    store: WorkRunStore,
    prior_attempt: Optional[Dict[str, Any]],
    *,
    effect_watermark_digest: str,
    state_delta_certainty: str,
) -> Optional[Dict[str, Any]]:
    """Close a stale provider attempt before a replacement attempt is opened."""

    if not isinstance(prior_attempt, dict):
        return prior_attempt
    if str(prior_attempt.get("status") or "").strip().lower() != "running":
        return prior_attempt
    interrupted_at = time.time()
    interrupted = {
        **prior_attempt,
        "status": "interrupted_unknown",
        "retryable": True,
        "retry_reason_code": "worker_restart",
        "error_category": "provider_interrupted",
        "error_code": "worker_restart",
        "state_delta_certainty": state_delta_certainty,
        "effect_watermark_digest": effect_watermark_digest,
        "finished_at": interrupted_at,
        "retry_scheduled_at": interrupted_at,
    }
    await asyncio.to_thread(store.record_attempt, interrupted)
    return interrupted


async def _close_provider_attempt_without_dispatch(
    store: WorkRunStore,
    attempt: Dict[str, Any],
    *,
    cancelled: bool,
    state_delta_certainty: str,
) -> Dict[str, Any]:
    """Close an opened provider-attempt receipt before its provider call starts."""

    finished_at = time.time()
    closed = {
        **attempt,
        "status": "cancelled" if cancelled else "not_dispatched",
        "retryable": False,
        "error_category": (
            "provider_cancelled" if cancelled else "provider_dispatch_blocked"
        ),
        "error_code": "user_cancelled" if cancelled else "claim_lost",
        "state_delta_certainty": state_delta_certainty,
        "finished_at": finished_at,
    }
    await asyncio.to_thread(store.record_attempt, closed)
    return closed


async def _close_attempt_from_canonical_output(
    store: WorkRunStore,
    receipt_id: str,
    *,
    effect_watermark_digest: str,
    state_delta_certainty: str,
) -> None:
    """Repair a crash window where output saved before attempt completion."""

    attempts = await asyncio.to_thread(store.list_attempts, receipt_id, limit=500)
    if not attempts:
        return
    latest = attempts[-1]
    status = str(latest.get("status") or "").strip().lower()
    if status == "complete":
        return
    if status not in {
        "interrupted_unknown",
        "output_checkpoint_missing",
        "running",
    }:
        return
    recovered = {
        **latest,
        "status": "complete",
        "retryable": False,
        "retry_reason_code": "canonical_output_recovered",
        "checkpoint_status": "canonical_output_durable",
        "effect_watermark_digest": effect_watermark_digest,
        "state_delta_certainty": state_delta_certainty,
        "finished_at": time.time(),
    }
    await asyncio.to_thread(store.record_attempt, recovered)


async def _hydrate_effect_state_from_ledger(
    store: WorkRunStore,
    receipt_id: str,
    action: Dict[str, Any],
) -> None:
    """Restore effect certainty that older Calendar merge allowlists may omit."""

    effects = await asyncio.to_thread(store.list_effects, receipt_id, limit=500)
    if not effects:
        return
    latest = effects[-1]
    action["effect_id"] = latest.get("id")
    action["effect_status"] = latest.get("status")
    action["effect_certainty"] = latest.get("certainty")
    action["reconcile_required"] = bool(latest.get("reconcile_required"))


def _running_lease_seconds(event: Dict[str, Any]) -> float:
    return _event_runtime_limit_seconds(event) + 60.0


def _running_action_is_stale(
    event: Dict[str, Any], action: Dict[str, Any], *, now: float
) -> bool:
    status = str(action.get("status") or "").strip().lower()
    if status not in {"running", "followup_running"}:
        return False
    started_field = (
        "followup_started_at" if status == "followup_running" else "started_at"
    )
    started_at = _coerce_epoch_seconds(action.get(started_field))
    if started_at is None:
        return True
    return now - started_at > _running_lease_seconds(event)


def _event_has_due_action(event: Dict[str, Any], *, now: float) -> bool:
    """Return whether at least one stored action needs the latest occurrence."""

    actions = _iter_actions(event)
    for action in actions:
        status = str(action.get("status") or "").strip().lower()
        if status in {"followup_pending", "prompt_resume_pending"}:
            return True
        if status in {"running", "followup_running"} and _running_action_is_stale(
            event, action, now=now
        ):
            return True
    occurrence_time = due_occurrence_time(event, now=now)
    if occurrence_time is None:
        return False
    event_id = str(event.get("id") or "").strip()
    for index, action in enumerate(actions):
        status = str(action.get("status") or "").strip().lower()
        if status == "reconcile_required":
            continue
        if status in {"running", "followup_running"}:
            continue
        if status == AUTHORIZATION_REQUIRED:
            authorization = action.get("authorization")
            action_id = str(
                action.get("request_id")
                or action.get("id")
                or f"{event_id}:tool:{index}"
            )
            try:
                current = build_authorization_request(
                    event_id,
                    event,
                    action_id,
                    action,
                    occurrence_time,
                )
            except (TypeError, ValueError):
                return True
            if isinstance(authorization, Mapping) and bool(
                authorization.get("id") == current.get("id")
                and authorization.get("request_digest") == current.get("request_digest")
                and str(authorization.get("status") or "").strip().lower()
                == AUTHORIZATION_REQUIRED
            ):
                # The exact occurrence is already waiting for a decision or a
                # wider configured permission ceiling. A changed occurrence,
                # policy, action, or scope digest remains due for re-evaluation.
                continue
        if not _action_has_run_for_occurrence(event, action, occurrence_time):
            return True
    return False


def _mark_action_occurrence(
    action: Dict[str, Any], occurrence_time: Optional[float]
) -> None:
    if occurrence_time is not None:
        action["last_occurrence_at"] = occurrence_time
    action.pop("running_occurrence_at", None)


def _run_summary(
    result: Dict[str, Any],
    *,
    action_name: Any = None,
    limit: int = 500,
) -> str:
    """Describe receipt output shape without retaining output or error content."""

    raw_label = str(action_name or "").strip()
    label = (
        raw_label
        if re.fullmatch(r"[a-zA-Z0-9_.-]{1,80}", raw_label)
        else "Scheduled action"
    )
    status = str(result.get("status") or "completed").strip().lower()
    status = status if re.fullmatch(r"[a-z0-9_.-]{1,64}", status) else "unknown"
    if result.get("error") is not None:
        return f"{label} ended with status {status}."
    raw = (
        result.get("error") if result.get("error") is not None else result.get("result")
    )
    if raw is None:
        return f"{label} ended with status {status}."
    if isinstance(raw, str):
        text = f"{label} returned text output ({len(raw)} characters)."
    elif isinstance(raw, dict):
        text = f"{label} returned structured output ({len(raw)} fields)."
    elif isinstance(raw, (list, tuple)):
        text = f"{label} returned {len(raw)} item(s)."
    else:
        text = f"{label} returned {type(raw).__name__} output."
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else f"{compact[: limit - 3]}..."


def _authorization_receipt_snapshot(value: Any) -> Dict[str, Any]:
    """Keep authorization evidence content-free in Calendar and Activity."""

    if not isinstance(value, Mapping):
        return {}
    snapshot: Dict[str, Any] = {}
    for key in (
        "schema_version",
        "id",
        "status",
        "occurrence_at",
        "request_digest",
        "action_definition_digest",
        "policy_id",
        "policy_digest",
        "required_scopes",
        "configured_scopes",
        "missing_scopes",
        "approval_required",
        "can_approve",
        "requested_at",
        "decision_id",
        "decided_at",
        "consumed_at",
        "invalidated_at",
        "invalidation_reason",
        "actor_kind",
    ):
        if value.get(key) is not None:
            item = value[key]
            snapshot[key] = list(item) if isinstance(item, (list, tuple)) else item
    return snapshot


def _append_run_record(
    event: Dict[str, Any],
    *,
    event_id: str,
    action: Dict[str, Any],
    action_id: str,
    occurrence_time: Optional[float],
    result: Dict[str, Any],
) -> Dict[str, Any]:
    """Append or replace one compact event-owned execution record."""

    result_status = str(result.get("status") or action.get("status") or "complete")
    unfinished = result_status in {
        AUTHORIZATION_REQUIRED,
        "authorization_approved",
        "cancel_requested",
        "running",
        "prompt_resume_pending",
        "followup_pending",
        "followup_running",
    }
    leased = result_status in {"running", "followup_pending", "followup_running"}
    finished_at = None
    if not unfinished:
        finished_at = (
            _coerce_epoch_seconds(action.get("followup_completed_at"))
            or _coerce_epoch_seconds(action.get("executed_at"))
            or time.time()
        )
    started_at = _coerce_epoch_seconds(action.get("started_at")) or (
        finished_at or time.time()
    )
    policy = event.get("background_job")
    policy = policy if isinstance(policy, dict) else {}
    ownership = policy.get("ownership")
    ownership = dict(ownership) if isinstance(ownership, dict) else {}
    ownership.setdefault("calendar_event_id", event_id)
    ownership.setdefault("owner_kind", "calendar_event")
    origin_session_id = _origin_session_id(action)
    origin_message_id = _origin_message_id(action)
    if origin_session_id:
        ownership.setdefault("conversation_id", origin_session_id)
    if origin_message_id:
        ownership.setdefault("message_id", origin_message_id)

    run_id = str(action.get("run_id") or "").strip()
    run_key_time = occurrence_time if occurrence_time is not None else started_at
    record_key = run_id or str(int(run_key_time * 1000))
    record_id = _stable_composite_id("receipt", event_id, action_id, record_key)
    history = event.get("run_history")
    history = list(history) if isinstance(history, list) else []
    previous = next(
        (
            item
            for item in history
            if isinstance(item, dict)
            and (
                item.get("id") == record_id
                or (
                    run_id
                    and str(item.get("run_id") or "") == run_id
                    and str(item.get("action_id") or "") == str(action_id)
                )
            )
        ),
        None,
    )
    if isinstance(previous, dict):
        record_id = str(previous.get("id") or record_id)
        started_at = _coerce_epoch_seconds(previous.get("started_at")) or started_at
    action["work_run_receipt_id"] = record_id
    try:
        recovery_count = max(0, int(action.get("recovery_count") or 0))
    except (TypeError, ValueError):
        recovery_count = 0
    lease_started_at = (
        _coerce_epoch_seconds(action.get("followup_started_at"))
        if result_status == "followup_running"
        else started_at
    )
    lease_expires_at = (
        lease_started_at + _running_lease_seconds(event)
        if leased and lease_started_at is not None
        else None
    )
    authorization = _authorization_receipt_snapshot(
        result.get("authorization") or action.get("authorization")
    )
    record = {
        "id": record_id,
        "run_id": run_id or None,
        "job_id": event_id,
        "event_id": event_id,
        "event_title": event.get("title") or event_id,
        "action_id": action_id,
        "action_kind": action.get("kind") or action.get("type") or "tool",
        "action_name": action.get("name") or "prompt",
        "occurrence_at": occurrence_time,
        "occurrence_id": (
            f"{event_id}:{int(occurrence_time)}"
            if occurrence_time is not None
            else None
        ),
        "started_at": started_at,
        "finished_at": finished_at,
        "status": result_status,
        "phase": (
            result.get("phase")
            or (
                "authorization"
                if result_status.startswith("authorization_")
                else "awaiting_followup"
                if result_status == "followup_pending"
                else (
                    "followup"
                    if action.get("followup_status") in {"running", "complete", "error"}
                    else "complete"
                )
            )
        ),
        "summary": _run_summary(result, action_name=action.get("name") or "prompt"),
        "ownership": ownership,
        "patience": (
            policy.get("patience") if isinstance(policy.get("patience"), dict) else {}
        ),
        "execution": (
            policy.get("execution") if isinstance(policy.get("execution"), dict) else {}
        ),
        "run_conversation_id": action.get("session_id"),
        "followup_status": action.get("followup_status"),
        "recovery_count": recovery_count,
        "recovered_at": _coerce_epoch_seconds(action.get("recovered_at")),
        "recovery_reason": result.get("recovery"),
        "recovery_reason_code": ("startup_resume" if result.get("recovery") else None),
        "tool_invoked": (
            result.get("tool_invoked")
            if isinstance(result.get("tool_invoked"), bool)
            else None
        ),
        "effect_status": action.get("effect_status"),
        "effect_certainty": action.get("effect_certainty"),
        "state_delta_certainty": result.get("state_delta_certainty"),
        "reconcile_required": bool(
            result.get("reconcile_required")
            or action.get("effect_certainty") == "unknown"
        ),
        "lease_expires_at": lease_expires_at,
    }
    if authorization:
        record["authorization"] = authorization
    history = [
        item
        for item in history
        if not isinstance(item, dict) or item.get("id") != record["id"]
    ]
    history.append(record)
    event["run_history"] = history[-200:]
    return record


def _record_interrupted_action(
    event: Dict[str, Any],
    *,
    event_id: str,
    action: Dict[str, Any],
    action_id: str,
    occurrence_time: Optional[float],
    detail: str,
    status: str = "error",
) -> Dict[str, Any]:
    action["status"] = status
    action["error"] = detail
    action["executed_at"] = time.time()
    action["interrupted_at"] = action["executed_at"]
    _mark_action_occurrence(action, occurrence_time)
    _mark_event_prompted(event)
    result = {
        "status": status,
        "event_id": event_id,
        "action_id": action_id,
        "occurrence_at": occurrence_time,
        "error": detail,
    }
    _append_run_record(
        event,
        event_id=event_id,
        action=action,
        action_id=action_id,
        occurrence_time=occurrence_time,
        result=result,
    )
    return result


def _recover_stale_running_actions(
    event: Dict[str, Any],
    *,
    event_id: str,
    occurrence_time: Optional[float],
    now: float,
) -> list[Dict[str, Any]]:
    recovered: list[Dict[str, Any]] = []
    accepted = False

    def recover_latest(latest: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        nonlocal accepted
        latest_status = str(latest.get("status") or "").strip().lower()
        if latest_status in {"acknowledged", "skipped", "cancelled", "paused"}:
            return None
        for idx, action in enumerate(_iter_actions(latest)):
            if not _running_action_is_stale(latest, action, now=now):
                continue
            action_id = str(
                action.get("request_id") or action.get("id") or f"{event_id}:tool:{idx}"
            )
            claimed_occurrence = _coerce_epoch_seconds(
                action.get("running_occurrence_at")
            )
            recovered_occurrence = (
                claimed_occurrence
                if claimed_occurrence is not None
                else occurrence_time
            )
            action_status = str(action.get("status") or "").strip().lower()
            if (
                action_status == "running"
                and str(action.get("kind") or action.get("type") or "").strip().lower()
                == "prompt"
            ):
                prompt = _normalize_prompt(action.get("prompt"))
                if (
                    recovered_occurrence is not None
                    and prompt is not None
                    and _prompt_checkpoint_matches(
                        latest,
                        action,
                        event_id=event_id,
                        action_id=action_id,
                        occurrence_time=recovered_occurrence,
                        prompt=prompt,
                    )
                ):
                    action["status"] = "prompt_resume_pending"
                    result = {
                        "status": "prompt_resume_pending",
                        "phase": "prompt_resume",
                        "event_id": event_id,
                        "action_id": action_id,
                        "occurrence_at": recovered_occurrence,
                        "tool_invoked": False,
                        "state_delta_certainty": "confirmed_no_change",
                        "recovery": (
                            "Previous prompt provider attempt stopped unexpectedly "
                            "and was queued to resume from its durable checkpoint."
                        ),
                    }
                    _append_run_record(
                        latest,
                        event_id=event_id,
                        action=action,
                        action_id=action_id,
                        occurrence_time=recovered_occurrence,
                        result=result,
                    )
                    recovered.append(result)
                else:
                    recovered.append(
                        _record_interrupted_action(
                            latest,
                            event_id=event_id,
                            action=action,
                            action_id=action_id,
                            occurrence_time=recovered_occurrence,
                            detail=(
                                "Scheduled prompt checkpoint changed or is invalid; "
                                "provider dispatch was blocked."
                            ),
                            status="reconcile_required",
                        )
                    )
                continue
            if action_status == "followup_running":
                # The tool result is already durable. A stopped follow-up can
                # be reclaimed without repeating that tool side effect.
                action["status"] = "followup_pending"
                action["followup_status"] = "pending"
                action.pop("followup_started_at", None)
                result = {
                    "status": "followup_pending",
                    "phase": "awaiting_followup",
                    "event_id": event_id,
                    "action_id": action_id,
                    "occurrence_at": recovered_occurrence,
                    "result": action.get("result"),
                    "tool_invoked": True,
                    "recovery": (
                        "Previous follow-up stopped unexpectedly and was queued "
                        "to resume."
                    ),
                }
                _append_run_record(
                    latest,
                    event_id=event_id,
                    action=action,
                    action_id=action_id,
                    occurrence_time=recovered_occurrence,
                    result=result,
                )
                recovered.append(result)
                continue
            recovered.append(
                _record_interrupted_action(
                    latest,
                    event_id=event_id,
                    action=action,
                    action_id=action_id,
                    occurrence_time=recovered_occurrence,
                    detail=(
                        "Previous scheduled run exceeded its lease or stopped "
                        "unexpectedly."
                    ),
                    status="interrupted_unknown",
                )
            )
        if not recovered:
            return None
        accepted = True
        return latest

    stored = calendar_store.update_event(event_id, recover_latest)
    if stored:
        event.clear()
        event.update(stored)
    if not accepted:
        recovered.clear()
    return recovered


def _claim_action_run(
    event_id: str,
    event: Dict[str, Any],
    action: Dict[str, Any],
    *,
    action_id: str,
    occurrence_time: Optional[float],
    force: bool,
) -> Optional[str]:
    """Atomically claim one action before any prompt or tool side effect.

    The in-process event lock keeps one worker tidy, while this storage claim
    prevents two worker processes from executing the same occurrence.
    """

    run_id = f"run-{uuid4().hex}"
    started_at = time.time()
    accepted = False
    claimed_action: Dict[str, Any] = {}

    def claim_latest(latest: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        nonlocal accepted, claimed_action
        latest_status = str(latest.get("status") or "").strip().lower()
        if latest_status in {"acknowledged", "skipped", "cancelled", "paused"}:
            return None
        latest_actions = latest.get("actions")
        if not isinstance(latest_actions, list):
            return None
        for index, current in enumerate(latest_actions):
            if not isinstance(current, dict):
                continue
            current_id = str(
                current.get("request_id")
                or current.get("id")
                or f"{event_id}:tool:{index}"
            )
            if current_id != action_id and str(
                current.get("status") or ""
            ).strip().lower() in {"running", "followup_running"}:
                return None
        updated_actions = list(latest_actions)
        for index, current in enumerate(latest_actions):
            if not isinstance(current, dict):
                continue
            current_id = str(
                current.get("request_id")
                or current.get("id")
                or f"{event_id}:tool:{index}"
            )
            if current_id != action_id:
                continue
            current_action = dict(current)
            if str(current_action.get("status") or "").strip().lower() == "running":
                return None
            if not force and _action_has_run_for_occurrence(
                latest, current_action, occurrence_time
            ):
                return None
            current_action["status"] = "running"
            current_action["started_at"] = started_at
            current_action["run_id"] = run_id
            current_action["run_control_revision"] = external_control_revision(
                current_action
            )
            current_action["running_occurrence_at"] = occurrence_time
            for stale_key in (
                "effect_certainty",
                "effect_id",
                "effect_ids",
                "effect_status",
                "followup_completed_at",
                "followup_error",
                "followup_message_id",
                "followup_started_at",
                "followup_status",
                "prompt_checkpoint",
                "reconciliation_outcome",
                "reconciliation_summary",
                "work_run_receipt_id",
            ):
                current_action.pop(stale_key, None)
            updated_actions[index] = current_action
            latest["actions"] = updated_actions
            latest["status"] = "running"
            claimed_action = current_action
            accepted = True
            return latest
        return None

    stored = calendar_store.update_event(event_id, claim_latest)
    if not stored or not accepted:
        return None
    action.clear()
    action.update(claimed_action)
    event["status"] = "running"
    if isinstance(stored.get("run_history"), list):
        event["run_history"] = list(stored["run_history"])
    return run_id


def _persist_prompt_checkpoint_claim(
    event_id: str,
    event: Dict[str, Any],
    action: Dict[str, Any],
    *,
    action_id: str,
    occurrence_time: float,
    prompt: str,
    session_id: str,
    chain_id: str,
) -> Optional[Dict[str, Any]]:
    """Persist exact prompt resume metadata while the run token owns the claim."""

    expected_run_id = str(action.get("run_id") or "").strip()
    expected_run_control_revision = run_control_revision(action)
    expected_prompt_digest = _canonical_digest(prompt)
    if not expected_run_id or expected_run_control_revision is None:
        return None
    accepted = False
    claimed_action: Dict[str, Any] = {}
    checkpoint: Dict[str, Any] = {}
    stored_action_index: Optional[int] = None

    def checkpoint_latest(latest: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        nonlocal accepted, claimed_action, checkpoint, stored_action_index
        latest_status = str(latest.get("status") or "").strip().lower()
        if latest_status in {"acknowledged", "skipped", "cancelled", "paused"}:
            return None
        latest_actions = latest.get("actions")
        if not isinstance(latest_actions, list):
            return None
        updated_actions = list(latest_actions)
        for index, current in enumerate(latest_actions):
            if not isinstance(current, dict):
                continue
            current_id = str(
                current.get("request_id")
                or current.get("id")
                or f"{event_id}:tool:{index}"
            )
            if current_id != action_id:
                continue
            current_action = dict(current)
            current_prompt = _normalize_prompt(current_action.get("prompt"))
            current_occurrence = _coerce_epoch_seconds(
                current_action.get("running_occurrence_at")
            )
            if not bool(
                str(current_action.get("status") or "").strip().lower() == "running"
                and str(current_action.get("run_id") or "").strip() == expected_run_id
                and run_control_revision(current_action)
                == expected_run_control_revision
                and external_control_revision(current_action)
                == expected_run_control_revision
                and current_prompt is not None
                and _canonical_digest(current_prompt) == expected_prompt_digest
                and current_occurrence is not None
                and abs(current_occurrence - occurrence_time) <= 0.0005
            ):
                return None
            for field in (
                "session_id",
                "chain_id",
                "message_id",
                "origin_session_id",
                "origin_message_id",
            ):
                if action.get(field):
                    current_action[field] = action[field]
            current_action["session_id"] = session_id
            current_action["chain_id"] = chain_id
            try:
                checkpoint = _build_prompt_checkpoint(
                    latest,
                    event_id=event_id,
                    action_id=action_id,
                    run_id=expected_run_id,
                    occurrence_time=occurrence_time,
                    session_id=session_id,
                    chain_id=chain_id,
                    prompt=current_prompt,
                )
            except (TypeError, ValueError):
                return None
            current_action["prompt_checkpoint"] = checkpoint
            updated_actions[index] = current_action
            latest["actions"] = updated_actions
            latest["status"] = "running"
            claimed_action = current_action
            stored_action_index = index
            accepted = True
            return latest
        return None

    stored = calendar_store.update_event(event_id, checkpoint_latest)
    if not stored or not accepted or stored_action_index is None or not checkpoint:
        return None
    stored_actions = stored.get("actions")
    if not isinstance(stored_actions, list):
        return None
    action.clear()
    action.update(claimed_action)
    event.clear()
    event.update(stored)
    refreshed_actions = list(stored_actions)
    refreshed_actions[stored_action_index] = action
    event["actions"] = refreshed_actions
    return dict(checkpoint)


def _claim_pending_prompt_resume(
    event_id: str,
    event: Dict[str, Any],
    action: Dict[str, Any],
    *,
    action_id: str,
) -> Dict[str, Any]:
    """Reclaim a stale prompt with the same run, receipt, and output ids."""

    expected_run_id = str(action.get("run_id") or "").strip()
    if not expected_run_id:
        return {"status": "already_claimed"}
    accepted = False
    claimed_action: Dict[str, Any] = {}
    outcome: Dict[str, Any] = {"status": "already_claimed"}
    stored_action_index: Optional[int] = None
    resumed_at = time.time()

    def claim_latest(latest: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        nonlocal accepted, claimed_action, outcome, stored_action_index
        latest_status = str(latest.get("status") or "").strip().lower()
        if latest_status in {"acknowledged", "skipped", "cancelled", "paused"}:
            return None
        latest_actions = latest.get("actions")
        if not isinstance(latest_actions, list):
            return None
        updated_actions = list(latest_actions)
        for index, current in enumerate(latest_actions):
            if not isinstance(current, dict):
                continue
            current_id = str(
                current.get("request_id")
                or current.get("id")
                or f"{event_id}:tool:{index}"
            )
            current_status = str(current.get("status") or "").strip().lower()
            if current_id != action_id and current_status in {
                "running",
                "followup_running",
            }:
                return None
            if current_id != action_id:
                continue
            if (
                current_status != "prompt_resume_pending"
                or str(current.get("run_id") or "").strip() != expected_run_id
            ):
                return None
            current_action = dict(current)
            checkpoint = current_action.get("prompt_checkpoint")
            checkpoint = checkpoint if isinstance(checkpoint, Mapping) else {}
            occurrence = _coerce_epoch_seconds(checkpoint.get("occurrence_at"))
            prompt = _normalize_prompt(current_action.get("prompt"))
            claimed_control_revision = run_control_revision(current_action)
            if (
                claimed_control_revision is None
                or external_control_revision(current_action) != claimed_control_revision
            ):
                result = _record_interrupted_action(
                    latest,
                    event_id=event_id,
                    action=current_action,
                    action_id=action_id,
                    occurrence_time=occurrence,
                    detail=(
                        "Scheduled prompt controls changed before recovery; the "
                        "old provider checkpoint was not resumed."
                    ),
                    status="reconcile_required",
                )
                updated_actions[index] = current_action
                latest["actions"] = updated_actions
                claimed_action = current_action
                stored_action_index = index
                accepted = True
                outcome = {"status": "control_changed", "result": result}
                return latest
            if (
                occurrence is None
                or prompt is None
                or not _prompt_checkpoint_matches(
                    latest,
                    current_action,
                    event_id=event_id,
                    action_id=action_id,
                    occurrence_time=occurrence,
                    prompt=prompt,
                )
            ):
                result = _record_interrupted_action(
                    latest,
                    event_id=event_id,
                    action=current_action,
                    action_id=action_id,
                    occurrence_time=occurrence,
                    detail=(
                        "Scheduled prompt checkpoint changed or is invalid; "
                        "provider dispatch was blocked."
                    ),
                    status="reconcile_required",
                )
                updated_actions[index] = current_action
                latest["actions"] = updated_actions
                claimed_action = current_action
                stored_action_index = index
                accepted = True
                outcome = {"status": "checkpoint_invalid", "result": result}
                return latest
            current_action["status"] = "running"
            current_action["started_at"] = resumed_at
            current_action["running_occurrence_at"] = occurrence
            try:
                recovery_count = int(current_action.get("recovery_count") or 0)
            except (TypeError, ValueError):
                recovery_count = 0
            current_action["recovery_count"] = recovery_count + 1
            current_action["recovered_at"] = resumed_at
            updated_actions[index] = current_action
            latest["actions"] = updated_actions
            latest["status"] = "running"
            claimed_action = current_action
            stored_action_index = index
            accepted = True
            outcome = {
                "status": "claimed",
                "occurrence_at": occurrence,
                "checkpoint": dict(checkpoint),
            }
            return latest
        return None

    stored = calendar_store.update_event(event_id, claim_latest)
    if not stored or not accepted or stored_action_index is None:
        return outcome
    stored_actions = stored.get("actions")
    if not isinstance(stored_actions, list):
        return {"status": "already_claimed"}
    action.clear()
    action.update(claimed_action)
    event.clear()
    event.update(stored)
    refreshed_actions = list(stored_actions)
    refreshed_actions[stored_action_index] = action
    event["actions"] = refreshed_actions
    return outcome


def _claim_authorized_tool_run(
    event_id: str,
    event: Dict[str, Any],
    action: Dict[str, Any],
    *,
    action_id: str,
    occurrence_time: float,
    force: bool,
) -> Dict[str, Any]:
    """Authorize and claim one exact tool occurrence in a single store CAS."""

    candidate_run_id = f"run-{uuid4().hex}"
    claim_time = time.time()
    outcome: Dict[str, Any] = {"status": "already_claimed"}
    stored_action_index: Optional[int] = None

    def claim_latest(latest: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        nonlocal outcome, stored_action_index
        latest_status = str(latest.get("status") or "").strip().lower()
        if latest_status in {"acknowledged", "skipped", "cancelled", "paused"}:
            outcome = {"status": "inactive"}
            return None
        latest_actions = latest.get("actions")
        if not isinstance(latest_actions, list):
            return None
        for index, current in enumerate(latest_actions):
            if not isinstance(current, dict):
                continue
            current_id = str(
                current.get("request_id")
                or current.get("id")
                or f"{event_id}:tool:{index}"
            )
            current_status = str(current.get("status") or "").strip().lower()
            if current_id != action_id and current_status in {
                "running",
                "followup_running",
            }:
                return None

        updated_actions = list(latest_actions)
        for index, current in enumerate(latest_actions):
            if not isinstance(current, dict):
                continue
            current_id = str(
                current.get("request_id")
                or current.get("id")
                or f"{event_id}:tool:{index}"
            )
            if current_id != action_id:
                continue
            current_action = dict(current)
            normalized_action = _normalize_action(current_action)
            if normalized_action is None or normalized_action.get("kind") != "tool":
                outcome = {"status": "authorization_error"}
                return None
            current_action = normalized_action
            if str(current_action.get("status") or "").strip().lower() == "running":
                return None
            if not force and _action_has_run_for_occurrence(
                latest, current_action, occurrence_time
            ):
                outcome = {"status": "already_executed"}
                return None

            previous_authorization = current_action.get("authorization")
            previous_authorization = (
                dict(previous_authorization)
                if isinstance(previous_authorization, Mapping)
                else {}
            )
            previous_run_id = str(current_action.get("run_id") or "").strip()
            expired = expire_authorization_for_occurrence(
                current_action, occurrence_time, at=claim_time
            )
            if expired and previous_run_id:
                expired_authorization = _authorization_receipt_snapshot(
                    current_action.get("authorization")
                )
                expired_occurrence = _coerce_epoch_seconds(
                    previous_authorization.get("occurrence_at")
                )
                current_action["run_id"] = previous_run_id
                current_action["started_at"] = (
                    _coerce_epoch_seconds(current_action.get("started_at"))
                    or _coerce_epoch_seconds(previous_authorization.get("requested_at"))
                    or claim_time
                )
                _append_run_record(
                    latest,
                    event_id=event_id,
                    action=current_action,
                    action_id=action_id,
                    occurrence_time=expired_occurrence,
                    result={
                        "status": "authorization_expired",
                        "phase": "authorization",
                        "event_id": event_id,
                        "action_id": action_id,
                        "occurrence_at": expired_occurrence,
                        "tool_invoked": False,
                        "state_delta_certainty": "confirmed_no_change",
                        "authorization": expired_authorization,
                    },
                )
            if expired:
                for field in (
                    "run_id",
                    "run_control_revision",
                    "running_occurrence_at",
                    "started_at",
                    "work_run_receipt_id",
                ):
                    current_action.pop(field, None)

            try:
                request = build_authorization_request(
                    event_id,
                    latest,
                    action_id,
                    current_action,
                    occurrence_time,
                )
            except (TypeError, ValueError):
                outcome = {"status": "authorization_error"}
                return None

            authorization = current_action.get("authorization")
            authorization = authorization if isinstance(authorization, Mapping) else {}
            authorization_status = (
                str(authorization.get("status") or "").strip().lower()
            )
            allowed = authorization_allows_dispatch(latest, current_action, request)
            # A consumed approval is evidence for an already-claimed run, not a
            # reusable grant for a fresh manual or replacement claim.
            if request.get("approval_required") and authorization_status == (
                AUTHORIZATION_CONSUMED
            ):
                allowed = False

            if not allowed:
                same_pending_request = bool(
                    authorization.get("id") == request.get("id")
                    and authorization.get("request_digest")
                    == request.get("request_digest")
                    and authorization_status == AUTHORIZATION_REQUIRED
                )
                stable_run_id = (
                    str(current_action.get("run_id") or "").strip()
                    if same_pending_request
                    else ""
                )
                stable_run_id = stable_run_id or candidate_run_id
                requested_at = (
                    _coerce_epoch_seconds(authorization.get("requested_at"))
                    if same_pending_request
                    else None
                ) or claim_time
                if same_pending_request:
                    current_action["status"] = AUTHORIZATION_REQUIRED
                else:
                    mark_authorization_required(
                        current_action, request, requested_at=requested_at
                    )
                current_action["run_id"] = stable_run_id
                current_action["started_at"] = requested_at
                current_action["running_occurrence_at"] = occurrence_time
                pending_authorization = _authorization_receipt_snapshot(
                    current_action.get("authorization")
                )
                _append_run_record(
                    latest,
                    event_id=event_id,
                    action=current_action,
                    action_id=action_id,
                    occurrence_time=occurrence_time,
                    result={
                        "status": AUTHORIZATION_REQUIRED,
                        "phase": "authorization",
                        "event_id": event_id,
                        "action_id": action_id,
                        "occurrence_at": occurrence_time,
                        "tool_invoked": False,
                        "state_delta_certainty": "confirmed_no_change",
                        "authorization": pending_authorization,
                    },
                )
                # The receipt id is derived from the run id. Keeping this helper
                # field in the action definition would change its auth digest.
                current_action.pop("work_run_receipt_id", None)
                updated_actions[index] = current_action
                latest["actions"] = updated_actions
                latest["status"] = AUTHORIZATION_REQUIRED
                stored_action_index = index
                outcome = {
                    "status": AUTHORIZATION_REQUIRED,
                    "run_id": stable_run_id,
                    "request": dict(request),
                    "authorization": pending_authorization,
                }
                return latest

            run_id = candidate_run_id
            if authorization_status == AUTHORIZATION_APPROVED_ONCE:
                run_id = str(current_action.get("run_id") or "").strip() or run_id
                if not consume_authorization(
                    current_action, request, consumed_at=claim_time
                ):
                    outcome = {"status": "authorization_conflict"}
                    return None
                current_action["authorization"] = {
                    **dict(current_action["authorization"]),
                    "claim_run_id": run_id,
                }
            else:
                current_action["authorization"] = {
                    **dict(request),
                    "status": "catalog_auto",
                    "actor_kind": "tool_catalog_policy",
                    "consumed_at": claim_time,
                    "can_approve": False,
                    "claim_run_id": run_id,
                }

            current_action["status"] = "running"
            current_action["started_at"] = claim_time
            current_action["run_id"] = run_id
            current_action["run_control_revision"] = external_control_revision(
                current_action
            )
            current_action["running_occurrence_at"] = occurrence_time
            for stale_key in (
                "effect_certainty",
                "effect_id",
                "effect_ids",
                "effect_status",
                "followup_completed_at",
                "followup_error",
                "followup_message_id",
                "followup_started_at",
                "followup_status",
                "prompt_checkpoint",
                "reconciliation_outcome",
                "reconciliation_summary",
                "work_run_receipt_id",
            ):
                current_action.pop(stale_key, None)
            authorization_snapshot = _authorization_receipt_snapshot(
                current_action.get("authorization")
            )
            _append_run_record(
                latest,
                event_id=event_id,
                action=current_action,
                action_id=action_id,
                occurrence_time=occurrence_time,
                result={
                    "status": "running",
                    "phase": "tool",
                    "event_id": event_id,
                    "action_id": action_id,
                    "occurrence_at": occurrence_time,
                    "tool_invoked": False,
                    "state_delta_certainty": "confirmed_no_change",
                    "authorization": authorization_snapshot,
                },
            )
            current_action.pop("work_run_receipt_id", None)
            updated_actions[index] = current_action
            latest["actions"] = updated_actions
            latest["status"] = "running"
            stored_action_index = index
            outcome = {
                "status": "claimed",
                "run_id": run_id,
                "request": dict(request),
                "authorization": authorization_snapshot,
            }
            return latest
        return None

    stored = calendar_store.update_event(event_id, claim_latest)
    if not stored or stored_action_index is None:
        return outcome
    stored_actions = stored.get("actions")
    if not isinstance(stored_actions, list) or not isinstance(
        stored_actions[stored_action_index], dict
    ):
        return {"status": "already_claimed"}
    claimed_action = dict(stored_actions[stored_action_index])
    action.clear()
    action.update(claimed_action)
    event.clear()
    event.update(stored)
    refreshed_actions = list(stored_actions)
    refreshed_actions[stored_action_index] = action
    event["actions"] = refreshed_actions
    return outcome


def _claim_pending_followup(
    event_id: str,
    event: Dict[str, Any],
    action: Dict[str, Any],
    *,
    action_id: str,
    recovering: bool,
) -> bool:
    """Atomically claim a durable follow-up without replacing its run id."""

    expected_run_id = str(action.get("run_id") or "").strip()
    if not expected_run_id:
        return False
    accepted = False
    claimed_action: Dict[str, Any] = {}
    followup_started_at = time.time()

    def claim_latest(latest: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        nonlocal accepted, claimed_action
        latest_status = str(latest.get("status") or "").strip().lower()
        if latest_status in {"acknowledged", "skipped", "cancelled", "paused"}:
            return None
        latest_actions = latest.get("actions")
        if not isinstance(latest_actions, list):
            return None
        updated_actions = list(latest_actions)
        for index, current in enumerate(latest_actions):
            if not isinstance(current, dict):
                continue
            current_id = str(
                current.get("request_id")
                or current.get("id")
                or f"{event_id}:tool:{index}"
            )
            current_status = str(current.get("status") or "").strip().lower()
            if current_id != action_id and current_status in {
                "running",
                "followup_running",
            }:
                return None
            if current_id != action_id:
                continue
            if current_status != "followup_pending":
                return None
            if str(current.get("run_id") or "").strip() != expected_run_id:
                return None
            current_action = dict(current)
            current_action["status"] = "followup_running"
            current_action["followup_status"] = "running"
            current_action["followup_started_at"] = followup_started_at
            if recovering:
                try:
                    recovery_count = int(current_action.get("recovery_count") or 0)
                except (TypeError, ValueError):
                    recovery_count = 0
                current_action["recovery_count"] = recovery_count + 1
                current_action["recovered_at"] = followup_started_at
            updated_actions[index] = current_action
            latest["actions"] = updated_actions
            latest["status"] = "running"
            claimed_action = current_action
            accepted = True
            return latest
        return None

    stored = calendar_store.update_event(event_id, claim_latest)
    if not stored or not accepted:
        return False
    action.clear()
    action.update(claimed_action)
    event["status"] = "running"
    if isinstance(stored.get("run_history"), list):
        event["run_history"] = list(stored["run_history"])
    return True


async def _reindex_calendar_event(
    app: FastAPI, event_id: str, event: Dict[str, Any]
) -> None:
    """Refresh search/index state after an atomic calendar-store mutation."""

    try:
        from app import routes as routes_module

        await asyncio.to_thread(routes_module._ingest_calendar_event, event_id, event)
    except Exception:
        pass


async def _project_event_work_runs(
    app: FastAPI,
    event_id: str,
    event: Dict[str, Any],
) -> bool:
    """Mirror Calendar receipts before later phases can perform side effects."""

    store = getattr(app.state, "work_run_store", None)
    if store is None:
        config_payload = getattr(app.state, "config", None)
        try:
            store = WorkRunStore(
                config_payload if isinstance(config_payload, dict) else {}
            )
            app.state.work_run_store = store
        except Exception:
            logger.warning("Could not initialize the work-run ledger", exc_info=True)
            return False
    try:
        await asyncio.to_thread(
            project_calendar_event,
            store,
            event_id,
            event,
            raise_on_error=True,
        )
    except Exception:
        logger.warning(
            "Could not project Calendar run history for %s", event_id, exc_info=True
        )
        return False
    return True


async def _persist_event(
    app: FastAPI,
    event_id: str,
    payload: Dict[str, Any],
    *,
    require_active: bool = False,
    replace_actions: bool = False,
    expected_action_id: Optional[str] = None,
    expected_run_id: Optional[str] = None,
    raise_on_projection_failure: bool = False,
) -> bool:
    """Atomically merge runner-owned state and re-index the stored event."""

    accepted = False

    def merge_latest(latest: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        nonlocal accepted
        latest_status = str(latest.get("status") or "").strip().lower()
        if require_active and latest_status in {
            "acknowledged",
            "skipped",
            "cancelled",
            "paused",
        }:
            return None
        expected_latest_index: Optional[int] = None
        if expected_action_id is not None:
            latest_actions = latest.get("actions")
            latest_actions = latest_actions if isinstance(latest_actions, list) else []
            matching: list[tuple[int, Dict[str, Any]]] = []
            for index, current in enumerate(latest_actions):
                if not isinstance(current, dict):
                    continue
                current_id = str(
                    current.get("request_id")
                    or current.get("id")
                    or f"{event_id}:tool:{index}"
                )
                if current_id == expected_action_id:
                    matching.append((index, current))
            if len(matching) != 1 or str(matching[0][1].get("run_id") or "") != str(
                expected_run_id or ""
            ):
                return None
            expected_latest_index = matching[0][0]
        merged = dict(latest)
        payload_actions = payload.get("actions")
        if isinstance(payload_actions, list):
            latest_actions = latest.get("actions")
            latest_actions = latest_actions if isinstance(latest_actions, list) else []
            if replace_actions and latest_actions:
                return None
            if replace_actions:
                merged["actions"] = list(payload_actions)
            elif expected_action_id is not None:
                payload_matches: list[Dict[str, Any]] = []
                for index, candidate in enumerate(payload_actions):
                    if not isinstance(candidate, dict):
                        continue
                    candidate_id = str(
                        candidate.get("request_id")
                        or candidate.get("id")
                        or f"{event_id}:tool:{index}"
                    )
                    if candidate_id == expected_action_id:
                        payload_matches.append(candidate)
                if len(payload_matches) != 1 or expected_latest_index is None:
                    return None
                current_action = latest_actions[expected_latest_index]
                payload_action = payload_matches[0]
                if external_control_revision(
                    current_action
                ) != external_control_revision(payload_action):
                    return None
                merged_action = merge_runner_action_state(
                    [current_action], [payload_action]
                )
                if len(merged_action) != 1:
                    return None
                updated_actions = list(latest_actions)
                updated_actions[expected_latest_index] = merged_action[0]
                merged["actions"] = updated_actions
            else:
                if not runner_snapshot_control_revisions_match(
                    latest_actions, payload_actions
                ):
                    return None
                merged["actions"] = merge_runner_action_state(
                    latest_actions, payload_actions
                )
        payload_history = payload.get("run_history")
        if isinstance(payload_history, list):
            latest_history = latest.get("run_history")
            latest_history = (
                list(latest_history) if isinstance(latest_history, list) else []
            )
            by_id = {
                str(item.get("id")): dict(item)
                for item in latest_history
                if isinstance(item, dict) and item.get("id")
            }
            order = [
                str(item.get("id"))
                for item in latest_history
                if isinstance(item, dict) and item.get("id")
            ]
            for item in payload_history:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                if expected_action_id is not None and not bool(
                    str(item.get("event_id") or "") == str(event_id)
                    and str(item.get("action_id") or "") == str(expected_action_id)
                    and str(item.get("run_id") or "") == str(expected_run_id or "")
                ):
                    continue
                record_id = str(item["id"])
                if record_id not in by_id:
                    order.append(record_id)
                by_id[record_id] = dict(item)
            merged["run_history"] = [
                by_id[item_id] for item_id in order if item_id in by_id
            ][-200:]
        if latest_status in {"acknowledged", "skipped", "cancelled", "paused"}:
            merged["status"] = latest_status
        elif payload.get("status") is not None:
            merged["status"] = payload["status"]
        accepted = True
        return merged

    # Keep the state claim synchronous and tiny so cancellation cannot leave a
    # detached writer that later restores stale ``running`` state.
    stored = calendar_store.update_event(event_id, merge_latest)
    if not stored or not accepted:
        return False
    if not await _project_event_work_runs(app, event_id, stored):
        if raise_on_projection_failure:
            raise _WorkRunProjectionError(
                "The initial work-run receipt could not be recorded."
            )
        return False
    await _reindex_calendar_event(app, event_id, stored)
    return True


async def _persist_claimed_event(
    app: FastAPI,
    event_id: str,
    event: Dict[str, Any],
    *,
    action: Dict[str, Any],
    action_id: str,
    require_active: bool = False,
    raise_on_projection_failure: bool = False,
) -> bool:
    """Persist one claimed action only while its run token still owns it."""

    run_id = str(action.get("run_id") or "").strip()
    if not run_id:
        return False
    return await _persist_event(
        app,
        event_id,
        event,
        require_active=require_active,
        expected_action_id=action_id,
        expected_run_id=run_id,
        raise_on_projection_failure=raise_on_projection_failure,
    )


async def _persist_post_dispatch_truth(
    app: FastAPI,
    *,
    event_id: str,
    event: Dict[str, Any],
    action: Dict[str, Any],
    action_id: str,
    occurrence_time: Optional[float],
    result: Dict[str, Any],
    effect_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Publish already-durable dispatch truth without restoring stale controls."""

    run_id = str(action.get("run_id") or "").strip()
    receipt_id = _receipt_id(event_id, action_id, action)
    store = getattr(app.state, "work_run_store", None)
    if not run_id or not isinstance(store, WorkRunStore):
        return None
    receipt = await asyncio.to_thread(store.get, receipt_id)
    if not isinstance(receipt, dict) or not bool(
        str(receipt.get("event_id") or "") == str(event_id)
        and str(receipt.get("action_id") or "") == str(action_id)
        and str(receipt.get("run_id") or "") == run_id
    ):
        return None

    effect: Dict[str, Any] = {}
    safe_effect_id = str(effect_id or action.get("effect_id") or "").strip()
    if safe_effect_id:
        effects = await asyncio.to_thread(store.list_effects, receipt_id, limit=500)
        matches = [
            item
            for item in effects
            if isinstance(item, dict)
            and str(item.get("id") or "") == safe_effect_id
            and str(item.get("receipt_id") or "") == receipt_id
            and str(item.get("run_id") or "") == run_id
            and str(item.get("tool_call_id") or "") == action_id
        ]
        if len(matches) != 1:
            return None
        effect = dict(matches[0])

    accepted = False
    outcome: Dict[str, Any] = {}
    stored_action: Dict[str, Any] = {}
    stored_index: Optional[int] = None

    def publish(latest: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        nonlocal accepted, outcome, stored_action, stored_index
        latest_actions = latest.get("actions")
        latest_actions = latest_actions if isinstance(latest_actions, list) else []
        matches: list[tuple[int, Dict[str, Any]]] = []
        for index, candidate in enumerate(latest_actions):
            if not isinstance(candidate, dict):
                continue
            candidate_id = str(
                candidate.get("request_id")
                or candidate.get("id")
                or f"{event_id}:tool:{index}"
            )
            if candidate_id == action_id:
                matches.append((index, candidate))
        if len(matches) != 1:
            return None
        index, candidate = matches[0]
        current_action = dict(candidate)
        if str(current_action.get("run_id") or "").strip() != run_id:
            return None

        completed_at = _coerce_epoch_seconds(action.get("executed_at")) or time.time()
        current_action["executed_at"] = completed_at
        current_action["tool_invoked"] = True
        if "result" in action:
            current_action["result"] = action["result"]

        effect_status = str(effect.get("status") or "").strip().lower()
        effect_certainty = str(effect.get("certainty") or "").strip().lower()
        if effect:
            current_action.update(
                {
                    "work_run_receipt_id": receipt_id,
                    "effect_id": safe_effect_id,
                    "effect_status": effect_status,
                    "effect_certainty": effect_certainty,
                    "reconcile_required": bool(effect.get("reconcile_required")),
                }
            )

        uncertain = bool(
            effect
            and (
                effect.get("reconcile_required") is True
                or effect_status in {"dispatched", "interrupted_unknown", "unknown"}
                or effect_certainty in {"uncertain", "unconfirmed", "unknown"}
            )
        )
        local_status = str(result.get("status") or "").strip().lower()
        if uncertain:
            detail = (
                "The tool was dispatched, but its final effect is uncertain; "
                "reconciliation is required."
            )
            current_action["status"] = "reconcile_required"
            current_action["reconcile_required"] = True
            current_action["error"] = detail
            status = "reconcile_required"
            state_delta_certainty = "unknown"
            summary = "Tool dispatch finished with an uncertain effect."
        elif local_status in {"interrupted_unknown", "reconcile_required"}:
            detail = str(
                result.get("error")
                or "Dispatched work finished without a safely publishable outcome."
            )
            current_action["status"] = local_status
            current_action["reconcile_required"] = bool(
                result.get("reconcile_required") or local_status == "reconcile_required"
            )
            current_action["error"] = detail
            status = local_status
            state_delta_certainty = str(
                result.get("state_delta_certainty") or "unknown"
            )
            summary = "Dispatched work ended with an uncertain completion state."
        elif local_status == "error":
            current_action["status"] = "error"
            current_action["error"] = str(
                result.get("error") or "The dispatched tool returned an error."
            )
            current_action.pop("reconcile_required", None)
            status = "error"
            state_delta_certainty = str(
                result.get("state_delta_certainty") or "not_applicable"
            )
            summary = "Tool dispatch failed without an unresolved external effect."
        else:
            current_action["status"] = "invoked"
            current_action.pop("error", None)
            current_action.pop("reconcile_required", None)
            status = "invoked"
            state_delta_certainty = effect_certainty or "not_applicable"
            summary = "Tool completed; newer control state remains authoritative."
            if action.get("followup_status") in {"pending", "running"} or action.get(
                "followup_prompt"
            ):
                current_action["followup_status"] = "cancelled"
                current_action["followup_completed_at"] = completed_at
                current_action.pop("followup_error", None)

        _mark_action_occurrence(current_action, occurrence_time)
        if str(latest.get("status") or "").strip().lower() not in {
            "acknowledged",
            "cancelled",
            "paused",
            "skipped",
        }:
            latest["status"] = "scheduled" if latest.get("rrule") else "prompted"
        receipt_result = {
            **result,
            "status": status,
            "phase": "post_dispatch",
            "event_id": event_id,
            "action_id": action_id,
            "occurrence_at": occurrence_time,
            "tool_invoked": True,
            "state_delta_certainty": state_delta_certainty,
            "reconcile_required": uncertain,
        }
        record = _append_run_record(
            latest,
            event_id=event_id,
            action=action,
            action_id=action_id,
            occurrence_time=occurrence_time,
            result=receipt_result,
        )
        record["summary"] = summary
        updated_actions = list(latest_actions)
        updated_actions[index] = current_action
        latest["actions"] = updated_actions
        accepted = True
        outcome = receipt_result
        stored_action = current_action
        stored_index = index
        return latest

    stored = calendar_store.update_event(event_id, publish)
    if not stored or not accepted or stored_index is None:
        return None
    actions = stored.get("actions")
    if not isinstance(actions, list):
        return None
    action.clear()
    action.update(stored_action)
    event.clear()
    event.update(stored)
    refreshed_actions = list(actions)
    refreshed_actions[stored_index] = action
    event["actions"] = refreshed_actions
    outcome["receipt_durable"] = await _project_event_work_runs(app, event_id, event)
    await _reindex_calendar_event(app, event_id, event)
    return outcome


async def _persist_claimed_or_post_dispatch_truth(
    app: FastAPI,
    *,
    event_id: str,
    event: Dict[str, Any],
    action: Dict[str, Any],
    action_id: str,
    occurrence_time: Optional[float],
    result: Dict[str, Any],
    effect_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Persist normally, then fall back to exact durable dispatch evidence."""

    if await _persist_claimed_event(
        app,
        event_id,
        event,
        action=action,
        action_id=action_id,
    ):
        return result
    return await _persist_post_dispatch_truth(
        app,
        event_id=event_id,
        event=event,
        action=action,
        action_id=action_id,
        occurrence_time=occurrence_time,
        result=result,
        effect_id=effect_id,
    )


async def _checkpoint_cooperative_cancellation(
    app: FastAPI,
    *,
    event_id: str,
    event: Dict[str, Any],
    action: Dict[str, Any],
    action_id: str,
    occurrence_time: Optional[float],
    phase: str,
    tool_invoked: bool,
    provider_dispatch_uncertain: bool = False,
    effect_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Atomically acknowledge cancellation at one exact pre-dispatch boundary.

    A stale worker never gets permission to dispatch: the callback requires the
    same event, action, run token, and runner phase that the caller observed. A
    request before any external work becomes a confirmed cancellation. A tool
    whose durable result already exists can cancel only its remaining provider
    follow-up, preserving the tool/effect evidence. Uncertain dispatched work is
    left for reconciliation rather than being mislabeled as safely cancelled.
    """

    expected_run_id = str(action.get("run_id") or "").strip()
    if not expected_run_id:
        return None
    expected_run_control_revision = run_control_revision(action)
    normalized_phase = str(phase or "").strip().lower()
    expected_status = (
        "followup_running" if normalized_phase == "followup" else "running"
    )
    outcome: Dict[str, Any] = {
        "status": "already_claimed",
        "phase": normalized_phase or "cancellation",
        "event_id": event_id,
        "action_id": action_id,
        "occurrence_at": occurrence_time,
        "tool_invoked": bool(tool_invoked),
        "dispatch_blocked": True,
    }
    accepted = False
    stored_action: Dict[str, Any] = {}
    stored_action_index: Optional[int] = None

    def checkpoint_latest(latest: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        nonlocal accepted, outcome, stored_action, stored_action_index
        latest_status = str(latest.get("status") or "").strip().lower()
        latest_actions = latest.get("actions")
        if not isinstance(latest_actions, list):
            return None
        matches: list[tuple[int, Dict[str, Any]]] = []
        for index, candidate in enumerate(latest_actions):
            if not isinstance(candidate, dict):
                continue
            candidate_id = str(
                candidate.get("request_id")
                or candidate.get("id")
                or f"{event_id}:tool:{index}"
            )
            if candidate_id == action_id:
                matches.append((index, candidate))
        if len(matches) != 1:
            return None
        index, candidate = matches[0]
        current_action = dict(candidate)
        if str(current_action.get("run_id") or "").strip() != expected_run_id:
            return None
        if str(current_action.get("status") or "").strip().lower() != expected_status:
            return None
        has_cancellation = cancellation_requested(
            current_action, expected_run_id=expected_run_id
        )
        current_run_control_revision = run_control_revision(current_action)
        control_changed = bool(
            expected_run_control_revision is None
            or current_run_control_revision is None
            or current_run_control_revision != expected_run_control_revision
            or external_control_revision(current_action) != current_run_control_revision
        )
        if not has_cancellation and not control_changed:
            outcome = {"status": "continue"}
            return None

        if control_changed and not has_cancellation:
            changed_at = time.time()
            if provider_dispatch_uncertain:
                detail = (
                    "The action changed while a provider attempt was in flight; "
                    "its outcome needs reconciliation."
                )
                current_action["status"] = "interrupted_unknown"
                current_action["reconcile_required"] = True
                current_action["error"] = detail
                current_action["interrupted_at"] = changed_at
                _mark_action_occurrence(current_action, occurrence_time)
                _mark_event_prompted(latest)
                result = {
                    "status": "interrupted_unknown",
                    "phase": "control_changed",
                    "event_id": event_id,
                    "action_id": action_id,
                    "occurrence_at": occurrence_time,
                    "tool_invoked": bool(tool_invoked),
                    "state_delta_certainty": "unknown",
                    "reconcile_required": True,
                    "dispatch_blocked": True,
                    "error": detail,
                }
                summary = (
                    "Action changed during dispatch; outcome needs reconciliation."
                )
            elif tool_invoked:
                effect_status = (
                    str(current_action.get("effect_status") or "").strip().lower()
                )
                effect_certainty = (
                    str(current_action.get("effect_certainty") or "").strip().lower()
                )
                uncertain = bool(
                    current_action.get("reconcile_required")
                    or effect_status in {"dispatched", "interrupted_unknown", "unknown"}
                    or effect_certainty in {"uncertain", "unconfirmed", "unknown"}
                )
                if uncertain:
                    detail = (
                        "The action changed after its tool was dispatched; the "
                        "effect outcome needs reconciliation."
                    )
                    current_action["status"] = "reconcile_required"
                    current_action["reconcile_required"] = True
                    current_action["error"] = detail
                    result_status = "reconcile_required"
                    state_delta_certainty = "unknown"
                    summary = "Action changed after tool dispatch; review is required."
                else:
                    current_action["status"] = "invoked"
                    current_action["followup_status"] = "cancelled"
                    current_action["followup_completed_at"] = changed_at
                    current_action.pop("followup_error", None)
                    current_action.pop("reconcile_required", None)
                    current_action.pop("error", None)
                    result_status = "invoked"
                    state_delta_certainty = effect_certainty or "not_applicable"
                    summary = (
                        "Tool completed; the changed action blocked its remaining "
                        "provider follow-up."
                    )
                current_action["tool_invoked"] = True
                _mark_action_occurrence(current_action, occurrence_time)
                _mark_event_prompted(latest)
                result = {
                    "status": result_status,
                    "phase": "control_changed",
                    "event_id": event_id,
                    "action_id": action_id,
                    "occurrence_at": occurrence_time,
                    "tool_invoked": True,
                    "followup_invoked": False,
                    "state_delta_certainty": state_delta_certainty,
                    "reconcile_required": uncertain,
                    "dispatch_blocked": True,
                    "partial_work": True,
                }
            else:
                authorization = current_action.get("authorization")
                if isinstance(authorization, dict) and str(
                    authorization.get("status") or ""
                ).strip().lower() not in {
                    "authorization_denied",
                    "expired",
                    "invalidated",
                }:
                    current_action["authorization"] = {
                        **authorization,
                        "status": "invalidated",
                        "invalidated_at": changed_at,
                        "invalidation_reason": "external_control_changed",
                        "can_approve": False,
                    }
                result = {
                    "status": "skipped",
                    "phase": "control_changed",
                    "event_id": event_id,
                    "action_id": action_id,
                    "occurrence_at": occurrence_time,
                    "tool_invoked": False,
                    "prompt_invoked": False,
                    "state_delta_certainty": "confirmed_no_change",
                    "reconcile_required": False,
                    "dispatch_blocked": True,
                    "retryable": True,
                }
                summary = (
                    "Action changed before dispatch; nothing ran and the latest "
                    "definition remains scheduled."
                )

            receipt = _append_run_record(
                latest,
                event_id=event_id,
                action=current_action,
                action_id=action_id,
                occurrence_time=occurrence_time,
                result=result,
            )
            receipt["summary"] = summary
            if not tool_invoked and not provider_dispatch_uncertain:
                for field in (
                    "run_id",
                    "run_control_revision",
                    "running_occurrence_at",
                    "started_at",
                    "work_run_receipt_id",
                    "prompt_checkpoint",
                    "result",
                    "error",
                    "executed_at",
                    "interrupted_at",
                    "effect_id",
                    "effect_status",
                    "effect_certainty",
                    "state_delta_certainty",
                    "reconcile_required",
                    "tool_invoked",
                    "followup_status",
                    "followup_error",
                    "followup_prompt",
                    "followup_tool_name",
                    "followup_tool_args",
                    "followup_message_id",
                    "followup_started_at",
                    "followup_completed_at",
                ):
                    current_action.pop(field, None)
                current_action["status"] = (
                    latest_status
                    if latest_status
                    in {"acknowledged", "skipped", "cancelled", "paused"}
                    else "scheduled"
                )
                if latest_status not in {
                    "acknowledged",
                    "skipped",
                    "cancelled",
                    "paused",
                }:
                    latest["status"] = "scheduled"
            updated_actions = list(latest_actions)
            updated_actions[index] = current_action
            latest["actions"] = updated_actions
            stored_action = current_action
            stored_action_index = index
            accepted = True
            outcome = result
            return latest

        acknowledged_at = time.time()
        cancel_requested_at = _coerce_epoch_seconds(
            current_action.get("cancel_requested_at")
        )
        current_effect_status = (
            str(current_action.get("effect_status") or "").strip().lower()
        )
        current_effect_certainty = (
            str(current_action.get("effect_certainty") or "").strip().lower()
        )
        effect_uncertain = bool(
            current_action.get("reconcile_required")
            or current_effect_status in {"dispatched", "interrupted_unknown", "unknown"}
            or current_effect_certainty in {"uncertain", "unconfirmed", "unknown"}
        )
        attention_required = provider_dispatch_uncertain or (
            tool_invoked and effect_uncertain
        )
        if attention_required:
            attention_status = (
                "interrupted_unknown"
                if provider_dispatch_uncertain
                else "reconcile_required"
            )
            detail = (
                "Cancellation was requested after provider dispatch began; the "
                "provider outcome is uncertain and needs reconciliation."
                if provider_dispatch_uncertain
                else "Cancellation was requested after tool dispatch, but the "
                "effect outcome is uncertain and needs reconciliation."
            )
            current_action["status"] = attention_status
            current_action["reconcile_required"] = True
            current_action["error"] = detail
            current_action["interrupted_at"] = acknowledged_at
            current_action["tool_invoked"] = bool(tool_invoked)
            if normalized_phase == "followup":
                current_action["followup_status"] = attention_status
                current_action["followup_error"] = detail
                current_action["followup_completed_at"] = acknowledged_at
            _mark_action_occurrence(current_action, occurrence_time)
            _mark_event_prompted(latest)
            result: Dict[str, Any] = {
                "status": attention_status,
                "phase": "cancellation",
                "event_id": event_id,
                "action_id": action_id,
                "occurrence_at": occurrence_time,
                "tool_invoked": bool(tool_invoked),
                "followup_invoked": False,
                "state_delta_certainty": "unknown",
                "reconcile_required": True,
                "cancel_requested": True,
                "cancellation_acknowledged": False,
                "error": detail,
            }
            summary = (
                "Cancellation requested after dispatch; outcome needs reconciliation."
            )
        else:
            current_action["status"] = "cancelled"
            current_action["cancelled_at"] = cancel_requested_at or acknowledged_at
            current_action["tool_invoked"] = bool(tool_invoked)
            current_action.pop("error", None)
            current_action.pop("reconcile_required", None)
            if tool_invoked:
                current_action["followup_status"] = "cancelled"
                current_action["followup_completed_at"] = acknowledged_at
                current_action.pop("followup_error", None)
                state_delta_certainty = (
                    current_effect_certainty or "no_change_since_checkpoint"
                )
                summary = (
                    "Tool completed; the remaining provider follow-up was cancelled."
                )
            else:
                current_action["executed_at"] = acknowledged_at
                current_action["prompt_invoked"] = False
                current_action["effect_certainty"] = "confirmed_no_change"
                state_delta_certainty = "confirmed_no_change"
                summary = "Scheduled action cancelled before dispatch."
            if effect_id and not tool_invoked:
                current_action["effect_id"] = effect_id
                current_action["effect_status"] = "not_dispatched"
                current_action["effect_certainty"] = "confirmed_no_change"
            _mark_action_occurrence(current_action, occurrence_time)
            latest["status"] = "scheduled" if latest.get("rrule") else "cancelled"
            result = {
                "status": "cancelled",
                "phase": "cancellation",
                "event_id": event_id,
                "action_id": action_id,
                "occurrence_at": occurrence_time,
                "tool_invoked": bool(tool_invoked),
                "prompt_invoked": False,
                "followup_invoked": False,
                "state_delta_certainty": state_delta_certainty,
                "cancel_requested": True,
                "cancellation_acknowledged": True,
                "partial_work": bool(tool_invoked),
            }

        updated_actions = list(latest_actions)
        updated_actions[index] = current_action
        latest["actions"] = updated_actions
        receipt = _append_run_record(
            latest,
            event_id=event_id,
            action=current_action,
            action_id=action_id,
            occurrence_time=occurrence_time,
            result=result,
        )
        receipt["summary"] = summary
        stored_action = current_action
        stored_action_index = index
        accepted = True
        outcome = result
        return latest

    stored = calendar_store.update_event(event_id, checkpoint_latest)
    if outcome.get("status") == "continue":
        return None
    if not stored or not accepted or stored_action_index is None:
        return outcome
    actions = stored.get("actions")
    if not isinstance(actions, list):
        return outcome
    action.clear()
    action.update(stored_action)
    event.clear()
    event.update(stored)
    refreshed_actions = list(actions)
    refreshed_actions[stored_action_index] = action
    event["actions"] = refreshed_actions
    outcome["receipt_durable"] = await _project_event_work_runs(app, event_id, event)
    await _reindex_calendar_event(app, event_id, event)
    return outcome


def _release_unprojected_claim(
    event: Dict[str, Any],
    *,
    event_id: str,
    action_id: str,
    run_id: str,
    occurrence_time: Optional[float],
) -> bool:
    """Release only the exact pre-dispatch claim whose receipt was not durable.

    The Calendar write happens before Activity projection, so a projection failure
    otherwise leaves the action ``running`` even though no provider or tool call
    began.  Re-read the latest event and change only runner-owned claim state so
    concurrent definition edits and user pause/stop controls remain authoritative.
    """

    expected_run_id = str(run_id or "").strip()
    if not expected_run_id:
        return False
    released = False

    def release_latest(latest: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        nonlocal released
        latest_actions = latest.get("actions")
        if not isinstance(latest_actions, list):
            return None
        matches: list[tuple[int, Dict[str, Any]]] = []
        for index, candidate in enumerate(latest_actions):
            if not isinstance(candidate, dict):
                continue
            candidate_id = str(
                candidate.get("request_id")
                or candidate.get("id")
                or f"{event_id}:tool:{index}"
            )
            if candidate_id == action_id:
                matches.append((index, candidate))
        if len(matches) != 1:
            return None
        index, latest_action = matches[0]
        if str(latest_action.get("run_id") or "").strip() != expected_run_id:
            return None
        if str(latest_action.get("status") or "").strip().lower() != "running":
            return None

        updated_action = dict(latest_action)
        claimed_occurrence = _coerce_epoch_seconds(
            updated_action.get("running_occurrence_at")
        )
        retry_occurrence = (
            claimed_occurrence if claimed_occurrence is not None else occurrence_time
        )
        authorization = updated_action.get("authorization")
        authorization = dict(authorization) if isinstance(authorization, dict) else {}
        claim_owned_authorization = bool(
            str(authorization.get("claim_run_id") or "").strip() == expected_run_id
        )
        restore_approval = bool(
            claim_owned_authorization
            and authorization.get("approval_required")
            and str(authorization.get("status") or "").strip().lower()
            == AUTHORIZATION_CONSUMED
        )
        if restore_approval:
            authorization["status"] = AUTHORIZATION_APPROVED_ONCE
            authorization.pop("consumed_at", None)
            authorization.pop("claim_run_id", None)
            updated_action["authorization"] = authorization
        elif claim_owned_authorization:
            authorization.pop("claim_run_id", None)
            updated_action["authorization"] = authorization
        receipt_result = {
            "status": "error",
            "phase": "receipt_gate",
            "event_id": event_id,
            "action_id": action_id,
            "occurrence_at": retry_occurrence,
            "tool_invoked": False,
            "state_delta_certainty": "confirmed_no_change",
            "error": "The initial work-run receipt could not be recorded.",
            "authorization": _authorization_receipt_snapshot(authorization),
        }
        _append_run_record(
            latest,
            event_id=event_id,
            action=updated_action,
            action_id=action_id,
            occurrence_time=retry_occurrence,
            result=receipt_result,
        )
        updated_action["status"] = (
            "authorization_approved" if restore_approval else "scheduled"
        )
        clear_fields = ["work_run_receipt_id", "run_control_revision"]
        if not restore_approval:
            clear_fields.extend(["run_id", "running_occurrence_at", "started_at"])
        else:
            updated_action.pop("running_occurrence_at", None)
        for field in clear_fields:
            updated_action.pop(field, None)

        updated_actions = list(latest_actions)
        updated_actions[index] = updated_action
        latest["actions"] = updated_actions
        if str(latest.get("status") or "").strip().lower() == "running":
            latest["status"] = "scheduled"
        released = True
        return latest

    stored = calendar_store.update_event(event_id, release_latest)
    if not stored or not released:
        return False
    event.clear()
    event.update(stored)
    return True


def _release_unprojected_authorization_receipt(
    event: Dict[str, Any],
    *,
    event_id: str,
    action_id: str,
    run_id: str,
    authorization: Mapping[str, Any],
) -> bool:
    """Keep a pending request due when its Activity card was not projected."""

    expected_run_id = str(run_id or "").strip()
    expected_authorization_id = str(authorization.get("id") or "").strip()
    expected_request_digest = str(authorization.get("request_digest") or "").strip()
    if (
        not expected_run_id
        or not expected_authorization_id
        or not expected_request_digest
    ):
        return False
    released = False
    stored_action_index: Optional[int] = None

    def release_latest(latest: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        nonlocal released, stored_action_index
        latest_actions = latest.get("actions")
        if not isinstance(latest_actions, list):
            return None
        matches: list[tuple[int, Dict[str, Any]]] = []
        for index, candidate in enumerate(latest_actions):
            if not isinstance(candidate, dict):
                continue
            candidate_id = str(
                candidate.get("request_id")
                or candidate.get("id")
                or f"{event_id}:tool:{index}"
            )
            if candidate_id == action_id:
                matches.append((index, candidate))
        if len(matches) != 1:
            return None
        index, latest_action = matches[0]
        latest_authorization = latest_action.get("authorization")
        if not isinstance(latest_authorization, Mapping):
            return None
        if not bool(
            str(latest_action.get("run_id") or "").strip() == expected_run_id
            and str(latest_action.get("status") or "").strip().lower()
            == AUTHORIZATION_REQUIRED
            and str(latest_authorization.get("status") or "").strip().lower()
            == AUTHORIZATION_REQUIRED
            and latest_authorization.get("id") == expected_authorization_id
            and latest_authorization.get("request_digest") == expected_request_digest
        ):
            return None
        updated_action = dict(latest_action)
        # The authorization and its stable run token remain intact. Only the
        # schedulable action state changes so a healthy tick retries projection.
        updated_action["status"] = "scheduled"
        updated_actions = list(latest_actions)
        updated_actions[index] = updated_action
        latest["actions"] = updated_actions
        if str(latest.get("status") or "").strip().lower() == AUTHORIZATION_REQUIRED:
            latest["status"] = "scheduled"
        stored_action_index = index
        released = True
        return latest

    stored = calendar_store.update_event(event_id, release_latest)
    if not stored or not released or stored_action_index is None:
        return False
    stored_actions = stored.get("actions")
    if not isinstance(stored_actions, list):
        return False
    event.clear()
    event.update(stored)
    return True


async def _publish_tool_status(
    app: FastAPI,
    *,
    tool_id: str,
    name: str,
    args: Dict[str, Any],
    status: str,
    result: Any = None,
    chain_id: Optional[str] = None,
    message_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> None:
    try:
        from app import routes as routes_module

        await routes_module.publish_console_event(  # type: ignore[attr-defined]
            app,
            {
                "type": "tool",
                "id": tool_id,
                "name": name,
                "args": args,
                "result": result,
                "chain_id": chain_id,
                "message_id": message_id,
                "status": status,
                "session_id": session_id,
            },
            default_agent=chain_id or session_id,
        )
    except Exception:
        pass


async def _publish_content(
    app: FastAPI,
    *,
    content: str,
    chain_id: Optional[str] = None,
    message_id: Optional[str] = None,
    session_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    try:
        from app import routes as routes_module

        payload: Dict[str, Any] = {
            "type": "content",
            "content": content,
            "chain_id": chain_id,
            "message_id": message_id,
            "session_id": session_id,
        }
        if metadata:
            payload["metadata"] = metadata
        if metadata and metadata.get("scheduled"):
            payload["agent_label"] = "Scheduled prompt"
            run_status = str(metadata.get("run_status") or "").strip().lower()
            payload["agent_status"] = (
                run_status
                if run_status in {"active", "complete", "error"}
                else ("error" if metadata.get("error") else "complete")
            )
            payload["provenance"] = build_agent_provenance(
                kind="scheduled_prompt",
                source_event_id=str(metadata.get("event_id") or "").strip() or None,
                branch_session_id=session_id,
                label="Scheduled prompt run",
            )
        await routes_module.publish_console_event(  # type: ignore[attr-defined]
            app,
            payload,
            default_agent=chain_id or session_id,
        )
    except Exception:
        pass


def _append_tool_to_conversation(
    *,
    session_id: Optional[str],
    message_id: Optional[str],
    request_id: str,
    name: str,
    args: Dict[str, Any],
    status: str,
    result: Any,
) -> None:
    if not session_id or not message_id:
        return
    try:
        from app import routes as routes_module

        routes_module._append_tool_event_to_conversation(  # type: ignore[attr-defined]
            session_id,
            message_id,
            name,
            args,
            result,
            status=status,
            request_id=request_id,
        )
    except Exception:
        pass


def _append_conversation_entry(
    *,
    session_id: Optional[str],
    entry: Dict[str, Any],
) -> bool:
    if not session_id:
        return False
    try:
        from app import routes as routes_module

        routes_module._append_conversation_entry(  # type: ignore[attr-defined]
            session_id, entry
        )
        stored = _load_conversation_entry(session_id, str(entry.get("id") or ""))
        return _conversation_entry_matches(
            stored,
            entry,
            keys={"id", "role", "text", "thought", "metadata"},
        )
    except Exception:
        return False


def _load_conversation_entry(
    session_id: Optional[str], message_id: str
) -> Optional[Dict[str, Any]]:
    if not session_id:
        return None
    try:
        from app.utils import conversation_store

        conversation = conversation_store.load_conversation(session_id)
    except Exception:
        return None
    return next(
        (
            dict(item)
            for item in reversed(conversation)
            if isinstance(item, dict) and item.get("id") == message_id
        ),
        None,
    )


def _conversation_entry_is_complete(entry: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(entry, dict):
        return False
    if str(entry.get("role") or "").strip().lower() not in {"ai", "assistant"}:
        return False
    metadata = entry.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    return str(metadata.get("status") or "").strip().lower() == "complete"


def _conversation_entry_matches(
    entry: Optional[Dict[str, Any]],
    expected: Dict[str, Any],
    *,
    keys: Optional[set[str]] = None,
) -> bool:
    if not isinstance(entry, dict):
        return False
    for key, value in expected.items():
        if keys is not None and key not in keys:
            continue
        if value is None:
            continue
        if key == "metadata" and isinstance(value, dict):
            metadata = entry.get("metadata")
            metadata = metadata if isinstance(metadata, dict) else {}
            if any(
                metadata.get(meta_key) != meta_value
                for meta_key, meta_value in value.items()
            ):
                return False
        elif entry.get(key) != value:
            return False
    return True


def _update_conversation_entry(
    *,
    session_id: Optional[str],
    message_id: str,
    updates: Dict[str, Any],
) -> bool:
    if not session_id:
        return False
    try:
        from app import routes as routes_module

        routes_module._update_conversation_entry(  # type: ignore[attr-defined]
            session_id, message_id, updates
        )
        stored = _load_conversation_entry(session_id, message_id)
        return _conversation_entry_matches(stored, updates)
    except Exception:
        return False


def _scheduled_tool_effect_policy(
    name: str, args: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Describe tools whose real-world effects must be journaled fail-closed."""

    try:
        from app.tool_catalog import get_tool_catalog_entry

        catalog = get_tool_catalog_entry(name)
    except Exception:
        catalog = {
            "origin": "custom",
            "category": "custom",
            "runtime": {},
            "persistence": {},
            "safety": {"risk_level": "unknown", "default_approval": "confirm"},
        }
    runtime = catalog.get("runtime")
    runtime = runtime if isinstance(runtime, dict) else {}
    persistence = catalog.get("persistence")
    persistence = persistence if isinstance(persistence, dict) else {}
    safety = catalog.get("safety")
    safety = safety if isinstance(safety, dict) else {}
    origin = str(catalog.get("origin") or "unknown").lower()
    risk = str(safety.get("risk_level") or "unknown").lower()
    network_mcp = name == "mcp.call" and bool(runtime.get("network", True))
    unknown_risk = origin != "builtin" or risk == "unknown"
    if not (bool(persistence.get("writes_state")) or network_mcp or unknown_risk):
        return None

    if network_mcp or bool(runtime.get("network")):
        scope = "external_network"
    elif bool(runtime.get("filesystem")):
        scope = "device_filesystem"
    elif bool(persistence.get("writes_state")):
        scope = "device_state"
    else:
        scope = "custom_unknown"

    target_keys = (
        "target",
        "path",
        "file",
        "filename",
        "url",
        "endpoint",
        "server",
        "resource_id",
        "event_id",
        "job_id",
        "channel",
        "repository",
        "repo",
    )
    target_key = next((key for key in target_keys if args.get(key) is not None), None)
    if target_key:
        redacted_target = f"{target_key}:{_canonical_digest(args[target_key])}"
    else:
        redacted_target = f"tool:{name}"
    idempotency_key = args.get("idempotency_key")
    if idempotency_key is None:
        idempotency_key = args.get("idempotencyKey")
    return {
        "tool_kind": origin,
        "effect_scope": scope,
        "redacted_target": redacted_target,
        "argument_digest": _canonical_digest(args),
        "idempotency_key": (
            _canonical_digest(idempotency_key) if idempotency_key is not None else None
        ),
        "approval_required": str(safety.get("default_approval") or "").lower()
        not in {"", "none", "never"},
        "default_approval": str(safety.get("default_approval") or "unspecified"),
    }


def _effect_permission_snapshot(
    request: Mapping[str, Any], *, checked_at: float
) -> Dict[str, Any]:
    scopes = request.get("required_scopes")
    scopes = list(scopes) if isinstance(scopes, (list, tuple)) else []
    return {
        "status": "granted",
        "scopes": scopes,
        "policy_id": request.get("policy_id"),
        "grant_id": request.get("id"),
        "actor_kind": "scheduled_job",
        "checked_at": checked_at,
    }


def _effect_approval_snapshot(
    action: Dict[str, Any], request: Mapping[str, Any]
) -> Dict[str, Any]:
    authorization = action.get("authorization")
    authorization = authorization if isinstance(authorization, Mapping) else {}
    required = bool(request.get("approval_required"))
    return {
        "required": required,
        "status": "approved_once" if required else "catalog_auto",
        "id": (authorization.get("decision_id") if required else request.get("id")),
        "policy_id": request.get("policy_id"),
        "method": "approve_once" if required else "catalog_auto",
        "actor_kind": (
            authorization.get("actor_kind") if required else "tool_catalog_policy"
        ),
        "decided_at": authorization.get("decided_at") if required else None,
    }


def _remote_effect_ids(result: Any) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    allowed = (
        "operation_id",
        "request_id",
        "resource_id",
        "version_id",
        "message_id",
        "event_id",
        "job_id",
        "transaction_id",
        "commit_id",
        "task_id",
        "thread_id",
        "deployment_id",
        "record_id",
    )
    return {key: result[key] for key in allowed if result.get(key) is not None}


def _tool_result_reports_error(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    status = str(result.get("status") or "").strip().lower()
    if status.startswith(("error", "fail", "partial", "unknown")) or status in {
        "aborted",
        "cancelled",
        "interrupted",
        "timed_out",
        "timeout",
    }:
        return True
    if result.get("ok") is False or result.get("success") is False:
        return True
    return result.get("error") not in {None, "", False}


async def _record_effect_transition(
    app: FastAPI,
    record: Dict[str, Any],
    *,
    expected_statuses: Optional[set[str]] = None,
    create_only: bool = False,
) -> Dict[str, Any]:
    store = _work_run_store(app)
    return await asyncio.to_thread(
        store.record_effect,
        record,
        expected_statuses=expected_statuses,
        create_only=create_only,
    )


async def _close_effect_without_dispatch(
    app: FastAPI,
    effect_record: Dict[str, Any],
    *,
    expected_status: str,
) -> Dict[str, Any]:
    """Confirm that a journaled intent never reached its external tool call."""

    closed = {
        **effect_record,
        "status": "not_dispatched",
        "certainty": "confirmed_no_change",
        "reconcile_required": False,
        "finished_at": time.time(),
        "error_category": "tool_cancelled",
        "error_code": "user_cancelled_before_dispatch",
    }
    return await _record_effect_transition(
        app,
        closed,
        expected_statuses={expected_status},
    )


async def _fail_before_tool_invocation(
    app: FastAPI,
    *,
    event_id: str,
    event: Dict[str, Any],
    action: Dict[str, Any],
    action_id: str,
    occurrence_time: Optional[float],
    name: str,
    args: Dict[str, Any],
    detail: str,
    chain_id: Optional[str],
    message_id: Optional[str],
    session_id: Optional[str],
) -> Dict[str, Any]:
    action["status"] = "error"
    action["error"] = detail
    action["executed_at"] = time.time()
    _mark_action_occurrence(action, occurrence_time)
    _mark_event_prompted(event)
    result_payload = {
        "status": "error",
        "event_id": event_id,
        "action_id": action_id,
        "occurrence_at": occurrence_time,
        "tool_invoked": False,
        "state_delta_certainty": "confirmed_no_change",
        "error": detail,
    }
    _append_run_record(
        event,
        event_id=event_id,
        action=action,
        action_id=action_id,
        occurrence_time=occurrence_time,
        result=result_payload,
    )
    await _persist_claimed_event(
        app,
        event_id,
        event,
        action=action,
        action_id=action_id,
    )
    await _publish_tool_status(
        app,
        tool_id=action_id,
        name=name,
        args=args,
        status="error",
        result=detail,
        chain_id=chain_id,
        message_id=message_id,
        session_id=session_id,
    )
    _append_tool_to_conversation(
        session_id=session_id,
        message_id=message_id,
        request_id=action_id,
        name=name,
        args=args,
        status="error",
        result=detail,
    )
    return result_payload


async def _invoke_tool(
    app: FastAPI,
    *,
    name: str,
    args: Dict[str, Any],
    user: str,
    action_context: Optional[Dict[str, Any]] = None,
) -> Any:
    manager = getattr(app.state, "memory_manager", None)
    if manager is None:
        raise RuntimeError("memory manager not available")
    signature = generate_signature(user, name, args)
    return await asyncio.to_thread(
        manager.invoke_tool,
        name,
        user=user,
        signature=signature,
        _action_context=action_context,
        **args,
    )


async def _run_prompt_followup(
    app: FastAPI,
    *,
    session_id: Optional[str],
    chain_id: Optional[str],
    prompt: str,
    tool_name: str,
    tool_args: Dict[str, Any],
    tool_result: Any,
    event_id: str,
    event: Dict[str, Any],
    action: Dict[str, Any],
    action_id: str,
    parent_session_id: Optional[str] = None,
    parent_message_id: Optional[str] = None,
    followup_message_id: Optional[str] = None,
) -> Dict[str, Any]:
    session_id = _ensure_task_conversation(
        session_id=session_id,
        event=event,
        event_id=event_id,
        parent_session_id=parent_session_id,
        parent_message_id=parent_message_id,
    )
    chain_id = chain_id or session_id

    try:
        from app import routes as routes_module
        from app.services import ModelContext as ServiceContext
        from app.utils import conversation_store
    except Exception:
        logger.warning("Scheduled follow-up runtime unavailable", exc_info=True)
        return {"status": "error", "error": "follow-up runtime unavailable"}

    now_ts = time.time()
    followup_id = followup_message_id or _stable_composite_id(
        "scheduled-message",
        event_id,
        action_id,
        int(now_ts * 1000),
        "tool-followup",
    )
    receipt_id = _receipt_id(event_id, action_id, action)
    store = _work_run_store(app)
    existing_output = _load_conversation_entry(session_id, followup_id)
    followup_occurrence = _coerce_epoch_seconds(action.get("running_occurrence_at"))
    cancellation = await _checkpoint_cooperative_cancellation(
        app,
        event_id=event_id,
        event=event,
        action=action,
        action_id=action_id,
        occurrence_time=followup_occurrence,
        phase="followup",
        tool_invoked=True,
    )
    if cancellation is not None:
        raise _ScheduledDispatchBlocked(cancellation)
    if _conversation_entry_is_complete(existing_output):
        try:
            await _hydrate_effect_state_from_ledger(store, receipt_id, action)
            await _close_attempt_from_canonical_output(
                store,
                receipt_id,
                effect_watermark_digest=_effect_watermark(action),
                state_delta_certainty=_attempt_state_certainty(action),
            )
        except Exception:
            logger.warning(
                "Could not reconcile provider attempt from canonical output for %s",
                receipt_id,
                exc_info=True,
            )
            return {
                "status": "error",
                "error": "canonical output recovery could not be recorded",
                "reconcile_required": True,
                "error_category": "provider_attempt_journal_unavailable",
                "state_delta_certainty": "unknown",
            }
        return {
            "status": "complete",
            "result": str(existing_output.get("text") or ""),
            "output_recovered": True,
        }
    user_entry_id = f"{followup_id}:user"
    existing_user = _load_conversation_entry(session_id, user_entry_id)
    user_entry = {
        "id": user_entry_id,
        "role": "user",
        "text": prompt,
        "timestamp": now_ts,
        "metadata": {
            "scheduled": True,
            "event_id": event_id,
            "action_id": action_id,
            "tool": tool_name,
        },
    }
    if existing_user is None and not _append_conversation_entry(
        session_id=session_id, entry=user_entry
    ):
        return {
            "status": "error",
            "error": "canonical output placeholder could not be recorded",
            "reconcile_required": True,
            "error_category": "provider_output_checkpoint_missing",
        }
    pending_entry = {
        "id": followup_id,
        "role": "ai",
        "text": "",
        "thought": "",
        "metadata": {"status": "pending", "scheduled": True},
        "timestamp": now_ts,
    }
    if existing_output is None and not _append_conversation_entry(
        session_id=session_id, entry=pending_entry
    ):
        return {
            "status": "error",
            "error": "canonical output placeholder could not be recorded",
            "reconcile_required": True,
            "error_category": "provider_output_checkpoint_missing",
        }
    await _publish_content(
        app,
        content="Scheduled prompt running.",
        chain_id=chain_id,
        message_id=followup_id,
        session_id=session_id,
        metadata={
            "scheduled": True,
            "event_id": event_id,
            "action_id": action_id,
            "tool": tool_name,
            "run_id": followup_id,
            "run_status": "active",
        },
    )

    context = routes_module.llm_service.get_context(session_id)
    if not getattr(context, "messages", None):
        try:
            history = conversation_store.load_conversation(session_id)
            for entry in history:
                role = entry.get("role")
                text = entry.get("text") or entry.get("content")
                if not role or not text:
                    continue
                meta = entry.get("metadata") or {}
                context.add_message(role, text, metadata=meta)
        except Exception:
            pass

    generation_ctx = ServiceContext(
        system_prompt=context.system_prompt,
        messages=list(context.messages),
        tools=list(context.tools),
        metadata=dict(context.metadata),
    )
    try:
        args_text = json.dumps(tool_args, ensure_ascii=False)
    except Exception:
        args_text = str(tool_args)
    try:
        result_text = json.dumps(tool_result, ensure_ascii=False)
    except Exception:
        result_text = str(tool_result)
    generation_ctx.add_message(
        "system",
        "Scheduled tool result:\n"
        f"- {tool_name} args={args_text} result={result_text}",
        metadata={"ephemeral": True, "tool_results": True, "scheduled": True},
    )
    generation_ctx.add_message(
        "user",
        prompt,
        metadata={"scheduled": True, "event_id": event_id, "action_id": action_id},
    )

    response_format = None
    try:
        if app.state.config.get("harmony_format"):
            response_format = "harmony"
    except Exception:
        response_format = None

    run_id = str(action.get("run_id") or "")
    step_id = _stable_composite_id("step", run_id or receipt_id, "provider-followup")
    checkpoint_id = _stable_composite_id("checkpoint", receipt_id, "tool-checkpoint")
    checkpoint_digest = _canonical_digest(
        {
            "run_id": run_id,
            "action_id": action_id,
            "tool_name": tool_name,
            "tool_result_digest": _canonical_digest(tool_result),
            "effect_watermark": _effect_watermark(action),
        }
    )
    try:
        await _hydrate_effect_state_from_ledger(store, receipt_id, action)
        previous_attempts = await asyncio.to_thread(
            store.list_attempts, receipt_id, limit=500
        )
    except Exception:
        logger.warning(
            "Could not read provider attempts for %s", receipt_id, exc_info=True
        )
        return {
            "status": "error",
            "error": "provider attempt ledger unavailable",
        }
    attempt_number = max(
        (
            int(item.get("attempt_number") or 0)
            for item in previous_attempts
            if isinstance(item, dict)
        ),
        default=0,
    )
    retry_limit = _provider_retry_limit(event)
    max_provider_attempts = 1 + retry_limit
    prior_attempt = previous_attempts[-1] if previous_attempts else None
    prior_attempt_status = (
        str(prior_attempt.get("status") or "").strip().lower()
        if isinstance(prior_attempt, dict)
        else ""
    )
    cancellation = await _checkpoint_cooperative_cancellation(
        app,
        event_id=event_id,
        event=event,
        action=action,
        action_id=action_id,
        occurrence_time=followup_occurrence,
        phase="followup",
        tool_invoked=True,
        provider_dispatch_uncertain=prior_attempt_status == "running",
    )
    if cancellation is not None:
        if (
            cancellation.get("status") == "interrupted_unknown"
            and prior_attempt_status == "running"
        ):
            try:
                await _close_interrupted_provider_attempt(
                    store,
                    prior_attempt,
                    effect_watermark_digest=_effect_watermark(action),
                    state_delta_certainty="unknown",
                )
            except Exception:
                logger.warning(
                    "Could not close cancelled interrupted follow-up attempt for %s",
                    receipt_id,
                    exc_info=True,
                )
        raise _ScheduledDispatchBlocked(cancellation)
    try:
        prior_attempt = await _close_interrupted_provider_attempt(
            store,
            prior_attempt,
            effect_watermark_digest=_effect_watermark(action),
            state_delta_certainty=_attempt_state_certainty(action),
        )
    except Exception:
        logger.warning(
            "Could not close interrupted provider attempt for %s",
            receipt_id,
            exc_info=True,
        )
        return {
            "status": "error",
            "error": "interrupted provider attempt could not be recorded",
        }
    prior_status = (
        str(prior_attempt.get("status") or "").strip().lower()
        if isinstance(prior_attempt, dict)
        else ""
    )
    if prior_status == "complete":
        prior_error_category = "provider_output_checkpoint_missing"
        prior_error_code = "worker_restart_after_generation"
    else:
        prior_error_category = (
            str(prior_attempt.get("error_category") or "provider_interrupted")
            if isinstance(prior_attempt, dict)
            else ""
        )
        prior_error_code = (
            str(prior_attempt.get("error_code") or "worker_restart")
            if isinstance(prior_attempt, dict)
            else ""
        )
    response: Dict[str, Any]
    final_error: Optional[BaseException] = None
    successful_attempt_record: Optional[Dict[str, Any]] = None
    while attempt_number < max_provider_attempts:
        cancellation = await _checkpoint_cooperative_cancellation(
            app,
            event_id=event_id,
            event=event,
            action=action,
            action_id=action_id,
            occurrence_time=followup_occurrence,
            phase="followup",
            tool_invoked=True,
        )
        if cancellation is not None:
            raise _ScheduledDispatchBlocked(cancellation)
        attempt_number += 1
        attempt_id = _stable_composite_id(
            "attempt", receipt_id, "provider-followup", attempt_number
        )
        previous_attempt_id = (
            str(prior_attempt.get("id") or "")
            if isinstance(prior_attempt, dict)
            else ""
        )
        started_at = time.time()
        attempt_record: Dict[str, Any] = {
            "id": attempt_id,
            "receipt_id": receipt_id,
            "run_id": run_id,
            "step_id": step_id,
            "attempt_number": attempt_number,
            "retry_number": max(0, attempt_number - 1),
            "is_retry": attempt_number > 1,
            "retry_of_attempt_id": previous_attempt_id or None,
            "retry_reason_code": prior_error_category or None,
            "checkpoint_id": checkpoint_id,
            "checkpoint_status": "tool_result_durable",
            "checkpoint_digest": checkpoint_digest,
            "effect_watermark_digest": _effect_watermark(action),
            "state_delta_certainty": _attempt_state_certainty(action),
            "status": "running",
            "created_at": started_at,
            "started_at": started_at,
        }
        try:
            await asyncio.to_thread(store.record_attempt, attempt_record)
        except Exception:
            logger.warning(
                "Could not start provider attempt %s", attempt_id, exc_info=True
            )
            return {
                "status": "error",
                "error": "provider attempt ledger unavailable",
            }
        cancellation = await _checkpoint_cooperative_cancellation(
            app,
            event_id=event_id,
            event=event,
            action=action,
            action_id=action_id,
            occurrence_time=followup_occurrence,
            phase="followup",
            tool_invoked=True,
        )
        if cancellation is not None:
            try:
                await _close_provider_attempt_without_dispatch(
                    store,
                    attempt_record,
                    cancelled=cancellation.get("status") == "cancelled",
                    state_delta_certainty=str(
                        cancellation.get("state_delta_certainty")
                        or _attempt_state_certainty(action)
                    ),
                )
            except Exception:
                logger.warning(
                    "Could not close blocked follow-up attempt %s",
                    attempt_record.get("id"),
                    exc_info=True,
                )
            raise _ScheduledDispatchBlocked(cancellation)

        attempt_ctx = ServiceContext(
            system_prompt=generation_ctx.system_prompt,
            messages=list(generation_ctx.messages),
            tools=list(generation_ctx.tools),
            metadata=dict(generation_ctx.metadata),
        )
        if attempt_number > 1:
            attempt_ctx.add_message(
                "system",
                _provider_recovery_envelope(
                    attempt_number=attempt_number,
                    max_attempts=max_provider_attempts,
                    prior_error_category=prior_error_category or "provider_interrupted",
                    prior_error_code=prior_error_code or "worker_restart",
                    action=action,
                    tool_name=tool_name,
                    output_message_id=followup_id,
                ),
                metadata={
                    "ephemeral": True,
                    "scheduled": True,
                    "provider_retry": True,
                    "attempt_number": attempt_number,
                },
            )
        try:
            response = await asyncio.to_thread(
                routes_module.llm_service.generate,
                [],
                session_id=session_id,
                response_format=response_format,
                context=attempt_ctx,
            )
        except Exception as exc:
            final_error = exc
            error_category, error_code, retryable = _provider_error_category(exc)
            will_retry = retryable and attempt_number < max_provider_attempts
            finished_at = time.time()
            failed_record = {
                **attempt_record,
                "status": "retry_scheduled" if will_retry else "error",
                "retryable": retryable,
                "error_category": error_category,
                "error_code": error_code,
                "state_delta_certainty": _attempt_state_certainty(action),
                "finished_at": finished_at,
                "retry_scheduled_at": finished_at if will_retry else None,
            }
            try:
                await asyncio.to_thread(store.record_attempt, failed_record)
            except Exception:
                logger.warning(
                    "Could not finish provider attempt %s", attempt_id, exc_info=True
                )
                return {
                    "status": "error",
                    "error": "provider attempt outcome could not be recorded",
                }
            prior_attempt = failed_record
            prior_error_category = error_category
            prior_error_code = error_code
            if will_retry:
                continue
            break

        returned_error = _provider_response_error(response)
        if returned_error is not None:
            error_category, error_code, retryable = returned_error
            final_error = RuntimeError(f"{error_category} ({error_code})")
            will_retry = retryable and attempt_number < max_provider_attempts
            finished_at = time.time()
            failed_record = {
                **attempt_record,
                "status": "retry_scheduled" if will_retry else "error",
                "retryable": retryable,
                "error_category": error_category,
                "error_code": error_code,
                "state_delta_certainty": _attempt_state_certainty(action),
                "finished_at": finished_at,
                "retry_scheduled_at": finished_at if will_retry else None,
            }
            try:
                await asyncio.to_thread(store.record_attempt, failed_record)
            except Exception:
                logger.warning(
                    "Could not finish provider attempt %s", attempt_id, exc_info=True
                )
                return {
                    "status": "error",
                    "error": "provider attempt outcome could not be recorded",
                }
            prior_attempt = failed_record
            prior_error_category = error_category
            prior_error_code = error_code
            if will_retry:
                continue
            break

        successful_attempt_record = attempt_record
        final_error = None
        break
    else:
        final_error = RuntimeError("provider retry budget exhausted")

    if final_error is not None:
        safe_provider_error = (
            f"provider follow-up failed: {prior_error_category or 'provider_error'} "
            f"({prior_error_code or 'provider_error'})"
        )
        await _publish_content(
            app,
            content=f"(scheduled prompt failed) {safe_provider_error}",
            chain_id=chain_id,
            message_id=followup_id,
            session_id=session_id,
            metadata={
                "scheduled": True,
                "event_id": event_id,
                "action_id": action_id,
                "tool": tool_name,
                "error": True,
                "run_id": followup_id,
                "run_status": "error",
            },
        )
        _update_conversation_entry(
            session_id=session_id,
            message_id=followup_id,
            updates={
                "text": "",
                "metadata": {"status": "error", "error": safe_provider_error},
            },
        )
        return {"status": "error", "error": safe_provider_error}

    text = str(response.get("text") or "")
    thought = response.get("thought")
    updates: Dict[str, Any] = {"text": text, "metadata": {"status": "complete"}}
    if isinstance(thought, str):
        updates["thought"] = thought
    if not _update_conversation_entry(
        session_id=session_id, message_id=followup_id, updates=updates
    ):
        missing_record = {
            **(successful_attempt_record or {}),
            "status": "output_checkpoint_missing",
            "retryable": False,
            "error_category": "provider_output_checkpoint_missing",
            "error_code": "canonical_conversation_write_failed",
            "state_delta_certainty": _attempt_state_certainty(action),
            "finished_at": time.time(),
        }
        try:
            await asyncio.to_thread(store.record_attempt, missing_record)
        except Exception:
            logger.warning(
                "Could not record missing provider output checkpoint for %s",
                receipt_id,
                exc_info=True,
            )
        await _publish_content(
            app,
            content="(scheduled prompt failed) provider output checkpoint missing",
            chain_id=chain_id,
            message_id=followup_id,
            session_id=session_id,
            metadata={
                "scheduled": True,
                "event_id": event_id,
                "action_id": action_id,
                "tool": tool_name,
                "error": True,
                "run_id": followup_id,
                "run_status": "error",
            },
        )
        return {
            "status": "error",
            "error": "provider output checkpoint missing",
            "reconcile_required": True,
            "error_category": "provider_output_checkpoint_missing",
            "state_delta_certainty": "unknown",
        }
    complete_record = {
        **(successful_attempt_record or {}),
        "status": "complete",
        "retryable": False,
        "state_delta_certainty": _attempt_state_certainty(action),
        "provider_response_id": response.get("response_id") or response.get("id"),
        "finish_reason": response.get("finish_reason"),
        "finished_at": time.time(),
    }
    try:
        await asyncio.to_thread(store.record_attempt, complete_record)
    except Exception:
        logger.warning(
            "Could not finish provider attempt %s",
            complete_record.get("id"),
            exc_info=True,
        )
        return {
            "status": "error",
            "error": "provider attempt outcome could not be recorded",
            "reconcile_required": True,
            "error_category": "provider_attempt_journal_unavailable",
            "state_delta_certainty": "unknown",
        }
    await _publish_content(
        app,
        content=text,
        chain_id=chain_id,
        message_id=followup_id,
        session_id=session_id,
        metadata={
            "scheduled": True,
            "event_id": event_id,
            "action_id": action_id,
            "tool": tool_name,
            "run_id": followup_id,
            "run_status": "complete",
        },
    )
    return {"status": "complete", "result": text}


async def _resume_pending_tool_followup(
    app: FastAPI,
    *,
    event_id: str,
    event: Dict[str, Any],
    action: Dict[str, Any],
    action_id: str,
    occurrence_time: Optional[float],
    recovering: bool,
) -> Dict[str, Any]:
    """Resume the model phase after a tool result has been stored durably."""

    if not _claim_pending_followup(
        event_id,
        event,
        action,
        action_id=action_id,
        recovering=recovering,
    ):
        return {
            "status": "already_claimed",
            "event_id": event_id,
            "action_id": action_id,
            "occurrence_at": occurrence_time,
            "tool_invoked": True,
        }

    control_block = await _checkpoint_cooperative_cancellation(
        app,
        event_id=event_id,
        event=event,
        action=action,
        action_id=action_id,
        occurrence_time=occurrence_time,
        phase="followup",
        tool_invoked=True,
    )
    if control_block is not None:
        return control_block

    running_followup = {
        "status": "followup_running",
        "phase": "followup",
        "event_id": event_id,
        "action_id": action_id,
        "occurrence_at": occurrence_time,
        "result": action.get("result"),
        "tool_invoked": True,
    }
    _append_run_record(
        event,
        event_id=event_id,
        action=action,
        action_id=action_id,
        occurrence_time=occurrence_time,
        result=running_followup,
    )
    if not await _persist_claimed_event(
        app,
        event_id,
        event,
        action=action,
        action_id=action_id,
        require_active=True,
    ):
        return {
            "status": "error",
            "event_id": event_id,
            "action_id": action_id,
            "occurrence_at": occurrence_time,
            "tool_invoked": True,
            "followup_invoked": False,
            "error": (
                "The follow-up receipt could not be recorded; the provider "
                "follow-up was not sent."
            ),
        }

    prompt = _normalize_prompt(action.get("followup_prompt") or action.get("prompt"))
    tool_name = str(action.get("followup_tool_name") or action.get("name") or "tool")
    raw_tool_args = action.get("followup_tool_args")
    if not isinstance(raw_tool_args, dict):
        raw_tool_args = action.get("args")
    tool_args = sanitize_args(raw_tool_args if isinstance(raw_tool_args, dict) else {})
    tool_result = action.get("result")
    session_id = action.get("session_id")
    chain_id = action.get("chain_id") or session_id

    if not prompt:
        followup: Dict[str, Any] = {
            "status": "error",
            "error": "durable follow-up is missing its prompt",
        }
    else:
        try:
            followup = await _run_prompt_followup(
                app,
                session_id=session_id,
                chain_id=chain_id,
                prompt=prompt,
                tool_name=tool_name,
                tool_args=tool_args,
                tool_result=tool_result,
                event_id=event_id,
                event=event,
                action=action,
                action_id=action_id,
                parent_session_id=_origin_session_id(action),
                parent_message_id=_origin_message_id(action),
                followup_message_id=action.get("followup_message_id"),
            )
        except _ScheduledDispatchBlocked as blocked:
            return dict(blocked.result)
        except asyncio.CancelledError:
            detail = (
                "Float stopped waiting for the scheduled follow-up during shutdown "
                "or after the wait cap; its provider call may still finish outside "
                "the scheduler. The tool was not repeated."
            )
            action["status"] = "interrupted_unknown"
            action["followup_status"] = "interrupted_unknown"
            action["followup_error"] = detail
            action["followup_completed_at"] = time.time()
            _mark_action_occurrence(action, occurrence_time)
            _mark_event_prompted(event)
            interrupted = {
                "status": "interrupted_unknown",
                "event_id": event_id,
                "action_id": action_id,
                "occurrence_at": occurrence_time,
                "result": tool_result,
                "tool_invoked": True,
                "error": detail,
            }
            _append_run_record(
                event,
                event_id=event_id,
                action=action,
                action_id=action_id,
                occurrence_time=occurrence_time,
                result=interrupted,
            )
            await asyncio.shield(
                _persist_claimed_event(
                    app,
                    event_id,
                    event,
                    action=action,
                    action_id=action_id,
                )
            )
            raise
        except Exception:
            logger.warning("Scheduled follow-up failed", exc_info=True)
            followup = {"status": "error", "error": "follow-up runtime error"}

    action["status"] = "invoked"
    action["followup_status"] = str(followup.get("status") or "complete")
    action["followup_completed_at"] = time.time()
    _mark_action_occurrence(action, occurrence_time)
    _mark_event_prompted(event)
    if action["followup_status"] == "error":
        action["followup_error"] = str(followup.get("error") or "follow-up failed")
        reconcile_required = bool(followup.get("reconcile_required"))
        action["reconcile_required"] = reconcile_required
        result_payload = {
            "status": "error",
            "event_id": event_id,
            "action_id": action_id,
            "occurrence_at": occurrence_time,
            "result": tool_result,
            "tool_invoked": True,
            "error": (
                "Tool invoked, but its provider output checkpoint needs "
                "reconciliation."
                if reconcile_required
                else "Tool invoked, but its follow-up failed."
            ),
            "reconcile_required": reconcile_required,
            "state_delta_certainty": (
                "unknown" if reconcile_required else _attempt_state_certainty(action)
            ),
        }
    else:
        action["followup_status"] = "complete"
        action.pop("followup_error", None)
        action.pop("reconcile_required", None)
        result_payload = {
            "status": "invoked",
            "event_id": event_id,
            "action_id": action_id,
            "occurrence_at": occurrence_time,
            "result": tool_result,
            "tool_invoked": True,
            "followup_status": "complete",
        }
    _append_run_record(
        event,
        event_id=event_id,
        action=action,
        action_id=action_id,
        occurrence_time=occurrence_time,
        result=result_payload,
    )
    await _persist_claimed_event(
        app,
        event_id,
        event,
        action=action,
        action_id=action_id,
    )
    return result_payload


async def _run_prompt_action(
    app: FastAPI,
    *,
    event: Dict[str, Any],
    session_id: Optional[str],
    chain_id: Optional[str],
    prompt: str,
    event_id: str,
    action_id: str,
    action: Optional[Dict[str, Any]] = None,
    parent_session_id: Optional[str] = None,
    parent_message_id: Optional[str] = None,
) -> Optional[str]:
    """Execute a scheduled prompt-only action by asking the model."""
    action = action if isinstance(action, dict) else {}
    session_id = _ensure_task_conversation(
        session_id=session_id,
        event=event,
        event_id=event_id,
        parent_session_id=parent_session_id,
        parent_message_id=parent_message_id,
    )
    chain_id = chain_id or session_id

    try:
        from app import routes as routes_module
        from app.services import ModelContext as ServiceContext
        from app.utils import conversation_store
    except Exception:
        return None

    now_ts = time.time()
    run_id = str(action.get("run_id") or "")
    checkpoint_occurrence: Optional[float] = None
    prompt_checkpoint = action.get("prompt_checkpoint")
    prompt_checkpoint = (
        dict(prompt_checkpoint) if isinstance(prompt_checkpoint, Mapping) else {}
    )
    if run_id:
        checkpoint_occurrence = _coerce_epoch_seconds(
            prompt_checkpoint.get("occurrence_at")
        )
        if (
            checkpoint_occurrence is None
            or not _prompt_checkpoint_matches(
                event,
                action,
                event_id=event_id,
                action_id=action_id,
                occurrence_time=checkpoint_occurrence,
                prompt=prompt,
            )
            or prompt_checkpoint.get("session_id") != session_id
        ):
            raise RuntimeError("scheduled prompt checkpoint is missing or invalid")
        session_id = str(prompt_checkpoint["session_id"])
        chain_id = str(prompt_checkpoint["chain_id"])
        followup_id = str(prompt_checkpoint["output_message_id"])
        user_entry_id = str(prompt_checkpoint["user_message_id"])
        receipt_id = str(prompt_checkpoint["receipt_id"])
        checkpoint_id = str(prompt_checkpoint["checkpoint_id"])
        checkpoint_digest = str(prompt_checkpoint["checkpoint_digest"])
    else:
        followup_id = _stable_composite_id(
            "scheduled-message",
            event_id,
            action_id,
            int(now_ts * 1000),
            "prompt",
        )
        user_entry_id = f"{followup_id}:user"
        receipt_id = _receipt_id(event_id, action_id, action)
        checkpoint_id = _stable_composite_id(
            "checkpoint", receipt_id, "prompt-checkpoint"
        )
        checkpoint_digest = _canonical_digest(
            {
                "run_id": run_id,
                "action_id": action_id,
                "prompt_digest": _canonical_digest(prompt),
            }
        )
    store = _work_run_store(app)
    existing_output = _load_conversation_entry(session_id, followup_id)
    if run_id:
        cancellation = await _checkpoint_cooperative_cancellation(
            app,
            event_id=event_id,
            event=event,
            action=action,
            action_id=action_id,
            occurrence_time=checkpoint_occurrence,
            phase="prompt",
            tool_invoked=False,
        )
        if cancellation is not None:
            raise _ScheduledDispatchBlocked(cancellation)
    if _conversation_entry_is_complete(existing_output):
        try:
            await _close_attempt_from_canonical_output(
                store,
                receipt_id,
                effect_watermark_digest=_canonical_digest([]),
                state_delta_certainty="no_change_since_checkpoint",
            )
        except Exception as exc:
            raise _ProviderOutputCheckpointError(
                "canonical output recovery could not be recorded"
            ) from exc
        return str(existing_output.get("text") or "")
    existing_user = _load_conversation_entry(session_id, user_entry_id)
    context = routes_module.llm_service.get_context(session_id)
    if not getattr(context, "messages", None):
        try:
            history = conversation_store.load_conversation(session_id)
            for entry in history:
                role = entry.get("role")
                text = entry.get("text") or entry.get("content")
                if not role or not text:
                    continue
                meta = entry.get("metadata") or {}
                context.add_message(role, text, metadata=meta)
        except Exception:
            pass

    # Load prior history before persisting this turn. Otherwise a new task chat
    # reloads the just-saved prompt and then adds it to the generation context a
    # second time below.
    if existing_user is None and not _append_conversation_entry(
        session_id=session_id,
        entry={
            "id": user_entry_id,
            "role": "user",
            "text": prompt,
            "timestamp": now_ts,
            "metadata": {
                "scheduled": True,
                "event_id": event_id,
                "action_id": action_id,
                "prompt_action": True,
            },
        },
    ):
        raise _ProviderOutputCheckpointError(
            "canonical output placeholder could not be recorded"
        )
    if existing_output is None and not _append_conversation_entry(
        session_id=session_id,
        entry={
            "id": followup_id,
            "role": "ai",
            "text": "",
            "thought": "",
            "metadata": {"status": "pending", "scheduled": True, "prompt_action": True},
            "timestamp": now_ts,
        },
    ):
        raise _ProviderOutputCheckpointError(
            "canonical output placeholder could not be recorded"
        )
    await _publish_content(
        app,
        content="Scheduled prompt running.",
        chain_id=chain_id,
        message_id=followup_id,
        session_id=session_id,
        metadata={
            "scheduled": True,
            "event_id": event_id,
            "action_id": action_id,
            "prompt_action": True,
            "run_id": followup_id,
            "run_status": "active",
        },
    )

    generation_ctx = ServiceContext(
        system_prompt=context.system_prompt,
        messages=list(context.messages),
        tools=list(context.tools),
        metadata=dict(context.metadata),
    )
    if existing_user is None:
        generation_ctx.add_message(
            "user",
            prompt,
            metadata={
                "scheduled": True,
                "event_id": event_id,
                "action_id": action_id,
                "prompt_action": True,
            },
        )

    response_format = None
    try:
        if app.state.config.get("harmony_format"):
            response_format = "harmony"
    except Exception:
        response_format = None

    step_id = _stable_composite_id("step", run_id or receipt_id, "provider-prompt")
    try:
        previous_attempts = await asyncio.to_thread(
            store.list_attempts, receipt_id, limit=500
        )
    except Exception as exc:
        await _publish_content(
            app,
            content=f"(scheduled prompt failed) {exc}",
            chain_id=chain_id,
            message_id=followup_id,
            session_id=session_id,
            metadata={
                "scheduled": True,
                "event_id": event_id,
                "action_id": action_id,
                "prompt_action": True,
                "error": True,
                "run_id": followup_id,
                "run_status": "error",
            },
        )
        raise RuntimeError("provider attempt ledger unavailable") from exc
    attempt_number = max(
        (
            int(item.get("attempt_number") or 0)
            for item in previous_attempts
            if isinstance(item, dict)
        ),
        default=0,
    )
    max_provider_attempts = 1 + _provider_retry_limit(event)
    prior_attempt = previous_attempts[-1] if previous_attempts else None
    prior_attempt_status = (
        str(prior_attempt.get("status") or "").strip().lower()
        if isinstance(prior_attempt, dict)
        else ""
    )
    if run_id:
        cancellation = await _checkpoint_cooperative_cancellation(
            app,
            event_id=event_id,
            event=event,
            action=action,
            action_id=action_id,
            occurrence_time=checkpoint_occurrence,
            phase="prompt",
            tool_invoked=False,
            provider_dispatch_uncertain=prior_attempt_status == "running",
        )
        if cancellation is not None:
            if (
                cancellation.get("status") == "interrupted_unknown"
                and prior_attempt_status == "running"
            ):
                try:
                    await _close_interrupted_provider_attempt(
                        store,
                        prior_attempt,
                        effect_watermark_digest=_canonical_digest([]),
                        state_delta_certainty="unknown",
                    )
                except Exception:
                    logger.warning(
                        "Could not close cancelled interrupted prompt attempt for %s",
                        receipt_id,
                        exc_info=True,
                    )
            raise _ScheduledDispatchBlocked(cancellation)
    try:
        prior_attempt = await _close_interrupted_provider_attempt(
            store,
            prior_attempt,
            effect_watermark_digest=_canonical_digest([]),
            state_delta_certainty="no_change_since_checkpoint",
        )
    except Exception as exc:
        raise RuntimeError(
            "interrupted provider attempt could not be recorded"
        ) from exc
    prior_status = (
        str(prior_attempt.get("status") or "").strip().lower()
        if isinstance(prior_attempt, dict)
        else ""
    )
    if prior_status == "complete":
        prior_error_category = "provider_output_checkpoint_missing"
        prior_error_code = "worker_restart_after_generation"
    else:
        prior_error_category = (
            str(prior_attempt.get("error_category") or "provider_interrupted")
            if isinstance(prior_attempt, dict)
            else ""
        )
        prior_error_code = (
            str(prior_attempt.get("error_code") or "worker_restart")
            if isinstance(prior_attempt, dict)
            else ""
        )
    response: Dict[str, Any]
    final_error: Optional[BaseException] = None
    successful_attempt_record: Optional[Dict[str, Any]] = None
    while attempt_number < max_provider_attempts:
        if run_id:
            cancellation = await _checkpoint_cooperative_cancellation(
                app,
                event_id=event_id,
                event=event,
                action=action,
                action_id=action_id,
                occurrence_time=checkpoint_occurrence,
                phase="prompt",
                tool_invoked=False,
            )
            if cancellation is not None:
                raise _ScheduledDispatchBlocked(cancellation)
        attempt_number += 1
        attempt_id = _stable_composite_id(
            "attempt", receipt_id, "provider-prompt", attempt_number
        )
        previous_attempt_id = (
            str(prior_attempt.get("id") or "")
            if isinstance(prior_attempt, dict)
            else ""
        )
        started_at = time.time()
        attempt_record: Dict[str, Any] = {
            "id": attempt_id,
            "receipt_id": receipt_id,
            "run_id": run_id,
            "step_id": step_id,
            "attempt_number": attempt_number,
            "retry_number": max(0, attempt_number - 1),
            "is_retry": attempt_number > 1,
            "retry_of_attempt_id": previous_attempt_id or None,
            "retry_reason_code": prior_error_category or None,
            "checkpoint_id": checkpoint_id,
            "checkpoint_status": "prompt_durable",
            "checkpoint_digest": checkpoint_digest,
            "effect_watermark_digest": _canonical_digest([]),
            "state_delta_certainty": "no_change_since_checkpoint",
            "status": "running",
            "created_at": started_at,
            "started_at": started_at,
        }
        try:
            await asyncio.to_thread(store.record_attempt, attempt_record)
        except Exception as exc:
            raise RuntimeError("provider attempt ledger unavailable") from exc
        if run_id:
            cancellation = await _checkpoint_cooperative_cancellation(
                app,
                event_id=event_id,
                event=event,
                action=action,
                action_id=action_id,
                occurrence_time=checkpoint_occurrence,
                phase="prompt",
                tool_invoked=False,
            )
            if cancellation is not None:
                try:
                    await _close_provider_attempt_without_dispatch(
                        store,
                        attempt_record,
                        cancelled=cancellation.get("status") == "cancelled",
                        state_delta_certainty=str(
                            cancellation.get("state_delta_certainty")
                            or "confirmed_no_change"
                        ),
                    )
                except Exception:
                    logger.warning(
                        "Could not close blocked prompt attempt %s",
                        attempt_record.get("id"),
                        exc_info=True,
                    )
                raise _ScheduledDispatchBlocked(cancellation)
        attempt_ctx = ServiceContext(
            system_prompt=generation_ctx.system_prompt,
            messages=list(generation_ctx.messages),
            tools=list(generation_ctx.tools),
            metadata=dict(generation_ctx.metadata),
        )
        if attempt_number > 1:
            attempt_ctx.add_message(
                "system",
                _prompt_provider_recovery_envelope(
                    attempt_number=attempt_number,
                    max_attempts=max_provider_attempts,
                    prior_error_category=prior_error_category or "provider_interrupted",
                    prior_error_code=prior_error_code or "worker_restart",
                    output_message_id=followup_id,
                ),
                metadata={
                    "ephemeral": True,
                    "scheduled": True,
                    "provider_retry": True,
                    "attempt_number": attempt_number,
                },
            )
        try:
            response = await asyncio.to_thread(
                routes_module.llm_service.generate,
                [],
                session_id=session_id,
                response_format=response_format,
                context=attempt_ctx,
            )
        except Exception as exc:
            final_error = exc
            error_category, error_code, retryable = _provider_error_category(exc)
            will_retry = retryable and attempt_number < max_provider_attempts
            finished_at = time.time()
            failed_record = {
                **attempt_record,
                "status": "retry_scheduled" if will_retry else "error",
                "retryable": retryable,
                "error_category": error_category,
                "error_code": error_code,
                "finished_at": finished_at,
                "retry_scheduled_at": finished_at if will_retry else None,
            }
            try:
                await asyncio.to_thread(store.record_attempt, failed_record)
            except Exception as ledger_exc:
                raise RuntimeError(
                    "provider attempt outcome could not be recorded"
                ) from ledger_exc
            prior_attempt = failed_record
            prior_error_category = error_category
            prior_error_code = error_code
            if will_retry:
                continue
            break
        returned_error = _provider_response_error(response)
        if returned_error is not None:
            error_category, error_code, retryable = returned_error
            final_error = RuntimeError(f"{error_category} ({error_code})")
            will_retry = retryable and attempt_number < max_provider_attempts
            finished_at = time.time()
            failed_record = {
                **attempt_record,
                "status": "retry_scheduled" if will_retry else "error",
                "retryable": retryable,
                "error_category": error_category,
                "error_code": error_code,
                "finished_at": finished_at,
                "retry_scheduled_at": finished_at if will_retry else None,
            }
            try:
                await asyncio.to_thread(store.record_attempt, failed_record)
            except Exception as ledger_exc:
                raise RuntimeError(
                    "provider attempt outcome could not be recorded"
                ) from ledger_exc
            prior_attempt = failed_record
            prior_error_category = error_category
            prior_error_code = error_code
            if will_retry:
                continue
            break
        successful_attempt_record = attempt_record
        final_error = None
        break
    else:
        final_error = RuntimeError("provider retry budget exhausted")

    if final_error is not None:
        safe_provider_error = (
            f"provider prompt failed: {prior_error_category or 'provider_error'} "
            f"({prior_error_code or 'provider_error'})"
        )
        await _publish_content(
            app,
            content=f"(scheduled prompt failed) {safe_provider_error}",
            chain_id=chain_id,
            message_id=followup_id,
            session_id=session_id,
            metadata={
                "scheduled": True,
                "event_id": event_id,
                "action_id": action_id,
                "prompt_action": True,
                "error": True,
                "run_id": followup_id,
                "run_status": "error",
            },
        )
        _update_conversation_entry(
            session_id=session_id,
            message_id=followup_id,
            updates={
                "text": "",
                "metadata": {"status": "error", "error": safe_provider_error},
            },
        )
        raise RuntimeError(safe_provider_error) from final_error

    text = str(response.get("text") or "")
    thought = response.get("thought")
    updates: Dict[str, Any] = {"text": text, "metadata": {"status": "complete"}}
    if isinstance(thought, str):
        updates["thought"] = thought
    if not _update_conversation_entry(
        session_id=session_id, message_id=followup_id, updates=updates
    ):
        missing_record = {
            **(successful_attempt_record or {}),
            "status": "output_checkpoint_missing",
            "retryable": False,
            "error_category": "provider_output_checkpoint_missing",
            "error_code": "canonical_conversation_write_failed",
            "finished_at": time.time(),
        }
        try:
            await asyncio.to_thread(store.record_attempt, missing_record)
        except Exception as exc:
            raise _ProviderOutputCheckpointError(
                "provider output checkpoint and attempt outcome are unavailable"
            ) from exc
        raise _ProviderOutputCheckpointError("provider output checkpoint missing")
    complete_record = {
        **(successful_attempt_record or {}),
        "status": "complete",
        "retryable": False,
        "provider_response_id": response.get("response_id") or response.get("id"),
        "finish_reason": response.get("finish_reason"),
        "finished_at": time.time(),
    }
    try:
        await asyncio.to_thread(store.record_attempt, complete_record)
    except Exception as exc:
        raise _ProviderOutputCheckpointError(
            "provider attempt outcome could not be recorded"
        ) from exc
    await _publish_content(
        app,
        content=text,
        chain_id=chain_id,
        message_id=followup_id,
        session_id=session_id,
        metadata={
            "scheduled": True,
            "event_id": event_id,
            "action_id": action_id,
            "prompt_action": True,
            "run_id": followup_id,
            "run_status": "complete",
        },
    )
    return text


async def _persist_reconciliation_block(
    app: FastAPI,
    *,
    event_id: str,
    event: Dict[str, Any],
    action: Dict[str, Any],
    action_id: str,
    occurrence_time: Optional[float],
    detail: str,
) -> Dict[str, Any]:
    """Pause a tool action without creating another effect or receipt."""

    action["status"] = "reconcile_required"
    action["reconcile_required"] = True
    action["error"] = detail
    action["executed_at"] = time.time()
    _mark_action_occurrence(action, occurrence_time)
    _mark_event_prompted(event)
    persisted = await _persist_event(app, event_id, event)
    return {
        "status": "reconcile_required",
        "event_id": event_id,
        "action_id": action_id,
        "occurrence_at": occurrence_time,
        "tool_invoked": False,
        "reconcile_required": True,
        "state_delta_certainty": "unknown",
        "blocked_state_persisted": persisted,
        "error": detail,
    }


async def _persist_effect_journal_unavailable(
    app: FastAPI,
    *,
    event_id: str,
    event: Dict[str, Any],
    action: Dict[str, Any],
    action_id: str,
    occurrence_time: Optional[float],
) -> Dict[str, Any]:
    """Fail closed before dispatch while leaving the due action retryable."""

    detail = (
        "The authoritative effect journal is unavailable; the tool was not "
        "dispatched and will be checked again before a later attempt."
    )
    action["status"] = "effect_journal_unavailable"
    action["error"] = detail
    action.pop("reconcile_required", None)
    persisted = await _persist_event(app, event_id, event)
    return {
        "status": "effect_journal_unavailable",
        "event_id": event_id,
        "action_id": action_id,
        "occurrence_at": occurrence_time,
        "tool_invoked": False,
        "retryable": True,
        "state_delta_certainty": "confirmed_no_change",
        "blocked_state_persisted": persisted,
        "error": detail,
    }


async def _run_tool_action(
    app: FastAPI,
    *,
    event_id: str,
    event: Dict[str, Any],
    action: Dict[str, Any],
    action_id: str,
    occurrence_time: Optional[float],
    force: bool,
) -> Dict[str, Any]:
    status = str(action.get("status") or "").lower()
    if status == "followup_pending":
        pending_occurrence = _coerce_epoch_seconds(action.get("running_occurrence_at"))
        return await _resume_pending_tool_followup(
            app,
            event_id=event_id,
            event=event,
            action=action,
            action_id=action_id,
            occurrence_time=(
                pending_occurrence
                if pending_occurrence is not None
                else occurrence_time
            ),
            recovering=True,
        )
    if status == "followup_running":
        return {
            "status": "already_running",
            "event_id": event_id,
            "action_id": action_id,
            "phase": "followup",
            "tool_invoked": True,
        }
    start = _event_start_time(event)
    if not force and occurrence_time is None:
        return {
            "status": "not_due",
            "event_id": event_id,
            "action_id": action_id,
            "start_time": start,
        }

    executed_at = _coerce_epoch_seconds(action.get("executed_at"))
    if not force and _action_has_run_for_occurrence(event, action, occurrence_time):
        return {
            "status": "already_executed",
            "event_id": event_id,
            "action_id": action_id,
            "executed_at": executed_at,
            "occurrence_at": occurrence_time,
        }
    if status in {"running"} and not force:
        return {
            "status": "already_running",
            "event_id": event_id,
            "action_id": action_id,
        }

    if _latest_action_status(event_id, action_id) in {"running", "followup_running"}:
        return {
            "status": "already_claimed",
            "event_id": event_id,
            "action_id": action_id,
        }

    calendar_requires_reconciliation = _action_requires_reconciliation(
        event, action, action_id
    )
    if calendar_requires_reconciliation:
        return await _persist_reconciliation_block(
            app,
            event_id=event_id,
            event=event,
            action=action,
            action_id=action_id,
            occurrence_time=occurrence_time,
            detail=(
                "A prior effect is uncertain; reconcile its external state before "
                "another tool invocation."
            ),
        )
    try:
        ledger_requires_reconciliation = await _ledger_requires_effect_reconciliation(
            app, event_id=event_id, action_id=action_id
        )
    except Exception:
        logger.warning(
            "Could not inspect authoritative effects for %s/%s",
            event_id,
            action_id,
            exc_info=True,
        )
        return await _persist_effect_journal_unavailable(
            app,
            event_id=event_id,
            event=event,
            action=action,
            action_id=action_id,
            occurrence_time=occurrence_time,
        )
    if ledger_requires_reconciliation:
        return await _persist_reconciliation_block(
            app,
            event_id=event_id,
            event=event,
            action=action,
            action_id=action_id,
            occurrence_time=occurrence_time,
            detail=(
                "A prior effect is uncertain; reconcile its external state before "
                "another tool invocation."
            ),
        )

    if occurrence_time is None:
        return {
            "status": "not_due",
            "event_id": event_id,
            "action_id": action_id,
            "start_time": start,
        }
    claim = _claim_authorized_tool_run(
        event_id,
        event,
        action,
        action_id=action_id,
        occurrence_time=occurrence_time,
        force=force,
    )
    claim_status = str(claim.get("status") or "")
    if claim_status == AUTHORIZATION_REQUIRED:
        receipt_durable = await _project_event_work_runs(app, event_id, event)
        projection_retry_released = False
        if not receipt_durable:
            projection_retry_released = _release_unprojected_authorization_receipt(
                event,
                event_id=event_id,
                action_id=action_id,
                run_id=str(claim.get("run_id") or ""),
                authorization=(
                    claim.get("authorization")
                    if isinstance(claim.get("authorization"), Mapping)
                    else {}
                ),
            )
        await _reindex_calendar_event(app, event_id, event)
        return {
            "status": AUTHORIZATION_REQUIRED,
            "phase": "authorization",
            "event_id": event_id,
            "action_id": action_id,
            "occurrence_at": occurrence_time,
            "run_id": claim.get("run_id"),
            "tool_invoked": False,
            "state_delta_certainty": "confirmed_no_change",
            "authorization": _authorization_receipt_snapshot(
                claim.get("authorization")
            ),
            "receipt_durable": receipt_durable,
            "retryable": projection_retry_released,
        }
    if claim_status != "claimed":
        return {
            "status": claim_status or "already_claimed",
            "event_id": event_id,
            "action_id": action_id,
            "tool_invoked": False,
        }
    authorization_request = claim.get("request")
    authorization_request = (
        dict(authorization_request)
        if isinstance(authorization_request, Mapping)
        else {}
    )
    initial_receipt_durable = await _project_event_work_runs(app, event_id, event)
    if not initial_receipt_durable:
        released = _release_unprojected_claim(
            event,
            event_id=event_id,
            action_id=action_id,
            run_id=str(action.get("run_id") or ""),
            occurrence_time=occurrence_time,
        )
        if not released:
            logger.warning(
                "Could not release unprojected scheduled claim %s/%s",
                event_id,
                action_id,
            )
        return {
            "status": "error",
            "event_id": event_id,
            "action_id": action_id,
            "occurrence_at": occurrence_time,
            "tool_invoked": False,
            "retryable": released,
            "state_delta_certainty": "confirmed_no_change",
            "error": (
                "The running receipt could not be recorded; the tool was not "
                "invoked."
            ),
        }
    await _reindex_calendar_event(app, event_id, event)

    name = action.get("name")
    user = str(action.get("user") or "scheduler")
    _preserve_action_origin(action)
    session_id, message_id, chain_id = _resolve_action_conversation(action)
    prompt = _normalize_prompt(action.get("prompt"))

    if not isinstance(name, str) or not name.strip():
        detail = "scheduled action missing tool name"
        raw_args = action.get("args")
        args = sanitize_args(raw_args if isinstance(raw_args, dict) else {})
        action["status"] = "error"
        action["error"] = detail
        action["executed_at"] = time.time()
        _mark_action_occurrence(action, occurrence_time)
        _mark_event_prompted(event)
        result_payload = {
            "status": "error",
            "event_id": event_id,
            "action_id": action_id,
            "error": detail,
        }
        _append_run_record(
            event,
            event_id=event_id,
            action=action,
            action_id=action_id,
            occurrence_time=occurrence_time,
            result=result_payload,
        )
        await _persist_claimed_event(
            app,
            event_id,
            event,
            action=action,
            action_id=action_id,
        )
        await _publish_tool_status(
            app,
            tool_id=action_id,
            name="tool",
            args=args,
            status="error",
            result=detail,
            chain_id=chain_id,
            message_id=message_id,
            session_id=session_id,
        )
        _append_tool_to_conversation(
            session_id=session_id,
            message_id=message_id,
            request_id=action_id,
            name="tool",
            args=args,
            status="error",
            result=detail,
        )
        return result_payload

    raw_args = action.get("args")
    raw_args = raw_args if isinstance(raw_args, dict) else {}
    try:
        from app.utils.tool_args import normalize_and_sanitize_tool_args

        _, args = normalize_and_sanitize_tool_args(name.strip(), raw_args)
    except ValueError:
        logger.info("Scheduled tool arguments failed validation", exc_info=True)
        detail = "Scheduled tool arguments failed validation."
        action["status"] = "error"
        action["error"] = detail
        action["executed_at"] = time.time()
        _mark_action_occurrence(action, occurrence_time)
        _mark_event_prompted(event)
        result_payload = {
            "status": "error",
            "event_id": event_id,
            "action_id": action_id,
            "error": detail,
        }
        _append_run_record(
            event,
            event_id=event_id,
            action=action,
            action_id=action_id,
            occurrence_time=occurrence_time,
            result=result_payload,
        )
        await _persist_claimed_event(
            app,
            event_id,
            event,
            action=action,
            action_id=action_id,
        )
        await _publish_tool_status(
            app,
            tool_id=action_id,
            name=name.strip(),
            args=sanitize_args(raw_args),
            status="error",
            result=detail,
            chain_id=chain_id,
            message_id=message_id,
            session_id=session_id,
        )
        _append_tool_to_conversation(
            session_id=session_id,
            message_id=message_id,
            request_id=action_id,
            name=name.strip(),
            args=sanitize_args(raw_args),
            status="error",
            result=detail,
        )
        return result_payload
    except Exception:
        args = sanitize_args(raw_args)

    # Route new-chat work only after the durable claim succeeds.
    _preserve_action_origin(action)
    session_id, message_id, chain_id = _resolve_action_conversation(action)
    prompt = _normalize_prompt(action.get("prompt"))
    if prompt and not session_id:
        session_id = _ensure_task_conversation(
            session_id=session_id,
            event=event,
            event_id=event_id,
            parent_session_id=_origin_session_id(action),
            parent_message_id=_origin_message_id(action),
        )
        chain_id = chain_id or session_id
        message_id = message_id or chain_id
        action["session_id"] = session_id
        action["chain_id"] = chain_id
        action["message_id"] = message_id

    effect_policy = _scheduled_tool_effect_policy(name, args)
    effect_record: Optional[Dict[str, Any]] = None
    effect_dispatched = False
    cancellation = await _checkpoint_cooperative_cancellation(
        app,
        event_id=event_id,
        event=event,
        action=action,
        action_id=action_id,
        occurrence_time=occurrence_time,
        phase="tool",
        tool_invoked=False,
    )
    if cancellation is not None:
        return cancellation
    if effect_policy is not None:
        receipt_id = _receipt_id(event_id, action_id, action)
        run_id = str(action.get("run_id") or "")
        effect_id = _stable_composite_id("effect", receipt_id, action_id)
        intended_at = time.time()
        effect_record = {
            "id": effect_id,
            "receipt_id": receipt_id,
            "run_id": run_id,
            "step_id": _stable_composite_id("step", run_id or receipt_id, "tool"),
            "tool_name": name,
            "tool_call_id": action_id,
            "tool_kind": effect_policy["tool_kind"],
            "effect_scope": effect_policy["effect_scope"],
            "replay_policy": "never_auto_replay",
            "status": "intent",
            "certainty": "pending",
            "redacted_target": effect_policy["redacted_target"],
            "argument_digest": effect_policy["argument_digest"],
            "idempotency_key": effect_policy.get("idempotency_key"),
            "approval_required": bool(authorization_request.get("approval_required")),
            "permission_scopes": list(
                authorization_request.get("required_scopes") or []
            ),
            "approval_snapshot": _effect_approval_snapshot(
                action, authorization_request
            ),
            "permission_snapshot": _effect_permission_snapshot(
                authorization_request, checked_at=intended_at
            ),
            "reconcile_required": False,
            "intended_at": intended_at,
        }
        try:
            await _record_effect_transition(app, effect_record, create_only=True)
        except Exception:
            logger.warning(
                "Could not record effect intent %s",
                effect_record.get("id"),
                exc_info=True,
            )
            return await _fail_before_tool_invocation(
                app,
                event_id=event_id,
                event=event,
                action=action,
                action_id=action_id,
                occurrence_time=occurrence_time,
                name=name,
                args=args,
                detail="Effect intent could not be recorded; the tool was not invoked.",
                chain_id=chain_id,
                message_id=message_id,
                session_id=session_id,
            )
        cancellation = await _checkpoint_cooperative_cancellation(
            app,
            event_id=event_id,
            event=event,
            action=action,
            action_id=action_id,
            occurrence_time=occurrence_time,
            phase="tool",
            tool_invoked=False,
            effect_id=effect_record["id"],
        )
        if cancellation is not None:
            try:
                await _close_effect_without_dispatch(
                    app, effect_record, expected_status="intent"
                )
            except Exception:
                logger.warning(
                    "Could not close cancelled effect intent %s",
                    effect_record.get("id"),
                    exc_info=True,
                )
            return cancellation

    try:
        await _publish_tool_status(
            app,
            tool_id=action_id,
            name=name,
            args=args,
            status="running",
            chain_id=chain_id,
            message_id=message_id,
            session_id=session_id,
        )
        cancellation = await _checkpoint_cooperative_cancellation(
            app,
            event_id=event_id,
            event=event,
            action=action,
            action_id=action_id,
            occurrence_time=occurrence_time,
            phase="tool",
            tool_invoked=False,
            effect_id=(effect_record.get("id") if effect_record else None),
        )
        if cancellation is not None:
            if effect_record is not None:
                try:
                    await _close_effect_without_dispatch(
                        app, effect_record, expected_status="intent"
                    )
                except Exception:
                    logger.warning(
                        "Could not close cancelled effect intent %s",
                        effect_record.get("id"),
                        exc_info=True,
                    )
            return cancellation
        if effect_record is not None:
            dispatched_at = time.time()
            effect_record = {
                **effect_record,
                "status": "dispatched",
                "certainty": "unknown",
                "reconcile_required": True,
                "dispatched_at": dispatched_at,
            }
            try:
                await _record_effect_transition(
                    app, effect_record, expected_statuses={"intent"}
                )
            except Exception:
                logger.warning(
                    "Could not record effect dispatch %s",
                    effect_record.get("id"),
                    exc_info=True,
                )
                return await _fail_before_tool_invocation(
                    app,
                    event_id=event_id,
                    event=event,
                    action=action,
                    action_id=action_id,
                    occurrence_time=occurrence_time,
                    name=name,
                    args=args,
                    detail=(
                        "Effect dispatch could not be recorded; the tool was not "
                        "invoked."
                    ),
                    chain_id=chain_id,
                    message_id=message_id,
                    session_id=session_id,
                )
            action["effect_id"] = effect_record["id"]
            action["effect_status"] = "dispatched"
            action["effect_certainty"] = "unknown"
            cancellation = await _checkpoint_cooperative_cancellation(
                app,
                event_id=event_id,
                event=event,
                action=action,
                action_id=action_id,
                occurrence_time=occurrence_time,
                phase="tool",
                tool_invoked=False,
                effect_id=effect_record["id"],
            )
            if cancellation is not None:
                try:
                    await _close_effect_without_dispatch(
                        app, effect_record, expected_status="dispatched"
                    )
                except Exception:
                    logger.warning(
                        "Could not close cancelled dispatched receipt %s",
                        effect_record.get("id"),
                        exc_info=True,
                    )
                return cancellation
            effect_dispatched = True
        result = await _invoke_tool(
            app,
            name=name,
            args=args,
            user=user,
            action_context={
                "conversation_id": session_id,
                "session_id": session_id,
                "message_id": message_id,
                "chain_id": chain_id,
                "response_id": chain_id or message_id,
                "request_id": action_id,
                "agent_id": chain_id or message_id or session_id or "scheduler",
                "agent_label": "scheduled action",
            },
        )
    except asyncio.CancelledError:
        if effect_record is not None:
            action["effect_status"] = (
                "unknown" if effect_dispatched else "not_dispatched"
            )
            action["effect_certainty"] = (
                "unknown" if effect_dispatched else "confirmed_no_change"
            )
            interrupted_effect = {
                **effect_record,
                "status": "unknown" if effect_dispatched else "not_dispatched",
                "certainty": (
                    "unknown" if effect_dispatched else "confirmed_no_change"
                ),
                "replay_policy": "never_auto_replay",
                "reconcile_required": effect_dispatched,
                "finished_at": time.time(),
                "error_category": "tool_interrupted",
                "error_code": "scheduler_cancelled",
            }
            try:
                await asyncio.shield(
                    _record_effect_transition(
                        app,
                        interrupted_effect,
                        expected_statuses={
                            "dispatched" if effect_dispatched else "intent"
                        },
                    )
                )
            except Exception:
                logger.warning(
                    "Could not mark interrupted effect %s unknown",
                    effect_record.get("id"),
                    exc_info=True,
                )
        interrupted = _record_interrupted_action(
            event,
            event_id=event_id,
            action=action,
            action_id=action_id,
            occurrence_time=occurrence_time,
            detail=(
                "Float stopped waiting during shutdown or after the wait cap. "
                "A non-cooperative external tool may still finish outside the "
                "scheduler."
            ),
            status="interrupted_unknown",
        )
        await asyncio.shield(
            _persist_claimed_or_post_dispatch_truth(
                app,
                event_id=event_id,
                event=event,
                action=action,
                action_id=action_id,
                occurrence_time=occurrence_time,
                result=interrupted,
                effect_id=(effect_record.get("id") if effect_record else None),
            )
        )
        raise
    except Exception as exc:
        if effect_record is not None:
            action["effect_status"] = "unknown"
            action["effect_certainty"] = "unknown"
            unknown_effect = {
                **effect_record,
                "status": "unknown",
                "certainty": "unknown",
                "replay_policy": "never_auto_replay",
                "reconcile_required": True,
                "finished_at": time.time(),
                "error_category": "tool_error_after_dispatch",
                "error_code": _safe_error_code(exc),
            }
            try:
                await _record_effect_transition(
                    app, unknown_effect, expected_statuses={"dispatched"}
                )
            except Exception:
                logger.warning(
                    "Could not mark failed effect %s unknown",
                    effect_record.get("id"),
                    exc_info=True,
                )
        safe_tool_error = (
            "Tool failed after dispatch: tool_error_after_dispatch "
            f"({_safe_error_code(exc)}); reconciliation is required."
            if effect_record is not None
            else f"Tool invocation failed: tool_error ({_safe_error_code(exc)})."
        )
        action["status"] = "error"
        action["error"] = safe_tool_error
        action["reconcile_required"] = effect_record is not None
        action["executed_at"] = time.time()
        _mark_action_occurrence(action, occurrence_time)
        _mark_event_prompted(event)
        result_payload = {
            "status": "error",
            "event_id": event_id,
            "action_id": action_id,
            "tool_invoked": True,
            "state_delta_certainty": (
                "unknown" if effect_record is not None else "not_applicable"
            ),
            "reconcile_required": effect_record is not None,
            "error": safe_tool_error,
        }
        _append_run_record(
            event,
            event_id=event_id,
            action=action,
            action_id=action_id,
            occurrence_time=occurrence_time,
            result=result_payload,
        )
        published_result = await _persist_claimed_or_post_dispatch_truth(
            app,
            event_id=event_id,
            event=event,
            action=action,
            action_id=action_id,
            occurrence_time=occurrence_time,
            result=result_payload,
            effect_id=(effect_record.get("id") if effect_record else None),
        )
        if published_result is not None:
            result_payload = published_result
        await _publish_tool_status(
            app,
            tool_id=action_id,
            name=name,
            args=args,
            status="error",
            result=safe_tool_error,
            chain_id=chain_id,
            message_id=message_id,
            session_id=session_id,
        )
        _append_tool_to_conversation(
            session_id=session_id,
            message_id=message_id,
            request_id=action_id,
            name=name,
            args=args,
            status="error",
            result=safe_tool_error,
        )
        return result_payload

    action["result"] = result
    action["executed_at"] = time.time()
    if effect_record is not None:
        if _tool_result_reports_error(result):
            action["effect_status"] = "unknown"
            action["effect_certainty"] = "unknown"
            reported_error_effect = {
                **effect_record,
                "status": "unknown",
                "certainty": "unknown",
                "replay_policy": "never_auto_replay",
                "reconcile_required": True,
                "result_digest": _canonical_digest(result),
                "remote_ids": _remote_effect_ids(result),
                "finished_at": action["executed_at"],
                "error_category": "tool_reported_error",
                "error_code": "error_result_after_dispatch",
            }
            try:
                await _record_effect_transition(
                    app,
                    reported_error_effect,
                    expected_statuses={"dispatched"},
                )
            except Exception:
                logger.warning(
                    "Could not mark error-returning effect %s unknown",
                    effect_record.get("id"),
                    exc_info=True,
                )
            action["status"] = "error"
            action["error"] = (
                "The tool returned an error after dispatch; its external effect is "
                "unknown and reconciliation is required."
            )
            _mark_action_occurrence(action, occurrence_time)
            _mark_event_prompted(event)
            result_payload = {
                "status": "error",
                "event_id": event_id,
                "action_id": action_id,
                "occurrence_at": occurrence_time,
                "tool_invoked": True,
                "state_delta_certainty": "unknown",
                "reconcile_required": True,
                "error": action["error"],
            }
            _append_run_record(
                event,
                event_id=event_id,
                action=action,
                action_id=action_id,
                occurrence_time=occurrence_time,
                result=result_payload,
            )
            published_result = await _persist_claimed_or_post_dispatch_truth(
                app,
                event_id=event_id,
                event=event,
                action=action,
                action_id=action_id,
                occurrence_time=occurrence_time,
                result=result_payload,
                effect_id=effect_record.get("id"),
            )
            return published_result or result_payload
        acknowledged_effect = {
            **effect_record,
            "status": "acknowledged",
            "certainty": "reported_success",
            "reconcile_required": False,
            "result_digest": _canonical_digest(result),
            "remote_ids": _remote_effect_ids(result),
            "acknowledged_at": action["executed_at"],
            "finished_at": action["executed_at"],
        }
        try:
            await _record_effect_transition(
                app, acknowledged_effect, expected_statuses={"dispatched"}
            )
        except Exception as exc:
            action["effect_status"] = "unknown"
            action["effect_certainty"] = "unknown"
            unknown_effect = {
                **effect_record,
                "status": "unknown",
                "certainty": "unknown",
                "replay_policy": "never_auto_replay",
                "reconcile_required": True,
                "result_digest": _canonical_digest(result),
                "finished_at": action["executed_at"],
                "error_category": "effect_acknowledgement_error",
                "error_code": _safe_error_code(exc),
            }
            try:
                await _record_effect_transition(
                    app, unknown_effect, expected_statuses={"dispatched"}
                )
            except Exception:
                logger.warning(
                    "Could not mark unacknowledged effect %s unknown",
                    effect_record.get("id"),
                    exc_info=True,
                )
            action["status"] = "error"
            action["error"] = (
                "Tool completed, but its reported effect could not be acknowledged "
                "durably; reconciliation is required."
            )
            _mark_action_occurrence(action, occurrence_time)
            _mark_event_prompted(event)
            result_payload = {
                "status": "error",
                "event_id": event_id,
                "action_id": action_id,
                "occurrence_at": occurrence_time,
                "tool_invoked": True,
                "state_delta_certainty": "unknown",
                "reconcile_required": True,
                "error": action["error"],
            }
            _append_run_record(
                event,
                event_id=event_id,
                action=action,
                action_id=action_id,
                occurrence_time=occurrence_time,
                result=result_payload,
            )
            published_result = await _persist_claimed_or_post_dispatch_truth(
                app,
                event_id=event_id,
                event=event,
                action=action,
                action_id=action_id,
                occurrence_time=occurrence_time,
                result=result_payload,
                effect_id=effect_record.get("id"),
            )
            return published_result or result_payload
        action["effect_status"] = "acknowledged"
        action["effect_certainty"] = "reported_success"
    if prompt:
        # Persist the tool output and exact follow-up inputs before making the
        # provider call. A replacement worker can now resume this phase while
        # retaining the same run id and without invoking the tool again.
        action["status"] = "followup_pending"
        action["followup_status"] = "pending"
        action["followup_prompt"] = prompt
        action["followup_tool_name"] = name
        action["followup_tool_args"] = args
        action["followup_message_id"] = _stable_composite_id(
            "scheduled-message",
            event_id,
            action_id,
            action.get("run_id"),
            "tool-followup",
        )
        result_payload = {
            "status": "followup_pending",
            "phase": "awaiting_followup",
            "event_id": event_id,
            "action_id": action_id,
            "occurrence_at": occurrence_time,
            "result": result,
            "tool_invoked": True,
        }
    else:
        action["status"] = "invoked"
        _mark_action_occurrence(action, occurrence_time)
        _mark_event_prompted(event)
        result_payload = {
            "status": "invoked",
            "event_id": event_id,
            "action_id": action_id,
            "occurrence_at": occurrence_time,
            "result": result,
            "tool_invoked": True,
        }
    _append_run_record(
        event,
        event_id=event_id,
        action=action,
        action_id=action_id,
        occurrence_time=occurrence_time,
        result=result_payload,
    )
    published_result = await _persist_claimed_or_post_dispatch_truth(
        app,
        event_id=event_id,
        event=event,
        action=action,
        action_id=action_id,
        occurrence_time=occurrence_time,
        result=result_payload,
        effect_id=(effect_record.get("id") if effect_record else None),
    )
    if published_result is None:
        return {
            "status": "error",
            "event_id": event_id,
            "action_id": action_id,
            "occurrence_at": occurrence_time,
            "result": result,
            "tool_invoked": True,
            "error": (
                "Tool completed, but its durable follow-up state was not " "accepted."
            ),
        }
    persisted_normally = published_result is result_payload
    result_payload = published_result
    await _publish_tool_status(
        app,
        tool_id=action_id,
        name=name,
        args=args,
        status="invoked",
        result=result,
        chain_id=chain_id,
        message_id=message_id,
        session_id=session_id,
    )
    _append_tool_to_conversation(
        session_id=session_id,
        message_id=message_id,
        request_id=action_id,
        name=name,
        args=args,
        status="invoked",
        result=result,
    )

    if prompt and persisted_normally:
        return await _resume_pending_tool_followup(
            app,
            event_id=event_id,
            event=event,
            action=action,
            action_id=action_id,
            occurrence_time=occurrence_time,
            recovering=False,
        )
    return result_payload


async def _run_scheduled_tools_for_event(
    app: FastAPI,
    event_id: str,
    *,
    action_id: Optional[str] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Run scheduled actions (tools + prompts) attached to a calendar event."""
    # Serialize one schedule definition without making an overnight job block
    # every other due calendar event.
    event_lock = _EVENT_RUN_LOCKS.setdefault(str(event_id), asyncio.Lock())
    async with event_lock:
        event = calendar_store.load_event(event_id)
        if not isinstance(event, dict) or not event:
            return {"status": "not_found", "event_id": event_id}
        event_status = str(event.get("status") or "pending").strip().lower()
        if not force and event_status in {
            "acknowledged",
            "skipped",
            "cancelled",
            "paused",
        }:
            return {
                "status": "inactive",
                "event_id": event_id,
                "event_status": event_status,
            }
        actions = _iter_actions(event)
        if not actions:
            fallback = _fallback_tool_from_description(event)
            if fallback:
                actions = [fallback]
                event["actions"] = [fallback]
                if not await _persist_event(
                    app, event_id, event, require_active=True, replace_actions=True
                ):
                    return {"status": "inactive", "event_id": event_id}
            else:
                return {"status": "no_actions", "event_id": event_id}

        now = time.time()
        occurrence_time = due_occurrence_time(event, now=now)
        if force:
            # Manual runs are distinct exact occurrences. They still pass the
            # same authorization gate and cannot reuse a scheduled approval.
            occurrence_time = now
        recovered_results = _recover_stale_running_actions(
            event,
            event_id=event_id,
            occurrence_time=occurrence_time,
            now=now,
        )
        if recovered_results:
            await _project_event_work_runs(app, event_id, event)
            await _reindex_calendar_event(app, event_id, event)
        event_status = str(event.get("status") or "pending").strip().lower()
        if not force and event_status in {
            "acknowledged",
            "skipped",
            "cancelled",
            "paused",
        }:
            return {
                "status": "inactive",
                "event_id": event_id,
                "event_status": event_status,
                "results": recovered_results,
            }
        actions = _iter_actions(event)
        recovered_action_ids = {
            str(item.get("action_id") or "")
            for item in recovered_results
            if item.get("status") not in {"followup_pending", "prompt_resume_pending"}
        }
        results: list[Dict[str, Any]] = list(recovered_results)
        ran_any = False
        had_error = any(
            item.get("status") in {"error", "interrupted_unknown"}
            for item in recovered_results
        )
        had_reconciliation_block = any(
            item.get("status") == "reconcile_required" for item in recovered_results
        )
        had_journal_block = any(
            item.get("status") == "effect_journal_unavailable"
            for item in recovered_results
        )
        had_authorization_block = any(
            item.get("status") == AUTHORIZATION_REQUIRED for item in recovered_results
        )
        had_cancelled = any(
            item.get("status") == "cancelled" for item in recovered_results
        )
        for idx, action in enumerate(actions):
            resolved_id = (
                action.get("request_id")
                or action.get("id")
                or (f"{event_id}:tool:{idx}")
            )
            resolved_id = str(resolved_id)
            if resolved_id in recovered_action_ids:
                continue
            if action_id and resolved_id != str(action_id):
                continue
            kind = str(action.get("kind") or action.get("type") or "").lower()
            if kind == "prompt":
                start = _event_start_time(event)
                if not force and occurrence_time is None:
                    result = {
                        "status": "not_due",
                        "event_id": event_id,
                        "action_id": resolved_id,
                        "start_time": start,
                    }
                else:
                    status = str(action.get("status") or "").lower()
                    prompt_occurrence_time = occurrence_time
                    if status == "prompt_resume_pending":
                        checkpoint = action.get("prompt_checkpoint")
                        checkpoint = (
                            checkpoint if isinstance(checkpoint, Mapping) else {}
                        )
                        prompt_occurrence_time = _coerce_epoch_seconds(
                            checkpoint.get("occurrence_at")
                        )
                    executed_at = _coerce_epoch_seconds(action.get("executed_at"))
                    if not force and _action_has_run_for_occurrence(
                        event, action, prompt_occurrence_time
                    ):
                        result = {
                            "status": "already_executed",
                            "event_id": event_id,
                            "action_id": resolved_id,
                            "executed_at": executed_at,
                            "occurrence_at": prompt_occurrence_time,
                        }
                    elif status in {"running"} and not force:
                        result = {
                            "status": "already_running",
                            "event_id": event_id,
                            "action_id": resolved_id,
                        }
                    else:
                        resume_claim: Dict[str, Any] = {}
                        if status == "prompt_resume_pending":
                            resume_claim = _claim_pending_prompt_resume(
                                event_id,
                                event,
                                action,
                                action_id=resolved_id,
                            )
                            if resume_claim.get("status") in {
                                "checkpoint_invalid",
                                "control_changed",
                            }:
                                result = dict(resume_claim.get("result") or {})
                                await _project_event_work_runs(app, event_id, event)
                                await _reindex_calendar_event(app, event_id, event)
                                results.append(result)
                                had_reconciliation_block = True
                                continue
                            claimed = resume_claim.get("status") == "claimed"
                            prompt_occurrence_time = _coerce_epoch_seconds(
                                resume_claim.get("occurrence_at")
                            )
                        else:
                            claimed = bool(
                                _claim_action_run(
                                    event_id,
                                    event,
                                    action,
                                    action_id=resolved_id,
                                    occurrence_time=prompt_occurrence_time,
                                    force=force,
                                )
                            )
                        if not claimed or prompt_occurrence_time is None:
                            result = {
                                "status": "already_claimed",
                                "event_id": event_id,
                                "action_id": resolved_id,
                            }
                            results.append(result)
                            continue
                        control_block = await _checkpoint_cooperative_cancellation(
                            app,
                            event_id=event_id,
                            event=event,
                            action=action,
                            action_id=resolved_id,
                            occurrence_time=prompt_occurrence_time,
                            phase="prompt",
                            tool_invoked=False,
                        )
                        if control_block is not None:
                            results.append(control_block)
                            continue
                        prompt = _normalize_prompt(action.get("prompt"))
                        if not prompt:
                            action["status"] = "error"
                            action["error"] = "scheduled action missing prompt"
                            action["executed_at"] = time.time()
                            _mark_action_occurrence(action, prompt_occurrence_time)
                            _mark_event_prompted(event)
                            result = {
                                "status": "error",
                                "event_id": event_id,
                                "action_id": resolved_id,
                                "error": "scheduled action missing prompt",
                            }
                            _append_run_record(
                                event,
                                event_id=event_id,
                                action=action,
                                action_id=resolved_id,
                                occurrence_time=prompt_occurrence_time,
                                result=result,
                            )
                            await _persist_claimed_event(
                                app,
                                event_id,
                                event,
                                action=action,
                                action_id=resolved_id,
                            )
                        else:
                            running_result = {
                                "status": "running",
                                "phase": "prompt",
                                "event_id": event_id,
                                "action_id": resolved_id,
                                "occurrence_at": prompt_occurrence_time,
                                "prompt_invoked": False,
                                "state_delta_certainty": "confirmed_no_change",
                            }
                            _append_run_record(
                                event,
                                event_id=event_id,
                                action=action,
                                action_id=resolved_id,
                                occurrence_time=prompt_occurrence_time,
                                result=running_result,
                            )
                            if not await _persist_claimed_event(
                                app,
                                event_id,
                                event,
                                action=action,
                                action_id=resolved_id,
                                require_active=True,
                            ):
                                result = {
                                    "status": "error",
                                    "event_id": event_id,
                                    "action_id": resolved_id,
                                    "occurrence_at": prompt_occurrence_time,
                                    "prompt_invoked": False,
                                    "error": (
                                        "The running receipt could not be recorded; "
                                        "the prompt was not sent."
                                    ),
                                }
                                results.append(result)
                                had_error = True
                                continue
                            _preserve_action_origin(action)
                            (
                                session_id,
                                message_id,
                                chain_id,
                            ) = _resolve_action_conversation(action)
                            session_id = _ensure_task_conversation(
                                session_id=session_id,
                                event=event,
                                event_id=event_id,
                                parent_session_id=_origin_session_id(action),
                                parent_message_id=_origin_message_id(action),
                            )
                            chain_id = chain_id or session_id
                            action["session_id"] = session_id
                            action["chain_id"] = chain_id
                            action["message_id"] = message_id or chain_id
                            prompt_checkpoint = _persist_prompt_checkpoint_claim(
                                event_id,
                                event,
                                action,
                                action_id=resolved_id,
                                occurrence_time=prompt_occurrence_time,
                                prompt=prompt,
                                session_id=session_id,
                                chain_id=chain_id,
                            )
                            if prompt_checkpoint is None:
                                control_block = (
                                    await _checkpoint_cooperative_cancellation(
                                        app,
                                        event_id=event_id,
                                        event=event,
                                        action=action,
                                        action_id=resolved_id,
                                        occurrence_time=prompt_occurrence_time,
                                        phase="prompt",
                                        tool_invoked=False,
                                    )
                                )
                                if control_block is not None:
                                    results.append(control_block)
                                    continue
                                action["status"] = "error"
                                action["error"] = (
                                    "Scheduled prompt checkpoint could not be "
                                    "recorded; provider dispatch was blocked."
                                )
                                action["executed_at"] = time.time()
                                _mark_action_occurrence(action, prompt_occurrence_time)
                                _mark_event_prompted(event)
                                result = {
                                    "status": "error",
                                    "event_id": event_id,
                                    "action_id": resolved_id,
                                    "occurrence_at": prompt_occurrence_time,
                                    "prompt_invoked": False,
                                    "state_delta_certainty": "confirmed_no_change",
                                    "error": action["error"],
                                }
                                _append_run_record(
                                    event,
                                    event_id=event_id,
                                    action=action,
                                    action_id=resolved_id,
                                    occurrence_time=prompt_occurrence_time,
                                    result=result,
                                )
                                await _persist_claimed_event(
                                    app,
                                    event_id,
                                    event,
                                    action=action,
                                    action_id=resolved_id,
                                )
                                results.append(result)
                                had_error = True
                                continue
                            try:
                                response_text = await _run_prompt_action(
                                    app,
                                    event=event,
                                    session_id=session_id,
                                    chain_id=chain_id,
                                    prompt=prompt,
                                    event_id=event_id,
                                    action=action,
                                    action_id=resolved_id,
                                    parent_session_id=_origin_session_id(action),
                                    parent_message_id=_origin_message_id(action),
                                )
                                action["status"] = "prompted"
                                action["result"] = response_text
                                action["executed_at"] = time.time()
                                _mark_action_occurrence(action, prompt_occurrence_time)
                                _mark_event_prompted(event)
                                result = {
                                    "status": "prompted",
                                    "event_id": event_id,
                                    "action_id": resolved_id,
                                    "result": response_text,
                                }
                                _append_run_record(
                                    event,
                                    event_id=event_id,
                                    action=action,
                                    action_id=resolved_id,
                                    occurrence_time=prompt_occurrence_time,
                                    result=result,
                                )
                                await _persist_claimed_event(
                                    app,
                                    event_id,
                                    event,
                                    action=action,
                                    action_id=resolved_id,
                                )
                            except _ScheduledDispatchBlocked as blocked:
                                result = dict(blocked.result)
                            except asyncio.CancelledError:
                                _record_interrupted_action(
                                    event,
                                    event_id=event_id,
                                    action=action,
                                    action_id=resolved_id,
                                    occurrence_time=prompt_occurrence_time,
                                    detail=(
                                        "Float stopped waiting for the scheduled "
                                        "prompt during shutdown or after the wait cap; "
                                        "its provider call may "
                                        "still finish outside the scheduler."
                                    ),
                                    status="interrupted_unknown",
                                )
                                await asyncio.shield(
                                    _persist_claimed_event(
                                        app,
                                        event_id,
                                        event,
                                        action=action,
                                        action_id=resolved_id,
                                    )
                                )
                                raise
                            except Exception as exc:
                                reconcile_required = isinstance(
                                    exc, _ProviderOutputCheckpointError
                                )
                                action["status"] = "error"
                                action["error"] = str(exc)
                                action["reconcile_required"] = reconcile_required
                                action["executed_at"] = time.time()
                                _mark_action_occurrence(action, prompt_occurrence_time)
                                _mark_event_prompted(event)
                                result = {
                                    "status": "error",
                                    "event_id": event_id,
                                    "action_id": resolved_id,
                                    "error": str(exc),
                                    "reconcile_required": reconcile_required,
                                    "state_delta_certainty": (
                                        "unknown" if reconcile_required else None
                                    ),
                                }
                                _append_run_record(
                                    event,
                                    event_id=event_id,
                                    action=action,
                                    action_id=resolved_id,
                                    occurrence_time=prompt_occurrence_time,
                                    result=result,
                                )
                                await _persist_claimed_event(
                                    app,
                                    event_id,
                                    event,
                                    action=action,
                                    action_id=resolved_id,
                                )
                                await _publish_content(
                                    app,
                                    content=f"(scheduled prompt failed) {exc}",
                                    chain_id=chain_id,
                                    session_id=session_id,
                                    metadata={
                                        "scheduled": True,
                                        "event_id": event_id,
                                        "action_id": resolved_id,
                                        "prompt_action": True,
                                        "error": True,
                                    },
                                )
            else:
                result = await _run_tool_action(
                    app,
                    event_id=event_id,
                    event=event,
                    action=action,
                    action_id=resolved_id,
                    occurrence_time=occurrence_time,
                    force=force,
                )
            results.append(result)
            status_val = str(result.get("status") or "")
            ran_any = ran_any or status_val in {"invoked", "prompted"}
            had_error = had_error or status_val in {"error", "interrupted_unknown"}
            had_reconciliation_block = (
                had_reconciliation_block or status_val == "reconcile_required"
            )
            had_journal_block = (
                had_journal_block or status_val == "effect_journal_unavailable"
            )
            had_authorization_block = (
                had_authorization_block or status_val == AUTHORIZATION_REQUIRED
            )
            had_cancelled = had_cancelled or status_val == "cancelled"
        outer_status = "error" if had_error else ("invoked" if ran_any else "ok")
        if not had_error and had_reconciliation_block:
            outer_status = "reconcile_required"
        elif not had_error and had_journal_block:
            outer_status = "effect_journal_unavailable"
        elif not had_error and had_authorization_block and not ran_any:
            outer_status = AUTHORIZATION_REQUIRED
        elif not had_error and had_cancelled and not ran_any:
            outer_status = "cancelled"
        return {
            "status": outer_status,
            "event_id": event_id,
            "occurrence_at": occurrence_time,
            "results": results,
        }


async def run_scheduled_tools_for_event(
    app: FastAPI,
    event_id: str,
    *,
    action_id: Optional[str] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Run one event while enforcing its per-occurrence wall-clock limit."""

    event = calendar_store.load_event(event_id)
    timeout_seconds = _event_runtime_limit_seconds(
        event if isinstance(event, dict) else {}
    )
    try:
        return await asyncio.wait_for(
            _run_scheduled_tools_for_event(
                app,
                event_id,
                action_id=action_id,
                force=force,
            ),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        detail = (
            "Float stopped waiting after the scheduled event exceeded its "
            f"{timeout_seconds:g}-second runtime limit; a non-cooperative external "
            "call may still finish outside the scheduler."
        )
        latest = calendar_store.load_event(event_id)
        timeout_results: list[Dict[str, Any]] = []
        if isinstance(latest, dict) and latest:
            occurrence_time = due_occurrence_time(latest, now=time.time())
            for index, action in enumerate(_iter_actions(latest)):
                if str(action.get("status") or "").strip().lower() != "running":
                    continue
                resolved_id = str(
                    action.get("request_id")
                    or action.get("id")
                    or f"{event_id}:tool:{index}"
                )
                timeout_results.append(
                    _record_interrupted_action(
                        latest,
                        event_id=event_id,
                        action=action,
                        action_id=resolved_id,
                        occurrence_time=occurrence_time,
                        detail=detail,
                        status="interrupted_unknown",
                    )
                )
            if timeout_results:
                await _persist_event(app, event_id, latest)
        return {
            "status": "error",
            "event_id": event_id,
            "results": timeout_results
            or [{"status": "error", "event_id": event_id, "error": detail}],
        }


async def run_due_scheduled_tools_once(app: FastAPI) -> int:
    """Scan stored events and run due scheduled actions."""
    now = time.time()
    due_event_ids: list[str] = []
    for event_id in calendar_store.list_events():
        event = calendar_store.load_event(event_id)
        if not isinstance(event, dict) or not event:
            continue
        if str(event.get("status") or "pending").strip().lower() in {
            "acknowledged",
            "skipped",
            "cancelled",
            "paused",
        }:
            continue
        actions = _iter_actions(event)
        if not actions:
            continue
        if not _event_has_due_action(event, now=now):
            continue
        due_event_ids.append(event_id)

    if not due_event_ids:
        return 0
    results = await asyncio.gather(
        *(run_scheduled_tools_for_event(app, event_id) for event_id in due_event_ids),
        return_exceptions=True,
    )
    ran = 0
    for res in results:
        if isinstance(res, Exception):
            continue
        for item in res.get("results") or []:
            if isinstance(item, dict) and item.get("status") in {"invoked", "prompted"}:
                ran += 1
    return ran


def _finish_dispatched_event(event_id: str, task: asyncio.Task) -> None:
    """Release one active slot and report task failures without leaking them."""

    if _ACTIVE_EVENT_TASKS.get(event_id) is task:
        _ACTIVE_EVENT_TASKS.pop(event_id, None)
    try:
        result = task.result()
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("Scheduled calendar event %s failed", event_id)
        return
    ran = sum(
        1
        for item in result.get("results") or []
        if isinstance(item, dict) and item.get("status") in {"invoked", "prompted"}
    )
    if ran:
        logger.info("Executed %d action(s) for calendar event %s", ran, event_id)


async def dispatch_due_scheduled_tools(app: FastAPI) -> int:
    """Start due events without making the scheduler wait for long-running jobs."""

    now = time.time()
    started = 0
    for event_id in calendar_store.list_events():
        event = calendar_store.load_event(event_id)
        if not isinstance(event, dict) or not event:
            continue
        if str(event.get("status") or "pending").strip().lower() in {
            "acknowledged",
            "skipped",
            "cancelled",
            "paused",
        }:
            continue
        active = _ACTIVE_EVENT_TASKS.get(event_id)
        if active is not None and not active.done():
            continue
        if not _event_has_due_action(event, now=now):
            continue
        task = asyncio.create_task(
            run_scheduled_tools_for_event(app, event_id),
            name=f"float-calendar-event:{event_id}",
        )
        _ACTIVE_EVENT_TASKS[event_id] = task
        task.add_done_callback(
            lambda finished, current_event_id=event_id: _finish_dispatched_event(
                current_event_id, finished
            )
        )
        started += 1
    return started


async def scheduled_tool_runner(app: FastAPI) -> None:
    """Background loop to execute due scheduled tool actions.

    This is a lightweight fallback for local/dev runs where Celery beat/workers
    may not be running yet. It is intentionally conservative and only executes
    actions that were explicitly scheduled into calendar events.
    """

    enabled = os.getenv("FLOAT_SCHEDULED_TOOLS_ENABLED", "true").lower() == "true"
    if not enabled:
        logger.info("Scheduled tool runner disabled via env")
        return

    poll_seconds = float(os.getenv("FLOAT_SCHEDULED_TOOLS_POLL_SECONDS", "10"))
    poll_seconds = max(1.0, poll_seconds)
    logger.info("Scheduled tool runner active (poll=%.1fs)", poll_seconds)

    try:
        while True:
            try:
                started = await dispatch_due_scheduled_tools(app)
                if started:
                    logger.info("Started %d scheduled calendar event(s)", started)
            except asyncio.CancelledError:  # pragma: no cover - shutdown path
                raise
            except Exception:
                logger.exception("Scheduled tool runner iteration failed")
            await asyncio.sleep(poll_seconds)
    except asyncio.CancelledError:  # pragma: no cover - shutdown path
        logger.info("Scheduled tool runner cancelled")
        raise
    finally:
        active_tasks = list(_ACTIVE_EVENT_TASKS.values())
        for task in active_tasks:
            task.cancel()
        if active_tasks:
            await asyncio.gather(*active_tasks, return_exceptions=True)
        _ACTIVE_EVENT_TASKS.clear()
