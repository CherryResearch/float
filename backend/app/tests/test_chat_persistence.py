import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def add_backend_to_sys_path():
    import sys

    backend_dir = Path(__file__).resolve().parents[2]
    backend_dir = str(backend_dir)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)


def test_chat_persists_assistant_updates(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

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
        },
    )
    assert resp.status_code == 200

    messages = conv_store.load_conversation("sess")
    assert any(m.get("id") == "m1:user" for m in messages)
    ai = next(m for m in messages if m.get("id") == "m1")
    assert ai.get("text") == "ok"
    assert (ai.get("metadata") or {}).get("status") == "complete"


def test_chat_persists_tool_proposals(monkeypatch, tmp_path):
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

    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}

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
    assert payload.get("metadata", {}).get("tool_response_pending") is True


def test_chat_dedupes_duplicate_tool_proposals(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}

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
    app.state.pending_tools = {}
    client = TestClient(app)
    resp = client.post(
        "/chat/continue",
        json={"session_id": "sess", "message_id": "m1", "model": None, "tools": []},
    )
    assert resp.status_code == 200

    messages = conv_store.load_conversation("sess")
    ai = next(m for m in messages if m.get("id") == "m1")
    assert ai.get("text") == "final answer"
    assert (ai.get("metadata") or {}).get("tool_continued") is True


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
                    "url": "/api/attachments/hash-one/image-one.png",
                    "content_hash": "hash-one",
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
                    "url": "/api/attachments/hash-two/camera.png",
                    "content_hash": "hash-two",
                    "origin": "captured",
                    "relative_path": "captured/hash-two/camera.png",
                    "capture_source": "chat_camera",
                }
            ],
        },
    )
    assert resp.status_code == 200
    assert captured["vision_workflow"] == "caption"
    assert captured["attachments"][0]["origin"] == "captured"
    assert any(
        ((entry.get("metadata") or {}).get("vision", {}).get("workflow") == "caption")
        for entry in captured["context"].messages
        if isinstance(entry, dict)
    )

    messages = conv_store.load_conversation("sess")
    user_entry = next(m for m in messages if m.get("id") == "m1:user")
    assert (user_entry.get("metadata") or {}).get("vision", {}).get("workflow") == (
        "caption"
    )
    assert user_entry.get("attachments")[0]["origin"] == "captured"


def test_chat_rehydrates_saved_attachments_into_context(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

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
                    "url": "/api/attachments/hash-two/camera.png",
                    "content_hash": "hash-two",
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

    rehydrated_context = captured["calls"][-1]["context"]
    rehydrated_user = next(
        entry
        for entry in rehydrated_context.messages
        if isinstance(entry, dict) and entry.get("content") == "describe this image"
    )
    assert rehydrated_user.get("metadata", {}).get("attachments")[0][
        "content_hash"
    ] == ("hash-two")


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
