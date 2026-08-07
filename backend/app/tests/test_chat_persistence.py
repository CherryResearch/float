import asyncio
import hashlib
import importlib
import threading
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

_IMAGE_HASH_ONE = hashlib.sha256(b"image-one").hexdigest()
_IMAGE_HASH_TWO = hashlib.sha256(b"image-two").hexdigest()
_IMAGE_HASH_EMPTY_TURN = hashlib.sha256(b"attachment-only-image").hexdigest()


def _mock_canonical_image_attachment(
    monkeypatch,
    routes,
    content_hash,
    *,
    capture_id=None,
):
    original = routes._attachment_public_descriptor

    def descriptor(candidate_hash):
        if candidate_hash == content_hash:
            result = {
                "content_hash": content_hash,
                "filename": "camera.png",
                "content_type": "image/png",
                "size": 42,
                "url": f"/api/attachments/{content_hash}/camera.png",
                "relative_path": f"captured/{content_hash}/camera.png",
                "origin": "captured",
            }
            if capture_id:
                result["capture_id"] = capture_id
                result["capture_source"] = "chat_camera"
            return result
        return original(candidate_hash)

    monkeypatch.setattr(routes, "_attachment_public_descriptor", descriptor)


@pytest.fixture(autouse=True)
def add_backend_to_sys_path():
    import sys

    backend_dir = Path(__file__).resolve().parents[2]
    backend_dir = str(backend_dir)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)


def _pin_default_workflow_settings(monkeypatch, tmp_path, enabled_modules=None):
    from app.utils import user_settings

    monkeypatch.setattr(
        user_settings,
        "USER_SETTINGS_PATH",
        tmp_path / "user_settings.json",
        raising=False,
    )
    user_settings.save_settings(
        {
            "default_workflow": "default",
            "enabled_workflow_modules": list(enabled_modules or []),
        }
    )


def _record_terminal_continuation(
    routes,
    app,
    *,
    session_id,
    message_id,
    request_id,
    name,
    args,
    result,
):
    """Record the server scope and terminal tool fact required by continuation."""

    from app.workflow_scope import build_capability_scope

    scope = build_capability_scope(
        workflow="default",
        channel="text",
        modules=[],
        tool_definitions=routes._registered_prompt_tool_definitions(
            app,
            allow_computer_capture=True,
        ),
    )
    routes._update_conversation_entry(
        session_id,
        message_id,
        {"metadata": {"capability_scope": scope}},
    )
    tool = {
        "id": request_id,
        "name": name,
        "args": dict(args),
        "result": result,
        "status": "invoked",
    }
    app.state.pending_tools = {
        request_id: {
            **tool,
            "session_id": session_id,
            "message_id": message_id,
            "chain_id": message_id,
            "server_recorded": True,
        }
    }
    return tool


def test_chat_persists_assistant_updates(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

    _pin_default_workflow_settings(monkeypatch, tmp_path)
    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}

    def fake_generate(
        prompt, session_id=None, model=None, attachments=None, context=None, **kwargs
    ):
        return {"text": "ok", "thought": "", "tools_used": [], "metadata": {}}

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    client = TestClient(app)
    resp = client.post(
        "/chat",
        json={
            "message": "hi",
            "session_id": "sess",
            "message_id": "m1",
            "use_rag": False,
            "workflow": "background_reflection",
        },
    )
    assert resp.status_code == 200

    messages = conv_store.load_conversation("sess")
    assert any(m.get("id") == "m1:user" for m in messages)
    ai = next(m for m in messages if m.get("id") == "m1")
    assert ai.get("text") == "ok"
    assert (ai.get("metadata") or {}).get("status") == "complete"
    assert ((ai.get("metadata") or {}).get("workflow") or {}).get("name") == ("default")
    scope = (ai.get("metadata") or {}).get("capability_scope") or {}
    assert scope.get("version") == 1
    assert scope.get("workflow") == "default"
    assert scope.get("channel") == "text"
    assert scope.get("modules") == []
    assert "help" in (scope.get("tool_names") or [])
    assert "tool_help" not in (scope.get("tool_names") or [])
    assert len(scope.get("tool_catalog_sha256") or "") == 64


def test_chat_regenerate_replaces_only_latest_pair_and_rebuilds_context(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

    _pin_default_workflow_settings(monkeypatch, tmp_path)
    conv_store.replace_conversation_content(
        "sess",
        [
            {"id": "m0:user", "role": "user", "text": "earlier question"},
            {"id": "m0", "role": "ai", "text": "earlier answer"},
            {"id": "m1:user", "role": "user", "text": "old question"},
            {
                "id": "m1",
                "role": "ai",
                "text": "old answer",
                "tools": [
                    {
                        "id": "old-tool",
                        "name": "recall",
                        "status": "invoked",
                    }
                ],
                "metadata": {"status": "complete"},
            },
        ],
    )
    routes.llm_service.contexts = {
        "sess": ModelContext(
            system_prompt="",
            messages=[
                {"role": "user", "content": "stale live question"},
                {"role": "assistant", "content": "old answer"},
            ],
        )
    }
    captured = {}

    def fake_generate(
        prompt, session_id=None, model=None, attachments=None, context=None, **kwargs
    ):
        captured["prompt"] = prompt
        captured["messages"] = list(context.messages)
        return {
            "text": "new answer",
            "thought": "",
            "tools_used": [],
            "metadata": {},
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    client = TestClient(app)
    response = client.post(
        "/chat",
        json={
            "message": "updated question",
            "session_id": "sess",
            "message_id": "m1",
            "regenerate": True,
            "use_rag": False,
        },
    )

    assert response.status_code == 200
    assert response.json()["message"] == "new answer"
    assert captured["prompt"] == "updated question"
    context_text = [
        item.get("content") for item in captured["messages"] if isinstance(item, dict)
    ]
    assert "earlier question" in context_text
    assert "earlier answer" in context_text
    assert "old question" not in context_text
    assert "old answer" not in context_text
    assert "stale live question" not in context_text

    messages = conv_store.load_conversation("sess")
    assert [item.get("id") for item in messages] == [
        "m0:user",
        "m0",
        "m1:user",
        "m1",
    ]
    assert messages[2].get("text") == "updated question"
    assert messages[3].get("text") == "new answer"
    assert not messages[3].get("tools")

    duplicate = client.post(
        "/chat",
        json={
            "message": "ordinary duplicate",
            "session_id": "sess",
            "message_id": "m1",
            "use_rag": False,
        },
    )
    assert duplicate.status_code == 409
    assert "Message id already exists" in duplicate.json()["detail"]


def test_chat_regenerate_rejects_a_turn_with_later_messages(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

    conv_store.replace_conversation_content(
        "sess",
        [
            {"id": "m1:user", "role": "user", "text": "first question"},
            {"id": "m1", "role": "ai", "text": "first answer"},
            {"id": "m2:user", "role": "user", "text": "later question"},
            {"id": "m2", "role": "ai", "text": "later answer"},
        ],
    )
    routes.llm_service.contexts = {"sess": ModelContext(system_prompt="")}

    def unexpected_generate(*args, **kwargs):
        raise AssertionError("an older turn must not be regenerated in place")

    monkeypatch.setattr(routes.llm_service, "generate", unexpected_generate)

    app = importlib.import_module("app.main").app
    client = TestClient(app)
    response = client.post(
        "/chat",
        json={
            "message": "changed first question",
            "session_id": "sess",
            "message_id": "m1",
            "regenerate": True,
            "use_rag": False,
        },
    )

    assert response.status_code == 409
    assert "Only the latest" in response.json()["detail"]
    assert [item.get("text") for item in conv_store.load_conversation("sess")] == [
        "first question",
        "first answer",
        "later question",
        "later answer",
    ]


@pytest.mark.parametrize(
    "provider_response",
    [
        {
            "text": "",
            "thought": "The model reasoned but did not answer.",
            "tools_used": [],
            "metadata": {},
        },
        {
            "text": "The provider timed out.",
            "thought": "",
            "tools_used": [],
            "metadata": {"error": "provider timeout", "category": "timeout"},
        },
    ],
    ids=["thought-only", "provider-error"],
)
def test_chat_regenerate_failure_preserves_saved_pair_and_terminal_tools(
    monkeypatch, tmp_path, provider_response
):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

    _pin_default_workflow_settings(monkeypatch, tmp_path)
    original_messages = [
        {"id": "m1:user", "role": "user", "text": "original question"},
        {
            "id": "m1",
            "role": "ai",
            "text": "original answer",
            "metadata": {"status": "complete"},
            "tools": [
                {
                    "id": "old-terminal",
                    "name": "help",
                    "args": {},
                    "status": "invoked",
                    "server_recorded": True,
                }
            ],
        },
    ]
    conv_store.save_conversation("sess", original_messages)
    routes.llm_service.contexts = {
        "sess": ModelContext(
            system_prompt="",
            messages=[
                {"role": "user", "content": "original question"},
                {"role": "assistant", "content": "original answer"},
            ],
        )
    }

    def fake_generate(*args, **kwargs):
        return {
            key: (dict(value) if isinstance(value, dict) else value)
            for key, value in provider_response.items()
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)
    app = importlib.import_module("app.main").app
    app.state.pending_tools = {
        "old-terminal": {
            "id": "old-terminal",
            "name": "help",
            "args": {},
            "status": "invoked",
            "session_id": "sess",
            "message_id": "m1",
            "chain_id": "m1",
            "server_recorded": True,
        }
    }

    response = TestClient(app).post(
        "/chat",
        json={
            "message": "replacement question",
            "session_id": "sess",
            "message_id": "m1",
            "regenerate": True,
            "use_rag": False,
        },
    )

    assert response.status_code == 200
    assert conv_store.load_conversation("sess") == original_messages
    assert set(app.state.pending_tools) == {"old-terminal"}
    live_messages = routes.llm_service.get_context("sess").messages
    assert [item.get("content") for item in live_messages] == [
        "original question",
        "original answer",
    ]


def test_chat_regenerate_rejects_unresolved_tool_proposal(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

    original_messages = [
        {"id": "m1:user", "role": "user", "text": "question"},
        {
            "id": "m1",
            "role": "ai",
            "text": "Requested tool help.",
            "tools": [
                {
                    "id": "pending-tool",
                    "name": "help",
                    "args": {},
                    "status": "proposed",
                    "server_recorded": True,
                }
            ],
        },
    ]
    conv_store.save_conversation("sess", original_messages)
    routes.llm_service.contexts = {"sess": ModelContext(system_prompt="")}

    def unexpected_generate(*args, **kwargs):
        raise AssertionError("generation must not run while a tool is unresolved")

    monkeypatch.setattr(routes.llm_service, "generate", unexpected_generate)
    app = importlib.import_module("app.main").app
    app.state.pending_tools = {
        "pending-tool": {
            "id": "pending-tool",
            "name": "help",
            "args": {},
            "status": "proposed",
            "session_id": "sess",
            "message_id": "m1",
            "chain_id": "m1",
        }
    }

    response = TestClient(app).post(
        "/chat",
        json={
            "message": "replacement question",
            "session_id": "sess",
            "message_id": "m1",
            "regenerate": True,
            "use_rag": False,
        },
    )

    assert response.status_code == 409
    assert "pending tool" in response.json()["detail"].lower()
    assert conv_store.load_conversation("sess") == original_messages
    assert set(app.state.pending_tools) == {"pending-tool"}


def test_chat_regenerate_replaces_terminal_tool_registry_and_signatures(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

    _pin_default_workflow_settings(monkeypatch, tmp_path)
    conv_store.save_conversation(
        "sess",
        [
            {"id": "m1:user", "role": "user", "text": "old question"},
            {
                "id": "m1",
                "role": "ai",
                "text": "old answer",
                "metadata": {"status": "complete"},
                "tools": [
                    {
                        "id": "old-terminal",
                        "name": "help",
                        "args": {},
                        "status": "invoked",
                        "server_recorded": True,
                    }
                ],
            },
        ],
    )
    routes.llm_service.contexts = {"sess": ModelContext(system_prompt="")}

    def fake_generate(*args, **kwargs):
        return {
            "text": "",
            "thought": "",
            "tools_used": [{"name": "help", "args": {}}],
            "metadata": {},
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)
    app = importlib.import_module("app.main").app
    app.state.pending_tools = {
        "old-terminal": {
            "id": "old-terminal",
            "name": "help",
            "args": {},
            "status": "invoked",
            "session_id": "sess",
            "message_id": "m1",
            "chain_id": "m1",
            "server_recorded": True,
        }
    }

    response = TestClient(app).post(
        "/chat",
        json={
            "message": "new question",
            "session_id": "sess",
            "message_id": "m1",
            "regenerate": True,
            "use_rag": False,
        },
    )

    assert response.status_code == 200
    tools = response.json()["tools_used"]
    assert len(tools) == 1
    assert tools[0]["name"] == "help"
    assert tools[0]["id"] != "old-terminal"
    assert "old-terminal" not in app.state.pending_tools
    assert tools[0]["id"] in app.state.pending_tools
    saved = conv_store.load_conversation("sess")
    assert saved[-2]["text"] == "new question"
    assert [tool["id"] for tool in saved[-1].get("tools") or []] == [tools[0]["id"]]


def test_chat_regenerate_defers_auto_tool_until_atomic_commit(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext
    from app.utils import user_settings

    monkeypatch.setattr(
        user_settings,
        "USER_SETTINGS_PATH",
        tmp_path / "user_settings.json",
        raising=False,
    )
    user_settings.save_settings(
        {
            "default_workflow": "default",
            "enabled_workflow_modules": [],
            "approval_level": "auto",
        }
    )
    original_messages = [
        {"id": "m1:user", "role": "user", "text": "old question"},
        {
            "id": "m1",
            "role": "ai",
            "text": "old answer",
            "metadata": {"status": "complete"},
            "tools": [
                {
                    "id": "old-terminal",
                    "name": "help",
                    "args": {},
                    "status": "invoked",
                    "server_recorded": True,
                }
            ],
        },
    ]
    conv_store.save_conversation("sess", original_messages)
    routes.llm_service.contexts = {"sess": ModelContext(system_prompt="")}

    def fake_generate(*args, **kwargs):
        return {
            "text": "",
            "thought": "",
            "tools_used": [{"name": "help", "args": {}}],
            "metadata": {},
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)
    app = importlib.import_module("app.main").app
    app.state.pending_tools = {
        "old-terminal": {
            "id": "old-terminal",
            "name": "help",
            "args": {},
            "status": "invoked",
            "session_id": "sess",
            "message_id": "m1",
            "chain_id": "m1",
            "server_recorded": True,
        }
    }
    invocations = []
    user_timeline = []

    async def fake_decide_tool(request, payload):
        saved_at_invocation = conv_store.load_conversation("sess")
        assert saved_at_invocation[-2]["text"] == "new question"
        assert saved_at_invocation[-1]["id"] == "m1"
        assert "old-terminal" not in request.app.state.pending_tools
        record = request.app.state.pending_tools[payload.request_id]
        record["status"] = "invoked"
        record["result"] = {"ok": True, "data": "help result"}
        routes._append_tool_event_to_conversation(
            "sess",
            "m1",
            record["name"],
            record["args"],
            record["result"],
            status="invoked",
            request_id=payload.request_id,
        )
        invocations.append(payload.request_id)
        return {"status": "invoked", "result": record["result"]}

    def fake_timeline(**kwargs):
        if kwargs.get("role") == "user":
            user_timeline.append(kwargs.get("text"))

    monkeypatch.setattr(routes, "decide_tool", fake_decide_tool)
    monkeypatch.setattr(routes, "log_timeline_message", fake_timeline)

    response = TestClient(app).post(
        "/chat",
        json={
            "message": "new question",
            "session_id": "sess",
            "message_id": "m1",
            "regenerate": True,
            "use_rag": False,
        },
    )

    assert response.status_code == 200
    assert len(invocations) == 1
    assert user_timeline == ["new question"]
    saved = conv_store.load_conversation("sess")
    assert saved[-2]["text"] == "new question"
    saved_tools = saved[-1].get("tools") or []
    assert [tool["id"] for tool in saved_tools] == invocations
    assert saved_tools[0]["status"] == "invoked"
    assert saved_tools[0]["result"] == {"ok": True, "data": "help result"}


def test_chat_regenerate_auto_tool_commit_failure_has_no_side_effect(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext
    from app.utils import user_settings

    monkeypatch.setattr(
        user_settings,
        "USER_SETTINGS_PATH",
        tmp_path / "user_settings.json",
        raising=False,
    )
    user_settings.save_settings(
        {
            "default_workflow": "default",
            "enabled_workflow_modules": [],
            "approval_level": "auto",
        }
    )
    original_messages = [
        {"id": "m1:user", "role": "user", "text": "old question"},
        {
            "id": "m1",
            "role": "ai",
            "text": "old answer",
            "metadata": {"status": "complete"},
        },
    ]
    conv_store.save_conversation("sess", original_messages)
    routes.llm_service.contexts = {"sess": ModelContext(system_prompt="")}

    def fake_generate(*args, **kwargs):
        return {
            "text": "",
            "thought": "",
            "tools_used": [{"name": "help", "args": {}}],
            "metadata": {},
        }

    invocations = []
    user_timeline = []

    async def unexpected_decide(*args, **kwargs):
        invocations.append((args, kwargs))
        raise AssertionError("auto-approved tool ran before regeneration commit")

    def fake_timeline(**kwargs):
        if kwargs.get("role") == "user":
            user_timeline.append(kwargs.get("text"))

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)
    monkeypatch.setattr(routes, "decide_tool", unexpected_decide)
    monkeypatch.setattr(routes, "log_timeline_message", fake_timeline)
    monkeypatch.setattr(routes, "_replace_latest_conversation_pair", lambda *a: False)
    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}

    response = TestClient(app).post(
        "/chat",
        json={
            "message": "new question",
            "session_id": "sess",
            "message_id": "m1",
            "regenerate": True,
            "use_rag": False,
        },
    )

    assert response.status_code == 409
    assert invocations == []
    assert user_timeline == []
    assert app.state.pending_tools == {}
    assert conv_store.load_conversation("sess") == original_messages


def test_concurrent_different_chat_ids_persist_in_request_order(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

    _pin_default_workflow_settings(monkeypatch, tmp_path)
    routes.llm_service.contexts = {"sess": ModelContext(system_prompt="")}
    first_started = threading.Event()
    release_first = threading.Event()
    generation_order = []

    def fake_generate(prompt, *args, **kwargs):
        generation_order.append(prompt)
        if prompt == "first":
            first_started.set()
            assert release_first.wait(timeout=3)
        return {
            "text": f"answer {prompt}",
            "thought": "",
            "tools_used": [],
            "metadata": {},
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)
    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}

    async def send_concurrently():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            first = asyncio.create_task(
                client.post(
                    "/chat",
                    json={
                        "message": "first",
                        "session_id": "sess",
                        "message_id": "m1",
                        "use_rag": False,
                    },
                )
            )
            assert await asyncio.to_thread(first_started.wait, 3)
            second = asyncio.create_task(
                client.post(
                    "/chat",
                    json={
                        "message": "second",
                        "session_id": "sess",
                        "message_id": "m2",
                        "use_rag": False,
                    },
                )
            )
            await asyncio.sleep(0.05)
            assert generation_order == ["first"]
            release_first.set()
            return await asyncio.gather(first, second)

    responses = asyncio.run(send_concurrently())

    assert [response.status_code for response in responses] == [200, 200]
    assert generation_order == ["first", "second"]
    assert [item["id"] for item in conv_store.load_conversation("sess")] == [
        "m1:user",
        "m1",
        "m2:user",
        "m2",
    ]


def test_chat_marks_resolved_inline_read_tools_for_continuation(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}

    def fake_generate(
        prompt, session_id=None, model=None, attachments=None, context=None, **kwargs
    ):
        return {
            "text": "I do not have fresh tool results yet.",
            "thought": "",
            "tools_used": [{"name": "recall", "args": {"key": "recent"}}],
            "metadata": {
                "inline_tool_payload": '{"tool":"recall","args":{"key":"recent"}}'
            },
        }

    async def fake_register_tool_proposals(*args, **kwargs):
        return [
            {
                "id": "tool-1",
                "name": "recall",
                "args": {"key": "recent"},
                "status": "invoked",
                "result": {
                    "status": "invoked",
                    "ok": True,
                    "data": {"matches": [{"snippet": "one useful note"}]},
                },
            }
        ]

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)
    monkeypatch.setattr(
        routes, "_register_tool_proposals", fake_register_tool_proposals
    )

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    client = TestClient(app)
    resp = client.post(
        "/chat",
        json={
            "message": "search memory",
            "session_id": "sess",
            "message_id": "m1",
            "use_rag": False,
        },
    )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["message"].startswith("Tool results:")
    metadata = payload.get("metadata") or {}
    assert metadata.get("status") == "pending"
    assert metadata.get("tool_response_pending") is True
    assert metadata.get("inline_tool_continuation_pending") is True

    messages = conv_store.load_conversation("sess")
    ai = next(m for m in messages if m.get("id") == "m1")
    assert ai.get("text", "").startswith("Tool results:")
    saved_meta = ai.get("metadata") or {}
    assert saved_meta.get("status") == "pending"
    assert saved_meta.get("tool_response_pending") is True
    assert saved_meta.get("inline_tool_continuation_pending") is True


@pytest.mark.parametrize("provider_text", ["", "response[[tool_call:0]]"])
def test_chat_marks_terminal_tool_only_response_for_continuation(
    monkeypatch, tmp_path, provider_text
):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}

    def fake_generate(*args, **kwargs):
        return {
            "text": provider_text,
            "thought": "",
            "tools_used": [
                {
                    "name": "remember",
                    "args": {"key": "photo.owl", "value": "saved image reference"},
                }
            ],
            "metadata": {"finish_reason": "tool_calls"},
        }

    async def fake_register_tool_proposals(*args, **kwargs):
        return [
            {
                "id": "remember-1",
                "name": "remember",
                "args": {"key": "photo.owl", "value": "saved image reference"},
                "status": "invoked",
                "result": {"status": "invoked", "ok": True},
                "server_recorded": True,
            }
        ]

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)
    monkeypatch.setattr(
        routes, "_register_tool_proposals", fake_register_tool_proposals
    )

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    response = TestClient(app).post(
        "/chat",
        json={
            "message": "remember this photo",
            "session_id": "sess",
            "message_id": "m1",
            "use_rag": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["message"].startswith("Tool results:")
    metadata = payload.get("metadata") or {}
    assert metadata.get("status") == "pending"
    assert metadata.get("tool_response_pending") is True
    assert metadata.get("tool_result_continuation_pending") is True
    assert metadata.get("inline_tool_continuation_pending") is not True

    saved = conv_store.load_conversation("sess")
    assistant = next(item for item in saved if item.get("id") == "m1")
    saved_metadata = assistant.get("metadata") or {}
    assert saved_metadata.get("tool_result_continuation_pending") is True


def test_chat_missing_mode_defaults_to_configured_api_not_service_mode(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

    _pin_default_workflow_settings(monkeypatch, tmp_path)
    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}
    captured = {}

    def fake_generate(
        prompt, session_id=None, model=None, attachments=None, context=None, **kwargs
    ):
        captured["metadata"] = kwargs.get("metadata")
        captured["capture_raw_api"] = kwargs.get("capture_raw_api")
        return {"text": "ok", "thought": "", "tools_used": [], "metadata": {}}

    def fail_provider_resolution(*args, **kwargs):
        raise AssertionError("provider resolution should not run for api fallback")

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)
    monkeypatch.setattr(
        routes.provider_manager,
        "resolve_inference_target",
        fail_provider_resolution,
    )

    original_mode = getattr(routes.llm_service, "mode", "api")
    routes.llm_service.mode = "local"

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    app.state.config["mode"] = "api"
    client = TestClient(app)
    resp = client.post(
        "/chat",
        json={
            "message": "hi",
            "session_id": "sess",
            "message_id": "m1",
            "use_rag": False,
        },
    )
    assert resp.status_code == 200
    assert captured["metadata"]["mode"] == "api"
    assert captured["capture_raw_api"] is True
    assert routes.llm_service.mode == "local"
    routes.llm_service.mode = original_mode


def test_chat_api_forwards_openai_metadata_and_persists_response_ids(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

    _pin_default_workflow_settings(monkeypatch, tmp_path)
    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}
    captured = {}

    def fake_generate(
        prompt, session_id=None, model=None, attachments=None, context=None, **kwargs
    ):
        captured["metadata"] = kwargs.get("metadata")
        captured["capture_raw_api"] = kwargs.get("capture_raw_api")
        return {
            "text": "ok",
            "thought": "",
            "tools_used": [],
            "metadata": {
                "response_id": "resp_123",
                "previous_response_id": "resp_prev",
                "output_ids": ["out_1", "out_2"],
            },
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    client = TestClient(app)
    resp = client.post(
        "/chat",
        json={
            "message": "hi",
            "session_id": "sess",
            "message_id": "m1",
            "use_rag": False,
            "mode": "api",
        },
    )
    assert resp.status_code == 200

    conversation_id = conv_store.get_or_create_conversation_id("sess")
    assert captured["metadata"] == {
        "session_name": "sess",
        "conversation_id": conversation_id,
        "message_id": "m1",
        "mode": "api",
        "workflow": "default",
    }
    assert captured["capture_raw_api"] is True

    messages = conv_store.load_conversation("sess")
    ai = next(m for m in messages if m.get("id") == "m1")
    metadata = ai.get("metadata") or {}
    assert metadata.get("response_id") == "resp_123"
    assert metadata.get("previous_response_id") == "resp_prev"
    assert metadata.get("output_ids") == ["out_1", "out_2"]
    assert metadata.get("conversation_id") == conversation_id
    assert metadata.get("message_id") == "m1"


def test_update_conversation_entry_clears_stale_failure_metadata(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    conv_store.save_conversation(
        "sess",
        [
            {
                "id": "m1",
                "role": "ai",
                "text": "old error",
                "metadata": {
                    "status": "error",
                    "error": "No connection adapters were found for '127.0.0.1:11434'",
                    "category": "http_error",
                    "endpoint": "127.0.0.1:11434",
                    "hint": "old hint",
                },
            }
        ],
    )

    from app import routes

    routes._update_conversation_entry(
        "sess",
        "m1",
        {
            "text": "fixed",
            "metadata": {
                "status": "complete",
                "provider": "ollama",
                "server_url": "http://127.0.0.1:11434/v1",
            },
        },
    )

    messages = conv_store.load_conversation("sess")
    ai = next(m for m in messages if m.get("id") == "m1")
    metadata = ai.get("metadata") or {}
    assert ai.get("text") == "fixed"
    assert metadata.get("status") == "complete"
    assert metadata.get("provider") == "ollama"
    assert metadata.get("server_url") == "http://127.0.0.1:11434/v1"
    assert "error" not in metadata
    assert "category" not in metadata
    assert "endpoint" not in metadata
    assert "hint" not in metadata


def test_append_conversation_entry_reuses_existing_message_id(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    conv_store.save_conversation(
        "sess",
        [
            {
                "id": "m1:user",
                "role": "user",
                "text": "hello",
            },
            {
                "id": "m1",
                "role": "ai",
                "text": "old reply",
                "metadata": {"status": "complete"},
            },
        ],
    )

    from app import routes

    routes._append_conversation_entry(
        "sess",
        {
            "id": "m1",
            "role": "ai",
            "text": "",
            "metadata": {"status": "pending"},
        },
    )

    messages = conv_store.load_conversation("sess")
    matching = [m for m in messages if m.get("id") == "m1"]
    assert len(matching) == 1
    assert matching[0].get("metadata", {}).get("status") == "pending"


def test_update_conversation_entry_updates_latest_duplicate(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    conv_store.save_conversation(
        "sess",
        [
            {
                "id": "m1",
                "role": "ai",
                "text": "old reply",
                "metadata": {"status": "complete"},
            },
            {
                "id": "m1",
                "role": "ai",
                "text": "",
                "metadata": {"status": "pending"},
            },
        ],
    )

    from app import routes

    routes._update_conversation_entry(
        "sess",
        "m1",
        {
            "text": "new reply",
            "metadata": {"status": "error", "empty_response": True},
        },
    )

    messages = conv_store.load_conversation("sess")
    matching = [m for m in messages if m.get("id") == "m1"]
    assert len(matching) == 2
    assert matching[0].get("text") == "old reply"
    assert matching[0].get("metadata", {}).get("status") == "complete"
    assert matching[1].get("text") == "new reply"
    assert matching[1].get("metadata", {}).get("status") == "error"
    assert matching[1].get("metadata", {}).get("empty_response") is True


def test_chat_local_thought_only_response_is_reported_clearly(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}

    def fake_resolve(*, provider, requested_model, allow_auto_start=True):
        assert provider == "ollama"
        assert requested_model == "ollama"
        return {
            "provider": "ollama",
            "model": "gemma4:e4b",
            "base_url": "http://127.0.0.1:11434/v1",
            "api_token": "",
            "runtime": {"server_running": True, "model_loaded": True},
        }

    def fake_generate(
        prompt, session_id=None, model=None, attachments=None, context=None, **kwargs
    ):
        return {
            "text": "",
            "thought": "I should use the remember tool.",
            "thought_trace": [
                {
                    "index": 0,
                    "text": "I should use the remember tool.",
                    "timestamp": 1.0,
                }
            ],
            "tools_used": [],
            "metadata": {
                "model_requested": "gemma4:e4b",
                "model_received": "gemma4:e4b",
            },
        }

    monkeypatch.setattr(
        routes.provider_manager,
        "resolve_inference_target",
        fake_resolve,
    )
    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    client = TestClient(app)
    resp = client.post(
        "/chat",
        json={
            "message": "remember this",
            "session_id": "sess",
            "message_id": "m1",
            "use_rag": False,
            "mode": "local",
            "model": "ollama",
        },
    )
    assert resp.status_code == 200

    payload = resp.json()
    assert "reasoning but no final answer" in payload["message"]
    metadata = payload.get("metadata") or {}
    assert metadata.get("status") == "error"
    assert metadata.get("empty_response") is True
    assert metadata.get("empty_response_reason") == "thought_only"
    assert metadata.get("provider") == "ollama"
    assert metadata.get("server_url") == "http://127.0.0.1:11434/v1"

    messages = conv_store.load_conversation("sess")
    ai = next(m for m in messages if m.get("id") == "m1")
    assert "reasoning but no final answer" in (ai.get("text") or "")
    assert (ai.get("metadata") or {}).get("empty_response") is True
    assert (ai.get("metadata") or {}).get("status") == "error"


def test_chat_local_provider_target_skips_reasoning_controls(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}
    captured = {}

    def fake_resolve(*, provider, requested_model, allow_auto_start=True):
        assert provider == "ollama"
        assert requested_model == "ollama"
        return {
            "provider": "ollama",
            "model": "gemma4:e4b",
            "base_url": "http://127.0.0.1:11434/v1",
            "api_token": "",
            "runtime": {"server_running": True, "model_loaded": True},
        }

    def fake_generate(
        prompt, session_id=None, model=None, attachments=None, context=None, **kwargs
    ):
        captured["model"] = model
        captured["reasoning"] = kwargs.get("reasoning")
        return {"text": "ok", "thought": "", "tools_used": [], "metadata": {}}

    monkeypatch.setattr(
        routes.provider_manager, "resolve_inference_target", fake_resolve
    )
    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    client = TestClient(app)
    resp = client.post(
        "/chat",
        json={
            "message": "remember this",
            "session_id": "sess",
            "message_id": "m1",
            "use_rag": False,
            "mode": "local",
            "model": "ollama",
            "thinking": "high",
        },
    )

    assert resp.status_code == 200
    assert captured["model"] == "gemma4:e4b"
    assert captured["reasoning"] is None
    metadata = resp.json().get("metadata") or {}
    assert metadata.get("model") == "gemma4:e4b"
    assert metadata.get("model_requested") == "ollama"
    assert metadata.get("model_resolved") == "gemma4:e4b"


def test_chat_direct_local_model_bypasses_provider_resolution(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}
    captured = {}

    def fail_resolve(*, provider, requested_model, allow_auto_start=True):
        raise AssertionError("direct-local chat should not resolve a provider target")

    def fake_generate(
        prompt, session_id=None, model=None, attachments=None, context=None, **kwargs
    ):
        captured["model"] = model
        return {"text": "ok", "thought": "", "tools_used": [], "metadata": {}}

    monkeypatch.setattr(
        routes.provider_manager, "resolve_inference_target", fail_resolve
    )
    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    client = TestClient(app)
    resp = client.post(
        "/chat",
        json={
            "message": "remember this",
            "session_id": "sess",
            "message_id": "m1",
            "use_rag": False,
            "mode": "local",
            "model": "gemma-4-E2B-it",
        },
    )

    assert resp.status_code == 200
    assert captured["model"] == "gemma-4-E2B-it"
    metadata = resp.json().get("metadata") or {}
    assert metadata.get("model") == "gemma-4-E2B-it"
    assert metadata.get("model_requested") == "gemma-4-E2B-it"
    assert metadata.get("model_resolved") in {None, "gemma-4-E2B-it"}


def test_chat_local_provider_resolution_error_updates_pending_message(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}

    def fake_resolve(*, provider, requested_model, allow_auto_start=True):
        assert provider == "lmstudio"
        assert requested_model == "lmstudio"
        raise RuntimeError(
            "No model is loaded for lmstudio. Load one in the runtime panel."
        )

    monkeypatch.setattr(
        routes.provider_manager,
        "resolve_inference_target",
        fake_resolve,
    )

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    client = TestClient(app)
    resp = client.post(
        "/chat",
        json={
            "message": "hi",
            "session_id": "sess",
            "message_id": "m1",
            "use_rag": False,
            "mode": "local",
            "model": "lmstudio",
        },
    )

    assert resp.status_code == 409
    assert "No model is loaded for lmstudio" in resp.json()["detail"]

    messages = conv_store.load_conversation("sess")
    ai = next(m for m in messages if m.get("id") == "m1")
    metadata = ai.get("metadata") or {}
    assert metadata.get("status") == "error"
    assert metadata.get("status_code") == 409
    assert metadata.get("category") == "http_exception"
    assert "No model is loaded for lmstudio" in (metadata.get("error") or "")
    assert "No model is loaded for lmstudio" in (ai.get("text") or "")


def test_chat_local_provider_model_mismatch_becomes_error(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}

    def fake_resolve(*, provider, requested_model, allow_auto_start=True):
        assert provider == "lmstudio"
        assert requested_model == "lmstudio"
        return {
            "provider": "lmstudio",
            "model": "google/gemma-3-270m",
            "base_url": "http://127.0.0.1:1234/v1",
            "api_token": "",
            "runtime": {"server_running": True, "model_loaded": True},
        }

    def fake_generate(
        prompt, session_id=None, model=None, attachments=None, context=None, **kwargs
    ):
        return {
            "text": "I am running on gpt-4o-mini.",
            "thought": "",
            "tools_used": [],
            "metadata": {
                "model_requested": "google/gemma-3-270m",
                "model_received": "openai/gpt-oss-20b",
                "model_mismatch": True,
            },
        }

    monkeypatch.setattr(
        routes.provider_manager,
        "resolve_inference_target",
        fake_resolve,
    )
    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    client = TestClient(app)
    resp = client.post(
        "/chat",
        json={
            "message": "FLOAT-S1 text-only",
            "session_id": "sess",
            "message_id": "m1",
            "use_rag": False,
            "mode": "local",
            "model": "lmstudio",
        },
    )

    assert resp.status_code == 200
    payload = resp.json()
    assert (
        payload["message"]
        == "Model mismatch: requested 'google/gemma-3-270m', received 'openai/gpt-oss-20b'."
    )
    metadata = payload.get("metadata") or {}
    assert metadata.get("status") == "error"
    assert metadata.get("category") == "model_mismatch"
    assert metadata.get("model_requested") == "google/gemma-3-270m"
    assert metadata.get("model_received") == "openai/gpt-oss-20b"

    messages = conv_store.load_conversation("sess")
    ai = next(m for m in messages if m.get("id") == "m1")
    assert ai.get("text") == payload["message"]
    assert (ai.get("metadata") or {}).get("category") == "model_mismatch"


def test_chat_restores_service_mode_after_local_override(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}

    def fake_resolve(*, provider, requested_model, allow_auto_start=True):
        assert provider == "ollama"
        return {
            "provider": "ollama",
            "model": "gemma4:e4b",
            "base_url": "http://127.0.0.1:11434/v1",
            "api_token": "",
            "runtime": {"server_running": True, "model_loaded": True},
        }

    def fake_generate(
        prompt, session_id=None, model=None, attachments=None, context=None, **kwargs
    ):
        return {
            "text": "",
            "thought": "I should use the remember tool.",
            "thought_trace": [
                {
                    "index": 0,
                    "text": "I should use the remember tool.",
                    "timestamp": 1.0,
                }
            ],
            "tools_used": [],
            "metadata": {},
        }

    monkeypatch.setattr(
        routes.provider_manager, "resolve_inference_target", fake_resolve
    )
    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    original_mode = getattr(routes.llm_service, "mode", "api")
    routes.llm_service.mode = "api"

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    client = TestClient(app)
    resp = client.post(
        "/chat",
        json={
            "message": "remember this",
            "session_id": "sess",
            "message_id": "m1",
            "use_rag": False,
            "mode": "local",
            "model": "ollama",
        },
    )
    assert resp.status_code == 200
    assert routes.llm_service.mode == "api"
    routes.llm_service.mode = original_mode


def test_chat_without_explicit_mode_uses_configured_mode_not_service_mode(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}
    captured = {}

    def fail_provider_resolution(*args, **kwargs):
        raise AssertionError(
            "provider resolution should not run for configured api mode"
        )

    def fake_generate(
        prompt, session_id=None, model=None, attachments=None, context=None, **kwargs
    ):
        captured["metadata"] = kwargs.get("metadata")
        return {"text": "ok", "thought": "", "tools_used": [], "metadata": {}}

    monkeypatch.setattr(
        routes,
        "_resolve_provider_inference_target_or_none",
        fail_provider_resolution,
    )
    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    original_cfg_mode = app.state.config.get("mode")
    original_service_mode = getattr(routes.llm_service, "mode", "api")
    app.state.config["mode"] = "api"
    routes.llm_service.mode = "local"

    try:
        client = TestClient(app)
        resp = client.post(
            "/chat",
            json={
                "message": "hi",
                "session_id": "sess",
                "message_id": "m1",
                "use_rag": False,
            },
        )
        assert resp.status_code == 200
        assert captured["metadata"]["mode"] == "api"
    finally:
        app.state.config["mode"] = original_cfg_mode
        routes.llm_service.mode = original_service_mode


def test_chat_persists_tool_proposals(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext
    from app.utils import user_settings

    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}
    monkeypatch.setattr(
        user_settings,
        "USER_SETTINGS_PATH",
        tmp_path / "user_settings.json",
        raising=False,
    )
    user_settings.save_settings({"approval_level": "all"})

    def fake_generate(
        prompt, session_id=None, model=None, attachments=None, context=None, **kwargs
    ):
        return {
            "text": "",
            "thought": "",
            "tools_used": [
                {"name": "search_web", "args": {"query": "tacos", "max_results": 2}}
            ],
            "metadata": {},
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    client = TestClient(app)
    resp = client.post(
        "/chat",
        json={
            "message": "find tacos",
            "session_id": "sess",
            "message_id": "m1",
            "use_rag": False,
        },
    )
    assert resp.status_code == 200

    messages = conv_store.load_conversation("sess")
    ai = next(m for m in messages if m.get("id") == "m1")
    assert "Requested tool" in (ai.get("text") or "")
    tools = ai.get("tools")
    assert isinstance(tools, list) and tools
    tool = tools[0]
    assert tool.get("name") == "search_web"
    assert tool.get("status") == "proposed"
    assert (ai.get("metadata") or {}).get("status") == "pending"


def test_chat_tool_proposals_emit_review_notification(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext
    from app.utils import user_settings

    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}
    monkeypatch.setattr(
        user_settings,
        "USER_SETTINGS_PATH",
        tmp_path / "user_settings.json",
        raising=False,
    )
    user_settings.save_settings(
        {
            "tool_resolution_notifications": True,
            "approval_level": "all",
        }
    )

    notifications = []

    def fake_emit_notification(app, **kwargs):
        notifications.append(kwargs)

    monkeypatch.setattr(routes, "emit_notification", fake_emit_notification)

    def fake_generate(
        prompt, session_id=None, model=None, attachments=None, context=None, **kwargs
    ):
        return {
            "text": "",
            "thought": "",
            "tools_used": [
                {"name": "search_web", "args": {"query": "tacos", "max_results": 2}}
            ],
            "metadata": {},
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    client = TestClient(app)
    resp = client.post(
        "/chat",
        json={
            "message": "find tacos",
            "session_id": "sess",
            "message_id": "m1",
            "use_rag": False,
        },
    )
    assert resp.status_code == 200

    assert len(notifications) == 1
    assert notifications[0]["category"] == "tool_resolution"
    assert notifications[0]["title"] == "Tool review needed"
    assert notifications[0]["data"]["tool_names"] == ["search_web"]
    assert notifications[0]["data"]["tool_ids"]
    assert notifications[0]["data"]["tool_args"] == [
        {"query": "tacos", "max_results": 2, "region": "us-en"}
    ]
    assert notifications[0]["data"]["tool_statuses"] == ["proposed"]


def test_chat_tool_proposals_skip_review_notification_when_disabled(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext
    from app.utils import user_settings

    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}
    monkeypatch.setattr(
        user_settings,
        "USER_SETTINGS_PATH",
        tmp_path / "user_settings.json",
        raising=False,
    )
    user_settings.save_settings(
        {
            "tool_resolution_notifications": False,
            "approval_level": "all",
        }
    )

    notifications = []

    def fake_emit_notification(app, **kwargs):
        notifications.append(kwargs)

    monkeypatch.setattr(routes, "emit_notification", fake_emit_notification)

    def fake_generate(
        prompt, session_id=None, model=None, attachments=None, context=None, **kwargs
    ):
        return {
            "text": "",
            "thought": "",
            "tools_used": [
                {"name": "search_web", "args": {"query": "tacos", "max_results": 2}}
            ],
            "metadata": {},
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    client = TestClient(app)
    resp = client.post(
        "/chat",
        json={
            "message": "find tacos",
            "session_id": "sess",
            "message_id": "m1",
            "use_rag": False,
        },
    )
    assert resp.status_code == 200

    assert notifications == []


def test_chat_tool_proposals_skip_review_notification_when_approval_is_auto(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext
    from app.utils import user_settings

    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}
    monkeypatch.setattr(
        user_settings,
        "USER_SETTINGS_PATH",
        tmp_path / "user_settings.json",
        raising=False,
    )
    user_settings.save_settings(
        {
            "tool_resolution_notifications": True,
            "approval_level": "auto",
        }
    )

    notifications = []

    def fake_emit_notification(app, **kwargs):
        notifications.append(kwargs)

    monkeypatch.setattr(routes, "emit_notification", fake_emit_notification)

    def fake_generate(
        prompt, session_id=None, model=None, attachments=None, context=None, **kwargs
    ):
        return {
            "text": "",
            "thought": "",
            "tools_used": [
                {"name": "search_web", "args": {"query": "tacos", "max_results": 2}}
            ],
            "metadata": {},
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    client = TestClient(app)
    resp = client.post(
        "/chat",
        json={
            "message": "find tacos",
            "session_id": "sess",
            "message_id": "m1",
            "use_rag": False,
        },
    )
    assert resp.status_code == 200

    assert notifications == []


def test_chat_masks_completion_text_when_tools_are_only_proposed(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext
    from app.utils import user_settings

    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}
    monkeypatch.setattr(
        user_settings,
        "USER_SETTINGS_PATH",
        tmp_path / "user_settings.json",
        raising=False,
    )
    user_settings.save_settings({"approval_level": "all"})

    def fake_generate(
        prompt, session_id=None, model=None, attachments=None, context=None, **kwargs
    ):
        return {
            "text": "Done. I created data/workspace/hello.txt.",
            "thought": "",
            "tools_used": [
                {
                    "name": "write_file",
                    "args": {"path": "data/workspace/hello.txt", "content": "hello"},
                }
            ],
            "metadata": {},
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    client = TestClient(app)
    resp = client.post(
        "/chat",
        json={
            "message": "create hello.txt",
            "session_id": "sess",
            "message_id": "m1",
            "use_rag": False,
        },
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["message"].startswith("Requested tool")
    assert "Awaiting approval." in payload["message"]
    assert payload.get("metadata", {}).get("status") == "pending"
    assert payload.get("metadata", {}).get("tool_response_pending") is True


def test_chat_dedupes_duplicate_tool_proposals(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext
    from app.utils import user_settings

    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}
    monkeypatch.setattr(
        user_settings,
        "USER_SETTINGS_PATH",
        tmp_path / "user_settings.json",
        raising=False,
    )
    user_settings.save_settings({"approval_level": "all"})

    def fake_generate(
        prompt, session_id=None, model=None, attachments=None, context=None, **kwargs
    ):
        duplicate = {
            "name": "read_file",
            "args": {"path": "data/workspace/hello.txt"},
        }
        return {
            "text": "",
            "thought": "",
            "tools_used": [dict(duplicate), dict(duplicate)],
            "metadata": {},
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    client = TestClient(app)
    resp = client.post(
        "/chat",
        json={
            "message": "read hello.txt",
            "session_id": "sess",
            "message_id": "m1",
            "use_rag": False,
        },
    )
    assert resp.status_code == 200
    payload = resp.json()
    tools_used = payload.get("tools_used") or []
    assert len(tools_used) == 1
    assert tools_used[0].get("name") == "read_file"

    registry = getattr(client.app.state, "pending_tools", {})
    assert isinstance(registry, dict)
    assert len(registry) == 1

    messages = conv_store.load_conversation("sess")
    ai = next(m for m in messages if m.get("id") == "m1")
    tools = ai.get("tools") or []
    assert len(tools) == 1
    assert tools[0].get("name") == "read_file"


def test_memory_write_proposals_are_key_idempotent_and_bounded(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from starlette.requests import Request

    attachment_hash = "a" * 64
    existing_tools = [
        {
            "id": f"remember-{index}",
            "name": "remember",
            "args": {
                "key": f"photo.{index}",
                "value": (
                    {"description": "value 0", "content_hash": attachment_hash}
                    if index == 0
                    else f"value {index}"
                ),
            },
            "status": "invoked",
            "result": {"status": "invoked", "ok": True},
            "server_recorded": True,
        }
        for index in range(7)
    ]
    conv_store.save_conversation(
        "sess",
        [
            {"id": "m1:user", "role": "user", "text": "remember these photos"},
            {"id": "m1", "role": "ai", "text": "", "tools": existing_tools},
        ],
    )

    app = importlib.import_module("app.main").app
    # Registry and transcript contain the same receipts in production. They must
    # count once, while distinct memory writes remain bounded for this turn.
    app.state.pending_tools = {
        tool["id"]: {
            **tool,
            "session_id": "sess",
            "message_id": "m1",
            "chain_id": "m1",
        }
        for tool in existing_tools
    }
    monkeypatch.setitem(app.state.config, "memory_writes_per_turn_limit", 8)

    request = Request(
        {"type": "http", "method": "POST", "path": "/", "headers": [], "app": app}
    )
    emitted = asyncio.run(
        routes._register_tool_proposals(
            request,
            tools=[
                {
                    "name": "remember",
                    "args": {
                        "key": "photo.zero.alias",
                        "value": {
                            "description": "reworded duplicate",
                            "content_hash": attachment_hash,
                        },
                    },
                },
                {
                    "name": "remember",
                    "args": {"key": "photo.7", "value": "last allowed photo"},
                },
                {
                    "name": "remember",
                    "args": {"key": "photo.8", "value": "over the turn limit"},
                },
            ],
            session_id="sess",
            message_id="m1",
            model="test-model",
            mode="api",
            default_agent="m1",
            force_tool_review=True,
            persist_events=False,
        )
    )

    assert [item["args"]["key"] for item in emitted] == ["photo.7"]


def test_image_turn_bounds_memory_writes_to_one_per_attachment(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from starlette.requests import Request

    conv_store.save_conversation(
        "sess",
        [
            {
                "id": "m1:user",
                "role": "user",
                "text": "remember these images",
                "attachments": [
                    {
                        "name": f"image-{index}.png",
                        "type": "image/png",
                        "url": f"/api/attachments/{str(index) * 64}/image.png",
                        "content_hash": str(index) * 64,
                    }
                    for index in (1, 2)
                ],
            },
            {"id": "m1", "role": "ai", "text": "", "tools": []},
        ],
    )
    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    request = Request(
        {"type": "http", "method": "POST", "path": "/", "headers": [], "app": app}
    )

    emitted = asyncio.run(
        routes._register_tool_proposals(
            request,
            tools=[
                {
                    "name": "remember",
                    "args": {"key": f"photo.{index}", "value": f"image {index}"},
                }
                for index in range(3)
            ],
            session_id="sess",
            message_id="m1",
            model="test-model",
            mode="api",
            default_agent="m1",
            force_tool_review=True,
            persist_events=False,
        )
    )

    assert [item["args"]["key"] for item in emitted] == ["photo.0", "photo.1"]


def test_memory_write_identity_preserves_case_sensitive_keys():
    from app import routes

    assert routes._memory_write_identity(
        "remember", {"key": "ProjectA", "value": "upper"}
    ) != routes._memory_write_identity(
        "remember", {"key": "projecta", "value": "lower"}
    )


@pytest.mark.parametrize(
    "retryable_status",
    ["error", "timeout", "timed_out", "cancelled"],
)
def test_failed_memory_write_can_retry_same_key(
    monkeypatch,
    tmp_path,
    retryable_status,
):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from starlette.requests import Request

    conv_store.save_conversation(
        "sess",
        [
            {"id": "m1:user", "role": "user", "text": "remember this"},
            {
                "id": "m1",
                "role": "ai",
                "text": "",
                "tools": [
                    {
                        "id": "failed-remember",
                        "name": "remember",
                        "args": {"key": "photo.owl", "value": ""},
                        "status": retryable_status,
                        "server_recorded": True,
                    }
                ],
            },
        ],
    )
    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    request = Request(
        {"type": "http", "method": "POST", "path": "/", "headers": [], "app": app}
    )

    emitted = asyncio.run(
        routes._register_tool_proposals(
            request,
            tools=[
                {
                    "name": "remember",
                    "args": {"key": "photo.owl", "value": "owl by the ravine"},
                }
            ],
            session_id="sess",
            message_id="m1",
            model="test-model",
            mode="api",
            default_agent="m1",
            force_tool_review=True,
            persist_events=False,
        )
    )

    assert len(emitted) == 1
    assert emitted[0]["args"]["key"] == "photo.owl"


def test_chat_continue_persists_text(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    conv_store.save_conversation(
        "sess",
        [
            {"id": "m1:user", "role": "user", "text": "hello"},
            {
                "id": "m1",
                "role": "ai",
                "text": "Requested tool search_web.",
                "metadata": {"status": "complete"},
            },
        ],
    )

    from app import routes
    from app.base_services import ModelContext

    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}

    def fake_generate(
        prompt, session_id=None, model=None, attachments=None, context=None, **kwargs
    ):
        return {"text": "final answer", "thought": "", "tools_used": [], "metadata": {}}

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    terminal_tool = _record_terminal_continuation(
        routes,
        app,
        session_id="sess",
        message_id="m1",
        request_id="search-1",
        name="search_web",
        args={"query": "hello"},
        result={"data": {"results": []}},
    )
    client = TestClient(app)
    resp = client.post(
        "/chat/continue",
        json={
            "session_id": "sess",
            "message_id": "m1",
            "model": None,
            "tools": [terminal_tool],
        },
    )
    assert resp.status_code == 200

    messages = conv_store.load_conversation("sess")
    ai = next(m for m in messages if m.get("id") == "m1")
    assert ai.get("text") == "final answer"
    assert (ai.get("metadata") or {}).get("tool_continued") is True


def test_chat_continue_without_explicit_mode_uses_configured_mode_not_service_mode(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    conv_store.save_conversation(
        "sess",
        [
            {"id": "m1:user", "role": "user", "text": "hello"},
            {
                "id": "m1",
                "role": "ai",
                "text": "Requested tool help.",
                "metadata": {"status": "pending"},
            },
        ],
    )

    from app import routes
    from app.base_services import ModelContext

    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}
    captured = {}

    def fail_provider_resolution(*args, **kwargs):
        raise AssertionError(
            "provider resolution should not run for configured api mode"
        )

    def fake_generate(
        prompt, session_id=None, model=None, attachments=None, context=None, **kwargs
    ):
        captured["metadata"] = kwargs.get("metadata")
        return {"text": "continued", "thought": "", "tools_used": [], "metadata": {}}

    monkeypatch.setattr(
        routes,
        "_resolve_provider_inference_target_or_none",
        fail_provider_resolution,
    )
    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    terminal_tool = _record_terminal_continuation(
        routes,
        app,
        session_id="sess",
        message_id="m1",
        request_id="help-1",
        name="help",
        args={},
        result={"data": {"tools": ["search_web"]}},
    )
    original_cfg_mode = app.state.config.get("mode")
    original_service_mode = getattr(routes.llm_service, "mode", "api")
    app.state.config["mode"] = "api"
    routes.llm_service.mode = "local"

    try:
        client = TestClient(app)
        resp = client.post(
            "/chat/continue",
            json={
                "session_id": "sess",
                "message_id": "m1",
                "tools": [terminal_tool],
            },
        )
        assert resp.status_code == 200
        assert captured["metadata"]["mode"] == "api"
    finally:
        app.state.config["mode"] = original_cfg_mode
        routes.llm_service.mode = original_service_mode


def test_chat_rejects_compare_workflow_with_fewer_than_two_images(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}
    called = {"generate": False}

    def fake_generate(
        prompt, session_id=None, model=None, attachments=None, context=None, **kwargs
    ):
        called["generate"] = True
        return {"text": "ok", "thought": "", "tools_used": [], "metadata": {}}

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    client = TestClient(app)
    resp = client.post(
        "/chat",
        json={
            "message": "compare these",
            "session_id": "sess",
            "message_id": "m1",
            "use_rag": False,
            "vision_workflow": "compare",
            "attachments": [
                {
                    "name": "image-one.png",
                    "type": "image/png",
                    "url": f"/api/attachments/{_IMAGE_HASH_ONE}/image-one.png",
                    "content_hash": _IMAGE_HASH_ONE,
                }
            ],
        },
    )
    assert resp.status_code == 400
    assert "at least two image attachments" in str(resp.json().get("detail", ""))
    assert called["generate"] is False


def test_chat_passes_vision_workflow_to_generate_and_persists_user_metadata(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

    _mock_canonical_image_attachment(
        monkeypatch,
        routes,
        _IMAGE_HASH_TWO,
        capture_id="capture-1",
    )
    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}
    captured = {}

    def fake_generate(
        prompt, session_id=None, model=None, attachments=None, context=None, **kwargs
    ):
        captured["attachments"] = attachments
        captured["context"] = context
        captured["vision_workflow"] = kwargs.get("vision_workflow")
        return {"text": "captioned", "thought": "", "tools_used": [], "metadata": {}}

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    client = TestClient(app)
    resp = client.post(
        "/chat",
        json={
            "message": "describe the image",
            "session_id": "sess",
            "message_id": "m1",
            "use_rag": False,
            "vision_workflow": "caption",
            "attachments": [
                {
                    "name": "camera.png",
                    "type": "image/png",
                    "url": "/api/captures/capture-1/content",
                    "content_hash": _IMAGE_HASH_TWO,
                    "origin": "captured",
                    "relative_path": "captures/transient/capture-1/camera.png",
                    "capture_source": "chat_camera",
                    "capture_id": "capture-1",
                    "transient": True,
                    "expires_at": "2026-07-25T12:00:00Z",
                }
            ],
        },
    )
    assert resp.status_code == 200
    assert captured["vision_workflow"] == "caption"
    assert captured["attachments"][0]["origin"] == "captured"
    assert captured["attachments"][0]["capture_id"] == "capture-1"
    # Once the content hash resolves to a durable stored attachment, unverified
    # client transient/expiry hints must not override its canonical lifecycle.
    assert "transient" not in captured["attachments"][0]
    assert "expires_at" not in captured["attachments"][0]
    provenance_message = next(
        entry
        for entry in captured["context"].messages
        if isinstance(entry, dict)
        and (entry.get("metadata") or {}).get("image_memory_provenance") is True
    )
    provenance_text = str(provenance_message.get("content") or "")
    assert (
        "content_hash is the durable cross-device attachment and sync identifier"
        in (provenance_text)
    )
    assert (
        "relative_path, when present, is the current managed deployment-relative "
        "storage location" in provenance_text
    )
    assert (
        "url is a reconstructable API retrieval route, not the durable saved location"
        in provenance_text
    )
    assert "relative_path when available" in provenance_text
    assert any(
        ((entry.get("metadata") or {}).get("vision", {}).get("workflow") == "caption")
        for entry in captured["context"].messages
        if isinstance(entry, dict)
    )
    assert not any(
        entry.get("role") == "user" and entry.get("content") == "describe the image"
        for entry in captured["context"].messages
        if isinstance(entry, dict)
    )

    messages = conv_store.load_conversation("sess")
    user_entry = next(m for m in messages if m.get("id") == "m1:user")
    assert (user_entry.get("metadata") or {}).get("vision", {}).get("workflow") == (
        "caption"
    )
    assert user_entry.get("attachments")[0]["origin"] == "captured"
    assert user_entry.get("attachments")[0]["capture_id"] == "capture-1"
    assert "transient" not in user_entry.get("attachments")[0]
    assert "expires_at" not in user_entry.get("attachments")[0]
    live_context = routes.llm_service.get_context("sess")
    assert [
        entry.get("content")
        for entry in live_context.messages
        if isinstance(entry, dict) and entry.get("role") == "user"
    ] == ["describe the image"]


def test_chat_rehydrates_saved_attachments_into_context(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

    _mock_canonical_image_attachment(monkeypatch, routes, _IMAGE_HASH_TWO)
    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}
    captured = {"calls": []}

    def fake_generate(
        prompt, session_id=None, model=None, attachments=None, context=None, **kwargs
    ):
        captured["calls"].append(
            {
                "prompt": prompt,
                "attachments": attachments,
                "context": context,
            }
        )
        return {"text": "ok", "thought": "", "tools_used": [], "metadata": {}}

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    client = TestClient(app)

    first = client.post(
        "/chat",
        json={
            "message": "describe this image",
            "session_id": "sess",
            "message_id": "m1",
            "use_rag": False,
            "attachments": [
                {
                    "name": "camera.png",
                    "type": "image/png",
                    "url": f"/api/attachments/{_IMAGE_HASH_TWO}/camera.png",
                    "content_hash": _IMAGE_HASH_TWO,
                    "origin": "captured",
                }
            ],
        },
    )
    assert first.status_code == 200

    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}

    second = client.post(
        "/chat",
        json={
            "message": "what was in that image",
            "session_id": "sess",
            "message_id": "m2",
            "use_rag": False,
        },
    )
    assert second.status_code == 200
    assert captured["calls"][-1]["attachments"][0]["content_hash"] == _IMAGE_HASH_TWO

    rehydrated_context = captured["calls"][-1]["context"]
    rehydrated_user = next(
        entry
        for entry in rehydrated_context.messages
        if isinstance(entry, dict) and entry.get("content") == "describe this image"
    )
    assert rehydrated_user.get("metadata", {}).get("attachments")[0][
        "content_hash"
    ] == (_IMAGE_HASH_TWO)


def test_chat_attachment_only_turn_restores_session_context_after_generate(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

    _mock_canonical_image_attachment(monkeypatch, routes, _IMAGE_HASH_EMPTY_TURN)
    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}

    def fake_generate(
        prompt, session_id=None, model=None, attachments=None, context=None, **kwargs
    ):
        routes.llm_service.set_context(context, session_id)
        return {"text": "ok", "thought": "", "tools_used": [], "metadata": {}}

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    client = TestClient(app)

    resp = client.post(
        "/chat",
        json={
            "message": "",
            "session_id": "sess",
            "message_id": "m-empty",
            "use_rag": False,
            "attachments": [
                {
                    "name": "camera.png",
                    "type": "image/png",
                    "url": f"/api/attachments/{_IMAGE_HASH_EMPTY_TURN}/camera.png",
                    "content_hash": _IMAGE_HASH_EMPTY_TURN,
                    "origin": "captured",
                }
            ],
        },
    )

    assert resp.status_code == 200
    live_context = routes.llm_service.get_context("sess")
    user_entries = [
        entry
        for entry in live_context.messages
        if isinstance(entry, dict) and entry.get("role") == "user"
    ]
    assert len(user_entries) == 1
    assert user_entries[0].get("content") == ""
    assert (
        user_entries[0].get("metadata", {}).get("attachments")[0]["content_hash"]
        == _IMAGE_HASH_EMPTY_TURN
    )


def test_chat_reuses_recent_image_for_attachment_only_follow_up_after_rehydrate(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

    _mock_canonical_image_attachment(monkeypatch, routes, _IMAGE_HASH_EMPTY_TURN)
    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}
    captured = {"calls": []}

    def fake_generate(
        prompt, session_id=None, model=None, attachments=None, context=None, **kwargs
    ):
        captured["calls"].append(
            {
                "prompt": prompt,
                "attachments": attachments,
                "context": context,
            }
        )
        return {"text": "ok", "thought": "", "tools_used": [], "metadata": {}}

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    client = TestClient(app)

    first = client.post(
        "/chat",
        json={
            "message": "",
            "session_id": "sess",
            "message_id": "m-empty",
            "use_rag": False,
            "attachments": [
                {
                    "name": "camera.png",
                    "type": "image/png",
                    "url": f"/api/attachments/{_IMAGE_HASH_EMPTY_TURN}/camera.png",
                    "content_hash": _IMAGE_HASH_EMPTY_TURN,
                    "origin": "captured",
                }
            ],
        },
    )
    assert first.status_code == 200

    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}

    second = client.post(
        "/chat",
        json={
            "message": "what about this?",
            "session_id": "sess",
            "message_id": "m-followup",
            "use_rag": False,
        },
    )
    assert second.status_code == 200
    assert (
        captured["calls"][-1]["attachments"][0]["content_hash"]
        == _IMAGE_HASH_EMPTY_TURN
    )


def test_chat_reuses_recent_image_for_direct_follow_up(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

    _mock_canonical_image_attachment(monkeypatch, routes, _IMAGE_HASH_TWO)
    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}
    captured = {"calls": []}

    def fake_generate(
        prompt, session_id=None, model=None, attachments=None, context=None, **kwargs
    ):
        captured["calls"].append(
            {
                "prompt": prompt,
                "attachments": attachments,
                "context": context,
            }
        )
        return {"text": "ok", "thought": "", "tools_used": [], "metadata": {}}

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    client = TestClient(app)

    first = client.post(
        "/chat",
        json={
            "message": "describe this image",
            "session_id": "sess",
            "message_id": "m1",
            "use_rag": False,
            "attachments": [
                {
                    "name": "camera.png",
                    "type": "image/png",
                    "url": f"/api/attachments/{_IMAGE_HASH_TWO}/camera.png",
                    "content_hash": _IMAGE_HASH_TWO,
                    "origin": "captured",
                }
            ],
        },
    )
    assert first.status_code == 200

    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}

    second = client.post(
        "/chat",
        json={
            "message": "what about this?",
            "session_id": "sess",
            "message_id": "m2",
            "use_rag": False,
        },
    )
    assert second.status_code == 200
    assert captured["calls"][-1]["attachments"][0]["content_hash"] == _IMAGE_HASH_TWO


def test_chat_context_drops_forged_attachment_marker_and_top_level_image_path(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}
    missing_hash = hashlib.sha256(b"missing-context-image").hexdigest()
    secret_path = tmp_path / "private-image.png"
    secret_path.write_bytes(b"private")
    captured = {}

    def fake_generate(
        prompt, session_id=None, model=None, attachments=None, context=None, **kwargs
    ):
        captured["context"] = context
        return {"text": "ok", "thought": "", "tools_used": [], "metadata": {}}

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)
    monkeypatch.setattr(routes, "_attachment_public_descriptor", lambda _hash: None)
    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    client = TestClient(app)

    response = client.post(
        "/chat",
        json={
            "message": "continue",
            "session_id": f"context-forgery-{uuid4()}",
            "message_id": f"context-forgery-turn-{uuid4()}",
            "use_rag": False,
            "context": {
                "system_prompt": "",
                "messages": [
                    {
                        "role": "user",
                        "content": "earlier image",
                        "metadata": {
                            "attachments": [
                                {
                                    "name": "image.png",
                                    "type": "image/png",
                                    "content_hash": missing_hash,
                                    "relative_path": str(secret_path),
                                    "path": str(secret_path),
                                    "source_url": (
                                        "https://example.test/image.png?token=secret"
                                    ),
                                    "_canonical_attachment_resolved": True,
                                }
                            ]
                        },
                    }
                ],
                "metadata": {
                    "images": [{"path": str(secret_path), "score": 1.0}],
                },
            },
        },
    )

    assert response.status_code == 200
    context = captured["context"]
    assert "images" not in context.metadata
    earlier = next(
        message
        for message in context.messages
        if isinstance(message, dict) and message.get("content") == "earlier image"
    )
    attachment = earlier["metadata"]["attachments"][0]
    assert attachment["name"] == "image.png"
    assert attachment["type"] == "image/png"
    for forbidden in (
        "content_hash",
        "relative_path",
        "path",
        "source_url",
        "_canonical_attachment_resolved",
    ):
        assert forbidden not in attachment


def test_chat_context_replaces_attachment_hints_with_canonical_descriptor(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}
    _mock_canonical_image_attachment(monkeypatch, routes, _IMAGE_HASH_TWO)
    captured = {}

    def fake_generate(
        prompt, session_id=None, model=None, attachments=None, context=None, **kwargs
    ):
        captured["context"] = context
        return {"text": "ok", "thought": "", "tools_used": [], "metadata": {}}

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)
    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    client = TestClient(app)

    response = client.post(
        "/chat",
        json={
            "message": "continue",
            "session_id": f"context-canonical-{uuid4()}",
            "message_id": f"context-canonical-turn-{uuid4()}",
            "use_rag": False,
            "context": {
                "system_prompt": "",
                "messages": [
                    {
                        "role": "user",
                        "content": "earlier image",
                        "metadata": {
                            "attachments": [
                                {
                                    "name": "forged-name.png",
                                    "type": "image/png",
                                    "content_hash": _IMAGE_HASH_TWO,
                                    "relative_path": "../../outside.png",
                                    "source_url": "https://example.test/?token=secret",
                                    "_canonical_attachment_resolved": True,
                                }
                            ]
                        },
                    }
                ],
                "metadata": {},
            },
        },
    )

    assert response.status_code == 200
    earlier = next(
        message
        for message in captured["context"].messages
        if isinstance(message, dict) and message.get("content") == "earlier image"
    )
    attachment = earlier["metadata"]["attachments"][0]
    assert attachment["_canonical_attachment_resolved"] is True
    assert attachment["content_hash"] == _IMAGE_HASH_TWO
    assert attachment["name"] == "camera.png"
    assert attachment["relative_path"] == (f"captured/{_IMAGE_HASH_TWO}/camera.png")
    assert attachment["url"] == f"/api/attachments/{_IMAGE_HASH_TWO}/camera.png"
    assert "source_url" not in attachment


def test_legacy_context_mutations_apply_attachment_and_image_path_ingress_rules(
    monkeypatch,
    tmp_path,
):
    from app import routes

    monkeypatch.setattr(routes, "_attachment_public_descriptor", lambda _hash: None)
    app = importlib.import_module("app.main").app
    client = TestClient(app)
    context_id = f"legacy-context-{uuid4()}"
    content_hash = hashlib.sha256(b"legacy-forged-image").hexdigest()
    secret_path = tmp_path / "legacy-private.png"
    secret_path.write_bytes(b"private")

    created = client.post(
        f"/context/{context_id}",
        json={"system_prompt": "", "messages": [], "tools": [], "metadata": {}},
    )
    assert created.status_code == 200
    added = client.post(
        f"/context/{context_id}/message",
        params={"role": "user", "content": "forged image"},
        json={
            "attachments": [
                {
                    "name": "image.png",
                    "type": "image/png",
                    "content_hash": content_hash,
                    "relative_path": str(secret_path),
                    "path": str(secret_path),
                    "_canonical_attachment_resolved": True,
                }
            ]
        },
    )
    assert added.status_code == 200
    attachment = added.json()["context"]["messages"][-1]["metadata"]["attachments"][0]
    assert attachment == {
        "name": "image.png",
        "type": "image/png",
        "content_type": "image/png",
    }

    updated = client.post(
        f"/context/{context_id}/metadata",
        params={"key": "images", "value": str(secret_path)},
    )
    assert updated.status_code == 200
    assert updated.json()["context"]["metadata"]["images"] == []


def test_llm_generate_enriches_attachments_before_service_dispatch(
    monkeypatch,
):
    from app import routes

    captured = {}
    _mock_canonical_image_attachment(monkeypatch, routes, _IMAGE_HASH_TWO)

    def fake_generate(prompt, **kwargs):
        captured["attachments"] = kwargs.get("attachments")
        return {"text": "ok", "thought": "", "tools_used": [], "metadata": {}}

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)
    app = importlib.import_module("app.main").app
    client = TestClient(app)
    response = client.post(
        "/llm/generate",
        json={
            "prompt": "describe",
            "mode": "api",
            "message_id": f"llm-attachment-{uuid4()}",
            "attachments": [
                {
                    "name": "forged.png",
                    "type": "image/png",
                    "content_hash": _IMAGE_HASH_TWO,
                    "relative_path": "../../outside.png",
                    "source_url": "https://example.test/?token=secret",
                }
            ],
        },
    )

    assert response.status_code == 200
    attachment = captured["attachments"][0]
    assert attachment["_canonical_attachment_resolved"] is True
    assert attachment["content_hash"] == _IMAGE_HASH_TWO
    assert attachment["name"] == "camera.png"
    assert attachment["relative_path"] == (f"captured/{_IMAGE_HASH_TWO}/camera.png")
    assert "source_url" not in attachment


def test_chat_allows_zero_rag_similarity_to_disable_threshold(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}

    class DummyRagService:
        embedding_model = "simple"

        def query(self, _text, top_k=5):
            assert top_k >= 1
            return [
                {
                    "id": "doc-1",
                    "text": "Paris is the capital of France.",
                    "metadata": {
                        "source": "workspace/reference/paris.txt",
                        "kind": "document",
                    },
                    "score": 0.2,
                }
            ]

    def fake_generate(
        prompt, session_id=None, model=None, attachments=None, context=None, **kwargs
    ):
        return {"text": "Paris.", "thought": "", "tools_used": [], "metadata": {}}

    monkeypatch.setattr(routes, "_get_rag_service", lambda: DummyRagService())
    monkeypatch.setattr(routes, "_get_clip_rag_service", lambda **kwargs: None)
    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    app.state.config["rag_chat_min_similarity"] = 0.0
    client = TestClient(app)
    resp = client.post(
        "/chat",
        json={
            "message": "What is the capital of France?",
            "session_id": "sess",
            "message_id": "m1",
            "use_rag": True,
        },
    )
    assert resp.status_code == 200

    messages = conv_store.load_conversation("sess")
    user_entry = next(m for m in messages if m.get("id") == "m1:user")
    rag_matches = user_entry.get("rag") or []
    assert len(rag_matches) == 1
    assert rag_matches[0]["source"] == "workspace/reference/paris.txt"


def test_chat_rag_prefers_exact_memory_reference_and_penalizes_recent_repeats(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}

    class DummyRagService:
        embedding_model = "simple"

        def query(self, _text, top_k=5):
            assert top_k >= 3
            return [
                {
                    "id": "tea-party",
                    "text": "Tea party memory",
                    "metadata": {
                        "kind": "memory",
                        "source": "memory/2025-12-02-tea-party",
                        "key": "2025-12-02-tea-party",
                    },
                    "score": 0.4607,
                },
                {
                    "id": "dinner",
                    "text": "Pulled seitan fajitas and strawberry kombucha for dinner.",
                    "metadata": {
                        "kind": "memory",
                        "source": "memory/2025-12-03-dinner",
                        "key": "2025-12-03-dinner",
                    },
                    "score": 0.4590,
                },
                {
                    "id": "calendar",
                    "text": "Calendar event memory",
                    "metadata": {
                        "kind": "memory",
                        "source": "memory/2025-12-04-calendar",
                        "key": "2025-12-04-calendar",
                    },
                    "score": 0.4541,
                },
            ]

    class DummyMemoryManager:
        def get_item(self, key, include_pruned=True, touch=False):
            return {
                "key": key,
                "value": {
                    "2025-12-02-tea-party": "Tea party memory",
                    "2025-12-03-dinner": (
                        "Pulled seitan fajitas and strawberry kombucha for dinner."
                    ),
                    "2025-12-04-calendar": "Calendar event memory",
                }.get(key, ""),
                "vectorize": True,
            }

        def lifecycle_multiplier(self, item):
            return 1.0

    def fake_generate(
        prompt, session_id=None, model=None, attachments=None, context=None, **kwargs
    ):
        return {"text": "ok", "thought": "", "tools_used": [], "metadata": {}}

    monkeypatch.setattr(routes, "_get_rag_service", lambda: DummyRagService())
    monkeypatch.setattr(routes, "_get_clip_rag_service", lambda **kwargs: None)
    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    monkeypatch.setattr(app.state, "memory_manager", DummyMemoryManager())
    conv_store.save_conversation(
        "sess",
        [
            {
                "id": "old-user",
                "role": "user",
                "text": "Earlier context",
                "metadata": {
                    "rag": {
                        "matches": [
                            {
                                "text": "Tea party memory",
                                "metadata": {
                                    "source": "memory/2025-12-02-tea-party",
                                    "key": "2025-12-02-tea-party",
                                },
                            }
                        ]
                    }
                },
            }
        ],
    )
    app.state.config["rag_chat_min_similarity"] = 0.45
    client = TestClient(app)
    resp = client.post(
        "/chat",
        json={
            "message": (
                "//2025-12-03-dinner\n\nContext references:\n"
                "- memory reference: 2025-12-03-dinner"
            ),
            "session_id": "sess",
            "message_id": "m1",
            "use_rag": True,
        },
    )
    assert resp.status_code == 200

    messages = conv_store.load_conversation("sess")
    user_entry = next(m for m in messages if m.get("id") == "m1:user")
    rag_matches = user_entry.get("rag") or []
    assert len(rag_matches) == 1
    assert rag_matches[0]["text"] == (
        "Pulled seitan fajitas and strawberry kombucha for dinner."
    )
    assert "tea-party" not in str(rag_matches[0]).lower()


def test_chat_rag_uses_memory_title_terms_as_secondary_signal(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}

    class DummyRagService:
        embedding_model = "simple"

        def query(self, _text, top_k=5):
            assert top_k >= 2
            return [
                {
                    "id": "tea-party",
                    "text": "Tea party memory",
                    "metadata": {
                        "kind": "memory",
                        "source": "memory/2025-12-02-tea-party",
                        "key": "2025-12-02-tea-party",
                        "title": "2025-12-02-tea-party",
                    },
                    "score": 0.4607,
                },
                {
                    "id": "dinner",
                    "text": "Pulled seitan fajitas and strawberry kombucha for dinner.",
                    "metadata": {
                        "kind": "memory",
                        "source": "memory/2025-12-03-dinner",
                        "key": "2025-12-03-dinner",
                        "title": "2025-12-03-dinner",
                    },
                    "score": 0.4590,
                },
            ]

    class DummyMemoryManager:
        def get_item(self, key, include_pruned=True, touch=False):
            return {
                "key": key,
                "title": key,
                "value": {
                    "2025-12-02-tea-party": "Tea party memory",
                    "2025-12-03-dinner": (
                        "Pulled seitan fajitas and strawberry kombucha for dinner."
                    ),
                }.get(key, ""),
                "vectorize": True,
            }

        def lifecycle_multiplier(self, item):
            return 1.0

    def fake_generate(
        prompt, session_id=None, model=None, attachments=None, context=None, **kwargs
    ):
        return {"text": "ok", "thought": "", "tools_used": [], "metadata": {}}

    monkeypatch.setattr(routes, "_get_rag_service", lambda: DummyRagService())
    monkeypatch.setattr(routes, "_get_clip_rag_service", lambda **kwargs: None)
    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    monkeypatch.setattr(app.state, "memory_manager", DummyMemoryManager())
    app.state.config["rag_chat_min_similarity"] = 0.45
    client = TestClient(app)
    resp = client.post(
        "/chat",
        json={
            "message": "What did I have for dinner?",
            "session_id": "sess",
            "message_id": "m1",
            "use_rag": True,
        },
    )
    assert resp.status_code == 200

    messages = conv_store.load_conversation("sess")
    user_entry = next(m for m in messages if m.get("id") == "m1:user")
    rag_matches = user_entry.get("rag") or []
    assert len(rag_matches) >= 2
    assert rag_matches[0]["text"] == (
        "Pulled seitan fajitas and strawberry kombucha for dinner."
    )


def test_chat_text_turn_filters_computer_capture_scope(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

    _pin_default_workflow_settings(monkeypatch, tmp_path)
    tool_defs = [
        {"name": "help", "description": "help", "parameters": {}},
        {"name": "remember", "description": "remember", "parameters": {}},
        {"name": "open_url", "description": "open", "parameters": {}},
        {"name": "computer.observe", "description": "observe", "parameters": {}},
        {"name": "camera.capture", "description": "capture", "parameters": {}},
        {"name": "capture.list", "description": "captures", "parameters": {}},
    ]
    routes.llm_service.contexts = {
        "default": ModelContext(system_prompt=""),
        "sess": ModelContext(system_prompt="", tools=tool_defs),
    }
    captured = {}

    def fake_generate(
        prompt, session_id=None, model=None, attachments=None, context=None, **kwargs
    ):
        captured["context"] = context
        return {"text": "ok", "thought": "", "tools_used": [], "metadata": {}}

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    client = TestClient(app)
    resp = client.post(
        "/chat",
        json={
            "message": "summarize the server log error",
            "session_id": "sess",
            "message_id": "m1",
            "use_rag": False,
        },
    )
    assert resp.status_code == 200

    ctx = captured.get("context")
    assert ctx is not None
    tool_names = [
        tool.get("name")
        for tool in ctx.tools
        if isinstance(tool, dict) and isinstance(tool.get("name"), str)
    ]
    assert tool_names == ["help", "remember"]
    assert "Browser, desktop, and camera control are out of scope" not in (
        ctx.system_prompt
    )
    assert "open_url" not in ctx.system_prompt
    scope_messages = [
        msg
        for msg in ctx.messages
        if isinstance(msg, dict)
        and msg.get("role") == "system"
        and (msg.get("metadata") or {}).get("turn_message_key") == "turn_scope"
    ]
    assert scope_messages
    assert "Turn mode: text/knowledge." in str(scope_messages[-1].get("content") or "")
    assert "Browser, desktop, and camera control are out of scope" in str(
        scope_messages[-1].get("content") or ""
    )
    system_text = " ".join(
        str(msg.get("content") or "")
        for msg in ctx.messages
        if isinstance(msg, dict) and msg.get("role") == "system"
    )
    assert (
        "Computer observations and camera captures are transient by default."
        not in system_text
    )
    assert "Computer Use" not in system_text
    assert "Camera Capture" not in system_text
    assert (ctx.metadata.get("workflow") or {}).get("modules") == []


def test_chat_computer_turn_keeps_computer_capture_scope(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

    _pin_default_workflow_settings(
        monkeypatch, tmp_path, enabled_modules=["computer_use"]
    )
    tool_defs = [
        {"name": "help", "description": "help", "parameters": {}},
        {"name": "open_url", "description": "open", "parameters": {}},
        {"name": "computer.observe", "description": "observe", "parameters": {}},
        {"name": "camera.capture", "description": "capture", "parameters": {}},
        {"name": "capture.list", "description": "captures", "parameters": {}},
    ]
    routes.llm_service.contexts = {
        "default": ModelContext(system_prompt=""),
        "sess": ModelContext(system_prompt="", tools=tool_defs),
    }
    captured = {}

    def fake_generate(
        prompt, session_id=None, model=None, attachments=None, context=None, **kwargs
    ):
        captured["context"] = context
        return {"text": "ok", "thought": "", "tools_used": [], "metadata": {}}

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    client = TestClient(app)
    resp = client.post(
        "/chat",
        json={
            "message": "take control of my computer and inspect the screen",
            "session_id": "sess",
            "message_id": "m1",
            "use_rag": False,
        },
    )
    assert resp.status_code == 200

    ctx = captured.get("context")
    assert ctx is not None
    tool_names = [
        tool.get("name")
        for tool in ctx.tools
        if isinstance(tool, dict) and isinstance(tool.get("name"), str)
    ]
    assert "computer.observe" in tool_names
    assert "camera.capture" in tool_names
    assert "capture.list" in tool_names
    assert "turn mode: computer/capture" not in ctx.system_prompt.lower()
    scope_messages = [
        msg
        for msg in ctx.messages
        if isinstance(msg, dict)
        and msg.get("role") == "system"
        and (msg.get("metadata") or {}).get("turn_message_key") == "turn_scope"
    ]
    assert scope_messages
    assert "Turn mode: computer/capture." in str(
        scope_messages[-1].get("content") or ""
    )
    system_text = " ".join(
        str(msg.get("content") or "")
        for msg in ctx.messages
        if isinstance(msg, dict) and msg.get("role") == "system"
    )
    assert (
        "Computer observations and camera captures are transient by default."
        in system_text
    )
    assert "Computer Use" in system_text
    assert (ctx.metadata.get("workflow") or {}).get("modules") == ["computer_use"]


def test_chat_computer_request_with_disabled_module_hides_computer_tools(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

    _pin_default_workflow_settings(monkeypatch, tmp_path)
    tool_defs = [
        {"name": "help", "description": "help", "parameters": {}},
        {"name": "tool_help", "description": "tool help", "parameters": {}},
        {"name": "read_capability_docs", "description": "docs", "parameters": {}},
        {"name": "computer.observe", "description": "observe", "parameters": {}},
        {"name": "camera.capture", "description": "capture", "parameters": {}},
    ]
    routes.llm_service.contexts = {
        "default": ModelContext(system_prompt=""),
        "sess": ModelContext(system_prompt="", tools=tool_defs),
    }
    captured = {}

    def fake_generate(
        prompt, session_id=None, model=None, attachments=None, context=None, **kwargs
    ):
        captured["context"] = context
        return {"text": "ok", "thought": "", "tools_used": [], "metadata": {}}

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    client = TestClient(app)
    resp = client.post(
        "/chat",
        json={
            "message": "take control of my computer and inspect the screen",
            "session_id": "sess",
            "message_id": "m1",
            "use_rag": False,
        },
    )
    assert resp.status_code == 200

    ctx = captured.get("context")
    assert ctx is not None
    tool_names = [
        tool.get("name")
        for tool in ctx.tools
        if isinstance(tool, dict) and isinstance(tool.get("name"), str)
    ]
    assert tool_names == ["help", "tool_help", "read_capability_docs"]
    assert (ctx.metadata.get("workflow") or {}).get("modules") == []
    assert ctx.metadata.get("disabled_modules") == ["computer_use"]
    system_text = " ".join(
        str(msg.get("content") or "")
        for msg in ctx.messages
        if isinstance(msg, dict) and msg.get("role") == "system"
    )
    assert "disabled workflow module(s): Computer Use" in system_text
    assert "Capability docs remain readable via skills:computer_use." in system_text
    assert (
        "Computer observations and camera captures are transient by default."
        not in system_text
    )
