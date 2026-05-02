"""On-demand tool documentation helper.

Use this tool to fetch richer, structured docs for available tools without
embedding full guidance in every model prompt.
"""

from __future__ import annotations

import json
from difflib import get_close_matches
from pathlib import Path
from typing import Any, Dict, List

from app import config as app_config
from app.tool_catalog import get_tool_catalog_entry
from app.tool_specs import BUILTIN_TOOL_SPECS
from app.utils import verify_signature
from app.workflow_profiles import workflow_catalog_payload

_DEFAULT_DISCOVERY_PRIORITY = [
    "help",
    "tool_help",
    "tool_info",
    "remember",
    "recall",
    "create_task",
    "list_tasks",
    "list_dir",
    "read_file",
    "write_file",
    "search_web",
    "crawl",
    "list_actions",
    "read_action_diff",
    "revert_actions",
    "reflect",
    "list_reflections",
    "read_threads_summary",
    "camera.capture",
    "capture.list",
]
_DISCOVERY_OVERFLOW_PRIORITY = [
    "list_actions",
    "read_action_diff",
    "revert_actions",
    *_DEFAULT_DISCOVERY_PRIORITY,
]
_DEFAULT_HELP_HIDDEN_NAMES = {
    "memory.save",
    "create_event",
    "open_url",
    "subchat",
    "route_to_local_model",
    "shell.exec",
    "patch.apply",
    "mcp.call",
    "generate_threads",
    "compact_conversation_plan",
    "compact_conversation_preview",
    "compact_conversation_write",
    "computer.session.start",
    "computer.session.stop",
    "computer.observe",
    "computer.act",
    "computer.navigate",
    "computer.windows.list",
    "computer.windows.focus",
    "computer.app.launch",
    "capture.promote",
    "capture.delete",
}
_FAMILY_HELP_HIDDEN_NAMES = {
    "memory.save",
    "create_event",
    "open_url",
}
_DEFAULT_DISCOVERY_SUITES = [
    {
        "name": "compact_conversation.*",
        "family": "conversation",
        "tools": [
            "compact_conversation_plan",
            "compact_conversation_preview",
            "compact_conversation_write",
        ],
        "summary": "Plan, preview, and write long-chat compaction.",
    },
    {
        "name": "computer.*",
        "family": "computer",
        "tools": [
            "computer.session.start",
            "computer.session.stop",
            "computer.observe",
            "computer.act",
            "computer.navigate",
            "computer.windows.list",
            "computer.windows.focus",
            "computer.app.launch",
        ],
        "summary": "Browser and desktop observation, navigation, and actions.",
    },
    {
        "name": "capture.*",
        "family": "capture",
        "tools": ["capture.list", "capture.promote", "capture.delete"],
        "summary": "List, promote, or delete transient captures.",
    },
    {
        "name": "system",
        "family": "system",
        "tools": ["shell.exec", "patch.apply", "mcp.call"],
        "summary": "Approval-gated shell, patch, and MCP access; inspect before use.",
    },
]
_COMPAT_TOOL_NAME_HINTS: Dict[str, tuple[str, ...]] = {
    "memory.read": ("recall", "remember", "memory.save"),
    "memory.recall": ("recall", "remember", "memory.save"),
    "memory.search": ("recall", "remember", "memory.save"),
    "memory.store": ("remember", "recall", "memory.save"),
    "memory.write": ("remember", "recall", "memory.save"),
    "memory.remember": ("remember", "recall", "memory.save"),
    "open.url": ("computer.navigate", "open_url"),
    "browser.open": ("computer.navigate", "open_url"),
    "shell": ("shell.exec",),
    "patch": ("patch.apply",),
    "mcp": ("mcp.call",),
}
_SPECIAL_ENTRY_ALIASES = {
    "addon": "modules",
    "addons": "modules",
    "add-on": "modules",
    "add-ons": "modules",
    "module": "modules",
    "modules": "modules",
    "runtime": "modules",
    "workflow": "modules",
    "workflows": "modules",
    "skill": "skills",
    "skills": "skills",
}
_TOOL_FAMILY_ALIASES: Dict[str, tuple[str, ...]] = {
    "action": ("history",),
    "actions": ("history",),
    "agent": ("workflow",),
    "agents": ("workflow",),
    "app": ("computer",),
    "apps": ("computer",),
    "browser": ("computer", "web"),
    "browsing": ("computer", "web"),
    "calendar": ("calendar",),
    "camera": ("capture",),
    "chat": ("conversation",),
    "chats": ("conversation",),
    "compact": ("conversation",),
    "compaction": ("conversation",),
    "context": ("conversation",),
    "desktop": ("computer",),
    "event": ("calendar",),
    "events": ("calendar",),
    "file": ("files",),
    "files": ("files",),
    "folder": ("files",),
    "folders": ("files",),
    "internet": ("web",),
    "knowledge": ("memory",),
    "memories": ("memory",),
    "mcp": ("integration",),
    "patch": ("system",),
    "patches": ("system",),
    "reminder": ("calendar",),
    "reminders": ("calendar",),
    "revert": ("history",),
    "schedule": ("calendar",),
    "scheduled": ("calendar",),
    "scheduler": ("calendar",),
    "screenshot": ("capture", "computer"),
    "screenshots": ("capture", "computer"),
    "search": ("web",),
    "shell": ("system",),
    "task": ("calendar",),
    "tasks": ("calendar",),
    "thread": ("threads",),
    "tool": ("help",),
    "tools": ("help",),
    "undo": ("history",),
    "url": ("web",),
    "urls": ("web",),
    "web": ("web",),
    "window": ("computer",),
    "windows": ("computer",),
    "workspace": ("files",),
    "workspaces": ("files",),
}

_TOOL_NOTES: Dict[str, Dict[str, Any]] = {
    "help": {
        "notes": [
            "Use this as the primary built-in documentation tool: call it with `{}` or omit `tool_name` to list tools, or pass one tool name for a focused guide.",
            "Default list mode returns a curated menu and suite summaries; use detail='brief' for one-line descriptions or pass a family/tool name for exact members.",
            "Only add `failed_tool_name`, `failed_args`, and `failed_error` when recovering from a real failed tool call; ordinary discovery should stay on the plain menu path.",
            "If browser or desktop control is needed, inspect `computer.session.start` first, then follow with `computer.navigate`, `computer.observe`, or `computer.act`.",
            "Pass `tool_name='modules'` to inspect live workflows, enabled modules, and packaged add-ons, or `tool_name='skills'` to inspect packaged skill files.",
        ],
        "examples": [
            {},
            {
                "failed_tool_name": "tool_info",
                "failed_args": {},
                "failed_error": "missing_tool",
            },
            {"tool_name": "computer.session.start"},
            {"tool_name": "modules"},
            {"tool_name": "skills"},
        ],
    },
    "tool_info": {
        "notes": [
            "Use this when the model needs one authoritative capability record for a built-in tool.",
            "The response mirrors the built-in catalog used by the UI.",
            "Inspect runtime and sandbox fields before assuming a tool has network, filesystem, or Python-style execution access.",
            "If a previous tool attempt failed, you can pass `failed_tool_name`, `failed_args`, and `failed_error` so the response carries a one-line recovery breadcrumb.",
            "It also accepts the special entries `modules` and `skills` for non-tool runtime catalogs.",
        ],
        "examples": [
            {"tool_name": "search_web", "include_schema": True},
            {
                "tool_name": "write_file",
                "failed_tool_name": "writefile",
                "failed_error": "unknown_tool",
            },
            {"tool_name": "modules", "include_schema": False},
        ],
    },
    "list_actions": {
        "notes": [
            "Use this to inspect tracked local write operations before proposing a revert.",
            "Actions can be scoped to one conversation or one response so the model can undo a whole batch instead of one write at a time.",
            "Read-only tools such as search are not expected to appear here; this history is for persisted local writes.",
        ],
        "examples": [
            {
                "conversation_id": "",
                "response_id": "",
                "include_reverted": True,
                "limit": 20,
            },
            {"response_id": "msg-123", "include_reverted": False, "limit": 10},
        ],
    },
    "read_action_diff": {
        "notes": [
            "Use this after `list_actions` when you need the concrete before/after diff for one tracked write.",
            "The response includes per-resource diffs, so one action can describe file edits, memory updates, or sync-applied changes consistently.",
        ],
        "examples": [
            {"action_id": "action-123"},
        ],
    },
    "revert_actions": {
        "notes": [
            "Use this to undo one tracked write or an entire batch from the same response or conversation.",
            "Reverts restore stored before-state snapshots for each tracked resource.",
            "Prefer reviewing `list_actions` and `read_action_diff` first when the user asks what would be reverted.",
            "Conflict checks can block reverts when newer active writes touched the same resources; only force a revert when the user explicitly accepts that tradeoff.",
        ],
        "safety": [
            "This changes local state by restoring stored before-snapshots.",
        ],
        "examples": [
            {"action_ids": ["action-123"], "force": False},
            {"response_id": "msg-123", "force": False},
            {"conversation_id": "sess-123", "force": False},
        ],
    },
    "tool_help": {
        "notes": [
            "Use this to discover which tools actually exist in the current environment before planning a multi-tool workflow.",
            "Prefer `help` for new calls; `tool_help` remains as a compatibility alias.",
            "Call `tool_help` with `{}` or omit `tool_name` when you need the curated menu for ordinary browsing; use `tool_name` as a fuzzy/category filter such as `computer`.",
            "Use detail='brief' when descriptions are needed, then `tool_info` with one exact name for schema.",
            "If a previous tool call failed, you can pass `failed_tool_name`, `failed_args`, and `failed_error` to get a one-line recovery breadcrumb without losing the menu.",
            "Prefer calling this over hand-listing tool handles from memory when the user asks what float can do.",
            "Pass `tool_name='modules'` for workflow/module/add-on state or `tool_name='skills'` for packaged skill-file discovery.",
            "If the user is asking about Float itself, its setup, or project layout, inspect the repo's root `README.md`; because that file is outside the managed `data/` sandbox, prefer `shell.exec` over `read_file` for that path.",
            "If the user asks for reminders, tasks, events, or scheduling, inspect `create_task` and `list_tasks` before claiming no scheduler exists.",
            "Check runtime and sandbox metadata before assuming shell, REPL, Python, network, or filesystem access.",
            "For browser or desktop automation, inspect the `computer.*` tools before describing what the runtime can click, type, or launch.",
            "For structured or semi-structured artifacts such as CSV, JSON, logs, or sampled document sets, prefer typed summaries and stable handles when the available tools support that flow.",
        ],
        "examples": [
            {},
            {
                "tool_name": "",
                "failed_tool_name": "tool_info",
                "failed_args": {},
                "failed_error": "missing_tool",
            },
            {"tool_name": "tool_info", "include_schema": True},
        ],
    },
    "open_url": {
        "notes": [
            "Legacy compatibility alias for browser navigation.",
            "Prefer `computer.navigate` for new plans so browser sessions and screenshots stay in one workflow.",
        ],
        "examples": [
            {"url": "https://example.com"},
        ],
    },
    "computer.observe": {
        "notes": [
            "Captures the current browser or desktop state for one computer-use session.",
            "Use this before planning a click or typing action when the current page, window, or screenshot may have changed.",
            "The result can include a screenshot attachment plus current URL and active window metadata.",
        ],
        "examples": [
            {"session_id": "sess-computer-1"},
        ],
    },
    "computer.session.start": {
        "notes": [
            "Start here for any browser or desktop control workflow; the returned `session_id` must be reused for later computer tools.",
            "For an isolated browser workflow, prefer `runtime='browser'`; for host desktop control, prefer `runtime='windows'`.",
            "Do not call `computer.app.launch`, `computer.navigate`, `computer.observe`, or `computer.act` without a real session ID from this tool or a previous result.",
        ],
        "examples": [
            {"runtime": "browser", "session_id": "reddit-browser"},
            {"runtime": "windows", "session_id": "desktop"},
        ],
    },
    "computer.act": {
        "notes": [
            "Executes one or more input actions such as click, double-click, scroll, type, wait, or keypress in the active computer session.",
            "Prefer small, verifiable action batches and re-observe after major page changes.",
            "If an action result already reports an error or denial, do not keep treating the tool as pending approval.",
        ],
        "safety": [
            "Mutates browser or desktop state and can require approval depending on the action batch.",
        ],
        "examples": [
            {
                "session_id": "sess-computer-1",
                "actions": [{"type": "click", "x": 320, "y": 180}],
            },
        ],
    },
    "computer.navigate": {
        "notes": [
            "Changes the current browser page for an active computer-use session.",
            "Use this instead of `open_url` for new computer-use workflows.",
        ],
        "examples": [
            {"session_id": "sess-computer-1", "url": "https://example.com"},
        ],
    },
    "computer.windows.list": {
        "notes": [
            "Lists currently available desktop windows for the active computer-use session.",
            "Use this before focusing a specific app window when the title may have changed.",
        ],
        "examples": [
            {"session_id": "sess-computer-1"},
        ],
    },
    "computer.windows.focus": {
        "notes": [
            "Brings a matching desktop window to the foreground in the current session.",
            "Use it after `computer.windows.list` or when you already know the target window title.",
        ],
        "safety": [
            "Changes desktop focus and can affect later input actions.",
        ],
        "examples": [
            {"session_id": "sess-computer-1", "window_title": "Untitled - Notepad"},
        ],
    },
    "computer.app.launch": {
        "notes": [
            "Launches a supported desktop application inside the current computer-use session.",
            "Requires an existing `session_id`; it is not the first step for browser automation.",
            "Use it for the narrow desktop MVP flow before listing or focusing windows.",
        ],
        "safety": [
            "Starts a local app process and can require approval.",
        ],
        "examples": [
            {"session_id": "sess-computer-1", "app": "notepad"},
        ],
    },
    "camera.capture": {
        "notes": [
            "Requests a still image from a connected client camera rather than the backend host.",
            "The resulting image is stored as a transient capture first and follows the configured capture retention window unless promoted.",
            "Use this when live camera context is needed for a local streaming or DIY realtime workflow.",
        ],
        "examples": [{}],
    },
    "capture.list": {
        "notes": [
            "Lists recent transient captures from computer observations, camera stills, and screen stills.",
            "Use it to inspect what the current session can still reference before the transient retention window expires.",
        ],
        "examples": [{"source": ""}, {"source": "camera"}],
    },
    "capture.promote": {
        "notes": [
            "Promotes a transient capture into durable attachment storage so later turns and memory flows can reference it again.",
            "Promotion preserves the original capture metadata and returns a durable attachment reference.",
        ],
        "safety": [
            "Turns transient image state into durable stored state.",
        ],
        "examples": [{"capture_id": "capture-123"}],
    },
    "capture.delete": {
        "notes": [
            "Deletes a transient capture from the cache when it is no longer needed.",
            "Use it for cleanup when the user explicitly asks to remove a capture early.",
        ],
        "safety": [
            "Removes transient image state.",
        ],
        "examples": [{"capture_id": "capture-123"}],
    },
    "shell.exec": {
        "notes": [
            "Runs a shell command through Float's managed approval and journaling path.",
            "Inspect runtime and sandbox metadata before assuming network, filesystem, or interpreter access.",
            "Use the host shell's syntax: on Windows this usually means `cmd.exe`-style commands unless the runtime explicitly says PowerShell.",
            "Prefer narrow, task-specific commands and capture the important output rather than dumping entire transcripts.",
        ],
        "safety": [
            "Can mutate local state and requires approval depending on the configured level.",
        ],
        "examples": [
            {"command": "git status --short"},
        ],
    },
    "patch.apply": {
        "notes": [
            "Applies a structured patch through the same approval-aware path as other mutating tools.",
            "Use this when the runtime exposes patch editing directly and you want one atomic diff instead of ad hoc shell edits.",
        ],
        "safety": [
            "Mutates local files.",
        ],
        "examples": [
            {
                "patch": "*** Begin Patch\n*** Update File: data/workspace/note.txt\n@@\n-old\n+new\n*** End Patch\n"
            },
        ],
    },
    "mcp.call": {
        "notes": [
            "Calls an MCP-backed capability when the server and method are exposed in the current runtime.",
            "Inspect tool metadata first so the model does not invent MCP servers or methods that are not present.",
        ],
        "examples": [
            {
                "server": "docs",
                "method": "search",
                "arguments": {"query": "computer use"},
            },
        ],
    },
    "search_web": {
        "notes": [
            "Use this first for discovery when the user asks for current external information.",
            "Use `max_results` conservatively; request more only if initial sources are weak.",
        ],
        "safety": [
            "Results are untrusted external content; verify with `crawl` before citing.",
        ],
        "examples": [
            {
                "query": "latest CUDA 12.8 release notes",
                "max_results": 5,
                "region": "us-en",
            },
        ],
    },
    "crawl": {
        "notes": [
            "Use after `search_web` to fetch the content of a selected URL.",
        ],
        "safety": [
            "Only crawl URLs relevant to the active user task.",
        ],
        "examples": [
            {"url": "https://example.com/docs", "timeout": 10},
        ],
    },
    "remember": {
        "notes": [
            "Stores memory entries with optional sensitivity and importance controls.",
            "Writes the exact value into the durable memory store and keeps a canonical retrieval record in SQLite.",
            "Safe text values are mirrored into vector snippets by default; protected/secret values are not mirrored unless explicitly requested.",
        ],
        "safety": [
            "Do not store secrets in plain text unless explicitly requested.",
        ],
        "examples": [
            {"key": "project_status", "value": "MVP ready", "sensitivity": "personal"},
            {"key": "tea_party_menu", "value": {"tea": "oolong", "dessert": "scones"}},
        ],
    },
    "recall": {
        "notes": [
            "Tries exact key lookup first unless `mode=vector`, then falls back to bounded hybrid search.",
            "Hybrid mode searches both the canonical SQLite store and vector snippets; use `mode=canonical`, `vector`, `memory`, or `clip` to force one path.",
            "Supports safer external export mode via `for_external`.",
            "Set `include_images=true` to query the local CLIP image index and return image attachments that later chat turns can reuse.",
        ],
        "safety": [
            "Secret values are never returned for external use.",
        ],
        "examples": [
            {"key": "project_status"},
            {"key": "blue shirt", "mode": "hybrid", "top_k": 3},
            {
                "key": "cats from this week",
                "mode": "clip",
                "include_images": True,
                "image_top_k": 2,
            },
            {"for_external": True, "allow_protected": False},
        ],
    },
    "write_file": {
        "notes": [
            "Writes local content under the workspace path.",
        ],
        "safety": [
            "Use explicit file paths and avoid destructive overwrite patterns.",
        ],
        "examples": [
            {"path": "notes/todo.txt", "content": "Ship tool_help support"},
        ],
    },
    "create_event": {
        "notes": [
            "Compatibility alias for the calendar/task creation flow.",
            "Use it when a model or older prompt expects an event-focused handle; the saved payload shape is the same as `create_task`.",
            "Time inputs can be unix timestamps, ISO strings, natural language, or `{date, time, timezone}` style objects.",
        ],
        "examples": [
            {
                "title": "Lunch with Maya",
                "start": {"date": "2026-03-25", "time": "12:30 pm"},
                "duration": "1h",
                "timezone": "America/Vancouver",
            }
        ],
    },
    "create_task": {
        "notes": [
            "This is the built-in way to create reminder-style tasks and upcoming events inside Float.",
            "It accepts unix timestamps, ISO datetimes, simple natural-language times, and `{date, time, timezone}` style objects when paired with `timezone` or `grounded_at`.",
            "Use `actions` to attach structured follow-up prompts or tool calls that should run from the saved task later.",
        ],
        "examples": [
            {
                "title": "Put in catering order",
                "start_time": "2026-03-25 12:00",
                "timezone": "America/Vancouver",
                "description": "Codex meetup reminder",
            },
            {
                "title": "Weekly planning",
                "start": {"date": "2026-03-27", "time": "9am"},
                "duration": "90m",
                "timezone": "America/Vancouver",
            },
            {
                "title": "Follow up on notes",
                "start_time": "tomorrow at 9am",
                "timezone": "America/Vancouver",
                "actions": [
                    {
                        "kind": "prompt",
                        "prompt": "Review the meetup checklist and ask what is still missing.",
                        "conversation_mode": "new_chat",
                    }
                ],
            },
        ],
    },
    "list_tasks": {
        "notes": [
            "Use this to read upcoming or already-saved Float calendar tasks back from local storage.",
            "By default it focuses on upcoming items; set `include_past=true` when the user explicitly wants historical entries too.",
            "Use it alongside `create_task` when the model needs to verify what is already scheduled before adding a duplicate reminder.",
        ],
        "examples": [
            {"limit": 10},
            {"include_past": True, "status": "scheduled", "limit": 20},
        ],
    },
    "read_file": {
        "notes": [
            "Use `list_dir` first when the exact path is uncertain.",
            "Reads are windowed by `start_line`, `line_count`, and `max_chars` so large files can be paged safely.",
            "Paths are resolved under the managed `data/` root, so prefer `workspace/...` or `data/workspace/...` for workspace files.",
            "For CSV or log analysis, inspect the header and a small early slice before requesting later chunks.",
        ],
        "safety": [
            "Do not request whole large files when a narrow excerpt will answer the question.",
        ],
        "examples": [
            {"path": "workspace/report.csv"},
            {
                "path": "workspace/report.csv",
                "start_line": 1,
                "line_count": 40,
                "max_chars": 8000,
            },
            {"path": "workspace/report.csv", "start_line": 400, "line_count": 80},
        ],
    },
    "list_dir": {
        "notes": [
            "Lists directories and files inside Float's managed data roots without reading file contents.",
            "Use `workspace_only=true` when you only need the writable workspace view.",
        ],
        "safety": [
            "Prefer narrow paths and modest `max_entries` limits to keep results readable.",
        ],
        "examples": [
            {"path": ".", "workspace_only": True},
            {"path": "files/uploads", "recursive": True, "max_entries": 25},
        ],
    },
}


def _available_tool_names() -> List[str]:
    return [
        str(name)
        for name in BUILTIN_TOOL_SPECS.keys()
        if isinstance(name, str) and name.strip()
    ]


def _default_discovery_suite_entries(available: List[str]) -> List[Dict[str, Any]]:
    available_set = set(available)
    suites: List[Dict[str, Any]] = []
    for suite in _DEFAULT_DISCOVERY_SUITES:
        names = [
            name
            for name in suite.get("tools", [])
            if isinstance(name, str) and name in available_set
        ]
        if not names:
            continue
        suites.append(
            {
                "name": suite["name"],
                "family": suite["family"],
                "count": len(names),
                "summary": suite["summary"],
                "exact_tools": names,
                "hint": (
                    f"Call help with tool_name='{suite['family']}' and detail='brief' "
                    "for descriptions, or tool_info with one exact tool for schema."
                ),
            }
        )
    return suites


def _default_discovery_names(available: List[str]) -> tuple[List[str], List[str]]:
    hidden = [name for name in available if name in _DEFAULT_HELP_HIDDEN_NAMES]
    visible = [name for name in available if name not in _DEFAULT_HELP_HIDDEN_NAMES]
    return visible, hidden


def _family_discovery_names(names: List[str]) -> List[str]:
    visible = [name for name in names if name not in _FAMILY_HELP_HIDDEN_NAMES]
    return visible or list(names)


def _compat_tool_name_suggestions(
    requested_name: str,
    available: List[str],
    *,
    limit: int = 4,
) -> List[str]:
    key = str(requested_name or "").strip().lower().replace("_", ".")
    hints = _COMPAT_TOOL_NAME_HINTS.get(key, ())
    suggestions: List[str] = []
    for hint in hints:
        if hint in available and hint not in suggestions:
            suggestions.append(hint)
        if len(suggestions) >= limit:
            return suggestions
    return suggestions


def _family_token(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _split_family_tokens(value: Any) -> set[str]:
    token = _family_token(value)
    if not token:
        return set()
    tokens = {token}
    normalized = token.replace("_", ".")
    tokens.add(normalized)
    for part in token.replace(".", "_").split("_"):
        if part:
            tokens.add(part)
            if part.endswith("s") and len(part) > 3:
                tokens.add(part[:-1])
    for part in normalized.split("."):
        if part:
            tokens.add(part)
            if part.endswith("s") and len(part) > 3:
                tokens.add(part[:-1])
    if token.endswith("s") and len(token) > 3:
        tokens.add(token[:-1])
    return tokens


def _query_family_terms(requested_name: str) -> set[str]:
    base = _family_token(requested_name)
    if not base:
        return set()
    normalized = base.replace("_", ".")
    terms = {base, normalized}
    if "." in normalized:
        terms.add(normalized.split(".", 1)[0])
    elif "_" in base:
        primary = base.split("_", 1)[0]
        if primary in _TOOL_FAMILY_ALIASES:
            terms.add(primary)
    else:
        terms.update(_split_family_tokens(base))
    expanded = set(terms)
    for term in terms:
        expanded.update(_TOOL_FAMILY_ALIASES.get(term, ()))
    return {term for term in expanded if term}


def _tool_family_terms(name: str) -> set[str]:
    terms = _split_family_tokens(name)
    lowered = str(name or "").strip().lower()
    parts = [part for part in lowered.split(".") if part]
    for idx in range(1, len(parts)):
        terms.add(".".join(parts[:idx]))
    try:
        catalog = get_tool_catalog_entry(name)
    except Exception:
        catalog = {}
    category = _family_token(
        catalog.get("category") if isinstance(catalog, dict) else ""
    )
    if category:
        terms.add(category)
        if category.endswith("s") and len(category) > 3:
            terms.add(category[:-1])
    for alias, targets in _TOOL_FAMILY_ALIASES.items():
        if any(target in terms for target in targets):
            terms.add(alias)
    return {term for term in terms if term}


def _tool_family_summaries(available: List[str]) -> List[Dict[str, Any]]:
    families: Dict[str, List[str]] = {}
    for name in available:
        try:
            catalog = get_tool_catalog_entry(name)
        except Exception:
            catalog = {}
        category = _family_token(
            catalog.get("category") if isinstance(catalog, dict) else ""
        )
        if category:
            families.setdefault(category, []).append(name)
        dotted_prefix = str(name or "").split(".", 1)[0]
        if dotted_prefix and dotted_prefix != name:
            families.setdefault(dotted_prefix, []).append(name)
    return [
        {"name": family, "count": len(sorted(set(names)))}
        for family, names in sorted(families.items())
        if names
    ]


def _balanced_tool_name_selection(
    names: List[str], limit: int
) -> tuple[List[str], List[str]]:
    if limit <= 0:
        return [], list(names)
    if len(names) <= limit:
        return list(names), []

    selected: List[str] = []
    seen: set[str] = set()

    def ordered_omitted() -> List[str]:
        priority_tail: List[str] = []
        priority_seen: set[str] = set()
        for name in _DISCOVERY_OVERFLOW_PRIORITY:
            if name not in names or name in seen or name in priority_seen:
                continue
            priority_tail.append(name)
            priority_seen.add(name)
        return priority_tail + [
            name for name in names if name not in seen and name not in priority_seen
        ]

    for name in _DEFAULT_DISCOVERY_PRIORITY:
        if name not in names or name in seen:
            continue
        selected.append(name)
        seen.add(name)
        if len(selected) >= limit:
            return selected, ordered_omitted()

    remaining_names = [name for name in names if name not in seen]
    remaining_slots = limit - len(selected)
    head_count = (remaining_slots + 1) // 2
    tail_count = remaining_slots - head_count
    for name in remaining_names[:head_count] + remaining_names[-tail_count:]:
        if name in seen:
            continue
        seen.add(name)
        selected.append(name)
    return selected, ordered_omitted()


def _compact_recovery_value(value: Any, *, max_chars: int = 48) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        compact = " ".join(value.split())
        if len(compact) > max_chars:
            return compact[: max(0, max_chars - 3)].rstrip() + "..."
        return compact
    if isinstance(value, dict):
        named = value.get("id") or value.get("name") or value.get("title")
        if isinstance(named, str) and named.strip():
            return named.strip()
        return "object"
    if isinstance(value, list):
        return f"{len(value)} item(s)"
    try:
        compact = json.dumps(value, ensure_ascii=False)
    except Exception:
        compact = str(value)
    compact = " ".join(compact.split())
    if len(compact) > max_chars:
        return compact[: max(0, max_chars - 3)].rstrip() + "..."
    return compact


def _compact_recovery_args(args: Any, *, max_parts: int = 3) -> str:
    if not isinstance(args, dict):
        return ""
    parts: list[str] = []
    for key in sorted(args.keys(), key=lambda item: str(item)):
        value = args.get(key)
        if value in (None, "", [], {}):
            continue
        compact = _compact_recovery_value(value)
        if not compact:
            continue
        parts.append(f"{key}={compact}")
        if len(parts) >= max_parts:
            break
    return ", ".join(parts)


def _compact_failed_call_line(
    tool_name: str = "",
    args: Any = None,
    error: str = "",
) -> str | None:
    normalized_tool = str(tool_name or "").strip()
    normalized_error = " ".join(str(error or "").split()).strip()
    if not normalized_tool and not normalized_error:
        return None
    args_text = _compact_recovery_args(args)
    if normalized_tool:
        call_text = (
            f"{normalized_tool}({args_text})" if args_text else f"{normalized_tool}()"
        )
    else:
        call_text = "tool call"
    if normalized_error:
        if len(normalized_error) > 72:
            normalized_error = normalized_error[:69].rstrip() + "..."
        return f"{call_text} -> {normalized_error}"
    return call_text


def _tool_name_suggestions(
    requested_name: str, available: List[str], *, limit: int = 3
) -> List[str]:
    needle = str(requested_name or "").strip().lower()
    if not needle:
        return []

    family_matches = _matching_tool_names(requested_name, available)
    lowered = {name.lower(): name for name in available}
    suggestions: List[str] = []
    seen: set[str] = set()

    def _add(candidate: str) -> None:
        resolved = lowered.get(candidate.lower(), candidate)
        if not resolved or resolved in seen:
            return
        seen.add(resolved)
        suggestions.append(resolved)

    for name in _compat_tool_name_suggestions(
        requested_name, available, limit=limit
    ):
        _add(name)
        if len(suggestions) >= limit:
            return suggestions[:limit]
    for name in family_matches:
        _add(name)
        if len(suggestions) >= limit:
            return suggestions[:limit]
    for candidate in get_close_matches(
        needle, list(lowered.keys()), n=limit, cutoff=0.55
    ):
        _add(candidate)
    for name in available:
        lowered_name = name.lower()
        if needle in lowered_name or any(
            part.startswith(needle) for part in lowered_name.split(".")
        ):
            _add(name)
        if len(suggestions) >= limit:
            break
    return suggestions[:limit]


def _normalize_detail_mode(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return "names"
    aliases = {
        "name": "names",
        "names": "names",
        "list": "names",
        "brief": "brief",
        "short": "brief",
        "summary": "brief",
        "rich": "rich",
        "full": "rich",
        "schema": "rich",
        "schemas": "rich",
    }
    return aliases.get(text, "names")


def _matching_tool_names(
    requested_name: str, available: List[str], *, include_close: bool = True
) -> List[str]:
    needle = _family_token(requested_name)
    if not needle:
        return []
    matches: List[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        if not name or name in seen:
            return
        seen.add(name)
        matches.append(name)

    query_terms = _query_family_terms(requested_name)
    for name in available:
        if _tool_family_terms(name).intersection(query_terms):
            _add(name)
    if matches:
        return matches

    for name in available:
        lowered = name.lower()
        parts = lowered.split(".")
        if (
            needle in lowered
            or lowered.startswith(needle)
            or any(part.startswith(needle) for part in parts)
        ):
            _add(name)
    if matches:
        return matches
    if not include_close:
        return matches
    lowered_available = {name.lower(): name for name in available}
    for candidate in get_close_matches(
        needle, list(lowered_available.keys()), n=8, cutoff=0.55
    ):
        _add(lowered_available.get(candidate, candidate))
    return matches


def _prop_type(prop: Dict[str, Any]) -> str:
    raw = prop.get("type")
    if isinstance(raw, str) and raw:
        return raw
    if isinstance(raw, list) and raw:
        first = raw[0]
        if isinstance(first, str) and first:
            return first
    return "any"


def _build_tool_entry(
    name: str,
    *,
    detail: str,
    include_schema: bool,
) -> Dict[str, Any]:
    spec = BUILTIN_TOOL_SPECS.get(name) or {}
    catalog = get_tool_catalog_entry(name)
    params = spec.get("parameters") if isinstance(spec, dict) else {}
    if not isinstance(params, dict):
        params = {}
    properties = params.get("properties")
    if not isinstance(properties, dict):
        properties = {}
    required = params.get("required")
    required_keys = (
        set(str(v) for v in required) if isinstance(required, list) else set()
    )

    argument_summary: List[Dict[str, Any]] = []
    for arg_name, arg_spec in properties.items():
        if not isinstance(arg_spec, dict):
            arg_spec = {}
        entry: Dict[str, Any] = {
            "name": str(arg_name),
            "type": _prop_type(arg_spec),
            "required": str(arg_name) in required_keys,
        }
        if "default" in arg_spec:
            entry["default"] = arg_spec.get("default")
        if arg_spec.get("title"):
            entry["title"] = arg_spec.get("title")
        argument_summary.append(entry)

    base: Dict[str, Any] = {
        "name": name,
        "status": catalog.get("status", "live"),
        "category": catalog.get("category", "custom"),
        "summary": catalog.get("summary")
        or spec.get("description", "No description available."),
        "description": spec.get("description", "No description available."),
        "required_args": sorted(required_keys),
    }
    if detail == "brief":
        return base

    notes = _TOOL_NOTES.get(name, {})
    base["arguments"] = argument_summary
    for field in (
        "display_name",
        "origin",
        "can_access",
        "cannot_access",
        "limit_hints",
        "runtime",
        "sandbox",
        "limits",
        "freshness",
        "persistence",
    ):
        value = catalog.get(field)
        if value:
            base[field] = value
    safety_notes: List[str] = []
    catalog_safety = catalog.get("safety")
    if isinstance(catalog_safety, dict):
        default_approval = catalog_safety.get("default_approval")
        risk_level = catalog_safety.get("risk_level")
        if risk_level:
            safety_notes.append(f"Risk level: {risk_level}.")
        if default_approval:
            safety_notes.append(f"Default approval: {default_approval}.")
    if notes.get("notes"):
        base["notes"] = list(notes["notes"])
    if notes.get("safety"):
        safety_notes.extend(str(item) for item in notes["safety"])
    if safety_notes:
        base["safety"] = safety_notes
    if notes.get("examples"):
        base["examples"] = list(notes["examples"])
    if include_schema:
        base["schema"] = params
    return base


def _skills_root() -> Path:
    return (app_config.REPO_ROOT / "modules" / "skills").resolve()


def _read_skill_summary(path: Path) -> str:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return ""
    for raw_line in lines:
        line = str(raw_line or "").strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        return line
    return ""


def _skills_catalog_payload() -> Dict[str, Any]:
    root = _skills_root()
    entries: List[Dict[str, Any]] = []
    if root.exists():
        for path in sorted(root.glob("*.md")):
            if path.name.lower() == "readme.md":
                continue
            entries.append(
                {
                    "id": path.stem,
                    "label": path.stem.replace("_", " "),
                    "path": str(path),
                    "summary": _read_skill_summary(path),
                }
            )
    return {
        "skills_root": str(root),
        "skills": entries,
        "count": len(entries),
    }


def _build_special_entry(
    name: str,
    *,
    detail: str,
    include_schema: bool,
) -> Dict[str, Any] | None:
    normalized = _SPECIAL_ENTRY_ALIASES.get(
        _family_token(name), str(name or "").strip().lower()
    )
    if normalized == "modules":
        catalog = workflow_catalog_payload()
        entry: Dict[str, Any] = {
            "name": "modules",
            "status": "live",
            "category": "runtime",
            "summary": "Runtime workflow catalog including built-in workflows, modules, and packaged add-ons.",
            "description": "Inspect this when you need the current workflow/module surface rather than guessing from memory.",
        }
        if detail in {"names", "brief"}:
            entry["workflows"] = [
                item.get("id")
                for item in catalog.get("workflows", [])
                if isinstance(item, dict)
            ]
            entry["modules"] = [
                item.get("id")
                for item in catalog.get("modules", [])
                if isinstance(item, dict)
            ]
            entry["addons"] = [
                item.get("id")
                for item in catalog.get("addons", [])
                if isinstance(item, dict)
            ]
            return entry
        entry["notes"] = [
            "Modules are live workflow capabilities such as computer use, camera capture, memory promotion, and host shell access.",
            "Workflows choose behavior style and default enabled modules for a run.",
            "Add-ons are packaged manifests discoverable from the repo and optional local data overrides.",
        ]
        entry["workflows"] = catalog.get("workflows", [])
        entry["modules"] = catalog.get("modules", [])
        entry["addons"] = catalog.get("addons", [])
        entry["addons_root"] = catalog.get("addons_root")
        entry["addons_roots"] = catalog.get("addons_roots", [])
        if include_schema:
            entry["schema"] = {
                "type": "object",
                "properties": {
                    "workflows": {"type": "array"},
                    "modules": {"type": "array"},
                    "addons": {"type": "array"},
                },
            }
        return entry
    if normalized == "skills":
        catalog = _skills_catalog_payload()
        entry = {
            "name": "skills",
            "status": "partial",
            "category": "runtime",
            "summary": "Packaged skill markdown files available in this repo.",
            "description": "These are repo-shipped guidance files, not first-class executable tools.",
        }
        if detail in {"names", "brief"}:
            entry["skills"] = [
                item.get("id")
                for item in catalog.get("skills", [])
                if isinstance(item, dict)
            ]
            entry["skills_root"] = catalog.get("skills_root")
            return entry
        entry["notes"] = [
            "Skills are markdown guidance files stored under the repo's modules/skills directory.",
            "They are discoverable here, but they are not yet dynamically injected as a full runtime capability layer.",
        ]
        entry["skills_root"] = catalog.get("skills_root")
        entry["skills"] = catalog.get("skills", [])
        if include_schema:
            entry["schema"] = {
                "type": "object",
                "properties": {
                    "skills_root": {"type": "string"},
                    "skills": {"type": "array"},
                },
            }
        return entry
    return None


def _tool_menu_payload(
    available: List[str],
    *,
    detail: str,
    include_schema: bool,
    max_tools: int,
    default_menu: bool = False,
) -> Dict[str, Any]:
    menu_available = list(available)
    hidden_names: List[str] = []
    suites: List[Dict[str, Any]] = []
    if default_menu:
        menu_available, hidden_names = _default_discovery_names(available)
        suites = _default_discovery_suite_entries(available)
    selected_names, omitted_names = _balanced_tool_name_selection(
        menu_available, max_tools
    )
    if detail == "names":
        tools_payload: List[Any] = list(selected_names)
    else:
        tools_payload = [
            _build_tool_entry(
                name,
                detail=detail,
                include_schema=include_schema,
            )
            for name in selected_names
        ]
    response: Dict[str, Any] = {
        "count": len(selected_names),
        "total_count": len(available),
        "tools": tools_payload,
    }
    if default_menu:
        response["default_menu"] = True
        response["hidden_count"] = len(hidden_names)
        response["hint"] = (
            "This is the curated default menu. Use detail='brief' for one-line "
            "descriptions, or pass a family/tool_name to inspect exact tools."
        )
        if suites:
            response["suites"] = suites
    families = _tool_family_summaries(available)
    if families:
        response["families"] = families
    remaining_count = len(omitted_names) + (
        len(hidden_names) if default_menu else 0
    )
    if remaining_count:
        response["remaining_count"] = remaining_count
    if omitted_names:
        response["more_tools"] = omitted_names[:max_tools]
    if detail == "rich":
        response["note"] = "Pass tool_name to get a single full tool guide."
    return response


def _recovery_menu_payload(
    available: List[str], *, max_tools: int = 8
) -> Dict[str, Any]:
    menu = _tool_menu_payload(
        available,
        detail="names",
        include_schema=False,
        max_tools=max_tools,
    )
    return {
        "count": menu.get("count", 0),
        "total_count": menu.get("total_count", len(available)),
        "tools": menu.get("tools", []),
        **(
            {"remaining_count": menu["remaining_count"]}
            if "remaining_count" in menu
            else {}
        ),
        **({"more_tools": menu["more_tools"]} if "more_tools" in menu else {}),
    }


def _run_tool_help(
    *,
    tool_key: str,
    tool_name: str = "",
    detail: str = "names",
    include_schema: bool = False,
    max_tools: int = 50,
    failed_tool_name: str = "",
    failed_args: Any = None,
    failed_error: str = "",
    user: str,
    signature: str,
) -> Dict[str, Any]:
    requested_name = str(tool_name or "").strip()
    failed_tool_name_text = str(failed_tool_name or "").strip()
    failed_args_value = failed_args if isinstance(failed_args, dict) else {}
    failed_error_text = str(failed_error or "").strip()
    normalized_detail = _normalize_detail_mode(detail)
    limited_max_tools = max(1, min(int(max_tools or 50), 50))
    include_schema_flag = bool(include_schema)

    payload = {
        "tool_name": requested_name,
        "detail": normalized_detail,
        "include_schema": include_schema_flag,
        "max_tools": limited_max_tools,
        "failed_tool_name": failed_tool_name_text,
        "failed_args": failed_args_value,
        "failed_error": failed_error_text,
    }
    verify_signature(signature, user, tool_key, payload)

    available = _available_tool_names()
    recovery_line = _compact_failed_call_line(
        failed_tool_name_text,
        failed_args_value,
        failed_error_text,
    )
    if requested_name:
        special_entry = _build_special_entry(
            requested_name,
            detail=normalized_detail,
            include_schema=include_schema_flag,
        )
        if special_entry is not None:
            response = {
                "query": payload,
                "count": 1,
                "tools": [special_entry],
            }
            if recovery_line:
                response["failed_call"] = recovery_line
            return response
        if requested_name not in BUILTIN_TOOL_SPECS:
            compat_suggestions = _compat_tool_name_suggestions(requested_name, available)
            if compat_suggestions:
                response = {
                    "query": payload,
                    "error": "unknown_tool",
                    "tool_name": requested_name,
                    "message": "That compatibility-style name is not a primary tool in this environment.",
                    "hint": "Use one of the suggested exact tool names, or call tool_info on one exact name for schema.",
                    "did_you_mean": compat_suggestions,
                    **_tool_menu_payload(
                        compat_suggestions,
                        detail=normalized_detail,
                        include_schema=include_schema_flag,
                        max_tools=limited_max_tools,
                    ),
                }
                if recovery_line:
                    response["failed_call"] = recovery_line
                return response
            matched_names = _family_discovery_names(
                _matching_tool_names(requested_name, available)
            )
            if matched_names:
                response = {
                    "query": payload,
                    "filtered_by": requested_name,
                    **_tool_menu_payload(
                        matched_names,
                        detail=normalized_detail,
                        include_schema=include_schema_flag,
                        max_tools=limited_max_tools,
                    ),
                }
                if recovery_line:
                    response["failed_call"] = recovery_line
                return response
            response = {
                "query": payload,
                "error": "unknown_tool",
                "tool_name": requested_name,
                "available": available,
                "message": "Couldn't inspect that exact tool; showing a compact menu instead.",
                "hint": "Retry with one returned tool name, or call help/tool_help with {} to browse the menu.",
                **_tool_menu_payload(
                    available,
                    detail=normalized_detail,
                    include_schema=include_schema_flag,
                    max_tools=limited_max_tools,
                    default_menu=True,
                ),
            }
            suggestions = _tool_name_suggestions(requested_name, available)
            if suggestions:
                response["did_you_mean"] = suggestions
            response["failed_call"] = recovery_line or _compact_failed_call_line(
                tool_key,
                {"tool_name": requested_name},
                "unknown_tool",
            )
            return response
        entries = [
            _build_tool_entry(
                requested_name,
                detail=normalized_detail,
                include_schema=include_schema_flag,
            )
        ]
        response = {
            "query": payload,
            "count": 1,
            "tools": entries,
        }
        if recovery_line:
            response["failed_call"] = recovery_line
        return response

    response: Dict[str, Any] = {
        "query": payload,
        **_tool_menu_payload(
            available,
            detail=normalized_detail,
            include_schema=include_schema_flag,
            max_tools=limited_max_tools,
            default_menu=True,
        ),
    }
    if recovery_line:
        response["failed_call"] = recovery_line
    return response


def tool_help(
    tool_name: str = "",
    detail: str = "names",
    include_schema: bool = False,
    max_tools: int = 50,
    failed_tool_name: str = "",
    failed_args: Any = None,
    failed_error: str = "",
    *,
    user: str,
    signature: str,
) -> Dict[str, Any]:
    """Return tool docs for one tool or a filtered list of tools."""
    return _run_tool_help(
        tool_key="tool_help",
        tool_name=tool_name,
        detail=detail,
        include_schema=include_schema,
        max_tools=max_tools,
        failed_tool_name=failed_tool_name,
        failed_args=failed_args,
        failed_error=failed_error,
        user=user,
        signature=signature,
    )


def help_tool(
    tool_name: str = "",
    detail: str = "names",
    include_schema: bool = False,
    max_tools: int = 50,
    failed_tool_name: str = "",
    failed_args: Any = None,
    failed_error: str = "",
    *,
    user: str,
    signature: str,
) -> Dict[str, Any]:
    """Primary compact help tool for built-in tool discovery."""
    return _run_tool_help(
        tool_key="help",
        tool_name=tool_name,
        detail=detail,
        include_schema=include_schema,
        max_tools=max_tools,
        failed_tool_name=failed_tool_name,
        failed_args=failed_args,
        failed_error=failed_error,
        user=user,
        signature=signature,
    )


def tool_info(
    tool_name: str,
    include_schema: bool = True,
    failed_tool_name: str = "",
    failed_args: Any = None,
    failed_error: str = "",
    *,
    user: str,
    signature: str,
) -> Dict[str, Any]:
    """Return one capability record for a built-in tool."""

    requested_name = str(tool_name or "").strip()
    failed_tool_name_text = str(failed_tool_name or "").strip()
    failed_args_value = failed_args if isinstance(failed_args, dict) else {}
    failed_error_text = str(failed_error or "").strip()
    include_schema_flag = bool(include_schema)
    payload = {
        "tool_name": requested_name,
        "include_schema": include_schema_flag,
        "failed_tool_name": failed_tool_name_text,
        "failed_args": failed_args_value,
        "failed_error": failed_error_text,
    }
    verify_signature(signature, user, "tool_info", payload)
    available = _available_tool_names()
    recovery_line = _compact_failed_call_line(
        failed_tool_name_text,
        failed_args_value,
        failed_error_text,
    )
    if not requested_name:
        response = {
            "error": "missing_tool",
            "available": available,
            "message": "Couldn't inspect a single tool because tool_name was missing.",
            "hint": "Call help or tool_help with {} first, then retry with one returned name.",
            "menu": _recovery_menu_payload(available),
        }
        response["failed_call"] = recovery_line or _compact_failed_call_line(
            "tool_info",
            {},
            "missing_tool",
        )
        return response
    special_entry = _build_special_entry(
        requested_name,
        detail="rich",
        include_schema=include_schema_flag,
    )
    if special_entry is not None:
        if recovery_line:
            special_entry["failed_call"] = recovery_line
        return special_entry
    if requested_name not in BUILTIN_TOOL_SPECS:
        compat_suggestions = _compat_tool_name_suggestions(requested_name, available)
        if compat_suggestions:
            response = {
                "error": "unknown_tool",
                "tool_name": requested_name,
                "message": "That compatibility-style name is not a primary tool in this environment.",
                "hint": "Use one of the suggested exact tool names, or call help with tool_name='memory' for the current memory surface.",
                "did_you_mean": compat_suggestions,
                "menu": _recovery_menu_payload(compat_suggestions),
            }
            if recovery_line:
                response["failed_call"] = recovery_line
            return response
        matched_names = _family_discovery_names(
            _matching_tool_names(requested_name, available, include_close=False)
        )
        if matched_names:
            response = {
                "error": "tool_family",
                "tool_name": requested_name,
                "message": "That looks like a tool family or fuzzy query, not one exact tool.",
                "hint": "Call help or tool_help with the same tool_name to browse the matching family, or retry tool_info with one exact returned name.",
                "menu": _recovery_menu_payload(matched_names),
            }
            if recovery_line:
                response["failed_call"] = recovery_line
            return response
        response = {
            "error": "unknown_tool",
            "tool_name": requested_name,
            "available": available,
            "message": "Couldn't inspect that exact tool; showing a compact menu instead.",
            "hint": "Call help or tool_help with {} for a menu, or retry with one returned name.",
            "menu": _recovery_menu_payload(available),
        }
        suggestions = _tool_name_suggestions(requested_name, available)
        if suggestions:
            response["did_you_mean"] = suggestions
        response["failed_call"] = recovery_line or _compact_failed_call_line(
            "tool_info",
            {"tool_name": requested_name},
            "unknown_tool",
        )
        return response
    entry = dict(get_tool_catalog_entry(requested_name))
    if not include_schema_flag:
        entry.pop("input_schema", None)
    if recovery_line:
        entry["failed_call"] = recovery_line
    return entry
