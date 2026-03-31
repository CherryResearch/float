import asyncio
import base64
import importlib

from fastapi.testclient import TestClient

PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7Z2ioAAAAASUVORK5CYII="
)


def _assert_not_running_on_event_loop():
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise AssertionError("expected sync work to run off the asyncio event loop")


def test_computer_capabilities_endpoint_reports_builtin_tools(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    app = importlib.import_module("app.main").app
    client = TestClient(app)

    resp = client.get("/api/computer/capabilities")

    assert resp.status_code == 200
    payload = resp.json()
    assert "computer.observe" in payload["tools"]
    assert "computer.act" in payload["tools"]
    assert "shell.exec" in payload["tools"]
    assert "browser" in payload["runtimes"]


def test_computer_session_routes_delegate_to_service(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    app = importlib.import_module("app.main").app
    fake_session = {
        "id": "sess-computer-1",
        "runtime": "browser",
        "status": "active",
        "width": 1024,
        "height": 768,
        "current_url": "https://example.com",
        "active_window": None,
        "last_screenshot_path": None,
        "created_at": 1.0,
        "updated_at": 1.0,
    }
    stop_calls = []

    monkeypatch.setattr(
        app.state.computer_service,
        "start_session",
        lambda **kwargs: dict(fake_session, metadata=kwargs.get("metadata")),
    )
    monkeypatch.setattr(
        app.state.computer_service,
        "get_session",
        lambda session_id: fake_session if session_id == "sess-computer-1" else None,
    )

    def fake_stop_session(session_id):
        stop_calls.append(session_id)
        return {"status": "stopped", "session_id": session_id}

    monkeypatch.setattr(app.state.computer_service, "stop_session", fake_stop_session)

    client = TestClient(app)

    create_resp = client.post(
        "/api/computer/sessions",
        json={
            "enabled": True,
            "runtime": "browser",
            "session_id": "sess-computer-1",
            "start_url": "https://example.com",
            "display": {"width": 1024, "height": 768},
            "allowed_domains": ["example.com"],
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()["session"]
    assert created["id"] == "sess-computer-1"
    assert created["metadata"]["allowed_domains"] == ["example.com"]

    get_resp = client.get("/api/computer/sessions/sess-computer-1")
    assert get_resp.status_code == 200
    assert get_resp.json()["session"]["current_url"] == "https://example.com"

    delete_resp = client.delete("/api/computer/sessions/sess-computer-1")
    assert delete_resp.status_code == 200
    assert delete_resp.json()["status"] == "stopped"
    assert stop_calls == ["sess-computer-1"]


def test_computer_session_routes_offload_start_session_from_async_loop(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    app = importlib.import_module("app.main").app

    def fake_start_session(**kwargs):
        _assert_not_running_on_event_loop()
        return {
            "id": str(kwargs.get("session_id") or "sess-browser-1"),
            "runtime": str(kwargs.get("runtime") or "browser"),
            "status": "active",
            "width": int(kwargs.get("width") or 1280),
            "height": int(kwargs.get("height") or 720),
            "current_url": kwargs.get("start_url"),
            "active_window": None,
            "last_screenshot_path": None,
            "created_at": 1.0,
            "updated_at": 1.0,
            "metadata": dict(kwargs.get("metadata") or {}),
        }

    monkeypatch.setattr(app.state.computer_service, "start_session", fake_start_session)

    client = TestClient(app)
    resp = client.post(
        "/api/computer/sessions",
        json={
            "enabled": True,
            "runtime": "browser",
            "session_id": "sess-browser-1",
            "start_url": "https://example.com",
            "display": {"width": 1024, "height": 768},
        },
    )

    assert resp.status_code == 200
    assert resp.json()["session"]["id"] == "sess-browser-1"


def test_computer_session_start_tool_returns_initial_observation(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    computer_tools = importlib.import_module("app.tools.computer_tools")

    session = {
        "id": "sess-browser-1",
        "runtime": "browser",
        "status": "active",
        "width": 1024,
        "height": 768,
        "current_url": "https://example.com",
        "active_window": None,
        "last_screenshot_path": "sess-browser-1.png",
        "created_at": 1.0,
        "updated_at": 1.0,
    }
    observation = {
        "summary": "Captured browser state",
        "session": dict(session),
        "attachment": {
            "url": "/api/captures/capture-1/content",
            "name": "capture-1.png",
            "capture_id": "capture-1",
        },
        "data": {
            "current_url": "https://example.com",
        },
    }

    class FakeService:
        def start_session(self, **kwargs):
            return dict(session, metadata=kwargs.get("metadata"))

        def observe(self, session_id):
            assert session_id == "sess-browser-1"
            return dict(observation)

    monkeypatch.setattr(
        computer_tools, "verify_signature", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(computer_tools, "get_computer_service", lambda: FakeService())

    result = computer_tools.computer_session_start(
        runtime="browser",
        session_id="sess-browser-1",
        start_url="https://example.com",
        width=1024,
        height=768,
        user="tester",
        signature="sig",
    )

    assert result["summary"] == "Captured browser state"
    assert result["session"]["id"] == "sess-browser-1"
    assert result["attachment"]["capture_id"] == "capture-1"


def test_chat_registers_computer_tool_with_injected_session_id(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}

    def fake_generate(
        prompt,
        session_id=None,
        model=None,
        attachments=None,
        context=None,
        **kwargs,
    ):
        return {
            "text": "",
            "thought": "",
            "tools_used": [
                {
                    "name": "computer.act",
                    "args": {"actions": [{"type": "click", "x": 12, "y": 24}]},
                }
            ],
            "metadata": {},
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    monkeypatch.setattr(
        app.state.computer_service,
        "ensure_session",
        lambda **kwargs: {
            "id": "sess-computer-chat-1",
            "runtime": "browser",
            "status": "active",
            "width": 1280,
            "height": 720,
            "current_url": kwargs.get("start_url"),
            "active_window": None,
            "last_screenshot_path": None,
            "created_at": 1.0,
            "updated_at": 1.0,
        },
    )

    client = TestClient(app)
    resp = client.post(
        "/chat",
        json={
            "session_id": "sess",
            "message": "Inspect the page and click the login button.",
            "use_rag": False,
            "computer": {
                "enabled": True,
                "runtime": "browser",
                "start_url": "https://example.com",
                "display": {"width": 1280, "height": 720},
            },
        },
    )

    assert resp.status_code == 200
    payload = resp.json()
    tools_used = payload.get("tools_used") or []
    assert tools_used
    assert tools_used[0]["name"] == "computer.act"
    assert tools_used[0]["args"]["session_id"] == "sess-computer-chat-1"

    proposal_id = tools_used[0]["id"]
    registry = getattr(client.app.state, "pending_tools", {})
    assert proposal_id in registry
    assert registry[proposal_id]["args"]["session_id"] == "sess-computer-chat-1"
    computer_meta = payload.get("metadata", {}).get("computer", {})
    assert (
        computer_meta.get("session_id")
        or computer_meta.get("id")
        or (computer_meta.get("session") or {}).get("id")
    ) == "sess-computer-chat-1"


def test_chat_computer_bootstrap_runs_off_event_loop(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}

    monkeypatch.setattr(
        routes.llm_service,
        "generate",
        lambda *args, **kwargs: {
            "text": "ok",
            "thought": "",
            "tools_used": [],
            "metadata": {},
        },
    )

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}

    def fake_ensure_session(**kwargs):
        _assert_not_running_on_event_loop()
        return {
            "id": "sess-browser-chat-1",
            "runtime": str(kwargs.get("runtime") or "browser"),
            "status": "active",
            "width": int(kwargs.get("width") or 1280),
            "height": int(kwargs.get("height") or 720),
            "current_url": kwargs.get("start_url"),
            "active_window": None,
            "last_screenshot_path": None,
            "created_at": 1.0,
            "updated_at": 1.0,
            "metadata": dict(kwargs.get("metadata") or {}),
        }

    monkeypatch.setattr(
        app.state.computer_service, "ensure_session", fake_ensure_session
    )

    client = TestClient(app)
    resp = client.post(
        "/chat",
        json={
            "session_id": "sess",
            "message": "Open the browser and inspect example.com.",
            "use_rag": False,
            "computer": {
                "enabled": True,
                "runtime": "browser",
                "session_id": "sess-browser-chat-1",
                "start_url": "https://example.com",
                "display": {"width": 1280, "height": 720},
            },
        },
    )

    assert resp.status_code == 200
    payload = resp.json()
    computer_meta = payload.get("metadata", {}).get("computer", {})
    assert (
        computer_meta.get("session_id")
        or computer_meta.get("id")
        or (computer_meta.get("session") or {}).get("id")
    ) == "sess-browser-chat-1"


def test_tool_decision_invokes_sync_tools_off_event_loop(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    app = importlib.import_module("app.main").app

    def fake_invoke_tool(name, *, user=None, signature=None, **kwargs):
        _assert_not_running_on_event_loop()
        return {"name": name, "ok": True}

    monkeypatch.setattr(app.state.memory_manager, "invoke_tool", fake_invoke_tool)

    client = TestClient(app)
    resp = client.post(
        "/api/tools/decision",
        json={
            "request_id": "offloop-tool",
            "decision": "accept",
            "name": "remember",
            "args": {"key": "offloop-note", "value": "threadpool check"},
            "session_id": "sess-offloop",
            "message_id": "msg-offloop",
        },
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "invoked"


def test_chat_auto_approved_tool_error_is_not_reported_as_pending(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}
    monkeypatch.setattr(
        routes.user_settings,
        "load_settings",
        lambda: {"approval_level": "auto"},
    )

    monkeypatch.setattr(
        routes.llm_service,
        "generate",
        lambda *args, **kwargs: {
            "text": "",
            "thought": "",
            "tools_used": [
                {
                    "name": "computer.session.start",
                    "args": {"runtime": "browser", "session_id": "reddit-browser"},
                }
            ],
            "metadata": {},
        },
    )

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}

    def fake_invoke_tool(name, *, user=None, signature=None, **kwargs):
        raise RuntimeError("Playwright sync API cannot run on the asyncio loop")

    monkeypatch.setattr(app.state.memory_manager, "invoke_tool", fake_invoke_tool)

    client = TestClient(app)
    resp = client.post(
        "/chat",
        json={
            "session_id": "sess",
            "message": "Start a computer use session in the browser.",
            "use_rag": False,
        },
    )

    assert resp.status_code == 200
    payload = resp.json()
    assert "Awaiting approval" not in payload["message"]
    assert "computer.session.start" in payload["message"]
    assert "Playwright sync API cannot run on the asyncio loop" in payload["message"]
    assert payload["metadata"].get("tool_response_pending") is not True
    assert payload["tools_used"][0]["status"] == "error"


def test_chat_adds_computer_use_session_guidance_for_desktop_requests(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}
    captured = {}

    def fake_generate(
        prompt,
        session_id=None,
        model=None,
        attachments=None,
        context=None,
        **kwargs,
    ):
        captured["messages"] = list(context.messages)
        return {"text": "ok", "thought": "", "tools_used": [], "metadata": {}}

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    client = TestClient(app)
    resp = client.post(
        "/chat",
        json={
            "session_id": "sess",
            "message": "take control of my computer and inspect the first Google Chrome tab",
            "use_rag": False,
        },
    )

    assert resp.status_code == 200
    system_messages = [
        item.get("content")
        for item in captured.get("messages", [])
        if item.get("role") == "system"
    ]
    guidance = "\n".join(
        text
        for text in system_messages
        if isinstance(text, str) and "computer.session.start" in text
    )
    assert "computer.session.start" in guidance
    assert "runtime='windows'" in guidance
    assert "do not invent fallback session ids" in guidance.lower()
    assert (
        "do not claim navigation, screenshots, embeds, or picture-in-picture"
        in guidance.lower()
    )


def test_workflows_catalog_lists_builtin_profiles(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    app = importlib.import_module("app.main").app
    client = TestClient(app)

    resp = client.get("/api/workflows/catalog")

    assert resp.status_code == 200
    payload = resp.json()
    assert any(item["id"] == "default" for item in payload["workflows"])
    assert any(item["id"] == "architect_planner" for item in payload["workflows"])
    assert any(item["id"] == "mini_execution" for item in payload["workflows"])
    assert any(item["id"] == "camera_capture" for item in payload["modules"])


def test_capture_routes_round_trip(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    capture_module = importlib.import_module("app.services.capture_service")
    app = importlib.import_module("app.main").app
    app.state.capture_service = capture_module.CaptureService(data_dir=tmp_path)
    client = TestClient(app)

    upload_resp = client.post(
        "/api/captures/upload",
        files={"file": ("capture.png", PNG_BYTES, "image/png")},
        data={"source": "camera", "sensitivity": "protected"},
    )

    assert upload_resp.status_code == 200
    capture = upload_resp.json()
    capture_id = capture["capture_id"]
    assert capture["transient"] is True
    assert capture["sensitivity"] == "protected"

    list_resp = client.get("/api/captures")
    assert list_resp.status_code == 200
    listed = list_resp.json()["captures"]
    assert any(item["capture_id"] == capture_id for item in listed)

    detail_resp = client.get(f"/api/captures/{capture_id}")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["capture"]["capture_id"] == capture_id

    promote_resp = client.post(
        f"/api/captures/{capture_id}/promote",
        json={"memory_refs": ["mem-1"]},
    )
    assert promote_resp.status_code == 200
    promoted = promote_resp.json()["capture"]
    assert promoted["promoted"] is True
    assert promoted["memory_refs"] == ["mem-1"]
    assert promote_resp.json()["attachment"]["content_hash"]

    delete_resp = client.delete(f"/api/captures/{capture_id}")
    assert delete_resp.status_code == 200
    assert delete_resp.json()["status"] == "deleted"
