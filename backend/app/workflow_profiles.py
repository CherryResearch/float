from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set

from app import config as app_config

WORKFLOW_DEFAULT = "default"
SKILL_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
MAX_PORTABLE_SKILL_ID_LENGTH = 120
WINDOWS_DEVICE_NAMES = {
    "aux",
    "con",
    "nul",
    "prn",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}
_SKILL_WRITE_LOCK = threading.RLock()
_SKILL_TEMP_CLEANUP_QUEUE: Set[tuple[str, str]] = set()
_SKILL_RECEIPT_MARKER = ".create-receipt."
_SKILL_RENAME_INTENT_PREFIX = ".rename-intent."
_SKILL_RENAME_STAGE_PREFIX = ".rename-stage."
_SKILL_RENAME_QUARANTINE_PREFIX = ".rename-quarantine."
_USE_WINDOWS_NO_REPLACE_RENAME = os.name == "nt"


class SkillConflictError(ValueError):
    """Raised when a valid skill lifecycle request conflicts with saved state."""


class SkillStorageError(RuntimeError):
    """Raised when a skill write fails for a server-side storage reason."""


BUILTIN_WORKFLOWS: Dict[str, Dict[str, Any]] = {
    "default": {
        "id": "default",
        "label": "Default",
        "description": "Balanced guidance for ordinary foreground chat.",
        "role": "general",
        "profile_kind": "foreground",
        "guidance_style": "balanced",
        "latency_tier": "interactive",
        "thinking_default": "auto",
        "selectable_in_chat": True,
        "selectable_as_default": True,
        "automatic_delegation": False,
        "tool_scope": "global",
        "module_scope": "global",
        "allow_continue_to": ["default", "mini_execution"],
        "supports_background": False,
        "supports_live": False,
        "enabled_modules": [],
    },
    "architect_planner": {
        "id": "architect_planner",
        "label": "Architect / Planner",
        "description": "Planning-oriented guidance with high default reasoning for foreground chat.",
        "role": "architect",
        "profile_kind": "foreground",
        "guidance_style": "planning",
        "latency_tier": "deliberate",
        "thinking_default": "high",
        "selectable_in_chat": True,
        "selectable_as_default": True,
        "automatic_delegation": False,
        "tool_scope": "global",
        "module_scope": "global",
        "allow_continue_to": ["architect_planner", "default", "mini_execution"],
        "supports_background": False,
        "supports_live": False,
        "enabled_modules": [],
    },
    "mini_execution": {
        "id": "mini_execution",
        "label": "Mini Execution",
        "description": "Concise execution guidance with low default reasoning for foreground turns.",
        "role": "worker",
        "profile_kind": "foreground",
        "guidance_style": "execution",
        "latency_tier": "fast",
        "thinking_default": "low",
        "selectable_in_chat": True,
        "selectable_as_default": True,
        "automatic_delegation": False,
        "tool_scope": "global",
        "module_scope": "global",
        "allow_continue_to": ["mini_execution"],
        "supports_background": False,
        "supports_live": False,
        "enabled_modules": [],
    },
    "background_reflection": {
        "id": "background_reflection",
        "label": "Background Reflection",
        "description": "System-only reflection guidance used by the background service.",
        "role": "background",
        "profile_kind": "system",
        "guidance_style": "reflection",
        "latency_tier": "deliberate",
        "thinking_default": "low",
        "selectable_in_chat": False,
        "selectable_as_default": False,
        "automatic_delegation": False,
        "tool_scope": "global",
        "module_scope": "global",
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


def resolve_foreground_workflow_name(value: str | None) -> str:
    """Resolve a workflow accepted by ordinary foreground chat entrypoints."""
    workflow = resolve_workflow_profile(value)
    if not bool(workflow.get("selectable_in_chat", True)):
        return WORKFLOW_DEFAULT
    return str(workflow.get("id") or WORKFLOW_DEFAULT)


def normalize_module_id(value: str | None) -> str:
    raw = str(value or "").strip()
    if raw in BUILTIN_MODULES:
        return raw
    if raw in _custom_module_ids():
        return raw
    return str(MODULE_ALIASES.get(raw) or "")


def normalize_module_id_from_catalog(
    value: str | None,
    module_catalog: Iterable[Dict[str, Any]],
) -> str:
    raw = str(value or "").strip()
    known_ids = {
        str(module.get("id") or "").strip()
        for module in module_catalog
        if str(module.get("id") or "").strip()
    }
    if raw in known_ids:
        return raw
    alias = str(MODULE_ALIASES.get(raw) or "")
    return alias if alias in known_ids else ""


def resolve_modules(
    workflow_name: str | None,
    requested_modules: Iterable[str] | None = None,
    *,
    include_workflow_defaults: bool = True,
    module_catalog: Iterable[Dict[str, Any]] | None = None,
) -> List[str]:
    workflow = resolve_workflow_profile(workflow_name)
    catalog = list(module_catalog) if module_catalog is not None else None

    def _normalize(value: Any) -> str:
        if catalog is not None:
            return normalize_module_id_from_catalog(str(value or ""), catalog)
        return normalize_module_id(str(value or "").strip())

    requested = {
        _normalize(item) for item in (requested_modules or []) if _normalize(item)
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
    module_catalog: Iterable[Dict[str, Any]] | None = None,
) -> str:
    workflow = resolve_workflow_profile(workflow_name)
    catalog = list(module_catalog) if module_catalog is not None else list_modules()
    if include_default_modules:
        enabled = resolve_modules(
            workflow.get("id"),
            modules,
            module_catalog=catalog,
        )
    else:
        enabled = [
            normalize_module_id_from_catalog(module_id, catalog)
            for module_id in (modules or [])
            if normalize_module_id_from_catalog(module_id, catalog)
        ]
    module_labels = {
        str(item.get("id") or ""): str(item.get("label") or item.get("id") or "")
        for item in catalog
    }
    enabled_labels = [
        module_labels[module_id] for module_id in enabled if module_id in module_labels
    ]
    guidance_style = str(workflow.get("guidance_style") or "balanced")
    guidance = {
        "planning": (
            "Plan carefully. Decompose the task and make dependencies, execution "
            "steps, and verification explicit."
        ),
        "execution": (
            "Keep this turn short and execution-focused. Prefer minimal narration "
            "and narrowly scoped follow-up steps."
        ),
        "reflection": (
            "Reflect within the bounded system task. Surface useful conclusions "
            "without implying that a foreground chat or worker was launched."
        ),
    }.get(
        guidance_style,
        "Balance reasoning quality with execution speed and use tools directly when helpful.",
    )
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


def active_data_root() -> Path:
    """Return Float's configured writable data root without caching env state."""

    configured = str(os.getenv("FLOAT_DATA_DIR") or "").strip()
    root = (
        Path(configured).expanduser() if configured else app_config.REPO_ROOT / "data"
    )
    if not root.is_absolute():
        root = app_config.REPO_ROOT / root
    return root.resolve()


def local_skills_root() -> Path:
    return (active_data_root() / "modules" / "skills").resolve()


def skill_roots() -> List[Path]:
    return [repo_skills_root(), local_skills_root()]


def addons_root() -> Path:
    root = (active_data_root() / "modules" / "addons").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def addon_roots() -> List[Path]:
    # Repo add-ons are the tracked defaults; data/ add-ons are local overrides.
    return [repo_addons_root(), addons_root()]


def _safe_existing_path(root: Path, path: Path) -> Path:
    """Resolve a regular file beneath root while rejecting every symlink."""

    if path.is_symlink():
        raise ValueError("Symlink-backed module and skill files are not supported.")
    resolved_root = root.resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(resolved_root)
    except Exception as exc:
        raise ValueError("Resolved file path escapes its managed root.") from exc
    if not resolved.is_file():
        raise ValueError("Managed document path is not a regular file.")
    return resolved


def _iter_addon_config_paths(root: Path) -> Iterable[Path]:
    """Yield canonical package configs plus legacy flat manifests."""

    if not root.exists():
        return []
    legacy_configs: List[Path] = []
    package_configs: List[Path] = []
    for path in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
        if path.is_symlink():
            continue
        if path.is_dir():
            config_path = path / "config.json"
            if config_path.is_symlink() or not config_path.is_file():
                continue
            try:
                package_configs.append(_safe_existing_path(root, config_path))
            except ValueError:
                continue
        elif path.is_file() and path.suffix.lower() == ".json":
            try:
                legacy_configs.append(_safe_existing_path(root, path))
            except ValueError:
                continue
    # Load flat legacy manifests first so a canonical package with the same id wins.
    return [*legacy_configs, *package_configs]


def _addon_fallback_id(path: Path) -> str:
    return path.parent.name if path.name.casefold() == "config.json" else path.stem


def list_addons() -> List[Dict[str, Any]]:
    entries_by_id: Dict[str, Dict[str, Any]] = {}
    for source, root in (("repo", repo_addons_root()), ("local", addons_root())):
        for path in _iter_addon_config_paths(root):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            fallback_id = _addon_fallback_id(path)
            addon_id = str(payload.get("id") or fallback_id).strip() or fallback_id
            entries_by_id[addon_id] = {
                "id": addon_id,
                "label": str(payload.get("label") or fallback_id),
                "description": str(payload.get("description") or "").strip(),
                "status": str(payload.get("status") or "available"),
                "path": str(path),
                "config_path": str(path),
                "package_path": str(path.parent),
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
    fallback_id = _addon_fallback_id(path)
    addon_id = str(payload.get("id") or fallback_id).strip() or fallback_id
    addon_label = str(payload.get("label") or fallback_id).strip() or fallback_id
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
        skill_id = normalize_skill_id(str(candidate.get("skill_id") or module_id))
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
            "config_path": str(path),
            "package_path": str(path.parent),
            "addon_id": addon_id,
            "addon_label": addon_label,
            "config": (
                candidate.get("config")
                if isinstance(candidate.get("config"), dict)
                else {}
            ),
            "assets": (
                candidate.get("assets")
                if isinstance(candidate.get("assets"), list)
                else []
            ),
        }


def _custom_module_candidates() -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for root_source, root in (("repo", repo_addons_root()), ("custom", addons_root())):
        for path in _iter_addon_config_paths(root):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            for entry in _iter_addon_module_entries(
                payload,
                source=root_source,
                path=path,
            ):
                candidates.append(entry)
    return candidates


def _classified_custom_modules() -> tuple[List[Dict[str, Any]], Set[str]]:
    protected_ids = {
        *[str(module_id).casefold() for module_id in BUILTIN_MODULES],
        *[str(alias).casefold() for alias in MODULE_ALIASES],
    }
    candidates_by_fold: Dict[str, List[Dict[str, Any]]] = {}
    for entry in _custom_module_candidates():
        module_id = str(entry.get("id") or "").strip()
        if module_id:
            candidates_by_fold.setdefault(module_id.casefold(), []).append(entry)

    entries_by_id: Dict[str, Dict[str, Any]] = {}
    rejected_tool_names: Set[str] = set()
    for folded_id, candidates in candidates_by_fold.items():
        exact_ids = {str(entry.get("id") or "").strip() for entry in candidates}
        selected_source = (
            "custom"
            if any(entry.get("source") == "custom" for entry in candidates)
            else "repo"
        )
        selected = [
            entry for entry in candidates if entry.get("source") == selected_source
        ]
        selected_paths = [str(entry.get("source_path") or "") for entry in selected]
        logical_addons = {
            _addon_fallback_id(Path(path)) for path in selected_paths if path
        }
        ambiguous = (
            len(exact_ids) != 1
            or len(selected_paths) != len(set(selected_paths))
            or len(logical_addons) != 1
        )
        if folded_id in protected_ids or ambiguous:
            for entry in candidates:
                rejected_tool_names.update(_coerce_tool_names(entry.get("tool_names")))
            continue
        module_id = exact_ids.pop()
        # Candidate order preserves canonical-over-legacy within one logical add-on;
        # selecting the local source first preserves exact repo-to-local overrides.
        entries_by_id[module_id] = selected[-1]

    return (
        sorted(entries_by_id.values(), key=lambda item: str(item["id"])),
        rejected_tool_names,
    )


def list_custom_modules() -> List[Dict[str, Any]]:
    modules, _rejected_tool_names = _classified_custom_modules()
    return modules


def rejected_custom_module_tool_names() -> Set[str]:
    _modules, rejected_tool_names = _classified_custom_modules()
    return rejected_tool_names


def _custom_module_ids() -> Set[str]:
    return {str(item.get("id") or "") for item in list_custom_modules()}


def _skill_summary_from_text(text: str) -> str:
    lines = str(text or "").lstrip("\ufeff").splitlines()
    content_start = 0
    if lines and str(lines[0] or "").strip() == "---":
        description = ""
        for index, raw_line in enumerate(lines[1:], start=1):
            line = str(raw_line or "").strip()
            if line == "---":
                content_start = index + 1
                break
            if not description and line.casefold().startswith("description:"):
                description = line.split(":", 1)[1].strip().strip("\"'")
        if description and description not in {">", "|"}:
            return description
    for raw_line in lines[content_start:]:
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


def _skill_path(root: Path, skill_id: str) -> Path:
    root_resolved = root.resolve()
    candidate = root_resolved / f"{skill_id}.md"
    if candidate.is_symlink():
        raise ValueError("Symlink-backed skill documents are not supported.")
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root_resolved)
    except Exception as exc:
        raise ValueError("Skill document path escapes its managed root.") from exc
    if candidate.exists() and not resolved.is_file():
        raise ValueError("Skill document path is not a regular file.")
    return resolved


def _known_skill_ids() -> Set[str]:
    ids: Set[str] = set()
    for root in skill_roots():
        if not root.exists():
            continue
        for path in root.glob("*.md"):
            if path.name.casefold() == "readme.md":
                continue
            ids.add(path.stem)
    return ids


def _portable_skill_id_format_reason(value: str | None) -> str:
    normalized = str(value or "").strip()
    if not normalized or not SKILL_ID_PATTERN.fullmatch(normalized):
        return "Use only letters, numbers, dots, dashes, and underscores."
    if len(normalized) > MAX_PORTABLE_SKILL_ID_LENGTH:
        return f"Skill ids must be {MAX_PORTABLE_SKILL_ID_LENGTH} characters or fewer."
    if normalized.casefold() == "readme":
        return "README is reserved for directory documentation."
    if normalized.startswith(".") or normalized.endswith("."):
        return "Skill ids cannot start or end with a dot."
    windows_stem = normalized.split(".", 1)[0].casefold()
    if windows_stem in WINDOWS_DEVICE_NAMES:
        return "That skill id is reserved by Windows."
    return ""


def portable_skill_id_reason(
    value: str | None,
    *,
    exclude_id: str | None = None,
) -> str:
    normalized = str(value or "").strip()
    format_reason = _portable_skill_id_format_reason(normalized)
    if format_reason:
        return format_reason
    excluded = str(exclude_id or "").strip()
    target_fold = normalized.casefold()
    collisions = sorted(
        existing
        for existing in _known_skill_ids()
        if existing != excluded and existing.casefold() == target_fold
    )
    if collisions:
        return f"A skill named '{collisions[0]}' already exists."
    return ""


def validate_new_skill_id(
    value: str | None,
    *,
    exclude_id: str | None = None,
) -> str:
    normalized = str(value or "").strip()
    reason = portable_skill_id_reason(normalized, exclude_id=exclude_id)
    if reason:
        if "already exists" in reason:
            raise SkillConflictError(reason)
        raise ValueError(reason)
    return normalized


def list_skills(*, include_body: bool = False) -> List[Dict[str, Any]]:
    entries_by_id: Dict[str, Dict[str, Any]] = {}
    for source, root in (("repo", repo_skills_root()), ("local", local_skills_root())):
        if not root.exists():
            continue
        for path in sorted(root.glob("*.md"), key=lambda item: item.name.casefold()):
            if path.name.casefold() == "readme.md" or path.is_symlink():
                continue
            try:
                safe_path = _safe_existing_path(root, path)
            except ValueError:
                continue
            entry = _skill_entry_from_path(
                safe_path,
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
    normalized = normalize_skill_id(skill_id)
    if not normalized:
        return None
    for source, root in (("local", local_skills_root()), ("repo", repo_skills_root())):
        path = _skill_path(root, normalized)
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
    if (
        not normalized
        or not SKILL_ID_PATTERN.fullmatch(normalized)
        or normalized.casefold() == "readme"
    ):
        return ""
    return normalized


def linked_modules_for_skill(skill_id: str | None) -> List[Dict[str, Any]]:
    normalized = normalize_skill_id(skill_id)
    if not normalized:
        return []
    linked: List[Dict[str, Any]] = []
    seen: Set[tuple[str, str, str]] = set()
    for module in list_modules():
        module_skill_id = normalize_skill_id(str(module.get("skill_id") or ""))
        if not module_skill_id or module_skill_id.casefold() != normalized.casefold():
            continue
        module_id = str(module.get("id") or "").strip()
        source = str(module.get("source") or "").strip()
        addon_id = str(module.get("addon_id") or "").strip()
        key = (module_id, source, addon_id)
        if not module_id or key in seen:
            continue
        seen.add(key)
        linked.append(
            {
                "id": module_id,
                "label": str(module.get("label") or module_id),
                "source": source,
                "addon_id": addon_id,
            }
        )
    return sorted(
        linked,
        key=lambda item: (
            str(item.get("label") or "").casefold(),
            str(item.get("id") or "").casefold(),
        ),
    )


def skill_doc_payload(
    skill_id: str | None,
    *,
    include_body: bool = True,
) -> Dict[str, Any] | None:
    normalized = normalize_skill_id(skill_id)
    if not normalized:
        return None
    repo_path = _skill_path(repo_skills_root(), normalized)
    local_path = _skill_path(local_skills_root(), normalized)
    active = get_skill_entry(normalized, include_body=include_body)
    linked_modules = linked_modules_for_skill(normalized)
    local_exists = local_path.is_file()
    rename_reason = ""
    if not local_exists:
        rename_reason = "Only saved local skill documents can be renamed."
    elif linked_modules:
        rename_reason = (
            "Linked skill documents cannot be renamed. Update the module link first."
        )
    return {
        "id": normalized,
        "doc_id": f"skills:{normalized}",
        "repo_path": str(repo_path),
        "local_path": str(local_path),
        "repo_exists": repo_path.is_file(),
        "local_exists": local_exists,
        "active": active,
        "linked_modules": linked_modules,
        "rename_allowed": not rename_reason,
        "rename_reason": rename_reason,
        "rename_block_reason": rename_reason,
    }


def _fsync_directory(path: Path) -> None:
    if _USE_WINDOWS_NO_REPLACE_RENAME:
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise SkillStorageError(
            "Unable to persist the skill directory update."
        ) from exc


def _receipt_target_path(receipt: Path) -> Path | None:
    name = receipt.name
    marker_index = name.rfind(_SKILL_RECEIPT_MARKER)
    if not name.startswith(".") or marker_index <= 1 or not name.endswith(".tmp"):
        return None
    target_name = name[1:marker_index]
    if Path(target_name).name != target_name or not target_name.endswith(".md"):
        return None
    return receipt.parent / target_name


def _same_managed_skill_target(root: Path, left: Path, right: Path) -> bool:
    """Compare case-portable skill identity without crossing the managed root."""

    root_resolved = root.resolve()
    try:
        left_parent = left.parent.resolve()
        right_parent = right.parent.resolve()
    except OSError:
        return False
    return (
        left_parent == root_resolved
        and right_parent == root_resolved
        and left.suffix.casefold() == ".md"
        and right.suffix.casefold() == ".md"
        and left.stem.casefold() == right.stem.casefold()
    )


def _unlink_published_receipt(receipt: Path, target: Path) -> bool:
    if not receipt.exists():
        return True
    if receipt.is_symlink() or target.is_symlink():
        return False
    if not receipt.is_file() or not target.is_file():
        return False
    try:
        if not os.path.samefile(receipt, target):
            return False
        receipt.unlink()
    except OSError:
        return False
    return True


def _queue_skill_temp_cleanup(receipt: Path, target: Path) -> None:
    with _SKILL_WRITE_LOCK:
        _SKILL_TEMP_CLEANUP_QUEUE.add((str(receipt), str(target)))


def _discard_skill_temp_cleanup(receipt: Path) -> None:
    receipt_resolved = receipt.resolve()
    for receipt_value, target_value in list(_SKILL_TEMP_CLEANUP_QUEUE):
        if Path(receipt_value).resolve() == receipt_resolved:
            _SKILL_TEMP_CLEANUP_QUEUE.discard((receipt_value, target_value))


def _unlink_managed_create_receipt(receipt: Path, target: Path) -> bool:
    """Unlink one recognizable receipt without touching its associated document."""

    if not receipt.exists():
        return True
    associated_target = _receipt_target_path(receipt)
    if associated_target is None or not _same_managed_skill_target(
        receipt.parent,
        associated_target,
        target,
    ):
        return False
    if receipt.is_symlink() or not receipt.is_file():
        return False
    try:
        # When identities match, unlinking only drops the staging hard link.
        # When they differ (or target is absent), the receipt is abandoned.
        if target.is_file() and not target.is_symlink():
            os.path.samefile(receipt, target)
        receipt.unlink()
    except OSError:
        return False
    return True


def _retry_skill_temp_cleanup(root: Path) -> None:
    resolved_root = root.resolve()
    pattern = f".*{_SKILL_RECEIPT_MARKER}*.tmp"
    for receipt in root.glob(pattern):
        target = _receipt_target_path(receipt)
        if target is None or not _same_managed_skill_target(root, target, target):
            continue
        if _unlink_managed_create_receipt(receipt, target):
            _discard_skill_temp_cleanup(receipt)

    for receipt_value, target_value in list(_SKILL_TEMP_CLEANUP_QUEUE):
        receipt = Path(receipt_value)
        target = Path(target_value)
        if (
            receipt.parent.resolve() == resolved_root
            and target.parent.resolve() == resolved_root
            and not receipt.exists()
        ):
            _SKILL_TEMP_CLEANUP_QUEUE.discard((receipt_value, target_value))


def _cleanup_associated_create_receipts(root: Path, target: Path) -> None:
    """Reconcile every receipt for target before mutating that document."""

    pattern = f".*{_SKILL_RECEIPT_MARKER}*.tmp"
    for receipt in root.glob(pattern):
        receipt_target = _receipt_target_path(receipt)
        if receipt_target is None or not _same_managed_skill_target(
            root,
            receipt_target,
            target,
        ):
            continue
        if not _unlink_managed_create_receipt(receipt, target):
            raise SkillStorageError(
                f"Unable to clean up a prior save receipt for '{target.stem}'."
            )
        _discard_skill_temp_cleanup(receipt)

    for receipt_value, target_value in list(_SKILL_TEMP_CLEANUP_QUEUE):
        receipt = Path(receipt_value)
        queued_target = Path(target_value)
        if not _same_managed_skill_target(root, queued_target, target):
            continue
        if receipt.exists():
            raise SkillStorageError(
                f"Unable to clean up a prior save receipt for '{target.stem}'."
            )
        _SKILL_TEMP_CLEANUP_QUEUE.discard((receipt_value, target_value))


def _remove_rename_intent(marker: Path) -> None:
    try:
        marker.unlink()
        _fsync_directory(marker.parent)
    except OSError as exc:
        raise SkillStorageError("Unable to clear a skill rename receipt.") from exc


def _quarantine_rename_intent(marker: Path) -> Path:
    descriptor, quarantine_name = tempfile.mkstemp(
        dir=str(marker.parent),
        prefix=_SKILL_RENAME_QUARANTINE_PREFIX,
        suffix=".json",
    )
    os.close(descriptor)
    quarantine = Path(quarantine_name)
    try:
        os.replace(marker, quarantine)
        _fsync_directory(marker.parent)
    except (OSError, SkillStorageError) as exc:
        try:
            quarantine.unlink()
        except OSError:
            pass
        raise SkillStorageError(
            "Unable to quarantine a non-actionable skill rename receipt."
        ) from exc
    return quarantine


def _rename_intent_paths(marker: Path, root: Path) -> tuple[Path, Path]:
    try:
        safe_marker = _safe_existing_path(root, marker)
        payload = json.loads(safe_marker.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SkillStorageError("A skill rename receipt is unreadable.") from exc
    if not isinstance(payload, dict):
        raise SkillStorageError("A skill rename receipt is invalid.")
    source_name = str(payload.get("source") or "")
    target_name = str(payload.get("target") or "")
    source_id = Path(source_name).stem
    target_id = Path(target_name).stem
    if (
        Path(source_name).name != source_name
        or Path(target_name).name != target_name
        or not source_name.endswith(".md")
        or not target_name.endswith(".md")
        or normalize_skill_id(source_id) != source_id
        or _portable_skill_id_format_reason(target_id)
        or source_id.casefold() == target_id.casefold()
    ):
        raise SkillStorageError("A skill rename receipt names an invalid path.")
    try:
        return _skill_path(root, source_id), _skill_path(root, target_id)
    except ValueError as exc:
        raise SkillStorageError("A skill rename receipt names an unsafe path.") from exc


def _recover_posix_rename_intents(root: Path) -> None:
    if _USE_WINDOWS_NO_REPLACE_RENAME:
        return
    for marker in sorted(root.glob(f"{_SKILL_RENAME_INTENT_PREFIX}*.json")):
        try:
            source, target = _rename_intent_paths(marker, root)
        except SkillStorageError:
            _quarantine_rename_intent(marker)
            continue
        source_exists = source.is_file()
        target_exists = target.is_file()
        if source_exists and target_exists:
            try:
                identities_match = os.path.samefile(source, target)
            except OSError:
                _quarantine_rename_intent(marker)
                continue
            if not identities_match:
                _quarantine_rename_intent(marker)
                continue
            try:
                source.unlink()
                _fsync_directory(root)
            except (OSError, SkillStorageError) as exc:
                raise SkillStorageError(
                    "Unable to finish a pending skill rename."
                ) from exc
        elif not source_exists and not target_exists:
            _quarantine_rename_intent(marker)
            continue
        _remove_rename_intent(marker)


def _write_rename_intent(root: Path, source: Path, target: Path) -> Path:
    descriptor, staging_name = tempfile.mkstemp(
        dir=str(root),
        prefix=_SKILL_RENAME_STAGE_PREFIX,
        suffix=".tmp",
    )
    staging = Path(staging_name)
    unique_suffix = staging.name[len(_SKILL_RENAME_STAGE_PREFIX) : -len(".tmp")]
    marker = root / f"{_SKILL_RENAME_INTENT_PREFIX}{unique_suffix}.json"
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            json.dump({"source": source.name, "target": target.name}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staging, marker)
        _fsync_directory(root)
    except (OSError, SkillStorageError) as exc:
        raise SkillStorageError("Unable to record the pending skill rename.") from exc
    finally:
        if staging.exists():
            try:
                staging.unlink()
            except OSError:
                pass
    return marker


def _acquire_skill_file_lock(handle: Any) -> None:
    if _USE_WINDOWS_NO_REPLACE_RENAME:
        import msvcrt

        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _release_skill_file_lock(handle: Any) -> None:
    if _USE_WINDOWS_NO_REPLACE_RENAME:
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _skill_lifecycle_guard(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".skill-lifecycle.lock"
    with _SKILL_WRITE_LOCK:
        try:
            handle = lock_path.open("a+b")
        except OSError as exc:
            raise SkillStorageError(
                "Unable to open the local skill store lock."
            ) from exc
        with handle:
            try:
                _acquire_skill_file_lock(handle)
            except OSError as exc:
                raise SkillStorageError(
                    "Unable to lock the local skill store."
                ) from exc
            try:
                _recover_posix_rename_intents(root)
                _retry_skill_temp_cleanup(root)
                yield
            finally:
                try:
                    _release_skill_file_lock(handle)
                except OSError:
                    # The guarded operation has already completed. Closing the
                    # descriptor still releases an OS-owned lock.
                    pass


def _atomic_write_text(target: Path, body: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        dir=str(target.parent),
        prefix=f".{target.name}{_SKILL_RECEIPT_MARKER}",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        try:
            with os.fdopen(
                file_descriptor, "w", encoding="utf-8", newline=""
            ) as handle:
                handle.write(str(body or ""))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            _fsync_directory(target.parent)
        except (OSError, SkillStorageError) as exc:
            raise SkillStorageError("Unable to update the skill document.") from exc
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                _queue_skill_temp_cleanup(temporary, target)


def _atomic_create_text(target: Path, body: str) -> None:
    """Create target without exposing a partial file or replacing a peer's file."""

    target.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        dir=str(target.parent),
        prefix=f".{target.name}{_SKILL_RECEIPT_MARKER}",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    published = False
    try:
        try:
            with os.fdopen(
                file_descriptor, "w", encoding="utf-8", newline=""
            ) as handle:
                handle.write(str(body or ""))
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise SkillStorageError("Unable to stage the skill document.") from exc
        if _USE_WINDOWS_NO_REPLACE_RENAME:
            try:
                # Windows rename is an atomic same-volume publish and fails when
                # the destination exists.
                os.rename(temporary, target)
                published = True
            except FileExistsError as exc:
                raise SkillConflictError(
                    f"A skill named '{target.stem}' already exists."
                ) from exc
            except OSError as exc:
                raise SkillStorageError(
                    "Unable to publish the skill document."
                ) from exc
            return

        try:
            # POSIX hard-link publication is atomic and has native no-replace
            # semantics. Unsupported filesystems fail closed.
            os.link(temporary, target)
            published = True
            _fsync_directory(target.parent)
        except FileExistsError as exc:
            raise SkillConflictError(
                f"A skill named '{target.stem}' already exists."
            ) from exc
        except SkillStorageError:
            raise
        except OSError as exc:
            raise SkillStorageError("Unable to publish the skill document.") from exc

        if not _unlink_published_receipt(temporary, target):
            _queue_skill_temp_cleanup(temporary, target)
    finally:
        if temporary.exists():
            if published and not _USE_WINDOWS_NO_REPLACE_RENAME:
                _queue_skill_temp_cleanup(temporary, target)
            else:
                try:
                    temporary.unlink()
                except OSError:
                    _queue_skill_temp_cleanup(temporary, target)


def _rename_no_replace(source: Path, target: Path, root: Path) -> None:
    if _USE_WINDOWS_NO_REPLACE_RENAME:
        try:
            os.rename(source, target)
        except FileExistsError as exc:
            raise SkillConflictError(
                f"A skill named '{target.stem}' already exists."
            ) from exc
        except OSError as exc:
            raise SkillStorageError("Unable to rename the skill document.") from exc
        return

    marker = _write_rename_intent(root, source, target)
    try:
        os.link(source, target)
    except FileExistsError as exc:
        try:
            _remove_rename_intent(marker)
        except SkillStorageError as cleanup_exc:
            raise SkillStorageError(
                "Unable to clear a conflicted skill rename receipt."
            ) from cleanup_exc
        raise SkillConflictError(
            f"A skill named '{target.stem}' already exists."
        ) from exc
    except OSError as exc:
        try:
            _remove_rename_intent(marker)
        except SkillStorageError as cleanup_exc:
            raise SkillStorageError(
                "Unable to clear a failed skill rename receipt."
            ) from cleanup_exc
        raise SkillStorageError(
            "Unable to publish the renamed skill document."
        ) from exc

    try:
        _fsync_directory(root)
        source.unlink()
        _fsync_directory(root)
    except (OSError, SkillStorageError) as exc:
        # The durable marker lets the next guarded operation finish a rename
        # interrupted after publication.
        raise SkillStorageError(
            "Unable to finish renaming the skill document."
        ) from exc
    _remove_rename_intent(marker)


def write_local_skill_doc(
    skill_id: str | None,
    body: str,
    *,
    create_only: bool = False,
) -> Dict[str, Any]:
    normalized = normalize_skill_id(skill_id)
    if not normalized:
        raise ValueError("Invalid skill id.")
    root = local_skills_root()
    with _skill_lifecycle_guard(root):
        target = _skill_path(root, normalized)
        _cleanup_associated_create_receipts(root, target)
        known_ids = _known_skill_ids()
        existing_exact = normalized in known_ids
        if create_only:
            collision = next(
                (
                    existing
                    for existing in sorted(known_ids)
                    if existing.casefold() == normalized.casefold()
                ),
                "",
            )
            if collision:
                raise SkillConflictError(f"A skill named '{collision}' already exists.")
            normalized = validate_new_skill_id(normalized)
            _atomic_create_text(target, str(body or ""))
        elif not existing_exact:
            normalized = validate_new_skill_id(normalized)
            _atomic_create_text(target, str(body or ""))
        else:
            _atomic_write_text(target, str(body or ""))
    payload = skill_doc_payload(normalized, include_body=True)
    if payload is None:
        raise ValueError("Unable to read saved skill doc.")
    return payload


def delete_local_skill_doc(skill_id: str | None) -> Dict[str, Any]:
    normalized = normalize_skill_id(skill_id)
    if not normalized:
        raise ValueError("Invalid skill id.")
    root = local_skills_root()
    with _skill_lifecycle_guard(root):
        target = _skill_path(root, normalized)
        _cleanup_associated_create_receipts(root, target)
        if target.exists():
            try:
                target.unlink()
                _fsync_directory(root)
            except OSError as exc:
                raise SkillStorageError("Unable to delete the skill document.") from exc
    payload = skill_doc_payload(normalized, include_body=True)
    if payload is None:
        raise ValueError("Unable to read skill doc.")
    return payload


def rename_local_skill_doc(
    skill_id: str | None,
    target_id: str | None,
) -> Dict[str, Any]:
    normalized = normalize_skill_id(skill_id)
    if not normalized:
        raise ValueError("Invalid skill id.")
    root = local_skills_root()
    with _skill_lifecycle_guard(root):
        target_normalized = str(target_id or "").strip()
        format_reason = _portable_skill_id_format_reason(target_normalized)
        if format_reason:
            raise ValueError(format_reason)
        source = _skill_path(root, normalized)
        target = _skill_path(root, target_normalized)
        _cleanup_associated_create_receipts(root, source)
        _cleanup_associated_create_receipts(root, target)
        if target_normalized.casefold() == normalized.casefold():
            raise SkillConflictError(
                "The new skill id must differ from the current id."
            )
        target_normalized = validate_new_skill_id(
            target_normalized,
            exclude_id=normalized,
        )
        current = skill_doc_payload(normalized, include_body=True)
        if current is None or not current.get("local_exists") or not source.is_file():
            raise SkillConflictError("Only saved local skill documents can be renamed.")
        if current.get("linked_modules"):
            raise SkillConflictError(
                "Linked skill documents cannot be renamed. Update the module link first."
            )
        if target.exists() or target.is_symlink():
            raise SkillConflictError(
                f"A skill named '{target_normalized}' already exists."
            )
        _rename_no_replace(source, target, root)
    document = skill_doc_payload(target_normalized, include_body=True)
    if document is None:
        raise ValueError("Unable to read renamed skill doc.")
    return document


def _module_catalog_entry(
    module_id: str,
    payload: Dict[str, Any],
    *,
    enabled_modules: Set[str] | None = None,
) -> Dict[str, Any]:
    entry = dict(payload)
    entry["id"] = str(entry.get("id") or module_id)
    entry.setdefault("source", "base")
    skill_id = normalize_skill_id(str(entry.get("skill_id") or module_id))
    entry["skill_id"] = skill_id
    entry["doc_id"] = f"skills:{skill_id}" if skill_id else ""
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
    skill_entry = get_skill_entry(skill_id) if skill_id else None
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
    catalog, _rejected_tool_names = module_catalog_snapshot(
        enabled_modules=enabled_modules
    )
    return catalog


def module_catalog_snapshot(
    *,
    enabled_modules: Iterable[str] | None = None,
) -> tuple[List[Dict[str, Any]], Set[str]]:
    custom_modules, rejected_tool_names = _classified_custom_modules()
    custom_ids = {str(item.get("id") or "") for item in custom_modules}

    def _normalize_known_module_id(value: Any) -> str:
        raw = str(value or "").strip()
        if raw in BUILTIN_MODULES or raw in custom_ids:
            return raw
        return str(MODULE_ALIASES.get(raw) or "")

    enabled = {
        _normalize_known_module_id(item)
        for item in (enabled_modules or [])
        if _normalize_known_module_id(item)
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
        for value in custom_modules
    )
    return (
        sorted(
            entries,
            key=lambda item: (
                str(item.get("source") or ""),
                str(item.get("id") or ""),
            ),
        ),
        rejected_tool_names,
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


def tool_enabled_by_modules(
    tool_name: str | None,
    enabled_modules: Iterable[str] | None,
    *,
    module_catalog: Iterable[Dict[str, Any]] | None = None,
    rejected_tool_names: Iterable[str] | None = None,
) -> bool:
    """Return whether a module-scoped tool has at least one enabled owner."""

    normalized_tool = str(tool_name or "").strip()
    if not normalized_tool:
        return True
    if module_catalog is None and rejected_tool_names is None:
        catalog, rejected = module_catalog_snapshot()
    else:
        catalog = list(module_catalog) if module_catalog is not None else list_modules()
        rejected = (
            set(rejected_tool_names)
            if rejected_tool_names is not None
            else rejected_custom_module_tool_names()
        )
    required_modules = {
        str(module.get("id") or "").strip()
        for module in catalog
        if normalized_tool in _coerce_tool_names(module.get("tool_names"))
        and str(module.get("id") or "").strip()
    }
    if not required_modules:
        return normalized_tool not in rejected
    known_modules = {
        str(module.get("id") or "").strip()
        for module in catalog
        if str(module.get("id") or "").strip()
    }
    enabled = {
        str(MODULE_ALIASES.get(str(item or "").strip()) or str(item or "").strip())
        for item in (enabled_modules or [])
        if str(MODULE_ALIASES.get(str(item or "").strip()) or str(item or "").strip())
        in known_modules
    }
    return bool(required_modules & enabled)


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
