from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

HANDOFF_SCHEMA_VERSION = "float.handoff.v1"


class HandoffGoal(BaseModel):
    id: Optional[str] = None
    title: str
    status: str = "open"
    owner: Optional[str] = None
    notes: Optional[str] = None


class HandoffApproval(BaseModel):
    id: str
    kind: str = "tool"
    label: str
    status: str = "pending"


class HandoffArtifact(BaseModel):
    schema_version: str = HANDOFF_SCHEMA_VERSION
    summary: str = ""
    recent_turn_ids: List[str] = Field(default_factory=list)
    open_goals: List[HandoffGoal] = Field(default_factory=list)
    pending_approvals: List[HandoffApproval] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


class WorkflowProfileMetadata(BaseModel):
    id: str
    label: str
    description: str = ""
    role: str = "general"
    latency_tier: str = "interactive"
    delegation_mode: str = "direct"
    thinking_default: str = "auto"
    preferred_continue: Optional[str] = None
    allow_continue_to: List[str] = Field(default_factory=list)
    enabled_modules: List[str] = Field(default_factory=list)
    supports_background: bool = False
    supports_live: bool = False
    handoff_schema: str = HANDOFF_SCHEMA_VERSION


class AgentProvenance(BaseModel):
    kind: str = "root"
    parent_agent_id: Optional[str] = None
    parent_session_id: Optional[str] = None
    parent_message_id: Optional[str] = None
    source_event_id: Optional[str] = None
    task_id: Optional[str] = None
    branch_session_id: Optional[str] = None
    label: Optional[str] = None


class AgentControls(BaseModel):
    available: List[str] = Field(default_factory=list)
    modes: Dict[str, str] = Field(default_factory=dict)
    redirect_note: Optional[str] = None
    redirect_workflow: Optional[str] = None
    redirect_mode: Optional[str] = None
    redirect_model: Optional[str] = None
    updated_at: Optional[float] = None


def _model_dump(model: BaseModel) -> Dict[str, Any]:
    return model.model_dump(exclude_none=True)


def merge_agent_payload(
    existing: Optional[Dict[str, Any]],
    update: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    base = dict(existing) if isinstance(existing, dict) else {}
    if not isinstance(update, dict):
        return base
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = merge_agent_payload(base.get(key), value)
            continue
        base[key] = value
    return base


def build_workflow_metadata(profile: Dict[str, Any]) -> Dict[str, Any]:
    return _model_dump(WorkflowProfileMetadata.model_validate(profile))


def build_handoff_artifact(
    *,
    summary: str = "",
    recent_turn_ids: Optional[List[str]] = None,
    open_goals: Optional[List[Dict[str, Any]]] = None,
    pending_approvals: Optional[List[Dict[str, Any]]] = None,
    notes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return _model_dump(
        HandoffArtifact(
            summary=str(summary or "").strip(),
            recent_turn_ids=[
                str(item).strip()
                for item in (recent_turn_ids or [])
                if str(item).strip()
            ],
            open_goals=[
                HandoffGoal.model_validate(item) for item in (open_goals or [])
            ],
            pending_approvals=[
                HandoffApproval.model_validate(item)
                for item in (pending_approvals or [])
            ],
            notes=[
                str(item).strip() for item in (notes or []) if str(item or "").strip()
            ],
        )
    )


def append_handoff_note(
    existing: Optional[Dict[str, Any]],
    note: str,
    *,
    fallback_summary: str = "",
) -> Dict[str, Any]:
    base = (
        HandoffArtifact.model_validate(existing)
        if isinstance(existing, dict)
        else HandoffArtifact(summary=fallback_summary)
    )
    cleaned = str(note or "").strip()
    if cleaned and cleaned not in base.notes:
        base.notes.append(cleaned)
    if fallback_summary and not base.summary:
        base.summary = fallback_summary
    return _model_dump(base)


def build_task_handoff(
    steps: List[Dict[str, Any]],
    *,
    summary: str = "",
    recent_turn_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    open_goals: List[Dict[str, Any]] = []
    for idx, step in enumerate(steps[:6], start=1):
        name = str(step.get("agent") or "").strip()
        if not name:
            continue
        open_goals.append(
            {
                "id": f"step-{idx}",
                "title": name,
                "status": "pending",
                "owner": "worker",
            }
        )
    summary_text = str(summary or "").strip()
    if not summary_text:
        if open_goals:
            summary_text = (
                f"Queued delegated run with {len(open_goals)} planned step(s)."
            )
        else:
            summary_text = "Queued delegated run."
    return build_handoff_artifact(
        summary=summary_text,
        recent_turn_ids=recent_turn_ids,
        open_goals=open_goals,
    )


def build_agent_provenance(
    *,
    kind: str = "root",
    parent_agent_id: Optional[str] = None,
    parent_session_id: Optional[str] = None,
    parent_message_id: Optional[str] = None,
    source_event_id: Optional[str] = None,
    task_id: Optional[str] = None,
    branch_session_id: Optional[str] = None,
    label: Optional[str] = None,
) -> Dict[str, Any]:
    return _model_dump(
        AgentProvenance(
            kind=str(kind or "root").strip() or "root",
            parent_agent_id=parent_agent_id,
            parent_session_id=parent_session_id,
            parent_message_id=parent_message_id,
            source_event_id=source_event_id,
            task_id=task_id,
            branch_session_id=branch_session_id,
            label=label,
        )
    )


def controls_for_status(
    status: str,
    *,
    allow_pause: bool = True,
    allow_redirect: bool = True,
    allow_stop: bool = True,
    pause_mode: str = "soft",
    redirect_mode: str = "queued_request",
    stop_mode: str = "runtime_revoke",
    existing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    normalized = str(status or "").strip().lower()
    finished = normalized in {
        "complete",
        "completed",
        "success",
        "error",
        "failed",
        "stopped",
    }
    available: List[str] = []
    if normalized == "paused":
        if allow_pause:
            available.append("resume")
    elif allow_pause and normalized in {
        "active",
        "running",
        "streaming",
        "queued",
        "pending",
        "waiting",
        "received",
    }:
        available.append("pause")
    if allow_redirect and not finished:
        available.append("redirect")
    if allow_stop and not finished:
        available.append("stop")
    modes: Dict[str, str] = {}
    if "pause" in available:
        modes["pause"] = pause_mode
    if "resume" in available:
        modes["resume"] = pause_mode
    if "redirect" in available:
        modes["redirect"] = redirect_mode
    if "stop" in available:
        modes["stop"] = stop_mode
    seed = dict(existing) if isinstance(existing, dict) else {}
    seed["available"] = available
    seed["modes"] = modes
    seed["updated_at"] = time.time()
    return _model_dump(AgentControls.model_validate(seed))
