"""Capability metadata for built-in tools.

This module keeps the user-facing "what can this tool really do?" details in a
single place so the API, tool-help output, and UI can stay aligned.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from app import config as app_config
from app.tool_specs import BUILTIN_TOOL_SPECS


def _entry(
    *,
    category: str,
    summary: str,
    description: Optional[str] = None,
    status: str = "live",
    runtime: Optional[Dict[str, Any]] = None,
    sandbox: Optional[Dict[str, Any]] = None,
    limits: Optional[Dict[str, Any]] = None,
    freshness: Optional[Dict[str, Any]] = None,
    persistence: Optional[Dict[str, Any]] = None,
    safety: Optional[Dict[str, Any]] = None,
    can_access: Optional[List[str]] = None,
    cannot_access: Optional[List[str]] = None,
    limit_hints: Optional[List[str]] = None,
    ui: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "category": category,
        "summary": summary,
        "description": description or summary,
        "status": status,
        "runtime": runtime or {},
        "sandbox": sandbox or {},
        "limits": limits or {},
        "freshness": freshness or {},
        "persistence": persistence or {},
        "safety": safety or {},
        "can_access": list(can_access or []),
        "cannot_access": list(cannot_access or []),
        "limit_hints": list(limit_hints or []),
        "ui": ui or {},
    }


_BUILTIN_TOOL_CATALOG: Dict[str, Dict[str, Any]] = {
    "tool_help": _entry(
        category="help",
        summary="Explain available tools, arguments, and examples.",
        description=(
            "Returns structured documentation for built-in tools and can list "
            "multiple tools in either brief or rich mode."
        ),
        runtime={"executor": "backend_python", "network": False, "filesystem": False},
        limits={"max_tools": 50},
        freshness={"type": "local_metadata"},
        persistence={"writes_state": False},
        safety={"risk_level": "low", "default_approval": "auto"},
        can_access=["built-in tool metadata and argument schemas"],
        cannot_access=["network, files, browser state, or user secrets"],
        limit_hints=["List mode caps results at 50 tools."],
    ),
    "tool_info": _entry(
        category="help",
        summary="Return one built-in tool capability record.",
        description=(
            "Returns the same structured capability metadata exposed via the "
            "tool catalog API for a single tool."
        ),
        runtime={"executor": "backend_python", "network": False, "filesystem": False},
        limits={"single_tool_lookup": True},
        freshness={"type": "local_metadata"},
        persistence={"writes_state": False},
        safety={"risk_level": "low", "default_approval": "auto"},
        can_access=["built-in tool capability metadata and schemas"],
        cannot_access=["network, files, browser state, or user secrets"],
    ),
    "list_actions": _entry(
        category="history",
        summary="List recorded write actions that can be inspected or reverted.",
        description=(
            "Returns persisted action-history summaries for write operations, "
            "groupable by conversation or response."
        ),
        runtime={"executor": "backend_python", "network": False, "filesystem": True},
        freshness={"type": "persisted_snapshot"},
        persistence={"writes_state": False},
        safety={"risk_level": "low", "default_approval": "auto"},
        can_access=["persisted action history summaries and grouping metadata"],
        cannot_access=["untracked external systems or browser state"],
    ),
    "read_action_diff": _entry(
        category="history",
        summary="Read the diff and raw before/after snapshots for one recorded action.",
        description=(
            "Loads one persisted action-history record and returns its resource-level "
            "diffs, including before/after payloads for reversible writes."
        ),
        runtime={"executor": "backend_python", "network": False, "filesystem": True},
        freshness={"type": "persisted_snapshot"},
        persistence={"writes_state": False},
        safety={"risk_level": "medium", "default_approval": "confirm"},
        can_access=["persisted write diffs and raw action payloads"],
        cannot_access=["live browser state or external systems"],
    ),
    "revert_actions": _entry(
        category="history",
        summary="Revert one action or a batch of actions by response or conversation.",
        description=(
            "Applies stored before-snapshots to undo prior write operations, "
            "with conflict checks against newer active changes."
        ),
        runtime={"executor": "backend_python", "network": False, "filesystem": True},
        freshness={"type": "live_local_state"},
        persistence={"writes_state": True, "stores_output": True},
        safety={"risk_level": "high", "default_approval": "confirm"},
        can_access=["tracked local write operations and their stored before states"],
        cannot_access=["untracked external side effects or read-only tools"],
        limit_hints=[
            "Use response_id or conversation_id to revert batches in one call.",
            "Newer conflicting writes can block a revert unless explicitly forced.",
        ],
    ),
    "crawl": _entry(
        category="web",
        summary="Fetch one URL and return clipped text content.",
        description=(
            "Performs a direct backend HTTP GET request and returns the first "
            "10,000 characters of the response body."
        ),
        runtime={"executor": "backend_python", "network": True, "filesystem": False},
        sandbox={"allowed_domains": ["*"], "javascript_aware": False},
        limits={
            "default_timeout_seconds": 5,
            "max_timeout_seconds": 60,
            "response_chars": 10000,
        },
        freshness={"type": "live_network"},
        persistence={"writes_state": False},
        safety={"risk_level": "medium", "default_approval": "confirm"},
        can_access=["public URLs over HTTP(S)"],
        cannot_access=["JavaScript-rendered pages, browser cookies, or local files"],
        limit_hints=["Response text is truncated to 10,000 characters."],
    ),
    "search_web": _entry(
        category="web",
        summary="Search public web results and return titles, links, and snippets.",
        description=(
            "Uses DuckDuckGo HTML endpoints first and falls back to a Jina proxy "
            "if the primary search response is blocked or empty."
        ),
        runtime={"executor": "backend_python", "network": True, "filesystem": False},
        sandbox={
            "allowed_domains": [
                "duckduckgo.com",
                "lite.duckduckgo.com",
                "r.jina.ai",
            ]
        },
        limits={"default_max_results": 5, "max_results": 10},
        freshness={
            "type": "live_network",
            "notes": "Depends on external provider indexing.",
        },
        persistence={"writes_state": False},
        safety={"risk_level": "medium", "default_approval": "confirm"},
        can_access=["public search results from supported providers"],
        cannot_access=["private accounts, browser sessions, or local files"],
        limit_hints=["`max_results` is capped at 10."],
    ),
    "open_url": _entry(
        category="web",
        summary="Placeholder browser-open tool.",
        description=(
            "This is still a stub: it confirms the requested URL string but does "
            "not open a real browser or fetch page contents."
        ),
        status="stub",
        runtime={"executor": "backend_python", "network": False, "filesystem": False},
        freshness={"type": "none"},
        persistence={"writes_state": False},
        safety={"risk_level": "low", "default_approval": "confirm"},
        can_access=["the provided URL string only"],
        cannot_access=["real browser state, page content, network fetches, or files"],
        limit_hints=["Stub behavior only; no browser handoff yet."],
    ),
    "read_file": _entry(
        category="files",
        summary="Read a bounded UTF-8 text window from Float's managed data directory.",
        description=(
            "Reads text-only files from the configured data root and returns "
            "a windowed excerpt plus paging metadata. Paths are normalized "
            "before resolution so model-provided `data/...` prefixes do not "
            "escape the sandbox."
        ),
        runtime={"executor": "backend_python", "network": False, "filesystem": True},
        sandbox={"read_roots": ["data/"], "write_roots": []},
        limits={
            "text_only": True,
            "default_start_line": 1,
            "default_line_count": 200,
            "max_line_count": 1000,
            "default_max_chars": 12000,
            "max_chars": 20000,
        },
        freshness={"type": "live_filesystem"},
        persistence={"writes_state": False},
        safety={"risk_level": "medium", "default_approval": "confirm"},
        can_access=["UTF-8 text files under `data/`"],
        cannot_access=["files outside `data/`, binary reads, or host shell commands"],
        limit_hints=[
            "Use `list_dir` first when you need to discover filenames.",
            "Reads are windowed by `start_line`, `line_count`, and `max_chars`.",
            "Text reads use UTF-8 with ignored decode errors.",
        ],
    ),
    "list_dir": _entry(
        category="files",
        summary="List files and folders within Float's managed data roots.",
        description=(
            "Enumerates directories under `data/` or only under "
            "`data/workspace/` when `workspace_only=true`, returning structured "
            "path, type, size, and modified-time metadata."
        ),
        runtime={"executor": "backend_python", "network": False, "filesystem": True},
        sandbox={"read_roots": ["data/"], "write_roots": []},
        limits={"default_max_entries": 100, "max_entries": 200},
        freshness={"type": "live_filesystem"},
        persistence={"writes_state": False},
        safety={"risk_level": "medium", "default_approval": "confirm"},
        can_access=[
            "directory metadata under `data/`",
            "workspace-only view via `workspace_only=true`",
        ],
        cannot_access=["paths outside `data/` or file contents"],
        limit_hints=[
            "Hidden entries are omitted unless `include_hidden=true`.",
            "Results are capped at 200 entries per call.",
        ],
    ),
    "write_file": _entry(
        category="files",
        summary="Write a UTF-8 text file into Float's workspace sandbox.",
        description=(
            "Writes or overwrites text files under `data/workspace/` only. Paths "
            "are normalized so model-provided `data/workspace/...` prefixes do "
            "not duplicate path segments."
        ),
        runtime={"executor": "backend_python", "network": False, "filesystem": True},
        sandbox={"read_roots": [], "write_roots": ["data/workspace/"]},
        limits={"text_only": True, "mode": "overwrite"},
        freshness={"type": "live_filesystem"},
        persistence={"writes_state": True, "stores_output": True},
        safety={"risk_level": "high", "default_approval": "confirm"},
        can_access=["UTF-8 text writes under `data/workspace/`"],
        cannot_access=[
            "append mode, binary writes, or paths outside the workspace root"
        ],
        limit_hints=[
            "Existing files are overwritten; there is no append or patch mode."
        ],
    ),
    "generate_threads": _entry(
        category="threads",
        summary="Rebuild semantic thread summaries from stored conversations.",
        description=(
            "Reads the conversation store, generates a new semantic threads "
            "summary, and persists it for later UI inspection."
        ),
        runtime={"executor": "backend_python", "network": False, "filesystem": True},
        sandbox={
            "read_roots": ["data/conversations/"],
            "write_roots": ["data/threads/"],
        },
        freshness={"type": "recomputed_snapshot"},
        persistence={"writes_state": True, "stores_output": True},
        safety={"risk_level": "medium", "default_approval": "confirm"},
        can_access=["stored conversations and thread summary output"],
        cannot_access=["arbitrary filesystem locations or live web data"],
        limit_hints=["Operates on the local conversation store only."],
    ),
    "read_threads_summary": _entry(
        category="threads",
        summary="Read the latest generated thread summary snapshot.",
        description=(
            "Returns the last persisted semantic threads summary without "
            "recomputing it."
        ),
        runtime={"executor": "backend_python", "network": False, "filesystem": True},
        sandbox={"read_roots": ["data/threads/"], "write_roots": []},
        freshness={
            "type": "persisted_snapshot",
            "notes": "May be stale until threads are regenerated.",
        },
        persistence={"writes_state": False},
        safety={"risk_level": "low", "default_approval": "auto"},
        can_access=["the latest persisted thread summary file"],
        cannot_access=["live recomputation or arbitrary files"],
    ),
    "create_event": _entry(
        category="calendar",
        summary="Compatibility alias for creating or updating an upcoming event.",
        description=(
            "Legacy event handle that maps to the same task/event persistence flow "
            "as `create_task`, including flexible timestamp and duration inputs."
        ),
        runtime={"executor": "backend_python", "network": False, "filesystem": True},
        sandbox={"write_roots": ["data/databases/calendar_events/"]},
        freshness={"type": "live_local_state"},
        persistence={"writes_state": True, "stores_output": True},
        safety={"risk_level": "medium", "default_approval": "confirm"},
        can_access=["local calendar/task storage"],
        cannot_access=["external calendar providers or unrelated filesystem paths"],
    ),
    "create_task": _entry(
        category="calendar",
        summary="Create or update an upcoming task/calendar event.",
        description=(
            "Persists a task/event using the same structured payload shape used "
            "by the popup task editor in the UI, including timezone-aware "
            "timestamps or relative natural-language times."
        ),
        runtime={"executor": "backend_python", "network": False, "filesystem": True},
        sandbox={"write_roots": ["data/databases/calendar_events/"]},
        freshness={"type": "live_local_state"},
        persistence={"writes_state": True, "stores_output": True},
        safety={"risk_level": "medium", "default_approval": "confirm"},
        can_access=["local calendar/task storage"],
        cannot_access=["external calendar providers or unrelated filesystem paths"],
    ),
    "memory.save": _entry(
        category="memory",
        summary="Legacy compatibility wrapper for saving a memory entry.",
        description=(
            "Compatibility surface for older prompts. New work should prefer "
            "`remember` and `recall`."
        ),
        status="legacy",
        runtime={"executor": "backend_python", "network": False, "filesystem": False},
        freshness={"type": "live_local_state"},
        persistence={"writes_state": True, "stores_output": True},
        safety={"risk_level": "medium", "default_approval": "confirm"},
        can_access=["shared durable memory"],
        cannot_access=["per-conversation memory isolation"],
    ),
    "remember": _entry(
        category="memory",
        summary="Store or update a durable memory entry.",
        description=(
            "Writes canonical memory data with lifecycle metadata and optionally "
            "mirrors safe content into vector retrieval so future hybrid recall "
            "can find it."
        ),
        runtime={"executor": "backend_python", "network": False, "filesystem": False},
        freshness={"type": "live_local_state"},
        persistence={"writes_state": True, "stores_output": True},
        safety={"risk_level": "medium", "default_approval": "confirm"},
        can_access=["shared durable memory and optional vector mirror"],
        cannot_access=["per-thread private memory stores"],
        limit_hints=[
            "Protected and secret values are not mirrored into vector search by default."
        ],
    ),
    "recall": _entry(
        category="memory",
        summary="Recall by exact key or search shared memory and knowledge snippets.",
        description=(
            "Exact lookup falls back to bounded hybrid recall across canonical "
            "memory and vectorized knowledge snippets; optional CLIP image recall "
            "can also return reusable image attachments."
        ),
        runtime={"executor": "backend_python", "network": False, "filesystem": False},
        limits={"default_top_k": 5, "max_top_k": 10, "default_image_top_k": 5},
        freshness={"type": "live_local_state"},
        persistence={"writes_state": False},
        safety={"risk_level": "medium", "default_approval": "confirm"},
        can_access=[
            "shared memory, canonical knowledge snippets, and optional CLIP image recall"
        ],
        cannot_access=["secret exports for external use"],
        limit_hints=["`top_k` and `image_top_k` are capped at 10."],
    ),
}


def _display_name(tool_name: str) -> str:
    special = {
        "list_actions": "List Actions",
        "read_action_diff": "Read Action Diff",
        "revert_actions": "Revert Actions",
        "tool_help": "Tool Help",
        "open_url": "Open URL",
        "read_file": "Read File",
        "list_dir": "List Directory",
        "write_file": "Write File",
        "generate_threads": "Generate Threads",
        "read_threads_summary": "Read Threads Summary",
        "create_event": "Create Event",
        "create_task": "Create Task",
        "memory.save": "Memory Save",
    }
    if tool_name in special:
        return special[tool_name]
    return tool_name.replace("_", " ").replace(".", " ").title()


def get_tool_catalog_entry(tool_name: str) -> Dict[str, Any]:
    """Return capability metadata for a built-in or schema-less custom tool."""

    name = str(tool_name or "").strip()
    if not name:
        raise ValueError("tool_name is required")

    spec = BUILTIN_TOOL_SPECS.get(name)
    entry = deepcopy(_BUILTIN_TOOL_CATALOG.get(name) or {})
    if not entry:
        return {
            "id": name,
            "display_name": _display_name(name),
            "origin": "custom",
            "status": "custom",
            "category": "custom",
            "summary": "Custom tool (schema details unavailable).",
            "description": "This tool is registered at runtime without a built-in capability record.",
            "input_schema": {
                "type": "object",
                "title": "Arguments",
                "additionalProperties": True,
                "properties": {},
            },
            "runtime": {},
            "sandbox": {},
            "limits": {},
            "freshness": {"type": "unknown"},
            "persistence": {"writes_state": False},
            "safety": {"risk_level": "unknown", "default_approval": "confirm"},
            "can_access": [],
            "cannot_access": [],
            "limit_hints": [],
            "ui": {"advanced": True},
        }

    entry["id"] = name
    entry["display_name"] = _display_name(name)
    entry["origin"] = "builtin"
    if spec and isinstance(spec, dict):
        entry["input_schema"] = deepcopy(spec.get("parameters") or {})
        entry.setdefault("summary", spec.get("description") or entry["summary"])
    else:
        entry["input_schema"] = {
            "type": "object",
            "title": "Arguments",
            "additionalProperties": True,
            "properties": {},
        }
    return entry


def get_tool_catalog(available: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
    """Return capability records for the requested or known tool names."""

    names = [
        str(name or "").strip() for name in (available or []) if str(name or "").strip()
    ]
    if not names:
        names = sorted(
            set(BUILTIN_TOOL_SPECS.keys()) | set(_BUILTIN_TOOL_CATALOG.keys())
        )
    return [get_tool_catalog_entry(name) for name in sorted(set(names))]


def get_tool_limits(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return shared environment limits and sandbox roots for the tool layer."""

    cfg = dict(config or {})
    raw_data_dir = Path(cfg.get("data_dir") or app_config.DEFAULT_DATA_DIR)
    if not raw_data_dir.is_absolute():
        raw_data_dir = (app_config.REPO_ROOT / raw_data_dir).resolve()
    else:
        try:
            raw_data_dir = raw_data_dir.resolve()
        except Exception:
            pass
    workspace_dir = (raw_data_dir / "workspace").resolve()
    return {
        "roots": {
            "data": raw_data_dir.as_posix(),
            "workspace": workspace_dir.as_posix(),
            "read": [raw_data_dir.as_posix()],
            "write": [workspace_dir.as_posix()],
        },
        "limits": {
            "search_web_max_results": 10,
            "crawl_default_timeout_seconds": 5,
            "crawl_max_timeout_seconds": 60,
            "crawl_response_chars": 10000,
            "list_dir_default_max_entries": 100,
            "list_dir_max_entries": 200,
            "tool_help_max_tools": 50,
            "recall_max_top_k": 10,
        },
        "notes": [
            "File reads are sandboxed to the data root.",
            "File writes are sandboxed to the workspace root.",
            "Tool limits reflect current built-in behavior and may be narrower than future custom-tool plans.",
        ],
    }
