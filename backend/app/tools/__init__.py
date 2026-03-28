"""Utility tool modules for the Float backend."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Dict

from . import actions as action_tools
from . import browser
from . import calendar as calendar_tools
from . import crawler, local_files, memory, threads
from . import tool_help as help_tools

if TYPE_CHECKING:  # pragma: no cover - circular import safe guard
    from app.base_services import MemoryManager

BUILTIN_TOOLS: Dict[str, Callable[..., Any]] = {
    "crawl": crawler.crawl,
    "search_web": crawler.search_web,
    "open_url": browser.open_url,
    "read_file": local_files.read_file,
    "list_dir": local_files.list_dir,
    "write_file": local_files.write_file,
    "create_event": calendar_tools.create_event,
    "create_task": calendar_tools.create_task,
    "generate_threads": threads.generate_threads_tool,
    "read_threads_summary": threads.read_threads_summary_tool,
    "memory.save": memory.legacy_memory_save,
    "remember": memory.remember,
    "recall": memory.recall,
    "list_actions": action_tools.list_actions,
    "read_action_diff": action_tools.read_action_diff,
    "revert_actions": action_tools.revert_actions,
    "tool_help": help_tools.tool_help,
    "tool_info": help_tools.tool_info,
}


def register_builtin_tools(manager: "MemoryManager") -> None:
    """Register the built-in tool set with the provided memory manager."""
    for name, func in BUILTIN_TOOLS.items():
        manager.register_tool(name, func)


__all__ = [
    "crawler",
    "browser",
    "calendar_tools",
    "local_files",
    "threads",
    "memory",
    "action_tools",
    "help_tools",
    "BUILTIN_TOOLS",
    "register_builtin_tools",
]
