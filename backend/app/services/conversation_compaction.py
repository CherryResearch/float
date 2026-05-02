"""Shared helpers for transparent conversation context compaction."""

from __future__ import annotations

import hashlib
import json
import math
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.agent_workflows import build_workflow_metadata
from app.models import ModelContext
from app.utils import conversation_store
from app.workflow_profiles import resolve_workflow_profile

DEFAULT_KEEP_LAST = 40
MAX_KEEP_LAST = 200
DEFAULT_MAX_SUMMARY_CHARS = 6000
MAX_SUMMARY_CHARS = 20000
DEFAULT_CONTEXT_WINDOW_TOKENS = 24000
DEFAULT_RESERVE_OUTPUT_TOKENS = 2048
DEFAULT_RESERVE_TOOL_TOKENS = 2500
DEFAULT_RESERVE_RETRIEVAL_TOKENS = 2500
DEFAULT_RESERVE_SYSTEM_TOKENS = 1500
DEFAULT_SOFT_TRIGGER_RATIO = 0.75
DEFAULT_HARD_TRIGGER_RATIO = 0.9
SUMMARY_MODE_DETERMINISTIC = "deterministic"
SUMMARY_MODE_LLM = "llm"
SUMMARY_MODES = {SUMMARY_MODE_DETERMINISTIC, SUMMARY_MODE_LLM}
SUMMARY_WORKFLOW_CONVERSATION_HANDOFF = "conversation_handoff"
SUMMARY_WORKFLOW_DECISION_FOCUS = "decision_focus"
SUMMARY_WORKFLOW_TASK_STATE = "task_state"
DEFAULT_SUMMARY_WORKFLOW = SUMMARY_WORKFLOW_CONVERSATION_HANDOFF
SUMMARY_WORKFLOWS: Dict[str, Dict[str, str]] = {
    SUMMARY_WORKFLOW_CONVERSATION_HANDOFF: {
        "label": "Conversation Handoff",
        "workflow_profile": "mini_execution",
        "focus": (
            "Preserve the task state, key decisions, tool outcomes, files, IDs, "
            "user preferences, and the immediate next step."
        ),
        "format": (
            "Use short headed sections: Goal, Decisions, Evidence, Open Items, "
            "Next Step."
        ),
    },
    SUMMARY_WORKFLOW_DECISION_FOCUS: {
        "label": "Decision Focus",
        "workflow_profile": "mini_execution",
        "focus": (
            "Preserve decisions, corrections, constraints, tradeoffs, and the "
            "reason the current direction was chosen."
        ),
        "format": (
            "Use short headed sections: Current Goal, Locked Decisions, "
            "Constraints, Rejected Paths, Remaining Questions."
        ),
    },
    SUMMARY_WORKFLOW_TASK_STATE: {
        "label": "Task State",
        "workflow_profile": "mini_execution",
        "focus": (
            "Preserve execution state, completed work, pending work, blockers, "
            "approvals, and concrete follow-ups."
        ),
        "format": (
            "Use short headed sections: Done, In Progress, Blockers, Pending "
            "Approval, Next Actions."
        ),
    },
}

LlmSummarizer = Callable[[Dict[str, Any]], Any]


def _stable_payload_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        )
    except Exception:
        return repr(value)


def bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(minimum, min(parsed, maximum))


def normalize_summary_mode(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"model", "semantic", "provider", "ai"}:
        return SUMMARY_MODE_LLM
    if raw in SUMMARY_MODES:
        return raw
    return SUMMARY_MODE_DETERMINISTIC


def normalize_summary_workflow(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in SUMMARY_WORKFLOWS:
        return raw
    return DEFAULT_SUMMARY_WORKFLOW


def normalize_summary_format_notes(value: Any, *, limit: int = 800) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip()


def normalize_summary_model(value: Any) -> str:
    return " ".join(str(value or "").split())


def message_text(message: Dict[str, Any]) -> str:
    raw = message.get("text")
    if raw is None:
        raw = message.get("content")
    if isinstance(raw, list):
        parts: List[str] = []
        for item in raw:
            if isinstance(item, dict):
                value = item.get("text") or item.get("content") or item.get("value")
                if value:
                    parts.append(str(value))
            elif item is not None:
                parts.append(str(item))
        raw = " ".join(parts)
    return " ".join(str(raw or "").split())


def excerpt(value: str, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def tool_count(message: Dict[str, Any]) -> int:
    total = 0
    tools = message.get("tools")
    if isinstance(tools, list):
        total += len(tools)
    metadata = message.get("metadata")
    if isinstance(metadata, dict):
        inline = metadata.get("inline_tool_payloads")
        if isinstance(inline, list):
            total += len(inline)
    return total


def message_id(message: Dict[str, Any], index: int) -> str:
    raw = (
        message.get("id")
        or message.get("message_id")
        or message.get("chain_id")
        or f"index-{index}"
    )
    return str(raw)


def is_compaction_marker_message(message: Dict[str, Any]) -> bool:
    if not isinstance(message, dict):
        return False
    metadata = message.get("metadata")
    marker = (
        metadata.get("context_compaction_marker")
        if isinstance(metadata, dict)
        else None
    )
    return isinstance(marker, dict) and bool(marker.get("snapshot_id"))


def is_compaction_summary_message(message: Dict[str, Any]) -> bool:
    if not isinstance(message, dict):
        return False
    metadata = message.get("metadata")
    compaction = (
        metadata.get("conversation_compaction") if isinstance(metadata, dict) else None
    )
    return isinstance(compaction, dict) and bool(
        compaction.get("source_conversation_id")
    )


def compaction_relevant_messages(
    messages: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    return [
        message
        for message in messages
        if isinstance(message, dict) and not is_compaction_marker_message(message)
    ]


def estimate_message_tokens(message: Dict[str, Any]) -> int:
    text = message_text(message)
    text_tokens = math.ceil(len(text) / 4) if text else 0
    tools = tool_count(message)
    attachments = message.get("attachments")
    attachment_count = len(attachments) if isinstance(attachments, list) else 0
    role_overhead = 10
    tool_overhead = tools * 48
    attachment_overhead = attachment_count * 24
    return max(12, role_overhead + text_tokens + tool_overhead + attachment_overhead)


def estimate_conversation_tokens(messages: List[Dict[str, Any]]) -> int:
    total = 0
    for message in messages:
        if isinstance(message, dict):
            total += estimate_message_tokens(message)
    return total


def _carry_forward_prior_summaries(
    prior_summaries: List[str],
    *,
    max_chars: int,
) -> str:
    summaries = [" ".join(str(item or "").split()) for item in prior_summaries]
    summaries = [item for item in summaries if item]
    if not summaries:
        return ""
    joined = "\n\n".join(
        f"Prior summary {index + 1}:\n{text}" for index, text in enumerate(summaries)
    )
    if len(joined) <= max_chars:
        return joined
    return joined[: max(0, max_chars - 3)].rstrip() + "..."


def build_context_budget_plan(
    messages: List[Dict[str, Any]],
    *,
    conversation_id: str = "",
    context_window_tokens: int = DEFAULT_CONTEXT_WINDOW_TOKENS,
    reserve_output_tokens: int = DEFAULT_RESERVE_OUTPUT_TOKENS,
    reserve_tool_tokens: int = DEFAULT_RESERVE_TOOL_TOKENS,
    reserve_retrieval_tokens: int = DEFAULT_RESERVE_RETRIEVAL_TOKENS,
    reserve_system_tokens: int = DEFAULT_RESERVE_SYSTEM_TOKENS,
    soft_trigger_ratio: float = DEFAULT_SOFT_TRIGGER_RATIO,
    hard_trigger_ratio: float = DEFAULT_HARD_TRIGGER_RATIO,
) -> Dict[str, Any]:
    messages = compaction_relevant_messages(messages)
    normalized_window = max(
        4096, int(context_window_tokens or DEFAULT_CONTEXT_WINDOW_TOKENS)
    )
    reserves = {
        "output_tokens": max(0, int(reserve_output_tokens or 0)),
        "tool_tokens": max(0, int(reserve_tool_tokens or 0)),
        "retrieval_tokens": max(0, int(reserve_retrieval_tokens or 0)),
        "system_tokens": max(0, int(reserve_system_tokens or 0)),
    }
    reserved_total = sum(reserves.values())
    usable_history_tokens = max(512, normalized_window - reserved_total)
    estimated_tokens_per_message = [
        estimate_message_tokens(message) if isinstance(message, dict) else 0
        for message in messages
    ]
    estimated_history_tokens = sum(estimated_tokens_per_message)
    utilization = (
        float(estimated_history_tokens) / float(usable_history_tokens)
        if usable_history_tokens > 0
        else 0.0
    )

    if normalized_window <= 24_000:
        context_profile = "short"
        recent_fraction = 0.45
        summary_fraction = 0.20
        min_tail_tokens = 1200
        max_tail_tokens = 5000
    elif normalized_window <= 128_000:
        context_profile = "medium"
        recent_fraction = 0.30
        summary_fraction = 0.15
        min_tail_tokens = 3000
        max_tail_tokens = 24000
    else:
        context_profile = "long"
        recent_fraction = 0.20
        summary_fraction = 0.10
        min_tail_tokens = 8000
        max_tail_tokens = 64000

    recommended_tail_tokens = max(
        min_tail_tokens,
        min(max_tail_tokens, int(usable_history_tokens * recent_fraction)),
    )
    recommended_summary_tokens = max(
        400,
        min(5000, int(usable_history_tokens * summary_fraction)),
    )
    running_tail_tokens = 0
    recommended_keep_last = 0
    for estimate in reversed(estimated_tokens_per_message):
        projected = running_tail_tokens + estimate
        if recommended_keep_last >= 4 and projected > recommended_tail_tokens:
            break
        running_tail_tokens = projected
        recommended_keep_last += 1
        if recommended_keep_last >= MAX_KEEP_LAST:
            break
    if messages and recommended_keep_last <= 0:
        recommended_keep_last = 1
        running_tail_tokens = estimated_tokens_per_message[-1]
    retained_start_index = max(0, len(messages) - recommended_keep_last)
    omitted_messages = max(0, len(messages) - recommended_keep_last)

    if utilization >= 1.0:
        status = "overflow"
    elif utilization >= float(hard_trigger_ratio):
        status = "hard_trigger"
    elif utilization >= float(soft_trigger_ratio):
        status = "soft_trigger"
    else:
        status = "ok"

    return {
        "conversation_id": str(conversation_id or "").strip(),
        "context_profile": context_profile,
        "context_window_tokens": normalized_window,
        "reserve_tokens": reserves,
        "reserved_total_tokens": reserved_total,
        "usable_history_tokens": usable_history_tokens,
        "estimated_history_tokens": estimated_history_tokens,
        "estimated_tail_tokens": running_tail_tokens,
        "estimated_omitted_tokens": max(
            0, estimated_history_tokens - running_tail_tokens
        ),
        "summary_token_budget": recommended_summary_tokens,
        "recommended_summary_chars": max(
            500,
            min(MAX_SUMMARY_CHARS, recommended_summary_tokens * 4),
        ),
        "utilization_ratio": round(utilization, 4),
        "soft_trigger_ratio": float(soft_trigger_ratio),
        "hard_trigger_ratio": float(hard_trigger_ratio),
        "status": status,
        "recommended_keep_last": recommended_keep_last,
        "retained_start_index": retained_start_index,
        "retained_end_index": len(messages) - 1 if recommended_keep_last else None,
        "retained_messages": recommended_keep_last,
        "omitted_messages": omitted_messages,
        "source_message_count": len(messages),
    }


def _build_snapshot_id(
    result: Dict[str, Any],
    *,
    target_conversation_id: str,
    replace: bool,
) -> str:
    payload = {
        "source_conversation_id": result.get("source_conversation_id"),
        "source_message_count": result.get("source_message_count"),
        "retained_start_index": result.get("retained_start_index"),
        "retained_end_index": result.get("retained_end_index"),
        "summary_preview": result.get("summary_preview"),
        "summary_method": result.get("summary_method"),
        "summary_workflow": result.get("summary_workflow"),
        "target_conversation_id": target_conversation_id,
        "replace": bool(replace),
    }
    digest = hashlib.sha256(_stable_payload_json(payload).encode("utf-8")).hexdigest()
    return f"ccs-{digest[:16]}"


def _build_compaction_marker_message(
    snapshot: Dict[str, Any],
) -> Dict[str, Any]:
    snapshot_id = str(snapshot.get("id") or snapshot.get("snapshot_id") or "").strip()
    target = str(snapshot.get("target_conversation_id") or "").strip()
    target_name = str(snapshot.get("target_conversation_name") or target or "").strip()
    created_at = str(snapshot.get("created_at") or "").strip()
    budget_status = str(snapshot.get("budget_status") or "").strip()
    marker_text = "Context compaction captured a working context snapshot."
    if target_name:
        marker_text = (
            f"Context compaction captured a working context snapshot for {target_name}. "
            "Full transcript remains saved here."
        )
    metadata = {
        "context_compaction_marker": {
            "snapshot_id": snapshot_id,
            "source_conversation_id": snapshot.get("source_conversation_id"),
            "target_conversation_id": target,
            "created_at": created_at,
            "source_message_count": snapshot.get("source_message_count"),
            "retained_start_index": snapshot.get("retained_start_index"),
            "retained_end_index": snapshot.get("retained_end_index"),
            "summary_method": snapshot.get("summary_method"),
            "summary_workflow": snapshot.get("summary_workflow"),
            "budget_status": budget_status,
        }
    }
    return {
        "id": f"context-compaction-marker-{snapshot_id}",
        "role": "system",
        "text": marker_text,
        "metadata": metadata,
    }


def persist_compaction_snapshot(
    result: Dict[str, Any],
    *,
    target_conversation_id: str,
    target_conversation_name: str,
    replace: bool,
    compacted_messages: List[Dict[str, Any]],
    context_budget_plan: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    snapshot_id = _build_snapshot_id(
        result,
        target_conversation_id=target_conversation_id,
        replace=replace,
    )
    source_conversation_id = str(result.get("source_conversation_id") or "").strip()
    snapshot = {
        "id": snapshot_id,
        "source_conversation_id": source_conversation_id,
        "source_conversation_name": result.get("source_conversation_name"),
        "target_conversation_id": target_conversation_id,
        "target_conversation_name": target_conversation_name,
        "replace": bool(replace),
        "created_at": result.get("created_at"),
        "source_message_count": result.get("source_message_count"),
        "omitted_messages": result.get("omitted_messages"),
        "retained_messages": result.get("retained_messages"),
        "retained_start_index": result.get("retained_start_index"),
        "retained_end_index": result.get("retained_end_index"),
        "summary_method": result.get("summary_method"),
        "summary_mode": result.get("summary_mode"),
        "summary_workflow": result.get("summary_workflow"),
        "summary_format_notes": result.get("summary_format_notes"),
        "summary_model": result.get("summary_model"),
        "summary_preview": result.get("summary_preview"),
        "budget_plan": context_budget_plan or None,
        "budget_status": (
            str(context_budget_plan.get("status") or "").strip()
            if isinstance(context_budget_plan, dict)
            else ""
        ),
        "messages": compacted_messages,
    }
    existing = conversation_store.load_context_snapshot(
        source_conversation_id, snapshot_id
    )
    if isinstance(existing, dict) and existing.get("marker_message_id"):
        snapshot["marker_message_id"] = existing.get("marker_message_id")
    snapshot = conversation_store.save_context_snapshot(
        source_conversation_id, snapshot
    )
    if replace:
        return snapshot

    source_messages = conversation_store.load_conversation(source_conversation_id)
    marker = _build_compaction_marker_message(snapshot)
    marker_id = marker.get("id")
    marker_exists = False
    for message in source_messages:
        if not isinstance(message, dict):
            continue
        if marker_id and str(message.get("id") or "").strip() == marker_id:
            marker_exists = True
            break
        metadata = message.get("metadata")
        marker_meta = (
            metadata.get("context_compaction_marker")
            if isinstance(metadata, dict)
            else None
        )
        if (
            isinstance(marker_meta, dict)
            and str(marker_meta.get("snapshot_id") or "").strip() == snapshot_id
        ):
            marker_exists = True
            break
    if not marker_exists:
        source_messages.append(marker)
        conversation_store.save_conversation(source_conversation_id, source_messages)
    snapshot["marker_message_id"] = marker_id
    return conversation_store.save_context_snapshot(source_conversation_id, snapshot)


def copy_compaction_lineage(
    source_conversation_id: str,
    target_conversation_id: str,
    *,
    parent_message_id: str = "",
) -> List[Dict[str, Any]]:
    refs = conversation_store.list_context_snapshot_refs(source_conversation_id)
    if not refs:
        return []
    selected_refs = refs
    parent_id = str(parent_message_id or "").strip()
    if parent_id:
        messages = conversation_store.load_conversation(source_conversation_id)
        parent_index = None
        index_by_message_id: Dict[str, int] = {}
        visible_snapshot_ids = set()
        for index, message in enumerate(messages):
            if not isinstance(message, dict):
                continue
            current_id = message_id(message, index)
            index_by_message_id[current_id] = index
            metadata = message.get("metadata")
            marker = (
                metadata.get("context_compaction_marker")
                if isinstance(metadata, dict)
                else None
            )
            if (
                isinstance(marker, dict)
                and str(marker.get("snapshot_id") or "").strip()
            ):
                visible_snapshot_ids.add(str(marker.get("snapshot_id")).strip())
            if current_id == parent_id and parent_index is None:
                parent_index = index
                break
        if parent_index is not None:
            selected_refs = []
            for ref in refs:
                if not isinstance(ref, dict):
                    continue
                snapshot_id = str(ref.get("id") or "").strip()
                marker_message_id = str(ref.get("marker_message_id") or "").strip()
                marker_index = index_by_message_id.get(marker_message_id)
                if snapshot_id and snapshot_id in visible_snapshot_ids:
                    selected_refs.append(ref)
                    continue
                if marker_index is not None and marker_index <= parent_index:
                    selected_refs.append(ref)
    copied_ids: List[str] = []
    for ref in selected_refs:
        snapshot_id = str(ref.get("id") or "").strip()
        if not snapshot_id:
            continue
        copied = conversation_store.copy_context_snapshot(
            source_conversation_id,
            target_conversation_id,
            snapshot_id,
        )
        copied_id = str(copied.get("id") or copied.get("snapshot_id") or "").strip()
        if copied_id:
            copied_ids.append(copied_id)
    target_refs = conversation_store.list_context_snapshot_refs(target_conversation_id)
    if copied_ids:
        allowed = set(copied_ids)
        return [
            ref
            for ref in target_refs
            if isinstance(ref, dict) and str(ref.get("id") or "").strip() in allowed
        ]
    return []


def summarize_omitted_messages(
    messages: List[Dict[str, Any]],
    *,
    max_summary_chars: int,
    prior_summaries: Optional[List[str]] = None,
) -> str:
    carry_forward = _carry_forward_prior_summaries(
        list(prior_summaries or []),
        max_chars=max(300, min(max_summary_chars // 2, 3200)),
    )
    role_counts: Dict[str, int] = {}
    total_tools = 0
    notable: List[str] = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "unknown")
        role_counts[role] = role_counts.get(role, 0) + 1
        total_tools += tool_count(message)
        text = message_text(message)
        if text and len(notable) < 12:
            notable.append(f"{index + 1}. {role}: {excerpt(text)}")

    role_summary = ", ".join(
        f"{role}={count}" for role, count in sorted(role_counts.items())
    )
    lines = [
        "Compacted earlier conversation context.",
        f"Omitted messages: {len(messages)}.",
        f"Role counts: {role_summary or 'none'}.",
        f"Tool/result payloads summarized: {total_tools}.",
    ]
    if carry_forward:
        lines.append("Prior carried summary:")
        lines.append(carry_forward)
    if notable:
        lines.append("Early-turn breadcrumbs:")
        lines.extend(notable)
    summary = "\n".join(lines)
    if len(summary) <= max_summary_chars:
        return summary
    return summary[: max(0, max_summary_chars - 3)].rstrip() + "..."


def _messages_for_llm_prompt(messages: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "unknown")
        text = excerpt(message_text(message), 420)
        tools = tool_count(message)
        tool_note = f" [tool payloads: {tools}]" if tools else ""
        if text:
            lines.append(f"{index + 1}. {role}{tool_note}: {text}")
        elif tool_note:
            lines.append(f"{index + 1}. {role}{tool_note}")
        if len("\n".join(lines)) > 16000:
            lines.append("... earlier context clipped for summarization prompt ...")
            break
    return "\n".join(lines)


def build_llm_summary_request(
    messages: List[Dict[str, Any]],
    *,
    max_summary_chars: int,
    summary_workflow: str = DEFAULT_SUMMARY_WORKFLOW,
    summary_format_notes: str = "",
    summary_model: str = "",
    prior_summaries: Optional[List[str]] = None,
) -> Dict[str, Any]:
    workflow_name = normalize_summary_workflow(summary_workflow)
    workflow = SUMMARY_WORKFLOWS[workflow_name]
    format_notes = normalize_summary_format_notes(summary_format_notes)
    model_name = normalize_summary_model(summary_model)
    carry_forward = _carry_forward_prior_summaries(
        list(prior_summaries or []),
        max_chars=4000,
    )
    context = ModelContext(
        system_prompt=(
            "You produce transparent context-compaction summaries for Float. "
            "The full transcript remains saved elsewhere; this summary is only a "
            "working-memory handoff. Preserve facts, decisions, provenance, and "
            "open work. Do not invent details."
        ),
        metadata={
            "workflow": build_workflow_metadata(
                resolve_workflow_profile(workflow.get("workflow_profile"))
            ),
            "compaction_workflow": {
                "name": workflow_name,
                "label": workflow.get("label"),
                "kind": "conversation_context_compaction",
                "summary_mode": SUMMARY_MODE_LLM,
                "max_summary_chars": int(max_summary_chars),
                "source_message_count": len(messages),
                "format_notes": format_notes,
            },
        },
    )
    prompt_lines = [
        "Summarize the omitted portion of this Float conversation.",
        f"Primary focus: {workflow.get('focus')}",
        f"Required structure: {workflow.get('format')}",
        f"Keep the final summary under {int(max_summary_chars)} characters.",
        (
            "Be concrete. Preserve tool outcomes, file paths, IDs, decisions, "
            "corrections, and user preferences. If something is uncertain, say so."
        ),
    ]
    if format_notes:
        prompt_lines.append(f"Additional format notes: {format_notes}")
    if carry_forward:
        prompt_lines.extend(
            [
                "",
                "Prior compaction summary to preserve and refresh if still relevant:",
                carry_forward,
            ]
        )
    prompt_lines.extend(
        [
            "",
            "Conversation excerpt to summarize:",
            _messages_for_llm_prompt(messages),
        ]
    )
    request = {
        "prompt": "\n".join(prompt_lines),
        "context": context,
        "session_id": f"conversation-compaction:{workflow_name}",
        "summary_workflow": workflow_name,
        "summary_format_notes": format_notes,
    }
    if model_name:
        request["model"] = model_name
    return request


def _llm_summary_text(
    messages: List[Dict[str, Any]],
    *,
    max_summary_chars: int,
    summary_workflow: str,
    summary_format_notes: str,
    summary_model: str,
    prior_summaries: Optional[List[str]],
    llm_summarizer: Optional[LlmSummarizer],
) -> Tuple[Optional[str], str]:
    if not callable(llm_summarizer):
        return None, "llm_summarizer_unavailable"
    request = build_llm_summary_request(
        messages,
        max_summary_chars=max_summary_chars,
        summary_workflow=summary_workflow,
        summary_format_notes=summary_format_notes,
        summary_model=summary_model,
        prior_summaries=prior_summaries,
    )
    try:
        raw = llm_summarizer(request)
    except Exception as exc:
        return None, f"llm_error: {exc}"
    if isinstance(raw, dict):
        text = raw.get("text")
        if not text:
            message = raw.get("message")
            if isinstance(message, dict):
                text = message.get("content") or message.get("text")
            elif message:
                text = message
        if not text:
            text = raw.get("content") or ""
    else:
        text = raw
    summary = " ".join(str(text or "").split())
    if not summary:
        return None, "llm_empty_summary"
    if len(summary) > max_summary_chars:
        summary = summary[: max(0, max_summary_chars - 3)].rstrip() + "..."
    return summary, ""


def default_target_conversation_id(
    source: str, created_at_epoch: Optional[float] = None
) -> str:
    safe_source = (
        str(source or "").replace("\\", "/").rstrip("/").split("/")[-1] or "chat"
    )
    created = int(time.time() if created_at_epoch is None else created_at_epoch)
    return f"compacted/{safe_source}-{created}"


def source_conversation_name(source: str) -> str:
    try:
        metadata = conversation_store._load_meta(source)  # type: ignore[attr-defined]
    except Exception:
        metadata = {}
    if isinstance(metadata, dict):
        display = str(metadata.get("display_name") or "").strip()
        if display:
            return display
    return str(source or "").strip()


def build_compaction(
    conversation_id: str,
    *,
    keep_last: int = DEFAULT_KEEP_LAST,
    max_summary_chars: int = DEFAULT_MAX_SUMMARY_CHARS,
    summary_mode: str = SUMMARY_MODE_DETERMINISTIC,
    summary_workflow: str = DEFAULT_SUMMARY_WORKFLOW,
    summary_format_notes: str = "",
    summary_model: str = "",
    context_budget_plan: Optional[Dict[str, Any]] = None,
    llm_summarizer: Optional[LlmSummarizer] = None,
    created_at_epoch: Optional[float] = None,
) -> Dict[str, Any]:
    started_at = time.perf_counter()
    source = str(conversation_id or "").strip()
    if not source:
        raise ValueError("conversation_id is required")

    keep_last = bounded_int(
        keep_last,
        default=DEFAULT_KEEP_LAST,
        minimum=1,
        maximum=MAX_KEEP_LAST,
    )
    max_summary_chars = bounded_int(
        max_summary_chars,
        default=DEFAULT_MAX_SUMMARY_CHARS,
        minimum=500,
        maximum=MAX_SUMMARY_CHARS,
    )
    requested_mode = normalize_summary_mode(summary_mode)
    requested_workflow = normalize_summary_workflow(summary_workflow)
    format_notes = normalize_summary_format_notes(summary_format_notes)
    model_name = normalize_summary_model(summary_model)

    messages = conversation_store.load_conversation(source)
    if not isinstance(messages, list) or not messages:
        raise FileNotFoundError(f"Conversation not found or empty: {source}")
    messages = compaction_relevant_messages(messages)
    if not messages:
        raise FileNotFoundError(f"Conversation not found or empty: {source}")

    retained = messages[-keep_last:]
    omitted = messages[: max(0, len(messages) - len(retained))]
    prior_summary_messages = [
        message for message in omitted if is_compaction_summary_message(message)
    ]
    prior_summaries = [
        message_text(message)
        for message in prior_summary_messages
        if message_text(message)
    ]
    omitted_for_summary = [
        message for message in omitted if not is_compaction_summary_message(message)
    ]
    fallback_reason = ""
    if requested_mode == SUMMARY_MODE_LLM and omitted:
        summary_text, fallback_reason = _llm_summary_text(
            omitted_for_summary,
            max_summary_chars=max_summary_chars,
            summary_workflow=requested_workflow,
            summary_format_notes=format_notes,
            summary_model=model_name,
            prior_summaries=prior_summaries,
            llm_summarizer=llm_summarizer,
        )
        summary_method = (
            SUMMARY_MODE_LLM if summary_text else SUMMARY_MODE_DETERMINISTIC
        )
    else:
        summary_text = None
        summary_method = SUMMARY_MODE_DETERMINISTIC

    if summary_text is None:
        summary_text = summarize_omitted_messages(
            omitted_for_summary,
            max_summary_chars=max_summary_chars,
            prior_summaries=prior_summaries,
        )

    created_at = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(time.time() if created_at_epoch is None else created_at_epoch),
    )
    source_name = source_conversation_name(source)
    summary_metadata = {
        "source_conversation_id": source,
        "source_conversation_name": source_name,
        "total_messages": len(messages),
        "omitted_messages": len(omitted),
        "retained_messages": len(retained),
        "created_at": created_at,
        "method": f"{summary_method}_summary",
        "requested_method": requested_mode,
        "summary_workflow": requested_workflow,
        "summary_format_notes": format_notes,
        "summary_model": model_name,
        "preview": True,
        "prior_compaction_summaries_carried": len(prior_summaries),
    }
    if isinstance(context_budget_plan, dict):
        summary_metadata["budget_plan"] = context_budget_plan
    if fallback_reason:
        summary_metadata["fallback_reason"] = fallback_reason
    summary_message = {
        "role": "system",
        "text": summary_text,
        "metadata": {"conversation_compaction": summary_metadata},
    }
    compacted = [summary_message, *retained] if omitted else list(retained)
    retained_start_index = len(omitted)
    target_id = default_target_conversation_id(source, created_at_epoch)
    target_name = (
        f"Compacted - {source_name}" if source_name else "Compacted conversation"
    )
    return {
        "status": "preview",
        "source_conversation_id": source,
        "source_conversation_name": source_name,
        "source_message_count": len(messages),
        "total_messages": len(messages),
        "compacted_messages": len(compacted),
        "omitted_messages": len(omitted),
        "retained_messages": len(retained),
        "retained_start_index": retained_start_index,
        "retained_end_index": len(messages) - 1 if retained else None,
        "summary_chars": len(summary_text),
        "summary_preview": summary_text,
        "summary_mode": requested_mode,
        "summary_method": summary_method,
        "summary_workflow": requested_workflow,
        "summary_format_notes": format_notes,
        "summary_model": model_name,
        "fallback_reason": fallback_reason,
        "budget_plan": context_budget_plan
        if isinstance(context_budget_plan, dict)
        else None,
        "created_at": created_at,
        "elapsed_ms": max(0, int((time.perf_counter() - started_at) * 1000)),
        "proposed_target_conversation_id": target_id,
        "proposed_target_conversation_name": target_name,
        "messages": compacted,
    }


def write_compaction(
    conversation_id: str,
    *,
    keep_last: int = DEFAULT_KEEP_LAST,
    max_summary_chars: int = DEFAULT_MAX_SUMMARY_CHARS,
    summary_mode: str = SUMMARY_MODE_DETERMINISTIC,
    summary_workflow: str = DEFAULT_SUMMARY_WORKFLOW,
    summary_format_notes: str = "",
    summary_model: str = "",
    target_conversation_id: str = "",
    replace: bool = False,
    context_budget_plan: Optional[Dict[str, Any]] = None,
    llm_summarizer: Optional[LlmSummarizer] = None,
    created_at_epoch: Optional[float] = None,
) -> Dict[str, Any]:
    started_at = time.perf_counter()
    result = build_compaction(
        conversation_id,
        keep_last=keep_last,
        max_summary_chars=max_summary_chars,
        summary_mode=summary_mode,
        summary_workflow=summary_workflow,
        summary_format_notes=summary_format_notes,
        summary_model=summary_model,
        context_budget_plan=context_budget_plan,
        llm_summarizer=llm_summarizer,
        created_at_epoch=created_at_epoch,
    )
    source = result["source_conversation_id"]
    target = source if replace else str(target_conversation_id or "").strip()
    if not target:
        target = result["proposed_target_conversation_id"]
    compacted = list(result.get("messages") or [])
    if compacted:
        first = compacted[0]
        if isinstance(first, dict):
            metadata = first.get("metadata")
            compaction = (
                metadata.get("conversation_compaction")
                if isinstance(metadata, dict)
                else None
            )
            if isinstance(compaction, dict):
                compaction["preview"] = False
                compaction["target_conversation_id"] = target
                compaction["replace"] = bool(replace)
                if isinstance(context_budget_plan, dict):
                    compaction["budget_plan"] = context_budget_plan
    conversation_store.save_conversation(target, compacted)
    display_name = str(result.get("proposed_target_conversation_name") or "").strip()
    if display_name:
        try:
            conversation_store.set_display_name(
                target,
                display_name,
                auto_generated=True,
                manual=False,
            )
        except Exception:
            pass
    snapshot = persist_compaction_snapshot(
        result,
        target_conversation_id=target,
        target_conversation_name=display_name or target,
        replace=replace,
        compacted_messages=compacted,
        context_budget_plan=context_budget_plan,
    )
    result["status"] = "written"
    result["target_conversation_id"] = target
    result["replace"] = bool(replace)
    result["context_snapshot"] = {
        "id": snapshot.get("id"),
        "marker_message_id": snapshot.get("marker_message_id"),
        "budget_status": snapshot.get("budget_status"),
    }
    result["elapsed_ms"] = max(0, int((time.perf_counter() - started_at) * 1000))
    result.pop("messages", None)
    return result
