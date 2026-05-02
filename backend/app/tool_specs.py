"""Tool specifications (schemas) exposed to the frontend.

These schemas are used for rendering a form-based tool editor in the UI.
They intentionally track the built-in tools registered in `app.tools`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _spec(
    name: str,
    description: str,
    parameters: Dict[str, Any],
    *,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "name": name,
        "description": description,
        "parameters": parameters,
    }
    if metadata:
        out["metadata"] = metadata
    return out


_SENSITIVITY_ENUM = ["mundane", "public", "personal", "protected", "secret"]


BUILTIN_TOOL_SPECS: Dict[str, Dict[str, Any]] = {
    "help": _spec(
        "help",
        "List tool names with {} or inspect one tool with compact defaults.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "tool_name": {
                    "type": "string",
                    "title": "Tool name (optional)",
                    "default": "",
                },
                "detail": {
                    "type": "string",
                    "title": "Detail level",
                    "enum": ["names", "brief", "rich"],
                    "default": "names",
                },
                "include_schema": {
                    "type": "boolean",
                    "title": "Include full schema",
                    "default": False,
                },
                "max_tools": {
                    "type": "integer",
                    "title": "Maximum tools in list mode",
                    "default": 50,
                    "minimum": 1,
                    "maximum": 50,
                },
                "failed_tool_name": {
                    "type": "string",
                    "title": "Failed tool name",
                    "default": "",
                },
                "failed_args": {
                    "type": "object",
                    "title": "Failed tool args",
                    "default": {},
                },
                "failed_error": {
                    "type": "string",
                    "title": "Failed tool error",
                    "default": "",
                },
            },
        },
        metadata={
            "ui": {
                "tool_name": {
                    "placeholder": "computer, files, tasks, or exact tool",
                },
                "detail": {"placeholder": "names"},
                "include_schema": {"advanced": True},
                "max_tools": {"advanced": True},
                "failed_tool_name": {"advanced": True},
                "failed_args": {"advanced": True},
                "failed_error": {"advanced": True},
            }
        },
    ),
    "tool_help": _spec(
        "tool_help",
        "Return tool guidance for one tool or list tools with {}.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "tool_name": {
                    "type": "string",
                    "title": "Tool name (optional)",
                    "default": "",
                },
                "detail": {
                    "type": "string",
                    "title": "Detail level",
                    "enum": ["names", "brief", "rich"],
                    "default": "names",
                },
                "include_schema": {
                    "type": "boolean",
                    "title": "Include full schema",
                    "default": False,
                },
                "max_tools": {
                    "type": "integer",
                    "title": "Maximum tools in list mode",
                    "default": 50,
                    "minimum": 1,
                    "maximum": 50,
                },
                "failed_tool_name": {
                    "type": "string",
                    "title": "Failed tool name",
                    "default": "",
                },
                "failed_args": {
                    "type": "object",
                    "title": "Failed tool args",
                    "default": {},
                },
                "failed_error": {
                    "type": "string",
                    "title": "Failed tool error",
                    "default": "",
                },
            },
        },
        metadata={
            "ui": {
                "tool_name": {
                    "placeholder": "memory, web, files, or exact tool",
                },
                "detail": {"placeholder": "names"},
                "include_schema": {"advanced": True},
                "max_tools": {"advanced": True},
                "failed_tool_name": {"advanced": True},
                "failed_args": {"advanced": True},
                "failed_error": {"advanced": True},
            }
        },
    ),
    "tool_info": _spec(
        "tool_info",
        "Return one exact tool capability record, or a compact family menu for fuzzy queries.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "tool_name": {
                    "type": "string",
                    "title": "Tool name",
                },
                "include_schema": {
                    "type": "boolean",
                    "title": "Include schema",
                    "default": True,
                },
                "failed_tool_name": {
                    "type": "string",
                    "title": "Failed tool name",
                    "default": "",
                },
                "failed_args": {
                    "type": "object",
                    "title": "Failed tool args",
                    "default": {},
                },
                "failed_error": {
                    "type": "string",
                    "title": "Failed tool error",
                    "default": "",
                },
            },
            "required": ["tool_name"],
        },
        metadata={
            "ui": {
                "tool_name": {
                    "placeholder": "search_web or family like web",
                },
                "include_schema": {"advanced": True},
                "failed_tool_name": {"advanced": True},
                "failed_args": {"advanced": True},
                "failed_error": {"advanced": True},
            }
        },
    ),
    "subchat": _spec(
        "subchat",
        "Control a child subchat: return to the parent chat or request more time.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "action": {
                    "type": "string",
                    "title": "Action",
                    "enum": ["return", "stop", "continue"],
                    "default": "return",
                },
                "note": {
                    "type": "string",
                    "title": "Brief note",
                    "default": "",
                },
                "requested_minutes": {
                    "type": "integer",
                    "title": "Requested minutes",
                    "default": 0,
                    "minimum": 0,
                    "maximum": 240,
                },
                "parent_session_id": {
                    "type": "string",
                    "title": "Parent session ID",
                    "default": "",
                },
                "parent_message_id": {
                    "type": "string",
                    "title": "Parent message ID",
                    "default": "",
                },
            },
        },
        metadata={
            "ui": {
                "parent_session_id": {"advanced": True},
                "parent_message_id": {"advanced": True},
            }
        },
    ),
    "reflect": _spec(
        "reflect",
        "Create a bounded thought task and optionally run one background reflection pass.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "question": {
                    "type": "string",
                    "title": "Question or topic",
                },
                "title": {
                    "type": "string",
                    "title": "Title",
                    "default": "",
                },
                "source_thread_id": {
                    "type": "string",
                    "title": "Source conversation",
                    "default": "",
                },
                "source": {
                    "type": "string",
                    "title": "Source",
                    "enum": ["user", "assistant", "reflection", "tool"],
                    "default": "user",
                },
                "patience": {
                    "type": "integer",
                    "title": "Patience",
                    "default": 1,
                    "minimum": 0,
                    "maximum": 3,
                },
                "memory_keys": {
                    "type": "array",
                    "title": "Linked memory keys",
                    "items": {"type": "string"},
                    "default": [],
                },
                "event_id": {
                    "type": "string",
                    "title": "Linked event/task id",
                    "default": "",
                },
                "run_now": {
                    "type": "boolean",
                    "title": "Run now",
                    "default": False,
                },
            },
            "required": ["question"],
        },
        metadata={
            "ui": {
                "question": {"multiline": True, "rows": 4},
                "source_thread_id": {"advanced": True},
                "source": {"advanced": True},
                "memory_keys": {"advanced": True},
                "event_id": {"advanced": True},
            }
        },
    ),
    "list_reflections": _spec(
        "list_reflections",
        "List recent/open thought tasks and optional reflection runs.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "status": {
                    "type": "string",
                    "title": "Status",
                    "enum": ["", "open", "waiting", "cooling", "resolved", "archived"],
                    "default": "",
                },
                "limit": {
                    "type": "integer",
                    "title": "Limit",
                    "default": 20,
                    "minimum": 1,
                    "maximum": 200,
                },
                "include_runs": {
                    "type": "boolean",
                    "title": "Include runs",
                    "default": False,
                },
            },
        },
    ),
    "route_to_local_model": _spec(
        "route_to_local_model",
        "Ask the user to continue this turn on a local model before sending private text to a non-local model.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "target_mode": {
                    "type": "string",
                    "title": "Target mode",
                    "enum": ["local", "server", "api"],
                    "default": "local",
                },
                "target_model": {
                    "type": "string",
                    "title": "Target model",
                    "default": "",
                },
                "reason": {
                    "type": "string",
                    "title": "Reason",
                    "default": "",
                },
                "sensitivity": {
                    "type": "string",
                    "title": "Detected sensitivity",
                    "default": "",
                },
                "labels": {
                    "type": "string",
                    "title": "Detected labels",
                    "default": "",
                },
                "source_mode": {
                    "type": "string",
                    "title": "Original mode",
                    "default": "",
                },
                "source_model": {
                    "type": "string",
                    "title": "Original model",
                    "default": "",
                },
            },
            "required": ["target_mode"],
        },
        metadata={
            "ui": {
                "reason": {"wide": True},
                "sensitivity": {"advanced": True},
                "labels": {"advanced": True},
                "source_mode": {"advanced": True},
                "source_model": {"advanced": True},
            }
        },
    ),
    "list_actions": _spec(
        "list_actions",
        "List persisted write actions that can be inspected or reverted.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "conversation_id": {
                    "type": "string",
                    "title": "Conversation ID",
                    "default": "",
                },
                "response_id": {
                    "type": "string",
                    "title": "Response ID",
                    "default": "",
                },
                "include_reverted": {
                    "type": "boolean",
                    "title": "Include reverted actions",
                    "default": True,
                },
                "limit": {
                    "type": "integer",
                    "title": "Max actions",
                    "default": 20,
                    "minimum": 1,
                    "maximum": 200,
                },
            },
        },
        metadata={
            "ui": {
                "conversation_id": {"advanced": True},
                "response_id": {"advanced": True},
            }
        },
    ),
    "read_action_diff": _spec(
        "read_action_diff",
        "Read the diff and before/after snapshots for one tracked write action.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "action_id": {
                    "type": "string",
                    "title": "Action ID",
                },
            },
            "required": ["action_id"],
        },
    ),
    "revert_actions": _spec(
        "revert_actions",
        "Revert one tracked action or a batch scoped to a response or conversation.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "action_ids": {
                    "type": "array",
                    "title": "Action IDs",
                    "items": {"type": "string"},
                },
                "response_id": {
                    "type": "string",
                    "title": "Response ID",
                    "default": "",
                },
                "conversation_id": {
                    "type": "string",
                    "title": "Conversation ID",
                    "default": "",
                },
                "force": {
                    "type": "boolean",
                    "title": "Force despite conflicts",
                    "default": False,
                },
            },
        },
        metadata={
            "ui": {
                "action_ids": {"advanced": True},
                "force": {"advanced": True},
            }
        },
    ),
    "crawl": _spec(
        "crawl",
        "Fetch a URL and return its contents (truncated).",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "url": {"type": "string", "title": "URL"},
                "timeout": {
                    "type": "integer",
                    "title": "Timeout (seconds)",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 60,
                },
            },
            "required": ["url"],
        },
    ),
    "search_web": _spec(
        "search_web",
        "Search the web and return structured results.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "query": {"type": "string", "title": "Query"},
                "max_results": {
                    "type": "integer",
                    "title": "Max results",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 10,
                },
                "region": {
                    "type": "string",
                    "title": "Region",
                    "default": "us-en",
                },
            },
            "required": ["query"],
        },
    ),
    "open_url": _spec(
        "open_url",
        "Open a URL through the browser computer runtime.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "url": {"type": "string", "title": "URL"},
            },
            "required": ["url"],
        },
    ),
    "computer.session.start": _spec(
        "computer.session.start",
        "Start or reuse a computer-use session.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "runtime": {
                    "type": "string",
                    "enum": ["browser", "windows"],
                    "default": "browser",
                },
                "session_id": {"type": "string", "title": "Session ID", "default": ""},
                "start_url": {"type": "string", "title": "Start URL", "default": ""},
                "width": {
                    "type": "integer",
                    "title": "Display width",
                    "default": 1280,
                    "minimum": 320,
                    "maximum": 3840,
                },
                "height": {
                    "type": "integer",
                    "title": "Display height",
                    "default": 720,
                    "minimum": 240,
                    "maximum": 2160,
                },
            },
        },
    ),
    "computer.session.stop": _spec(
        "computer.session.stop",
        "Stop a computer-use session.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "session_id": {"type": "string", "title": "Session ID"},
            },
            "required": ["session_id"],
        },
    ),
    "computer.observe": _spec(
        "computer.observe",
        "Capture a screenshot and summary of the current computer session.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "session_id": {"type": "string", "title": "Session ID"},
            },
            "required": ["session_id"],
        },
    ),
    "computer.act": _spec(
        "computer.act",
        "Apply click, type, scroll, keypress, wait, or navigation actions.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "session_id": {"type": "string", "title": "Session ID"},
                "actions": {
                    "type": "array",
                    "title": "Actions",
                    "items": {
                        "type": "object",
                        "additionalProperties": True,
                        "properties": {
                            "type": {"type": "string", "title": "Action type"},
                            "x": {"type": "integer", "title": "X"},
                            "y": {"type": "integer", "title": "Y"},
                            "button": {"type": "string", "title": "Button"},
                            "text": {"type": "string", "title": "Text"},
                            "keys": {
                                "type": ["string", "array"],
                                "title": "Keys",
                            },
                            "delta_x": {"type": "integer", "title": "Delta X"},
                            "delta_y": {"type": "integer", "title": "Delta Y"},
                            "ms": {"type": "integer", "title": "Wait (ms)"},
                            "url": {"type": "string", "title": "URL"},
                            "app": {"type": "string", "title": "App"},
                            "window_title": {
                                "type": "string",
                                "title": "Window title",
                            },
                        },
                        "required": ["type"],
                    },
                },
            },
            "required": ["session_id", "actions"],
        },
    ),
    "computer.navigate": _spec(
        "computer.navigate",
        "Navigate a browser computer-use session to a URL.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "session_id": {"type": "string", "title": "Session ID"},
                "url": {"type": "string", "title": "URL"},
            },
            "required": ["session_id", "url"],
        },
    ),
    "computer.windows.list": _spec(
        "computer.windows.list",
        "List visible Windows desktop windows.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "session_id": {"type": "string", "title": "Session ID"},
            },
            "required": ["session_id"],
        },
    ),
    "computer.windows.focus": _spec(
        "computer.windows.focus",
        "Focus a Windows desktop window by title.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "session_id": {"type": "string", "title": "Session ID"},
                "window_title": {"type": "string", "title": "Window title"},
            },
            "required": ["session_id", "window_title"],
        },
    ),
    "computer.app.launch": _spec(
        "computer.app.launch",
        "Launch a desktop application in the Windows runtime.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "session_id": {"type": "string", "title": "Session ID"},
                "app": {"type": "string", "title": "Executable or app"},
                "args": {
                    "type": "array",
                    "title": "Arguments",
                    "items": {"type": "string"},
                },
            },
            "required": ["session_id", "app"],
        },
    ),
    "camera.capture": _spec(
        "camera.capture",
        "Capture a still image from a connected client camera and return it as a transient capture.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        },
    ),
    "capture.list": _spec(
        "capture.list",
        "List recent transient captures from computer, camera, or screen sources.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "source": {
                    "type": "string",
                    "title": "Source filter",
                    "enum": ["", "computer", "camera", "screen"],
                    "default": "",
                },
            },
        },
    ),
    "capture.promote": _spec(
        "capture.promote",
        "Promote a transient capture into durable attachment storage.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "capture_id": {"type": "string", "title": "Capture ID"},
            },
            "required": ["capture_id"],
        },
    ),
    "capture.delete": _spec(
        "capture.delete",
        "Delete a transient capture from the cache.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "capture_id": {"type": "string", "title": "Capture ID"},
            },
            "required": ["capture_id"],
        },
    ),
    "shell.exec": _spec(
        "shell.exec",
        "Run a shell command on the host and capture stdout/stderr.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "command": {"type": "string", "title": "Command"},
                "cwd": {"type": "string", "title": "Working directory", "default": ""},
                "timeout_seconds": {
                    "type": "integer",
                    "title": "Timeout (seconds)",
                    "default": 20,
                    "minimum": 1,
                    "maximum": 300,
                },
            },
            "required": ["command"],
        },
    ),
    "patch.apply": _spec(
        "patch.apply",
        "Write or append text content to a local file.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "path": {"type": "string", "title": "Path"},
                "content": {"type": "string", "title": "Content"},
                "mode": {
                    "type": "string",
                    "title": "Mode",
                    "enum": ["replace", "append", "create"],
                    "default": "replace",
                },
            },
            "required": ["path", "content"],
        },
        metadata={"ui": {"content": {"multiline": True, "rows": 8}}},
    ),
    "mcp.call": _spec(
        "mcp.call",
        "Call an MCP endpoint by server and method name.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "server": {"type": "string", "title": "MCP server"},
                "method": {"type": "string", "title": "Method"},
                "arguments": {
                    "type": "object",
                    "title": "Arguments",
                    "additionalProperties": True,
                },
            },
            "required": ["server", "method"],
        },
    ),
    "read_file": _spec(
        "read_file",
        "Read a bounded text window from a local file.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "path": {"type": "string", "title": "Path"},
                "start_line": {
                    "type": "integer",
                    "title": "Start line",
                    "default": 1,
                    "minimum": 1,
                    "maximum": 1000000,
                },
                "line_count": {
                    "type": "integer",
                    "title": "Line count",
                    "default": 200,
                    "minimum": 1,
                    "maximum": 1000,
                },
                "max_chars": {
                    "type": "integer",
                    "title": "Max chars",
                    "default": 12000,
                    "minimum": 200,
                    "maximum": 20000,
                },
            },
            "required": ["path"],
        },
        metadata={
            "ui": {
                "start_line": {"advanced": True},
                "line_count": {"advanced": True},
                "max_chars": {"advanced": True},
            }
        },
    ),
    "list_dir": _spec(
        "list_dir",
        "List files and folders inside Float's managed data roots.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "path": {
                    "type": "string",
                    "title": "Path",
                    "default": ".",
                },
                "workspace_only": {
                    "type": "boolean",
                    "title": "Workspace only",
                    "default": False,
                },
                "recursive": {
                    "type": "boolean",
                    "title": "Recursive",
                    "default": False,
                },
                "include_hidden": {
                    "type": "boolean",
                    "title": "Include hidden",
                    "default": False,
                },
                "max_entries": {
                    "type": "integer",
                    "title": "Max entries",
                    "default": 100,
                    "minimum": 1,
                    "maximum": 200,
                },
            },
        },
        metadata={
            "ui": {
                "workspace_only": {"advanced": True},
                "recursive": {"advanced": True},
                "include_hidden": {"advanced": True},
                "max_entries": {"advanced": True},
            }
        },
    ),
    "write_file": _spec(
        "write_file",
        "Write content to a local file.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "path": {"type": "string", "title": "Path"},
                "content": {"type": "string", "title": "Content"},
            },
            "required": ["path", "content"],
        },
        metadata={"ui": {"content": {"multiline": True, "rows": 8}}},
    ),
    "generate_threads": _spec(
        "generate_threads",
        "Generate semantic threads from conversations.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "infer_topics": {
                    "type": "boolean",
                    "title": "Infer topics",
                    "default": True,
                },
                "tags": {
                    "type": "array",
                    "title": "Tags",
                    "items": {"type": "string"},
                },
                "openai_key": {
                    "type": "string",
                    "title": "OpenAI key (optional)",
                },
            },
        },
        metadata={"ui": {"openai_key": {"secret": True}}},
    ),
    "read_threads_summary": _spec(
        "read_threads_summary",
        "Read the last generated threads summary.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        },
    ),
    "compact_conversation_plan": _spec(
        "compact_conversation_plan",
        "Plan conversation compaction from a target context budget without writing state.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "conversation_id": {
                    "type": "string",
                    "title": "Conversation ID",
                },
                "context_window_tokens": {
                    "type": "integer",
                    "title": "Context window tokens",
                    "default": 24000,
                    "minimum": 4096,
                    "maximum": 1000000,
                },
                "reserve_output_tokens": {
                    "type": "integer",
                    "title": "Reserved output tokens",
                    "default": 2048,
                    "minimum": 0,
                    "maximum": 200000,
                },
                "reserve_tool_tokens": {
                    "type": "integer",
                    "title": "Reserved tool tokens",
                    "default": 2500,
                    "minimum": 0,
                    "maximum": 200000,
                },
                "reserve_retrieval_tokens": {
                    "type": "integer",
                    "title": "Reserved retrieval tokens",
                    "default": 2500,
                    "minimum": 0,
                    "maximum": 200000,
                },
                "reserve_system_tokens": {
                    "type": "integer",
                    "title": "Reserved system tokens",
                    "default": 1500,
                    "minimum": 0,
                    "maximum": 200000,
                },
                "soft_trigger_ratio": {
                    "type": "number",
                    "title": "Soft trigger ratio",
                    "default": 0.75,
                    "minimum": 0.1,
                    "maximum": 1.0,
                },
                "hard_trigger_ratio": {
                    "type": "number",
                    "title": "Hard trigger ratio",
                    "default": 0.9,
                    "minimum": 0.1,
                    "maximum": 1.0,
                },
                "summary_mode": {
                    "type": "string",
                    "title": "Summary mode for recommended payloads",
                    "enum": ["deterministic", "llm"],
                    "default": "deterministic",
                },
                "summary_workflow": {
                    "type": "string",
                    "title": "Summary workflow for recommended payloads",
                    "enum": [
                        "conversation_handoff",
                        "decision_focus",
                        "task_state",
                    ],
                    "default": "conversation_handoff",
                },
                "summary_format_notes": {
                    "type": "string",
                    "title": "Optional format notes",
                    "default": "",
                },
                "summary_model": {
                    "type": "string",
                    "title": "Model override (optional)",
                    "default": "",
                },
            },
            "required": ["conversation_id"],
        },
        metadata={
            "ui": {
                "reserve_output_tokens": {"advanced": True},
                "reserve_tool_tokens": {"advanced": True},
                "reserve_retrieval_tokens": {"advanced": True},
                "reserve_system_tokens": {"advanced": True},
                "soft_trigger_ratio": {"advanced": True},
                "hard_trigger_ratio": {"advanced": True},
                "summary_format_notes": {"advanced": True},
                "summary_model": {"advanced": True},
            }
        },
    ),
    "compact_conversation_preview": _spec(
        "compact_conversation_preview",
        "Preview conversation context compaction without writing state.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "conversation_id": {
                    "type": "string",
                    "title": "Conversation ID",
                },
                "keep_last": {
                    "type": "integer",
                    "title": "Recent messages to keep",
                    "default": 40,
                    "minimum": 1,
                    "maximum": 200,
                },
                "max_summary_chars": {
                    "type": "integer",
                    "title": "Summary character cap",
                    "default": 6000,
                    "minimum": 500,
                    "maximum": 20000,
                },
                "summary_mode": {
                    "type": "string",
                    "title": "Summary mode",
                    "enum": ["deterministic", "llm"],
                    "default": "deterministic",
                },
                "summary_workflow": {
                    "type": "string",
                    "title": "Summary workflow",
                    "enum": [
                        "conversation_handoff",
                        "decision_focus",
                        "task_state",
                    ],
                    "default": "conversation_handoff",
                },
                "summary_format_notes": {
                    "type": "string",
                    "title": "Optional format notes",
                    "default": "",
                },
                "summary_model": {
                    "type": "string",
                    "title": "Model override (optional)",
                    "default": "",
                },
            },
            "required": ["conversation_id"],
        },
        metadata={
            "ui": {
                "max_summary_chars": {"advanced": True},
                "summary_format_notes": {"advanced": True},
                "summary_model": {"advanced": True},
            }
        },
    ),
    "compact_conversation_write": _spec(
        "compact_conversation_write",
        "Write a compacted conversation copy after preview or explicit approval.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "conversation_id": {
                    "type": "string",
                    "title": "Conversation ID",
                },
                "keep_last": {
                    "type": "integer",
                    "title": "Recent messages to keep",
                    "default": 40,
                    "minimum": 1,
                    "maximum": 200,
                },
                "max_summary_chars": {
                    "type": "integer",
                    "title": "Summary character cap",
                    "default": 6000,
                    "minimum": 500,
                    "maximum": 20000,
                },
                "summary_mode": {
                    "type": "string",
                    "title": "Summary mode",
                    "enum": ["deterministic", "llm"],
                    "default": "deterministic",
                },
                "summary_workflow": {
                    "type": "string",
                    "title": "Summary workflow",
                    "enum": [
                        "conversation_handoff",
                        "decision_focus",
                        "task_state",
                    ],
                    "default": "conversation_handoff",
                },
                "summary_format_notes": {
                    "type": "string",
                    "title": "Optional format notes",
                    "default": "",
                },
                "summary_model": {
                    "type": "string",
                    "title": "Model override (optional)",
                    "default": "",
                },
                "target_conversation_id": {
                    "type": "string",
                    "title": "Target conversation ID",
                    "default": "",
                },
                "replace": {
                    "type": "boolean",
                    "title": "Replace source conversation",
                    "default": False,
                },
            },
            "required": ["conversation_id"],
        },
        metadata={
            "ui": {
                "max_summary_chars": {"advanced": True},
                "summary_format_notes": {"advanced": True},
                "summary_model": {"advanced": True},
                "target_conversation_id": {"advanced": True},
                "replace": {"advanced": True},
            }
        },
    ),
    "create_event": _spec(
        "create_event",
        "Compatibility alias for creating or updating a calendar event with flexible time input.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "id": {
                    "type": "string",
                    "title": "Event ID (optional)",
                },
                "title": {
                    "type": "string",
                    "title": "Title",
                },
                "summary": {
                    "type": "string",
                    "title": "Title alias",
                },
                "name": {
                    "type": "string",
                    "title": "Title alias",
                },
                "description": {
                    "type": "string",
                    "title": "Notes",
                },
                "location": {
                    "type": "string",
                    "title": "Location",
                },
                "start_time": {
                    "type": ["number", "string", "object"],
                    "title": "Start time (unix, ISO, natural language, or {date,time,timezone})",
                },
                "start": {
                    "type": ["number", "string", "object"],
                    "title": "Start alias",
                },
                "starts_at": {
                    "type": ["number", "string", "object"],
                    "title": "Start alias",
                },
                "when": {
                    "type": ["number", "string", "object"],
                    "title": "Start alias",
                },
                "end_time": {
                    "type": ["number", "string", "object"],
                    "title": "End time (unix, ISO, natural language, or {date,time,timezone})",
                },
                "end": {
                    "type": ["number", "string", "object"],
                    "title": "End alias",
                },
                "ends_at": {
                    "type": ["number", "string", "object"],
                    "title": "End alias",
                },
                "grounded_at": {
                    "type": ["number", "string"],
                    "title": "Grounded at",
                },
                "duration_min": {
                    "type": ["integer", "number", "string"],
                    "title": "Duration (minutes, optional)",
                },
                "duration": {
                    "type": ["integer", "number", "string"],
                    "title": "Duration alias",
                },
                "timezone": {
                    "type": "string",
                    "title": "Time zone",
                    "default": "UTC",
                },
                "status": {
                    "type": "string",
                    "title": "Status",
                    "default": "pending",
                },
                "rrule": {
                    "type": "string",
                    "title": "Recurrence rule (optional)",
                },
                "actions": {
                    "type": "array",
                    "title": "Structured actions",
                    "items": {"type": "object"},
                },
            },
            "required": [],
            "anyOf": [
                {"required": ["title", "start_time"]},
                {"required": ["title", "start"]},
                {"required": ["summary", "start_time"]},
                {"required": ["summary", "start"]},
                {"required": ["name", "start_time"]},
                {"required": ["name", "start"]},
                {"required": ["title", "when"]},
                {"required": ["summary", "when"]},
                {"required": ["name", "when"]},
            ],
        },
        metadata={
            "ui": {
                "description": {"multiline": True, "rows": 4},
                "location": {"advanced": True},
                "end_time": {"advanced": True},
                "end": {"advanced": True},
                "grounded_at": {"advanced": True},
                "duration_min": {"advanced": True},
                "duration": {"advanced": True},
                "rrule": {"advanced": True},
                "actions": {"advanced": True},
            }
        },
    ),
    "list_tasks": _spec(
        "list_tasks",
        "List saved calendar tasks/events from local Float storage.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "status": {
                    "type": "string",
                    "title": "Status filter",
                    "default": "",
                },
                "include_past": {
                    "type": "boolean",
                    "title": "Include past events",
                    "default": False,
                },
                "limit": {
                    "type": "integer",
                    "title": "Max events",
                    "default": 20,
                    "minimum": 1,
                    "maximum": 100,
                },
            },
        },
        metadata={
            "ui": {
                "status": {"advanced": True},
                "include_past": {"advanced": True},
                "limit": {"advanced": True},
            }
        },
    ),
    "create_task": _spec(
        "create_task",
        "Create or update a calendar task/event that appears in upcoming tasks and can optionally carry structured follow-up actions.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "id": {
                    "type": "string",
                    "title": "Event ID (optional)",
                },
                "title": {
                    "type": "string",
                    "title": "Title",
                },
                "summary": {
                    "type": "string",
                    "title": "Title alias",
                },
                "name": {
                    "type": "string",
                    "title": "Title alias",
                },
                "description": {
                    "type": "string",
                    "title": "Notes",
                },
                "location": {
                    "type": "string",
                    "title": "Location",
                },
                "start_time": {
                    "type": ["number", "string", "object"],
                    "title": "Start time (unix, ISO, natural language, or {date,time,timezone})",
                },
                "start": {
                    "type": ["number", "string", "object"],
                    "title": "Start alias",
                },
                "starts_at": {
                    "type": ["number", "string", "object"],
                    "title": "Start alias",
                },
                "when": {
                    "type": ["number", "string", "object"],
                    "title": "Start alias",
                },
                "end_time": {
                    "type": ["number", "string", "object"],
                    "title": "End time (unix, ISO, natural language, or {date,time,timezone})",
                },
                "end": {
                    "type": ["number", "string", "object"],
                    "title": "End alias",
                },
                "ends_at": {
                    "type": ["number", "string", "object"],
                    "title": "End alias",
                },
                "grounded_at": {
                    "type": ["number", "string"],
                    "title": "Grounded at",
                },
                "duration_min": {
                    "type": ["integer", "number", "string"],
                    "title": "Duration (minutes, optional)",
                },
                "duration": {
                    "type": ["integer", "number", "string"],
                    "title": "Duration alias",
                },
                "timezone": {
                    "type": "string",
                    "title": "Time zone",
                    "default": "UTC",
                },
                "status": {
                    "type": "string",
                    "title": "Status",
                    "default": "pending",
                },
                "rrule": {
                    "type": "string",
                    "title": "Recurrence rule (optional)",
                },
                "actions": {
                    "type": "array",
                    "title": "Structured actions",
                    "items": {"type": "object"},
                },
            },
            "required": [],
            "anyOf": [
                {"required": ["title", "start_time"]},
                {"required": ["title", "start"]},
                {"required": ["summary", "start_time"]},
                {"required": ["summary", "start"]},
                {"required": ["name", "start_time"]},
                {"required": ["name", "start"]},
                {"required": ["title", "when"]},
                {"required": ["summary", "when"]},
                {"required": ["name", "when"]},
            ],
        },
        metadata={
            "ui": {
                "description": {"multiline": True, "rows": 4},
                "location": {"advanced": True},
                "end_time": {"advanced": True},
                "end": {"advanced": True},
                "grounded_at": {"advanced": True},
                "duration_min": {"advanced": True},
                "duration": {"advanced": True},
                "rrule": {"advanced": True},
                "actions": {"advanced": True},
            }
        },
    ),
    "memory.save": _spec(
        "memory.save",
        "Legacy compatibility tool for saving a memory entry.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "text": {"type": "string", "title": "Text"},
                "namespace": {"type": "string", "title": "Namespace"},
                "key": {"type": "string", "title": "Key (optional)"},
                "tags": {
                    "type": "array",
                    "title": "Tags",
                    "items": {"type": "string"},
                },
                "vectorize": {"type": "boolean", "title": "Vectorize"},
                "graph_triples": {
                    "type": "array",
                    "title": "Graph triples",
                    "items": {"type": "string"},
                },
                "privacy": {"type": "string", "title": "Privacy"},
                "source": {"type": "string", "title": "Source"},
                "reflect_after_save": {
                    "type": "boolean",
                    "title": "Reflect after save",
                    "default": False,
                },
                "reflection_prompt": {
                    "type": "string",
                    "title": "Reflection prompt",
                    "default": "",
                },
                "reflection_run_now": {
                    "type": "boolean",
                    "title": "Run reflection now",
                    "default": False,
                },
            },
            "required": ["text"],
        },
        metadata={
            "ui": {
                "text": {"multiline": True, "rows": 6},
                "reflect_after_save": {"advanced": True},
                "reflection_prompt": {
                    "advanced": True,
                    "multiline": True,
                    "rows": 3,
                },
                "reflection_run_now": {"advanced": True},
            }
        },
    ),
    "remember": _spec(
        "remember",
        "Store or update a memory item, keeping canonical SQLite memory in sync and optionally mirroring searchable snippets into vector retrieval.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "key": {"type": "string", "title": "Key"},
                "value": {
                    "type": ["string", "number", "boolean", "object", "array"],
                    "title": "Value",
                    "items": {},
                },
                "importance": {
                    "type": "number",
                    "title": "Importance (0-1)",
                    "minimum": 0,
                    "maximum": 1,
                },
                "sensitivity": {
                    "type": "string",
                    "title": "Sensitivity",
                    "enum": _SENSITIVITY_ENUM,
                },
                "hint": {"type": "string", "title": "Hint (for secret values)"},
                "pinned": {"type": "boolean", "title": "Pinned"},
                "importance_floor": {
                    "type": "number",
                    "title": "Importance floor",
                    "minimum": 0,
                    "maximum": 1,
                },
                "vectorize": {"type": "boolean", "title": "Vectorize"},
                "lifecycle": {
                    "type": "string",
                    "title": "Lifecycle",
                    "enum": ["evergreen", "reviewable", "prunable"],
                },
                "grounded_at": {
                    "type": ["number", "string"],
                    "title": "Grounded at",
                },
                "occurs_at": {
                    "type": ["number", "string"],
                    "title": "Occurs at",
                },
                "review_at": {
                    "type": ["number", "string"],
                    "title": "Review at",
                },
                "decay_at": {
                    "type": ["number", "string"],
                    "title": "Decay at",
                },
                "reflect_after_save": {
                    "type": "boolean",
                    "title": "Reflect after save",
                    "default": False,
                },
                "reflection_prompt": {
                    "type": "string",
                    "title": "Reflection prompt",
                    "default": "",
                },
                "reflection_run_now": {
                    "type": "boolean",
                    "title": "Run reflection now",
                    "default": False,
                },
            },
            "required": ["key", "value"],
        },
        metadata={
            "ui": {
                "value": {"multiline": True, "rows": 4},
                "importance": {"advanced": True},
                "sensitivity": {"advanced": True},
                "hint": {"advanced": True},
                "pinned": {"advanced": True},
                "importance_floor": {"advanced": True},
                "vectorize": {"advanced": True},
                "lifecycle": {"advanced": True},
                "grounded_at": {"advanced": True},
                "occurs_at": {"advanced": True},
                "review_at": {"advanced": True},
                "decay_at": {"advanced": True},
                "reflect_after_save": {"advanced": True},
                "reflection_prompt": {
                    "advanced": True,
                    "multiline": True,
                    "rows": 3,
                },
                "reflection_run_now": {"advanced": True},
            }
        },
    ),
    "recall": _spec(
        "recall",
        "Recall by exact key or search the canonical store plus vector snippets. Defaults to hybrid search when exact lookup misses.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "key": {
                    "type": "string",
                    "title": "Key or search query (optional)",
                    "default": "",
                },
                "for_external": {"type": "boolean", "title": "For external use"},
                "allow_protected": {
                    "type": "boolean",
                    "title": "Allow protected values",
                },
                "mode": {
                    "type": "string",
                    "title": "Recall mode",
                    "enum": ["hybrid", "canonical", "vector", "memory", "clip"],
                    "default": "hybrid",
                },
                "top_k": {
                    "type": "integer",
                    "title": "Max matches",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 10,
                },
                "include_images": {
                    "type": "boolean",
                    "title": "Include image recall",
                    "default": False,
                },
                "image_top_k": {
                    "type": "integer",
                    "title": "Max image matches",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 10,
                },
            },
        },
        metadata={
            "ui": {
                "for_external": {"advanced": True},
                "allow_protected": {"advanced": True},
                "mode": {"advanced": True},
                "top_k": {"advanced": True},
                "include_images": {"advanced": True},
                "image_top_k": {"advanced": True},
            }
        },
    ),
}


def get_tool_specs(available: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Return UI-facing tool specs for a given tool name list."""
    names = list(available or [])
    if not names:
        names = list(BUILTIN_TOOL_SPECS.keys())
    out: List[Dict[str, Any]] = []
    for name in sorted({str(n) for n in names if n}):
        spec = BUILTIN_TOOL_SPECS.get(name)
        if spec:
            out.append(spec)
            continue
        out.append(
            _spec(
                name,
                "Custom tool (no schema available).",
                {
                    "type": "object",
                    "title": "Arguments",
                    "additionalProperties": True,
                    "properties": {},
                },
            )
        )
    return out
