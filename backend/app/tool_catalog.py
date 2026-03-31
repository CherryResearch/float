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
    "help": _entry(
        category="help",
        summary="List tools or inspect one tool with compact defaults.",
        description=(
            "Returns lean built-in tool guidance so the model can verify a capability "
            "without pulling full schemas unless requested."
        ),
        runtime={"executor": "backend_python", "network": False, "filesystem": False},
        limits={"max_tools": 50},
        freshness={"type": "local_metadata"},
        persistence={"writes_state": False},
        safety={"risk_level": "low", "default_approval": "auto"},
        can_access=["built-in tool metadata and argument schemas"],
        cannot_access=["network, files, browser state, or user secrets"],
        limit_hints=[
            "Defaults are brief, schema-free, and capped for lower token usage."
        ],
    ),
    "tool_help": _entry(
        category="help",
        summary="Explain available tools, arguments, and examples.",
        description=(
            "Returns structured documentation for built-in tools. Prefer `help` "
            "for the lean default experience; this alias remains available for compatibility."
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
        summary="Legacy browser-open alias backed by the computer runtime.",
        description=(
            "Compatibility alias that starts or reuses the shared browser computer "
            "session and navigates it to the requested URL."
        ),
        status="legacy",
        runtime={"executor": "backend_python", "network": True, "filesystem": True},
        freshness={"type": "live_runtime"},
        persistence={"writes_state": True, "stores_output": True},
        safety={"risk_level": "medium", "default_approval": "confirm"},
        can_access=["browser navigation and browser screenshots"],
        cannot_access=["private accounts unless separately authenticated in-session"],
        limit_hints=["Prefer `computer.navigate` for new prompts."],
    ),
    "computer.session.start": _entry(
        category="computer",
        summary="Start or reuse a browser or Windows desktop control session.",
        runtime={"executor": "backend_python", "network": True, "filesystem": True},
        persistence={"writes_state": True, "stores_output": True},
        freshness={"type": "live_runtime"},
        safety={"risk_level": "medium", "default_approval": "confirm"},
        can_access=["browser state or Windows desktop state, depending on runtime"],
        cannot_access=["durable session recovery across backend restarts"],
        limit_hints=["Sessions are process-local in v1."],
    ),
    "computer.session.stop": _entry(
        category="computer",
        summary="Stop a browser or Windows desktop control session.",
        runtime={"executor": "backend_python", "network": False, "filesystem": True},
        persistence={"writes_state": True, "stores_output": True},
        freshness={"type": "live_runtime"},
        safety={"risk_level": "medium", "default_approval": "confirm"},
        can_access=["the targeted computer session"],
        cannot_access=["other sessions unless explicitly addressed"],
    ),
    "computer.observe": _entry(
        category="computer",
        summary="Capture a screenshot and runtime metadata from the active session.",
        runtime={"executor": "backend_python", "network": False, "filesystem": True},
        persistence={"writes_state": False, "stores_output": True},
        freshness={"type": "live_runtime"},
        safety={"risk_level": "medium", "default_approval": "confirm"},
        can_access=["current URL or active window title and screenshots"],
        cannot_access=["DOM extraction or OCR-rich element inspection in v1"],
        limit_hints=["Observation returns image attachments for follow-up turns."],
    ),
    "computer.act": _entry(
        category="computer",
        summary="Apply clicks, typing, keypresses, scrolling, waiting, or navigation actions.",
        runtime={"executor": "backend_python", "network": True, "filesystem": True},
        persistence={"writes_state": True, "stores_output": True},
        freshness={"type": "live_runtime"},
        safety={"risk_level": "high", "default_approval": "confirm"},
        can_access=["the active browser or desktop session and its screenshots"],
        cannot_access=[
            "hidden desktop automation features not supported by the selected runtime"
        ],
        limit_hints=["Action batches are executed sequentially."],
    ),
    "computer.navigate": _entry(
        category="computer",
        summary="Navigate the active browser session to a URL.",
        runtime={"executor": "backend_python", "network": True, "filesystem": True},
        persistence={"writes_state": True, "stores_output": True},
        freshness={"type": "live_runtime"},
        safety={"risk_level": "high", "default_approval": "confirm"},
        can_access=["live browser navigation and screenshots"],
        cannot_access=["desktop app launch or window focus"],
    ),
    "computer.windows.list": _entry(
        category="computer",
        summary="List visible Windows desktop windows.",
        runtime={"executor": "backend_python", "network": False, "filesystem": False},
        persistence={"writes_state": False},
        freshness={"type": "live_runtime"},
        safety={"risk_level": "medium", "default_approval": "confirm"},
        can_access=["visible Windows desktop window titles"],
        cannot_access=["non-Windows hosts or unavailable pywinauto environments"],
    ),
    "computer.windows.focus": _entry(
        category="computer",
        summary="Focus a Windows desktop window by title.",
        runtime={"executor": "backend_python", "network": False, "filesystem": False},
        persistence={"writes_state": True, "stores_output": True},
        freshness={"type": "live_runtime"},
        safety={"risk_level": "high", "default_approval": "confirm"},
        can_access=["focus changes on a Windows desktop window"],
        cannot_access=["window element inspection or drag-and-drop in v1"],
    ),
    "computer.app.launch": _entry(
        category="computer",
        summary="Launch a desktop application in the Windows runtime.",
        runtime={"executor": "backend_python", "network": False, "filesystem": False},
        persistence={"writes_state": True, "stores_output": True},
        freshness={"type": "live_runtime"},
        safety={"risk_level": "high", "default_approval": "confirm"},
        can_access=["desktop app launch on Windows"],
        cannot_access=["sandboxed isolation in v1"],
    ),
    "camera.capture": _entry(
        category="capture",
        summary="Capture a still image from a connected client camera.",
        status="experimental",
        runtime={"executor": "client_browser", "network": False, "filesystem": True},
        persistence={"writes_state": True, "stores_output": True},
        freshness={"type": "live_client_state"},
        safety={"risk_level": "low", "default_approval": "auto"},
        can_access=[
            "a camera exposed to the connected UI client after user permission"
        ],
        cannot_access=[
            "backend-only environments with no camera-capable client attached"
        ],
        limit_hints=[
            "The capture is transient by default and follows the configured capture retention policy."
        ],
    ),
    "capture.list": _entry(
        category="capture",
        summary="List recent transient captures from computer, camera, or screen sources.",
        runtime={"executor": "backend_python", "network": False, "filesystem": True},
        persistence={"writes_state": False, "stores_output": True},
        freshness={"type": "live_filesystem"},
        safety={"risk_level": "low", "default_approval": "auto"},
        can_access=["transient capture metadata and capture content URLs"],
        cannot_access=[
            "durable attachments unless a capture has already been promoted"
        ],
    ),
    "capture.promote": _entry(
        category="capture",
        summary="Promote a transient capture into durable attachment storage.",
        runtime={"executor": "backend_python", "network": False, "filesystem": True},
        persistence={"writes_state": True, "stores_output": True},
        freshness={"type": "live_filesystem"},
        safety={"risk_level": "high", "default_approval": "confirm"},
        can_access=["transient captures and durable attachment storage"],
        cannot_access=[
            "automatic memory graph linking beyond stored attachment metadata in v1"
        ],
        limit_hints=[
            "Promotion preserves the transient capture and creates a durable attachment reference."
        ],
    ),
    "capture.delete": _entry(
        category="capture",
        summary="Delete a transient capture from the cache.",
        runtime={"executor": "backend_python", "network": False, "filesystem": True},
        persistence={"writes_state": True, "stores_output": True},
        freshness={"type": "live_filesystem"},
        safety={"risk_level": "high", "default_approval": "confirm"},
        can_access=["transient capture files and metadata"],
        cannot_access=[
            "durable attachments that were already promoted from the capture"
        ],
    ),
    "shell.exec": _entry(
        category="system",
        summary="Run a host shell command and capture stdout/stderr.",
        runtime={"executor": "backend_python", "network": True, "filesystem": True},
        persistence={"writes_state": True, "stores_output": True},
        freshness={"type": "live_runtime"},
        safety={"risk_level": "high", "default_approval": "confirm"},
        can_access=["the host shell environment and current filesystem permissions"],
        cannot_access=["any sandbox narrower than the current Float host process"],
        limit_hints=[
            "Command output is clipped to the last 12,000 characters per stream."
        ],
    ),
    "patch.apply": _entry(
        category="system",
        summary="Write or append text content to a local file.",
        runtime={"executor": "backend_python", "network": False, "filesystem": True},
        persistence={"writes_state": True, "stores_output": True},
        freshness={"type": "live_filesystem"},
        safety={"risk_level": "high", "default_approval": "confirm"},
        can_access=["local text file writes at the requested path"],
        cannot_access=["binary patch application or structural diff merges in v1"],
        limit_hints=[
            "Current implementation is a text write helper, not a git-style patch engine."
        ],
    ),
    "mcp.call": _entry(
        category="integration",
        summary="Call an MCP endpoint when a bridge is configured.",
        status="experimental",
        runtime={"executor": "backend_python", "network": True, "filesystem": False},
        persistence={"writes_state": False},
        freshness={"type": "runtime_dependent"},
        safety={"risk_level": "medium", "default_approval": "confirm"},
        can_access=["configured MCP bridges only"],
        cannot_access=["arbitrary remote procedure calls without a configured bridge"],
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
        "help": "Help",
        "tool_help": "Tool Help",
        "open_url": "Open URL",
        "computer.session.start": "Computer Session Start",
        "computer.session.stop": "Computer Session Stop",
        "computer.observe": "Computer Observe",
        "computer.act": "Computer Act",
        "computer.navigate": "Computer Navigate",
        "computer.windows.list": "Computer Windows List",
        "computer.windows.focus": "Computer Windows Focus",
        "computer.app.launch": "Computer App Launch",
        "shell.exec": "Shell Exec",
        "patch.apply": "Patch Apply",
        "mcp.call": "MCP Call",
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
            "computer_default_width": 1280,
            "computer_default_height": 720,
            "shell_exec_timeout_seconds": 20,
        },
        "notes": [
            "File reads are sandboxed to the data root.",
            "File writes are sandboxed to the workspace root.",
            "Computer sessions are process-local and screenshots are stored under data/files/screenshots/computer_use/.",
            "Tool limits reflect current built-in behavior and may be narrower than future custom-tool plans.",
        ],
    }
