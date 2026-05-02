"""Conversation maintenance tools."""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.services.conversation_compaction import (
    DEFAULT_CONTEXT_WINDOW_TOKENS,
    DEFAULT_HARD_TRIGGER_RATIO,
    DEFAULT_KEEP_LAST,
    DEFAULT_MAX_SUMMARY_CHARS,
    DEFAULT_RESERVE_OUTPUT_TOKENS,
    DEFAULT_RESERVE_RETRIEVAL_TOKENS,
    DEFAULT_RESERVE_SYSTEM_TOKENS,
    DEFAULT_RESERVE_TOOL_TOKENS,
    DEFAULT_SOFT_TRIGGER_RATIO,
    DEFAULT_SUMMARY_WORKFLOW,
    build_compaction,
    build_context_budget_plan,
    source_conversation_name,
    write_compaction,
)
from app.utils import conversation_store, verify_signature


def _llm_summarizer(request: Dict[str, Any]) -> Any:
    """Use the configured Float LLM runtime when available; callers fall back."""

    from app import routes as routes_module

    if not isinstance(request, dict):
        request = {"prompt": str(request or "")}
    return routes_module.llm_service.generate(
        request.get("prompt") or "",
        session_id=str(request.get("session_id") or "conversation-compaction"),
        context=request.get("context"),
        model=request.get("model"),
    )


def _signature_payload(
    *,
    conversation_id: str,
    keep_last: int,
    max_summary_chars: int,
    summary_mode: str,
    summary_workflow: str,
    summary_format_notes: str,
    summary_model: str,
    target_conversation_id: str = "",
    replace: Optional[bool] = None,
    write: Optional[bool] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "conversation_id": conversation_id,
        "keep_last": keep_last,
        "max_summary_chars": max_summary_chars,
        "summary_mode": summary_mode,
    }
    if (
        str(summary_workflow or "").strip()
        and summary_workflow != DEFAULT_SUMMARY_WORKFLOW
    ):
        payload["summary_workflow"] = summary_workflow
    if str(summary_format_notes or "").strip():
        payload["summary_format_notes"] = summary_format_notes
    if str(summary_model or "").strip():
        payload["summary_model"] = summary_model
    if target_conversation_id:
        payload["target_conversation_id"] = target_conversation_id
    if replace is not None:
        payload["replace"] = bool(replace)
    if write is not None:
        payload["write"] = bool(write)
    return payload


def _plan_signature_payload(
    *,
    conversation_id: str,
    context_window_tokens: int,
    reserve_output_tokens: int,
    reserve_tool_tokens: int,
    reserve_retrieval_tokens: int,
    reserve_system_tokens: int,
    soft_trigger_ratio: float,
    hard_trigger_ratio: float,
    summary_mode: str,
    summary_workflow: str,
    summary_format_notes: str,
    summary_model: str,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "conversation_id": conversation_id,
        "context_window_tokens": context_window_tokens,
        "summary_mode": summary_mode,
    }
    if reserve_output_tokens != DEFAULT_RESERVE_OUTPUT_TOKENS:
        payload["reserve_output_tokens"] = reserve_output_tokens
    if reserve_tool_tokens != DEFAULT_RESERVE_TOOL_TOKENS:
        payload["reserve_tool_tokens"] = reserve_tool_tokens
    if reserve_retrieval_tokens != DEFAULT_RESERVE_RETRIEVAL_TOKENS:
        payload["reserve_retrieval_tokens"] = reserve_retrieval_tokens
    if reserve_system_tokens != DEFAULT_RESERVE_SYSTEM_TOKENS:
        payload["reserve_system_tokens"] = reserve_system_tokens
    if float(soft_trigger_ratio) != float(DEFAULT_SOFT_TRIGGER_RATIO):
        payload["soft_trigger_ratio"] = soft_trigger_ratio
    if float(hard_trigger_ratio) != float(DEFAULT_HARD_TRIGGER_RATIO):
        payload["hard_trigger_ratio"] = hard_trigger_ratio
    if (
        str(summary_workflow or "").strip()
        and summary_workflow != DEFAULT_SUMMARY_WORKFLOW
    ):
        payload["summary_workflow"] = summary_workflow
    if str(summary_format_notes or "").strip():
        payload["summary_format_notes"] = summary_format_notes
    if str(summary_model or "").strip():
        payload["summary_model"] = summary_model
    return payload


def compact_conversation_plan(
    conversation_id: str,
    context_window_tokens: int = DEFAULT_CONTEXT_WINDOW_TOKENS,
    reserve_output_tokens: int = DEFAULT_RESERVE_OUTPUT_TOKENS,
    reserve_tool_tokens: int = DEFAULT_RESERVE_TOOL_TOKENS,
    reserve_retrieval_tokens: int = DEFAULT_RESERVE_RETRIEVAL_TOKENS,
    reserve_system_tokens: int = DEFAULT_RESERVE_SYSTEM_TOKENS,
    soft_trigger_ratio: float = DEFAULT_SOFT_TRIGGER_RATIO,
    hard_trigger_ratio: float = DEFAULT_HARD_TRIGGER_RATIO,
    summary_mode: str = "deterministic",
    summary_workflow: str = "conversation_handoff",
    summary_format_notes: str = "",
    summary_model: str = "",
    *,
    user: str,
    signature: str,
) -> Dict[str, Any]:
    """Plan conversation compaction from a runtime context budget."""

    payload = _plan_signature_payload(
        conversation_id=conversation_id,
        context_window_tokens=context_window_tokens,
        reserve_output_tokens=reserve_output_tokens,
        reserve_tool_tokens=reserve_tool_tokens,
        reserve_retrieval_tokens=reserve_retrieval_tokens,
        reserve_system_tokens=reserve_system_tokens,
        soft_trigger_ratio=soft_trigger_ratio,
        hard_trigger_ratio=hard_trigger_ratio,
        summary_mode=summary_mode,
        summary_workflow=summary_workflow,
        summary_format_notes=summary_format_notes,
        summary_model=summary_model,
    )
    verify_signature(signature, user, "compact_conversation_plan", payload)
    messages = conversation_store.load_conversation(conversation_id)
    if not isinstance(messages, list) or not messages:
        raise FileNotFoundError(f"Conversation not found or empty: {conversation_id}")
    plan = build_context_budget_plan(
        messages,
        conversation_id=conversation_id,
        context_window_tokens=context_window_tokens,
        reserve_output_tokens=reserve_output_tokens,
        reserve_tool_tokens=reserve_tool_tokens,
        reserve_retrieval_tokens=reserve_retrieval_tokens,
        reserve_system_tokens=reserve_system_tokens,
        soft_trigger_ratio=soft_trigger_ratio,
        hard_trigger_ratio=hard_trigger_ratio,
    )
    plan["source_conversation_name"] = source_conversation_name(conversation_id)
    plan["summary_mode"] = summary_mode
    plan["summary_workflow"] = summary_workflow
    plan["summary_format_notes"] = summary_format_notes
    plan["summary_model"] = summary_model
    plan["recommended_preview_payload"] = {
        "conversation_id": conversation_id,
        "keep_last": plan.get("recommended_keep_last"),
        "max_summary_chars": plan.get("recommended_summary_chars"),
        "summary_mode": summary_mode,
        "summary_workflow": summary_workflow,
        "summary_format_notes": summary_format_notes,
        "summary_model": summary_model,
    }
    plan["recommended_write_payload"] = {
        **plan["recommended_preview_payload"],
        "target_conversation_id": "",
        "replace": False,
    }
    return plan


def compact_conversation_preview(
    conversation_id: str,
    keep_last: int = DEFAULT_KEEP_LAST,
    max_summary_chars: int = DEFAULT_MAX_SUMMARY_CHARS,
    summary_mode: str = "deterministic",
    summary_workflow: str = "conversation_handoff",
    summary_format_notes: str = "",
    summary_model: str = "",
    *,
    user: str,
    signature: str,
) -> Dict[str, Any]:
    """Preview a compacted copy of a persisted conversation."""

    payload = _signature_payload(
        conversation_id=conversation_id,
        keep_last=keep_last,
        max_summary_chars=max_summary_chars,
        summary_mode=summary_mode,
        summary_workflow=summary_workflow,
        summary_format_notes=summary_format_notes,
        summary_model=summary_model,
    )
    verify_signature(signature, user, "compact_conversation_preview", payload)
    use_llm = str(summary_mode or "").strip().lower() == "llm"
    return build_compaction(
        conversation_id,
        keep_last=keep_last,
        max_summary_chars=max_summary_chars,
        summary_mode=summary_mode,
        summary_workflow=summary_workflow,
        summary_format_notes=summary_format_notes,
        summary_model=summary_model,
        llm_summarizer=_llm_summarizer if use_llm else None,
    )


def compact_conversation_write(
    conversation_id: str,
    keep_last: int = DEFAULT_KEEP_LAST,
    max_summary_chars: int = DEFAULT_MAX_SUMMARY_CHARS,
    summary_mode: str = "deterministic",
    summary_workflow: str = "conversation_handoff",
    summary_format_notes: str = "",
    summary_model: str = "",
    target_conversation_id: str = "",
    replace: bool = False,
    *,
    user: str,
    signature: str,
) -> Dict[str, Any]:
    """Write a compacted copy of a persisted conversation."""

    payload = _signature_payload(
        conversation_id=conversation_id,
        keep_last=keep_last,
        max_summary_chars=max_summary_chars,
        summary_mode=summary_mode,
        summary_workflow=summary_workflow,
        summary_format_notes=summary_format_notes,
        summary_model=summary_model,
        target_conversation_id=target_conversation_id,
        replace=replace,
    )
    verify_signature(signature, user, "compact_conversation_write", payload)
    use_llm = str(summary_mode or "").strip().lower() == "llm"
    return write_compaction(
        conversation_id,
        keep_last=keep_last,
        max_summary_chars=max_summary_chars,
        summary_mode=summary_mode,
        summary_workflow=summary_workflow,
        summary_format_notes=summary_format_notes,
        summary_model=summary_model,
        target_conversation_id=target_conversation_id,
        replace=replace,
        llm_summarizer=_llm_summarizer if use_llm else None,
    )


def compact_conversation(
    conversation_id: str,
    keep_last: int = DEFAULT_KEEP_LAST,
    max_summary_chars: int = DEFAULT_MAX_SUMMARY_CHARS,
    write: bool = False,
    target_conversation_id: str = "",
    replace: bool = False,
    summary_mode: str = "deterministic",
    summary_workflow: str = "conversation_handoff",
    summary_format_notes: str = "",
    summary_model: str = "",
    *,
    user: str,
    signature: str,
) -> Dict[str, Any]:
    """Backward-compatible wrapper for older callers.

    New UI/tool flows should use compact_conversation_preview or
    compact_conversation_write so approval policy can distinguish reads from
    writes.
    """

    payload = _signature_payload(
        conversation_id=conversation_id,
        keep_last=keep_last,
        max_summary_chars=max_summary_chars,
        summary_mode=summary_mode,
        summary_workflow=summary_workflow,
        summary_format_notes=summary_format_notes,
        summary_model=summary_model,
        target_conversation_id=target_conversation_id,
        replace=replace,
        write=write,
    )
    verify_signature(signature, user, "compact_conversation", payload)
    use_llm = str(summary_mode or "").strip().lower() == "llm"
    if write:
        return write_compaction(
            conversation_id,
            keep_last=keep_last,
            max_summary_chars=max_summary_chars,
            summary_mode=summary_mode,
            summary_workflow=summary_workflow,
            summary_format_notes=summary_format_notes,
            summary_model=summary_model,
            target_conversation_id=target_conversation_id,
            replace=replace,
            llm_summarizer=_llm_summarizer if use_llm else None,
        )
    return build_compaction(
        conversation_id,
        keep_last=keep_last,
        max_summary_chars=max_summary_chars,
        summary_mode=summary_mode,
        summary_workflow=summary_workflow,
        summary_format_notes=summary_format_notes,
        summary_model=summary_model,
        llm_summarizer=_llm_summarizer if use_llm else None,
    )
