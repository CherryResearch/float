"""Utilities for launching a real MCP server alongside the backend."""
from __future__ import annotations

import json
import logging
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from app.utils import conversation_store

try:  # pragma: no cover - optional dependency at runtime
    from mcp.server.fastmcp import FastMCP
except Exception:  # pragma: no cover - tolerate missing optional dependency
    FastMCP = None  # type: ignore

logger = logging.getLogger(__name__)


class MCPServerError(RuntimeError):
    """Error raised when the MCP server fails to start."""


class _StubTCPServer(ThreadingHTTPServer):
    """Small HTTP stub used when FastMCP is unavailable."""

    allow_reuse_address = True
    daemon_threads = True


class _StubHandler(BaseHTTPRequestHandler):
    server_version = "float-mcp-stub/1.0"

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # pragma: no cover - exercised in integration tests
        self._send_json(
            {
                "status": "ok",
                "provider": "stub",
                "path": self.path,
            }
        )

    def do_POST(self) -> None:  # pragma: no cover - exercised in integration tests
        self._send_json(
            {
                "status": "ok",
                "provider": "stub",
                "path": self.path,
            }
        )

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        logger.debug("MCP stub: " + format, *args)


class FloatMCPServer:
    """Launches a minimal MCP server with curated tools for Float."""

    def __init__(self, memory_manager: Any | None = None) -> None:
        self._memory_manager = memory_manager
        self._thread: threading.Thread | None = None
        self._server: FastMCP | None = None
        self._url: str | None = None
        self._provider: str = "stub"

    @property
    def url(self) -> str | None:
        return self._url

    @property
    def provider(self) -> str:
        return self._provider

    def start(self) -> tuple[str, str]:
        """Start an MCP server (real or stub) and return its URL and provider name."""
        if FastMCP is not None:
            try:
                logger.debug("Attempting to start FastMCP provider")
                url = self._start_fastmcp()
                self._provider = "fastmcp"
                self._url = url
                return url, self._provider
            except Exception as exc:  # pragma: no cover - fall back to stub
                logger.warning("FastMCP startup failed, falling back to stub: %s", exc)
        # Fall back to stub server
        url = self._start_stub()
        self._provider = "stub"
        self._url = url
        return url, self._provider

    # ------------------------------------------------------------------
    # FastMCP implementation
    # ------------------------------------------------------------------
    def _start_fastmcp(self) -> str:
        if FastMCP is None:  # pragma: no cover - defensive guard
            raise MCPServerError("FastMCP is not available")
        port = self._bind_port()
        server = FastMCP(
            name="float-mcp",
            instructions="Tools for exploring Float conversations and memories.",
            host="127.0.0.1",
            port=port,
        )
        self._register_tools(server)
        thread = threading.Thread(
            target=self._run_fastmcp,
            args=(server,),
            name="float-mcp-server",
            daemon=True,
        )
        thread.start()
        if not self._wait_for_listen(port):
            raise MCPServerError(
                "Timed out waiting for FastMCP to listen on port %s" % port
            )
        self._thread = thread
        self._server = server
        return f"http://127.0.0.1:{port}"

    def _run_fastmcp(self, server: FastMCP) -> None:
        try:
            server.run("streamable-http")
        except Exception as exc:  # pragma: no cover - background thread failure
            logger.error("FastMCP server terminated unexpectedly: %s", exc)

    def _register_tools(self, server: FastMCP) -> None:
        """Register a curated set of tools exposed through MCP."""

        @server.tool(name="ping", description="Simple liveness probe returning 'pong'.")
        def ping() -> str:
            return "pong"

        @server.tool(
            name="list_conversations",
            description="List the most recent stored conversation files.",
        )
        def list_conversations(limit: int = 20) -> list[str]:
            names = conversation_store.list_conversations()
            if limit > 0:
                names = names[:limit]
            return names

        @server.tool(
            name="read_conversation",
            description="Return the contents of a stored conversation as JSON.",
        )
        def read_conversation(name: str) -> dict[str, Any]:
            if not name:
                raise ValueError("Conversation name must be provided")
            messages = conversation_store.load_conversation(name)
            return {"name": name, "messages": messages}

        @server.tool(
            name="list_memories",
            description="List memory keys and metadata from the active MemoryManager.",
        )
        def list_memories(limit: int = 20) -> list[dict[str, Any]]:
            manager = self._memory_manager
            if manager is None:
                return []
            try:
                keys = manager.list_items()
            except Exception as exc:  # pragma: no cover - defensive
                raise ValueError(f"Memory manager unavailable: {exc}") from exc
            results = []
            for key in keys[:limit]:
                item = manager.get_item(key) if hasattr(manager, "get_item") else None
                results.append(
                    {
                        "key": key,
                        "item": item,
                    }
                )
            return results

        @server.tool(
            name="get_memory",
            description="Fetch a memory item by key from the MemoryManager.",
        )
        def get_memory(key: str) -> dict[str, Any] | None:
            if not key:
                raise ValueError("Memory key must be provided")
            manager = self._memory_manager
            if manager is None:
                raise ValueError("Memory manager not initialised")
            return manager.get_item(key)

    # ------------------------------------------------------------------
    # Stub implementation
    # ------------------------------------------------------------------
    def _start_stub(self) -> str:
        port = self._bind_port()
        server = _StubTCPServer(("127.0.0.1", port), _StubHandler)
        thread = threading.Thread(
            target=server.serve_forever, daemon=True, name="float-mcp-stub"
        )
        thread.start()
        if not self._wait_for_listen(port):
            raise MCPServerError("Stub MCP server failed to listen on port %s" % port)
        self._thread = thread
        self._server = server  # type: ignore[assignment]
        return f"http://127.0.0.1:{port}"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _bind_port() -> int:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        return port

    @staticmethod
    def _wait_for_listen(port: int, attempts: int = 40, delay: float = 0.1) -> bool:
        start = time.time()
        for _ in range(attempts):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    return True
            except OSError:
                time.sleep(delay)
        logger.debug("MCP port %s did not open within %.2fs", port, time.time() - start)
        return False

    def serialize_status(self) -> dict[str, Any]:
        return {
            "url": self._url,
            "provider": self._provider,
        }
