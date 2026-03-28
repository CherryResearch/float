"""Lifecycle helper for spinning up the MCP server alongside FastAPI."""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI

from .mcp_server import FloatMCPServer, MCPServerError

logger = logging.getLogger(__name__)


async def start_mcp_loop(app: FastAPI) -> None:
    """Ensure an MCP endpoint is available for the UI."""
    existing_url = os.getenv("MCP_SERVER_URL")
    if existing_url:
        logger.info("Using externally provided MCP server at %s", existing_url)
        if hasattr(app.state, "config"):
            try:
                app.state.config["mcp_url"] = existing_url
            except Exception:
                pass
        app.state.mcp_provider = "external"
        return

    memory_manager = getattr(app.state, "memory_manager", None)
    server = FloatMCPServer(memory_manager=memory_manager)
    try:
        url, provider = server.start()
    except MCPServerError as exc:
        logger.warning("Unable to start MCP server: %s", exc)
        return

    os.environ["MCP_SERVER_URL"] = url
    if hasattr(app.state, "config"):
        try:
            app.state.config["mcp_url"] = url
        except Exception:
            pass
    app.state.mcp_server = server
    app.state.mcp_provider = provider
    logger.info("MCP provider '%s' listening at %s", provider, url)
