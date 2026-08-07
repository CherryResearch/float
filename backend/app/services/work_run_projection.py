"""Compact projections from legacy background-work stores into Activity."""

from __future__ import annotations

import math
from typing import Any, Dict, Mapping

from app.services.work_run_store import WorkRunStore
from app.utils import calendar_store

JsonDict = Dict[str, Any]


def _epoch(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if math.isfinite(result) else 0.0


def calendar_run_receipt(
    event_id: str,
    event: Mapping[str, Any],
    record: Mapping[str, Any],
) -> JsonDict:
    """Add stable Calendar context without accepting the whole event payload."""

    receipt = dict(record)
    receipt.setdefault("source", "calendar")
    receipt.setdefault("job_id", event_id)
    receipt.setdefault("event_id", event_id)
    receipt.setdefault("event_title", event.get("title") or event_id)
    ownership = receipt.get("ownership")
    ownership = dict(ownership) if isinstance(ownership, Mapping) else {}
    ownership.setdefault("owner_kind", "calendar_event")
    ownership.setdefault("calendar_event_id", event_id)
    receipt["ownership"] = ownership
    return receipt


def reflection_run_receipt(
    task: Mapping[str, Any],
    run: Mapping[str, Any],
) -> JsonDict:
    """Build one completed reflection receipt without raw output or thought text."""

    metadata = task.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    metadata_ownership = metadata.get("ownership")
    metadata_ownership = (
        metadata_ownership if isinstance(metadata_ownership, Mapping) else {}
    )
    conversation_id = str(
        task.get("source_thread_id")
        or metadata_ownership.get("conversation_id")
        or metadata.get("conversation_id")
        or metadata.get("session_id")
        or ""
    ).strip()
    event_id = str(
        task.get("event_id") or metadata_ownership.get("calendar_event_id") or ""
    ).strip()
    owner_kind = str(metadata_ownership.get("owner_kind") or "").strip()
    if not owner_kind:
        owner_kind = (
            "conversation"
            if conversation_id
            else "calendar_event"
            if event_id
            else "manual"
        )
    ownership = {
        "owner_kind": owner_kind,
        "calendar_event_id": event_id or None,
        "conversation_id": conversation_id or None,
        "message_id": metadata_ownership.get("message_id")
        or metadata.get("message_id"),
        "parent_job_id": metadata_ownership.get("parent_job_id")
        or metadata.get("parent_job_id"),
        "parent_agent_id": metadata_ownership.get("parent_agent_id")
        or metadata.get("parent_agent_id"),
    }
    patience_budget = task.get("patience_budget")
    patience_budget = patience_budget if isinstance(patience_budget, Mapping) else {}
    patience = {
        "stop_condition": "reflection_patience_budget",
        "max_attempts": patience_budget.get("max_reasoning_turns"),
        "max_runtime_seconds": patience_budget.get("max_runtime_seconds"),
    }
    execution = run.get("generation")
    execution = dict(execution) if isinstance(execution, Mapping) else {}
    if run.get("thought_trace_count") is not None:
        execution.setdefault("thought_trace_length", run["thought_trace_count"])
    execution.setdefault(
        "model", execution.get("received_model") or execution.get("requested_model")
    )
    created_at = _epoch(run.get("created_at"))
    return {
        "id": run.get("id"),
        "source": "reflection",
        "run_id": run.get("id"),
        "job_id": task.get("id"),
        "event_id": event_id or None,
        "event_title": task.get("title") or "Reflection",
        "action_id": task.get("id"),
        "action_kind": "reflection",
        "action_name": "reflection",
        "occurrence_at": created_at,
        "occurrence_id": run.get("id"),
        "started_at": created_at,
        "finished_at": created_at,
        "status": "complete",
        "phase": "complete",
        "summary": run.get("compact_note") or "Reflection completed.",
        "ownership": ownership,
        "patience": patience,
        "execution": execution,
    }


def project_calendar_event(
    store: WorkRunStore,
    event_id: str,
    event: Mapping[str, Any],
    *,
    raise_on_error: bool = False,
) -> JsonDict:
    """Idempotently project every retained receipt for one Calendar event."""

    stats: JsonDict = {"seen": 0, "recorded": 0, "invalid": 0, "failed": 0}
    history = event.get("run_history")
    if not isinstance(history, list):
        return stats
    for raw_record in history:
        stats["seen"] += 1
        if not isinstance(raw_record, Mapping) or not raw_record.get("id"):
            stats["invalid"] += 1
            continue
        try:
            store.upsert(
                calendar_run_receipt(event_id, event, raw_record),
                source="calendar",
            )
            stats["recorded"] += 1
        except Exception:
            stats["failed"] += 1
            if raise_on_error:
                raise
    return stats


def delete_calendar_event_with_receipts(
    store: WorkRunStore,
    event_id: str,
) -> JsonDict:
    """Preserve retained receipts and reject active work before deletion."""

    projected: JsonDict = {"seen": 0, "recorded": 0, "invalid": 0, "failed": 0}

    def guard(event: Dict[str, Any]) -> None:
        if store.has_active_run(event_id=event_id):
            raise calendar_store.CalendarEventActiveRunError(event_id)
        projected.update(
            project_calendar_event(
                store,
                event_id,
                event,
                raise_on_error=True,
            )
        )
        if store.has_active_run(event_id=event_id):
            raise calendar_store.CalendarEventActiveRunError(event_id)

    projected["deleted"] = calendar_store.delete_event(event_id, guard=guard)
    return projected


__all__ = [
    "calendar_run_receipt",
    "delete_calendar_event_with_receipts",
    "project_calendar_event",
    "reflection_run_receipt",
]
