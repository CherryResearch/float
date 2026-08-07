from __future__ import annotations

from typing import Any

TOOL_NAME_ALIASES = {
    "camera": "camera.capture",
    "memory.read": "recall",
    "memory.recall": "recall",
    "memory.search": "recall",
    "memory.store": "remember",
    "memory.write": "remember",
    "memory.remember": "remember",
    "open.url": "computer.navigate",
    "browser.open": "computer.navigate",
    "shell": "shell.exec",
    "patch": "patch.apply",
    "mcp": "mcp.call",
    "writefile": "write_file",
    "readfile": "read_file",
    "listdir": "list_dir",
    "tool": "help",
    "tools": "help",
}

# These legacy handles remain callable because their argument or signature
# contracts differ from the canonical tool. They must not be normalized at
# invocation time, but they are hidden from every new model-visible catalog.
MODEL_HIDDEN_COMPATIBILITY_NAMES = {
    "create_event": "create_task",
    "memory.save": "remember",
    "open_url": "computer.navigate",
    "tool_help": "help",
}


def normalize_tool_name(value: Any) -> str:
    try:
        name = str(value or "").strip()
    except Exception:
        name = ""
    return TOOL_NAME_ALIASES.get(name, name)


__all__ = [
    "MODEL_HIDDEN_COMPATIBILITY_NAMES",
    "TOOL_NAME_ALIASES",
    "normalize_tool_name",
]
