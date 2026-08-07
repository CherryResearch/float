"""Shared tool availability and approval policy helpers."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

WORKFLOW_DISABLED = "disabled"
WORKFLOW_TEXT = "text"
WORKFLOW_LIVE = "live"
WORKFLOW_BOTH = "both"
WORKFLOW_SCOPES = {WORKFLOW_DISABLED, WORKFLOW_TEXT, WORKFLOW_LIVE, WORKFLOW_BOTH}

APPROVAL_LOW = "low"
APPROVAL_HIGH = "high"
APPROVAL_REQUIREMENTS = {APPROVAL_LOW, APPROVAL_HIGH}

CLIENT_RESOLUTION_TOOL_NAMES = {"camera.capture", "route_to_local_model"}

DEFAULT_LIVE_TOOL_NAMES = {
    "help",
    "tool_info",
    "list_actions",
    "list_dir",
    "read_file",
    "read_threads_summary",
    "list_tasks",
    "list_reflections",
    "remember",
    "recall",
    "route_to_local_model",
    "search_web",
    "capture.list",
}

LOW_APPROVAL_TOOL_NAMES = {
    *DEFAULT_LIVE_TOOL_NAMES,
    "read_capability_docs",
    "camera.capture",
    "computer.observe",
    "computer.windows.list",
    "compact_conversation_plan",
    "compact_conversation_preview",
    "crawl",
    "reflect",
    "read_action_diff",
    "route_to_local_model",
    "subchat",
}


def normalize_tool_workflow(value: Any, default: str = WORKFLOW_TEXT) -> str:
    raw = str(value or "").strip().lower()
    if raw in WORKFLOW_SCOPES:
        return raw
    if raw in {"off", "none", "false", "no", "disabled"}:
        return WORKFLOW_DISABLED
    if raw in {"chat", "normal", "text_chat", "text-mode", "text_mode"}:
        return WORKFLOW_TEXT
    if raw in {"voice", "stream", "realtime", "live_stream", "live-mode", "live_mode"}:
        return WORKFLOW_LIVE
    if raw in {"all", "enabled", "true", "yes"}:
        return WORKFLOW_BOTH
    fallback = str(default or "").strip().lower()
    return fallback if fallback in WORKFLOW_SCOPES else WORKFLOW_TEXT


def normalize_approval_requirement(value: Any, default: str = APPROVAL_HIGH) -> str:
    raw = str(value or "").strip().lower()
    if raw in APPROVAL_REQUIREMENTS:
        return raw
    if raw in {"auto", "allow", "allowed", "easy"}:
        return APPROVAL_LOW
    if raw in {"confirm", "manual", "review", "strict"}:
        return APPROVAL_HIGH
    fallback = str(default or "").strip().lower()
    return fallback if fallback in APPROVAL_REQUIREMENTS else APPROVAL_HIGH


def scope_allows_workflow(scope: Any, workflow: Any) -> bool:
    normalized_scope = normalize_tool_workflow(scope)
    normalized_workflow = normalize_tool_workflow(workflow)
    if normalized_scope == WORKFLOW_DISABLED:
        return False
    if normalized_scope == WORKFLOW_BOTH:
        return normalized_workflow in {WORKFLOW_TEXT, WORKFLOW_LIVE}
    return normalized_scope == normalized_workflow


def workflow_scope_to_flags(scope: Any) -> Dict[str, bool]:
    normalized = normalize_tool_workflow(scope)
    return {
        WORKFLOW_TEXT: normalized in {WORKFLOW_TEXT, WORKFLOW_BOTH},
        WORKFLOW_LIVE: normalized in {WORKFLOW_LIVE, WORKFLOW_BOTH},
    }


def flags_to_workflow_scope(flags: Mapping[str, Any]) -> str:
    text = bool(flags.get(WORKFLOW_TEXT))
    live = bool(flags.get(WORKFLOW_LIVE))
    if text and live:
        return WORKFLOW_BOTH
    if text:
        return WORKFLOW_TEXT
    if live:
        return WORKFLOW_LIVE
    return WORKFLOW_DISABLED


def default_tool_policy(tool_name: str) -> Dict[str, str]:
    name = str(tool_name or "").strip()
    workflow = WORKFLOW_BOTH if name in DEFAULT_LIVE_TOOL_NAMES else WORKFLOW_TEXT
    approval = APPROVAL_LOW if name in LOW_APPROVAL_TOOL_NAMES else APPROVAL_HIGH
    return {"workflow": workflow, "approval": approval}


def tool_name_safe_for_live_transport(tool_name: str) -> bool:
    """Return true when the tool name is safe for realtime function transport."""

    name = str(tool_name or "").strip()
    return bool(name) and all(char.isalnum() or char in {"_", "-"} for char in name)


def normalize_tool_policy(
    value: Any,
    *,
    tool_name: str = "",
    default: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    defaults = dict(default or default_tool_policy(tool_name))
    workflow = defaults.get("workflow", WORKFLOW_TEXT)
    approval = defaults.get("approval", APPROVAL_HIGH)

    if isinstance(value, Mapping):
        flags = value.get("workflows")
        if isinstance(flags, Mapping):
            workflow = flags_to_workflow_scope(flags)
        else:
            workflow = normalize_tool_workflow(
                value.get("workflow")
                or value.get("scope")
                or value.get("workflow_scope")
                or workflow,
                default=workflow,
            )
        approval = normalize_approval_requirement(
            value.get("approval") or value.get("approval_requirement") or approval,
            default=approval,
        )
    elif value is not None:
        workflow = normalize_tool_workflow(value, default=workflow)

    return {"workflow": workflow, "approval": approval}


def normalize_tool_policies(value: Any) -> Dict[str, Dict[str, str]]:
    if not isinstance(value, Mapping):
        return {}
    policies: Dict[str, Dict[str, str]] = {}
    for key, raw_policy in value.items():
        name = str(key or "").strip()
        if not name:
            continue
        policies[name] = normalize_tool_policy(raw_policy, tool_name=name)
    return policies


def effective_tool_policy(
    tool_name: str,
    settings: Optional[Mapping[str, Any]] = None,
) -> Dict[str, str]:
    name = str(tool_name or "").strip()
    defaults = default_tool_policy(name)
    raw_policies = (
        settings.get("tool_policies") if isinstance(settings, Mapping) else {}
    )
    if not isinstance(raw_policies, Mapping) or name not in raw_policies:
        return defaults
    return normalize_tool_policy(
        raw_policies.get(name), tool_name=name, default=defaults
    )


def tool_allowed_in_workflow(
    tool_name: str,
    workflow: str,
    settings: Optional[Mapping[str, Any]] = None,
) -> bool:
    policy = effective_tool_policy(tool_name, settings)
    return scope_allows_workflow(policy.get("workflow"), workflow)


def tool_auto_invokable_in_workflow(
    tool_name: str,
    workflow: str,
    settings: Optional[Mapping[str, Any]] = None,
) -> bool:
    normalized_workflow = normalize_tool_workflow(workflow)
    if not tool_allowed_in_workflow(tool_name, normalized_workflow, settings):
        return False
    if normalized_workflow != WORKFLOW_LIVE:
        return True
    policy = effective_tool_policy(tool_name, settings)
    if policy.get("approval") != APPROVAL_LOW:
        return False
    if not tool_name_safe_for_live_transport(str(tool_name or "")):
        return False
    return str(tool_name or "").strip() not in CLIENT_RESOLUTION_TOOL_NAMES


def approval_allows_auto_for_tool(
    approval_level: str | None,
    tool_name: str,
    settings: Optional[Mapping[str, Any]] = None,
) -> bool:
    normalized = str(approval_level or "all").strip().lower()
    if normalized == "auto":
        return True
    if normalized == "high":
        return (
            effective_tool_policy(tool_name, settings).get("approval") == APPROVAL_LOW
        )
    return False


def tool_policy_payload(
    tool_name: str,
    settings: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    name = str(tool_name or "").strip()
    defaults = default_tool_policy(name)
    policy = effective_tool_policy(name, settings)
    live_allowed = scope_allows_workflow(policy.get("workflow"), WORKFLOW_LIVE)
    unavailable_reason = ""
    if live_allowed and policy.get("approval") != APPROVAL_LOW:
        unavailable_reason = "high_approval_required"
    elif live_allowed and name in CLIENT_RESOLUTION_TOOL_NAMES:
        unavailable_reason = "client_resolution_required"
    elif live_allowed and not tool_name_safe_for_live_transport(name):
        unavailable_reason = "transport_unsafe_name"
    return {
        "workflow": policy["workflow"],
        "approval": policy["approval"],
        "workflows": workflow_scope_to_flags(policy["workflow"]),
        "defaults": {
            "workflow": defaults["workflow"],
            "approval": defaults["approval"],
            "workflows": workflow_scope_to_flags(defaults["workflow"]),
        },
        "overridden": policy != defaults,
        "live_auto": tool_auto_invokable_in_workflow(name, WORKFLOW_LIVE, settings),
        "live_unavailable_reason": unavailable_reason,
    }
