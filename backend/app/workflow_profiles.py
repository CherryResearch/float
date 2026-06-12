from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set

from app import config as app_config

WORKFLOW_DEFAULT = "default"
SKILL_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")

BUILTIN_WORKFLOWS: Dict[str, Dict[str, Any]] = {
    "default": {
        "id": "default",
        "label": "Default",
        "description": "Balanced reasoning with normal tool access and moderate latency.",
        "role": "general",
        "latency_tier": "interactive",
        "delegation_mode": "direct",
        "thinking_default": "auto",
        "preferred_continue": "mini_execution",
        "allow_continue_to": ["default", "mini_execution"],
        "supports_background": False,
        "supports_live": False,
        "enabled_modules": ["computer_use"],
    },
    "architect_planner": {
        "id": "architect_planner",
        "label": "Architect / Planner",
        "description": "Higher-reasoning planning workflow that prefers decomposition and explicit handoff.",
        "role": "architect",
        "latency_tier": "deliberate",
        "delegation_mode": "delegate",
        "thinking_default": "high",
        "preferred_continue": "mini_execution",
        "allow_continue_to": ["architect_planner", "default", "mini_execution"],
        "supports_background": False,
        "supports_live": False,
        "enabled_modules": ["computer_use"],
    },
    "mini_execution": {
        "id": "mini_execution",
        "label": "Mini Execution",
        "description": "Short, low-latency execution bursts for in-between tool steps and recursive continue loops.",
        "role": "worker",
        "latency_tier": "fast",
        "delegation_mode": "execute",
        "thinking_default": "low",
        "preferred_continue": "mini_execution",
        "allow_continue_to": ["mini_execution"],
        "supports_background": False,
        "supports_live": False,
        "enabled_modules": ["computer_use"],
    },
    "background_reflection": {
        "id": "background_reflection",
        "label": "Background Reflection",
        "description": "Bounded background thinking over memories, recent conversations, and unresolved questions.",
        "role": "background",
        "latency_tier": "deliberate",
        "delegation_mode": "introspection",
        "thinking_default": "low",
        "preferred_continue": "mini_execution",
        "allow_continue_to": ["background_reflection", "mini_execution", "default"],
        "supports_background": True,
        "supports_live": False,
        "enabled_modules": [],
    },
}

BUILTIN_MODULES: Dict[str, Dict[str, Any]] = {
    "computer_use": {
        "id": "computer_use",
        "label": "Computer Use",
        "description": "Browser and desktop observation, camera capture, capture promotion, and approval-gated host actions.",
        "status": "live",
        "skill_id": "computer_use",
        "tool_names": [
            "computer.session.start",
            "computer.session.stop",
            "computer.observe",
            "computer.act",
            "computer.navigate",
            "computer.windows.list",
            "computer.windows.focus",
            "computer.app.launch",
            "camera.capture",
            "capture.list",
            "capture.promote",
            "capture.delete",
            "shell.exec",
            "patch.apply",
            "mcp.call",
        ],
    },
}

MODULE_ALIASES: Dict[str, str] = {
    "camera_capture": "computer_use",
    "memory_promotion": "computer_use",
    "host_shell": "computer_use",
}

CLIENT_RESOLUTION_TOOLS = {"camera.capture", "route_to_local_model"}

TRUST_TIER_MAP: Dict[str, int] = {
    "computer.observe": 1,
    "camera.capture": 1,
    "route_to_local_model": 1,
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


def normalize_module_id(value: str | None) -> str:
    raw = str(value or "").strip()
    if raw in BUILTIN_MODULES:
        return raw
    if raw in _custom_module_ids():
        return raw
    return str(MODULE_ALIASES.get(raw) or "")


def resolve_modules(
    workflow_name: str | None,
    requested_modules: Iterable[str] | None = None,
    *,
    include_workflow_defaults: bool = True,
) -> List[str]:
    workflow = resolve_workflow_profile(workflow_name)
    requested = {
        normalize_module_id(str(item or "").strip())
        for item in (requested_modules or [])
        if normalize_module_id(str(item or "").strip())
    }
    defaults = (
        set(workflow.get("enabled_modules") or [])
        if include_workflow_defaults
        else set()
    )
    return sorted(defaults | requested)


def workflow_prompt(
    workflow_name: str | None,
    *,
    modules: Iterable[str] | None = None,
    include_default_modules: bool = True,
) -> str:
    workflow = resolve_workflow_profile(workflow_name)
    if include_default_modules:
        enabled = resolve_modules(workflow.get("id"), modules)
    else:
        enabled = [
            normalize_module_id(str(module_id or "").strip())
            for module_id in (modules or [])
            if normalize_module_id(str(module_id or "").strip())
        ]
    enabled_labels = [
        str(BUILTIN_MODULES[module_id]["label"])
        for module_id in enabled
        if module_id in BUILTIN_MODULES
    ]
    workflow_id = str(workflow.get("id") or WORKFLOW_DEFAULT)
    if workflow_id == "architect_planner":
        guidance = "Plan carefully. Decompose the task, keep the plan coherent, and hand off into shorter execution bursts."
    elif workflow_id == "mini_execution":
        guidance = "Keep this turn short and execution-focused. Prefer minimal narration and narrowly scoped follow-up steps."
    else:
        guidance = "Balance reasoning quality with execution speed and use tools directly when helpful."
    modules_text = (
        f" Enabled modules this turn: {', '.join(enabled_labels)}."
        if enabled_labels
        else ""
    )
    return guidance + modules_text


def capture_policy_prompt(
    *,
    retention_days: int,
    default_sensitivity: str,
    raw_image_access: bool,
    summary_fallback: bool,
) -> str:
    raw_text = (
        "Raw capture images are available to the model when policy allows."
        if raw_image_access
        else "Raw capture images may be hidden unless explicitly promoted or approved."
    )
    summary_text = (
        "Summary fallback is allowed when raw image access is restricted."
        if summary_fallback
        else "Do not assume summary fallback is available when raw image access is restricted."
    )
    return (
        "Computer observations and camera captures are transient by default. "
        f"They are retained for about {max(0, int(retention_days))} day(s) unless promoted with capture.promote. "
        f"Default sensitivity: '{default_sensitivity}'. "
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


def continue_transition_allowed(
    current_workflow: str | None, next_workflow: str | None
) -> bool:
    current = resolve_workflow_profile(current_workflow)
    next_name = resolve_workflow_name(next_workflow)
    allowed = current.get("allow_continue_to") or []
    return next_name in allowed


def repo_addons_root() -> Path:
    root = (app_config.REPO_ROOT / "modules" / "addons").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def repo_skills_root() -> Path:
    return (app_config.REPO_ROOT / "modules" / "skills").resolve()


def local_skills_root() -> Path:
    return (app_config.REPO_ROOT / "data" / "modules" / "skills").resolve()


def skill_roots() -> List[Path]:
    return [repo_skills_root(), local_skills_root()]


def addons_root() -> Path:
    root = (app_config.REPO_ROOT / "data" / "modules" / "addons").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def addon_roots() -> List[Path]:
    # Repo add-ons are the tracked defaults; data/ add-ons are local overrides.
    return [repo_addons_root(), addons_root()]


def list_addons() -> List[Dict[str, Any]]:
    entries_by_id: Dict[str, Dict[str, Any]] = {}
    for source, root in (("repo", repo_addons_root()), ("local", addons_root())):
        for path in sorted(root.iterdir()):
            if not path.is_file() or path.suffix.lower() not in {".json"}:
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            addon_id = str(payload.get("id") or path.stem)
            entries_by_id[addon_id] = {
                "id": addon_id,
                "label": str(payload.get("label") or path.stem),
                "description": str(payload.get("description") or "").strip(),
                "status": str(payload.get("status") or "available"),
                "path": str(path),
                "source": source,
            }
    return sorted(entries_by_id.values(), key=lambda item: item["id"])


def _coerce_tool_names(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    seen: Set[str] = set()
    names: List[str] = []
    for item in value:
        name = str(item or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def _iter_addon_module_entries(
    payload: Dict[str, Any],
    *,
    source: str,
    path: Path,
) -> Iterable[Dict[str, Any]]:
    addon_id = str(payload.get("id") or path.stem).strip() or path.stem
    addon_label = str(payload.get("label") or path.stem).strip() or path.stem
    candidates: List[Any] = []
    if isinstance(payload.get("modules"), list):
        candidates.extend(payload.get("modules") or [])
    if isinstance(payload.get("module"), dict):
        candidates.append(payload.get("module"))
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        module_id = str(candidate.get("id") or "").strip()
        if not module_id:
            continue
        label = str(candidate.get("label") or module_id.replace("_", " ")).strip()
        skill_id = str(candidate.get("skill_id") or module_id).strip()
        yield {
            "id": module_id,
            "label": label or module_id,
            "description": str(candidate.get("description") or "").strip(),
            "status": str(
                candidate.get("status") or payload.get("status") or "available"
            ),
            "skill_id": skill_id,
            "tool_names": _coerce_tool_names(candidate.get("tool_names")),
            "source": source,
            "source_path": str(path),
            "addon_id": addon_id,
            "addon_label": addon_label,
        }


def list_custom_modules() -> List[Dict[str, Any]]:
    entries_by_id: Dict[str, Dict[str, Any]] = {}
    for root_source, root in (("repo", repo_addons_root()), ("custom", addons_root())):
        for path in sorted(root.iterdir()):
            if not path.is_file() or path.suffix.lower() != ".json":
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                payload = {}
            if not isinstance(payload, dict):
                continue
            for entry in _iter_addon_module_entries(
                payload,
                source=root_source,
                path=path,
            ):
                entries_by_id[str(entry["id"])] = entry
    return sorted(entries_by_id.values(), key=lambda item: str(item["id"]))


def _custom_module_ids() -> Set[str]:
    return {str(item.get("id") or "") for item in list_custom_modules()}


def _skill_summary_from_text(text: str) -> str:
    for raw_line in text.splitlines():
        line = str(raw_line or "").strip()
        if not line or line.startswith("#"):
            continue
        return line
    return ""


def _skill_entry_from_path(
    path: Path,
    *,
    source: str,
    include_body: bool,
) -> Dict[str, Any] | None:
    try:
        body = path.read_text(encoding="utf-8").strip()
    except Exception:
        return None
    entry: Dict[str, Any] = {
        "id": path.stem,
        "label": path.stem.replace("_", " "),
        "summary": _skill_summary_from_text(body),
        "path": str(path),
        "source": source,
    }
    if include_body:
        entry["body"] = body
    return entry


def list_skills(*, include_body: bool = False) -> List[Dict[str, Any]]:
    entries_by_id: Dict[str, Dict[str, Any]] = {}
    for source, root in (("repo", repo_skills_root()), ("local", local_skills_root())):
        if not root.exists():
            continue
        for path in sorted(root.glob("*.md")):
            if path.name.lower() == "readme.md":
                continue
            entry = _skill_entry_from_path(
                path,
                source=source,
                include_body=include_body,
            )
            if entry is not None:
                entries_by_id[str(entry["id"])] = entry
    return sorted(entries_by_id.values(), key=lambda item: str(item["id"]))


def get_skill_entry(
    skill_id: str | None,
    *,
    include_body: bool = False,
) -> Dict[str, Any] | None:
    normalized = str(skill_id or "").strip()
    if not normalized:
        return None
    for source, root in (("local", local_skills_root()), ("repo", repo_skills_root())):
        path = root / f"{normalized}.md"
        if not path.exists():
            continue
        entry = _skill_entry_from_path(
            path,
            source=source,
            include_body=include_body,
        )
        if entry is not None:
            return entry
    return None


def normalize_skill_id(value: str | None) -> str:
    normalized = str(value or "").strip()
    if not normalized or not SKILL_ID_PATTERN.fullmatch(normalized):
        return ""
    return normalized


def skill_doc_payload(
    skill_id: str | None,
    *,
    include_body: bool = True,
) -> Dict[str, Any] | None:
    normalized = normalize_skill_id(skill_id)
    if not normalized:
        return None
    repo_path = repo_skills_root() / f"{normalized}.md"
    local_path = local_skills_root() / f"{normalized}.md"
    active = get_skill_entry(normalized, include_body=include_body)
    return {
        "id": normalized,
        "doc_id": f"skills:{normalized}",
        "repo_path": str(repo_path),
        "local_path": str(local_path),
        "repo_exists": repo_path.exists(),
        "local_exists": local_path.exists(),
        "active": active,
    }


def write_local_skill_doc(skill_id: str | None, body: str) -> Dict[str, Any]:
    normalized = normalize_skill_id(skill_id)
    if not normalized:
        raise ValueError("Invalid skill id.")
    root = local_skills_root()
    root.mkdir(parents=True, exist_ok=True)
    target = (root / f"{normalized}.md").resolve()
    try:
        target.relative_to(root.resolve())
    except Exception as exc:
        raise ValueError("Invalid skill path.") from exc
    target.write_text(str(body or ""), encoding="utf-8")
    payload = skill_doc_payload(normalized, include_body=True)
    if payload is None:
        raise ValueError("Unable to read saved skill doc.")
    return payload


def delete_local_skill_doc(skill_id: str | None) -> Dict[str, Any]:
    normalized = normalize_skill_id(skill_id)
    if not normalized:
        raise ValueError("Invalid skill id.")
    root = local_skills_root()
    target = (root / f"{normalized}.md").resolve()
    try:
        target.relative_to(root.resolve())
    except Exception as exc:
        raise ValueError("Invalid skill path.") from exc
    if target.exists():
        target.unlink()
    payload = skill_doc_payload(normalized, include_body=True)
    if payload is None:
        raise ValueError("Unable to read skill doc.")
    return payload


def _module_catalog_entry(
    module_id: str,
    payload: Dict[str, Any],
    *,
    enabled_modules: Set[str] | None = None,
) -> Dict[str, Any]:
    entry = dict(payload)
    entry["id"] = str(entry.get("id") or module_id)
    entry.setdefault("source", "base")
    skill_id = str(entry.get("skill_id") or module_id).strip()
    entry["skill_id"] = skill_id
    entry["doc_id"] = f"skills:{skill_id}"
    entry["tool_names"] = _coerce_tool_names(entry.get("tool_names"))
    entry["assets"] = (
        entry.get("assets") if isinstance(entry.get("assets"), list) else []
    )
    config_value = entry.get("config")
    entry["config"] = config_value if isinstance(config_value, dict) else {}
    config_path = str(entry.get("config_path") or "").strip()
    if config_path:
        entry["config_path"] = config_path
    if enabled_modules is not None:
        entry["enabled"] = module_id in enabled_modules
    skill_entry = get_skill_entry(skill_id)
    entry["skill_available"] = bool(skill_entry)
    if skill_entry is not None:
        entry["skill_summary"] = str(skill_entry.get("summary") or "")
        entry["skill_path"] = str(skill_entry.get("path") or "")
        entry["skill_source"] = str(skill_entry.get("source") or "")
    return entry


def list_modules(
    *,
    enabled_modules: Iterable[str] | None = None,
) -> List[Dict[str, Any]]:
    enabled = {
        normalize_module_id(str(item or "").strip())
        for item in (enabled_modules or [])
        if normalize_module_id(str(item or "").strip())
    }
    entries: List[Dict[str, Any]] = [
        _module_catalog_entry(module_id, value, enabled_modules=enabled)
        for module_id, value in BUILTIN_MODULES.items()
    ]
    entries.extend(
        _module_catalog_entry(
            str(value.get("id") or ""),
            value,
            enabled_modules=enabled,
        )
        for value in list_custom_modules()
    )
    return sorted(
        entries,
        key=lambda item: (str(item.get("source") or ""), str(item.get("id") or "")),
    )


def tool_module_ids(tool_name: str | None) -> List[str]:
    normalized = str(tool_name or "").strip()
    if not normalized:
        return []
    matches: List[str] = []
    for module in list_modules():
        if normalized in _coerce_tool_names(module.get("tool_names")):
            module_id = str(module.get("id") or "").strip()
            if module_id:
                matches.append(module_id)
    return sorted(set(matches))


def skill_catalog_payload(*, include_body: bool = False) -> Dict[str, Any]:
    skills = list_skills(include_body=include_body)
    return {
        "skills_root": str(repo_skills_root()),
        "skills_roots": [str(path) for path in skill_roots()],
        "skills": skills,
        "count": len(skills),
    }


def workflow_catalog_payload(
    *,
    enabled_modules: Iterable[str] | None = None,
) -> Dict[str, Any]:
    return {
        "workflows": [dict(value) for value in BUILTIN_WORKFLOWS.values()],
        "modules": list_modules(enabled_modules=enabled_modules),
        "addons": list_addons(),
        "addons_root": str(addons_root()),
        "addons_roots": [str(path) for path in addon_roots()],
        "skills_root": str(repo_skills_root()),
        "skills_roots": [str(path) for path in skill_roots()],
    }
