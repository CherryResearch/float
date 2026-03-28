"""Supplemental tool metadata routes."""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, HTTPException, Request

from app.tool_catalog import get_tool_catalog, get_tool_catalog_entry, get_tool_limits
from app.tools import BUILTIN_TOOLS

router = APIRouter()


def _available_tool_names(request: Request) -> List[str]:
    manager = getattr(request.app.state, "memory_manager", None)
    registered = getattr(manager, "tools", None)
    if isinstance(registered, dict) and registered:
        return sorted(str(name) for name in registered.keys())
    return sorted(str(name) for name in BUILTIN_TOOLS.keys())


@router.get("/tools/catalog")
async def tool_catalog(request: Request) -> dict:
    """Return capability metadata for currently available tools."""

    return {"tools": get_tool_catalog(_available_tool_names(request))}


@router.get("/tools/catalog/{tool_name}")
async def tool_catalog_entry(tool_name: str, request: Request) -> dict:
    """Return one tool capability record."""

    available = set(_available_tool_names(request))
    if tool_name not in available:
        raise HTTPException(status_code=404, detail="Tool not found")
    return {"tool": get_tool_catalog_entry(tool_name)}


@router.get("/tools/limits")
async def tool_limits(request: Request) -> dict:
    """Return shared sandbox roots and tool-layer caps."""

    config = getattr(request.app.state, "config", None)
    return get_tool_limits(config if isinstance(config, dict) else {})
