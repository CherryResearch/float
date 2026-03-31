from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

from app import config as app_config

WORKFLOW_DEFAULT = "default"

BUILTIN_WORKFLOWS: Dict[str, Dict[str, Any]] = {
    "default": {
        "id": "default",
        "label": "Default",
        "description": "Balanced reasoning with normal tool access and moderate latency.",
        "thinking_default": "auto",
        "preferred_continue": "mini_execution",
        "allow_continue_to": ["default", "mini_execution"],
        "enabled_modules": [
            "computer_use",
            "camera_capture",
            "memory_promotion",
        ],
    },
    "architect_planner": {
        "id": "architect_planner",
        "label": "Architect / Planner",
        "description": "Higher-reasoning planning workflow that prefers decomposition and explicit handoff.",
        "thinking_default": "high",
        "preferred_continue": "mini_execution",
        "allow_continue_to": ["architect_planner", "default", "mini_execution"],
        "enabled_modules": [
            "computer_use",
            "camera_capture",
            "memory_promotion",
            "host_shell",
        ],
    },
    "mini_execution": {
        "id": "mini_execution",
        "label": "Mini Execution",
        "description": "Short, low-latency execution bursts for in-between tool steps and recursive continue loops.",
        "thinking_default": "low",
        "preferred_continue": "mini_execution",
        "allow_continue_to": ["mini_execution"],
        "enabled_modules": [
            "computer_use",
            "camera_capture",
        ],
    },
}

BUILTIN_MODULES: Dict[str, Dict[str, Any]] = {
    "computer_use": {
        "id": "computer_use",
        "label": "Computer Use",
        "description": "Browser and desktop observation plus direct UI actions.",
        "status": "live",
    },
    "camera_capture": {
        "id": "camera_capture",
        "label": "Camera Capture",
        "description": "Still-image capture from a connected camera via the client.",
        "status": "experimental",
    },
    "memory_promotion": {
        "id": "memory_promotion",
        "label": "Memory Promotion",
        "description": "Promote transient captures into durable attachments and later memory workflows.",
        "status": "live",
    },
    "host_shell": {
        "id": "host_shell",
        "label": "Host Shell",
        "description": "Approval-gated shell, patch, and host mutation tools.",
        "status": "live",
    },
}

CLIENT_RESOLUTION_TOOLS = {"camera.capture"}

TRUST_TIER_MAP: Dict[str, int] = {
    "computer.observe": 1,
    "camera.capture": 1,
    "capture.list": 1,
    "computer.session.start": 2,
    "computer.session.stop": 2,
    "computer.navigate": 2,
    "computer.act": 2,
    "computer.windows.list": 2,
    "computer.windows.focus": 2,
    "computer.app.launch": 2,
    "capture.promote": 3,
    "capture.delete": 3,
    "shell.exec": 3,
    "patch.apply": 3,
    "mcp.call": 3,
}


def resolve_workflow_profile(value: str | None) -> Dict[str, Any]:
    raw = str(value or "").strip().lower()
    if raw in BUILTIN_WORKFLOWS:
        return dict(BUILTIN_WORKFLOWS[raw])
    return dict(BUILTIN_WORKFLOWS[WORKFLOW_DEFAULT])


def resolve_workflow_name(value: str | None) -> str:
    return str(resolve_workflow_profile(value).get("id") or WORKFLOW_DEFAULT)


def resolve_modules(
    workflow_name: str | None,
    requested_modules: Iterable[str] | None = None,
) -> List[str]:
    workflow = resolve_workflow_profile(workflow_name)
    requested = {
        str(item or "").strip()
        for item in (requested_modules or [])
        if str(item or "").strip() in BUILTIN_MODULES
    }
    defaults = set(workflow.get("enabled_modules") or [])
    return sorted(defaults | requested)


def workflow_prompt(
    workflow_name: str | None,
    *,
    modules: Iterable[str] | None = None,
) -> str:
    workflow = resolve_workflow_profile(workflow_name)
    enabled = resolve_modules(workflow.get("id"), modules)
    enabled_labels = [
        str(BUILTIN_MODULES[module_id]["label"])
        for module_id in enabled
        if module_id in BUILTIN_MODULES
    ]
    workflow_id = str(workflow.get("id") or WORKFLOW_DEFAULT)
    if workflow_id == "architect_planner":
        guidance = (
            "Operate in architect/planner mode: decompose tasks carefully, keep the plan coherent, "
            "and prefer explicit handoff into shorter execution bursts rather than long mutation chains."
        )
    elif workflow_id == "mini_execution":
        guidance = (
            "Operate in mini-execution mode: favor short, low-latency tool loops, minimal narration, "
            "and narrowly scoped follow-up steps."
        )
    else:
        guidance = (
            "Operate in the default workflow: balance reasoning quality with execution speed and use tools directly when helpful."
        )
    modules_text = (
        f" Enabled modules: {', '.join(enabled_labels)}."
        if enabled_labels
        else " No optional modules are enabled."
    )
    return guidance + modules_text


def capture_policy_prompt(
    *,
    retention_days: int,
    default_sensitivity: str,
    raw_image_access: bool,
    summary_fallback: bool,
) -> str:
    raw_text = "Raw capture images are available to the model when policy allows." if raw_image_access else "Raw capture images may be hidden unless explicitly promoted or approved."
    summary_text = "Summary fallback is allowed when raw image access is restricted." if summary_fallback else "Do not assume summary fallback is available when raw image access is restricted."
    return (
        "Capture policy: computer observations and camera captures are transient by default. "
        f"They are retained for about {max(0, int(retention_days))} day(s) unless promoted with capture.promote. "
        f"The default sensitivity is '{default_sensitivity}'. "
        f"{raw_text} {summary_text} "
        "Promoted captures become durable attachments that later turns can reference again."
    )


def trust_tier_for_tool(tool_name: str) -> int:
    return int(TRUST_TIER_MAP.get(str(tool_name or "").strip(), 4))


def approval_allows_auto(approval_level: str | None, tool_name: str) -> bool:
    normalized = str(approval_level or "all").strip().lower()
    if normalized == "auto":
        return True
    if normalized == "high":
        return trust_tier_for_tool(tool_name) <= 2
    return False


def continue_transition_allowed(current_workflow: str | None, next_workflow: str | None) -> bool:
    current = resolve_workflow_profile(current_workflow)
    next_name = resolve_workflow_name(next_workflow)
    allowed = current.get("allow_continue_to") or []
    return next_name in allowed


def addons_root() -> Path:
    root = (app_config.REPO_ROOT / "data" / "modules" / "addons").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def list_addons() -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    root = addons_root()
    for path in sorted(root.iterdir()):
        if not path.is_file() or path.suffix.lower() not in {".json"}:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        entries.append(
            {
                "id": str(payload.get("id") or path.stem),
                "label": str(payload.get("label") or path.stem),
                "description": str(payload.get("description") or "").strip(),
                "status": str(payload.get("status") or "available"),
                "path": str(path),
            }
        )
    return entries


def workflow_catalog_payload() -> Dict[str, Any]:
    return {
        "workflows": [dict(value) for value in BUILTIN_WORKFLOWS.values()],
        "modules": [dict(value) for value in BUILTIN_MODULES.values()],
        "addons": list_addons(),
        "addons_root": str(addons_root()),
    }
