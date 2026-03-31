import importlib

from fastapi.testclient import TestClient


def test_chat_continue_registers_tool_proposals(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    conv_store.save_conversation(
        "sess",
        [
            {"role": "user", "text": "hello"},
            {"role": "ai", "text": "thinking..."},
        ],
    )

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
            "tools_used": [{"name": "recall", "args": {"key": "tea_party_menu_2025"}}],
            "metadata": {},
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    client = TestClient(app)
    resp = client.post(
        "/chat/continue",
        json={
            "session_id": "sess",
            "message_id": "m1",
            "model": None,
            "tools": [
                {
                    "id": "tool-1",
                    "name": "recall",
                    "args": {"key": "tea_party_menu"},
                    "result": {
                        "error": "not_found",
                        "suggestions": ["tea_party_menu_2025"],
                    },
                    "status": "invoked",
                }
            ],
        },
    )
    assert resp.status_code == 200
    payload = resp.json()
    tools_used = payload.get("tools_used") or []
    assert isinstance(tools_used, list)
    assert tools_used and tools_used[0].get("status") in {"proposed", "invoked"}
    assert tools_used[0].get("args", {}).get("key") == "tea_party_menu_2025"
    proposal_id = tools_used[0].get("id")
    assert proposal_id

    registry = getattr(client.app.state, "pending_tools", {})
    if proposal_id in registry:
        assert registry[proposal_id]["message_id"] == "m1"
    else:
        assert tools_used[0].get("status") == "invoked"


def test_chat_continue_unresolved_loop_returns_minimal_error_note(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    conv_store.save_conversation(
        "sess",
        [
            {"id": "m1:user", "role": "user", "text": "hello"},
            {"id": "m1", "role": "ai", "text": "Requested tool recall."},
        ],
    )

    from app import routes
    from app.base_services import ModelContext

    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}
    calls = {"count": 0}

    def fake_generate(
        prompt,
        session_id=None,
        model=None,
        attachments=None,
        context=None,
        **kwargs,
    ):
        calls["count"] += 1
        return {
            "text": "",
            "thought": "",
            "tools_used": [{"name": "recall", "args": {"key": "tea_party_menu"}}],
            "metadata": {},
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    client = TestClient(app)
    resp = client.post(
        "/chat/continue",
        json={
            "session_id": "sess",
            "message_id": "m1",
            "model": None,
            "tools": [
                {
                    "id": "tool-1",
                    "name": "recall",
                    "args": {"key": "tea_party_menu"},
                    "result": {"error": "not_found"},
                    "status": "error",
                }
            ],
        },
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert calls["count"] == 2
    assert payload.get("message", "").startswith(
        "I couldn't finish the continuation from tool results."
    )
    assert "- recall: error - not_found" in (payload.get("message") or "")
    metadata = payload.get("metadata") or {}
    assert metadata.get("retry_without_tools") is True
    assert metadata.get("unresolved_tool_loop") is True
    assert payload.get("tools_used") == []

    messages = conv_store.load_conversation("sess")
    ai = next(m for m in messages if m.get("id") == "m1")
    assert ai.get("text") == payload.get("message")


def test_chat_continue_ignores_repeated_tool_requests_with_text(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    conv_store.save_conversation(
        "sess",
        [
            {"id": "m1:user", "role": "user", "text": "hello"},
            {"id": "m1", "role": "ai", "text": "Requested tool recall."},
        ],
    )

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
            "text": "I couldn't find that key, but the lookup already finished.",
            "thought": "",
            "tools_used": [{"name": "recall", "args": {"key": "tea_party_menu"}}],
            "metadata": {},
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    client = TestClient(app)
    resp = client.post(
        "/chat/continue",
        json={
            "session_id": "sess",
            "message_id": "m1",
            "model": None,
            "tools": [
                {
                    "id": "tool-1",
                    "name": "recall",
                    "args": {"key": "tea_party_menu"},
                    "result": {"error": "not_found"},
                    "status": "error",
                }
            ],
        },
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert (
        payload.get("message")
        == "I couldn't find that key, but the lookup already finished."
    )
    assert payload.get("tools_used") == []
    metadata = payload.get("metadata") or {}
    assert metadata.get("tool_response_pending") is not True
    assert metadata.get("repeated_tool_requests_ignored") is True

    registry = getattr(client.app.state, "pending_tools", {})
    assert registry == {}


def test_chat_continue_does_not_requeue_resolved_tool_errors(monkeypatch, tmp_path):
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
                "text": "Requested tools recall and computer.observe.",
            },
        ],
    )

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
                    "name": "recall",
                    "args": {"key": "tea_party_menu"},
                    "status": "error",
                    "result": {"error": "not_found"},
                },
                {
                    "name": "computer.observe",
                    "args": {"session_id": "anime-browser"},
                    "status": "error",
                    "result": {"error": "Unknown computer session 'anime-browser'"},
                },
            ],
            "metadata": {"tool_response_pending": True},
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    client = TestClient(app)
    resp = client.post(
        "/chat/continue",
        json={
            "session_id": "sess",
            "message_id": "m1",
            "model": None,
            "tools": [
                {
                    "id": "tool-1",
                    "name": "recall",
                    "args": {"key": "tea_party_menu"},
                    "result": {"error": "not_found"},
                    "status": "error",
                },
                {
                    "id": "tool-2",
                    "name": "computer.observe",
                    "args": {"session_id": "anime-browser"},
                    "result": {"error": "Unknown computer session 'anime-browser'"},
                    "status": "error",
                },
            ],
        },
    )

    assert resp.status_code == 200
    payload = resp.json()
    message = payload.get("message") or ""
    assert message.startswith("I couldn't finish the continuation from tool results.")
    assert "- recall: error - not_found" in message
    assert (
        "- computer.observe: error - Unknown computer session 'anime-browser'"
        in message
    )
    metadata = payload.get("metadata") or {}
    assert metadata.get("tool_response_pending") is not True


def test_chat_continue_unresolved_loop_shows_recall_not_found_suggestions(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    conv_store.save_conversation(
        "sess",
        [
            {"id": "m1:user", "role": "user", "text": "hello"},
            {"id": "m1", "role": "ai", "text": "Requested tool recall."},
        ],
    )

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
            "tools_used": [{"name": "recall", "args": {"key": "tea_party_menu"}}],
            "metadata": {},
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    client = TestClient(app)
    resp = client.post(
        "/chat/continue",
        json={
            "session_id": "sess",
            "message_id": "m1",
            "model": None,
            "tools": [
                {
                    "id": "tool-1",
                    "name": "recall",
                    "args": {"key": "tea_party_menu"},
                    "result": {
                        "status": "invoked",
                        "ok": True,
                        "data": {
                            "error": "not_found",
                            "suggestions_detail": [
                                {"key": "tea_party_menu_2025"},
                                {"key": "tea_party_menu_ideas_2025"},
                            ],
                        },
                    },
                    "status": "invoked",
                }
            ],
        },
    )
    assert resp.status_code == 200
    payload = resp.json()
    message = payload.get("message") or ""
    assert message.startswith("I couldn't finish the continuation from tool results.")
    assert (
        "- recall: error - not_found (try: tea_party_menu_2025, "
        "tea_party_menu_ideas_2025)" in message
    )


def test_chat_continue_unresolved_loop_returns_minimal_text(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    conv_store.save_conversation(
        "sess",
        [
            {"id": "m1:user", "role": "user", "text": "hello"},
            {"id": "m1", "role": "ai", "text": "Requested tool recall."},
        ],
    )

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
            "tools_used": [{"name": "recall", "args": {"key": "tea_party_menu"}}],
            "metadata": {},
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    client = TestClient(app)
    resp = client.post(
        "/chat/continue",
        json={
            "session_id": "sess",
            "message_id": "m1",
            "model": None,
            "tools": [
                {
                    "id": "tool-1",
                    "name": "recall",
                    "args": {"key": "tea_party_menu"},
                    "result": {"ok": True},
                    "status": "invoked",
                }
            ],
        },
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload.get("message", "").startswith(
        "I couldn't finish the continuation from tool results."
    )
    assert "- recall: invoked" in (payload.get("message") or "")
    assert "Tool results:" not in (payload.get("message") or "")
    metadata = payload.get("metadata") or {}
    assert metadata.get("unresolved_tool_loop") is True


def test_chat_continue_passes_recalled_image_attachments_to_generate(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    conv_store.save_conversation(
        "sess",
        [
            {"id": "m1:user", "role": "user", "text": "look up cat photos"},
            {"id": "m1", "role": "ai", "text": "Requested tool recall."},
        ],
    )

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
        captured["attachments"] = attachments
        return {
            "text": "I found the cat photos.",
            "thought": "",
            "tools_used": [],
            "metadata": {},
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    client = TestClient(app)
    resp = client.post(
        "/chat/continue",
        json={
            "session_id": "sess",
            "message_id": "m1",
            "tools": [
                {
                    "id": "tool-1",
                    "name": "recall",
                    "args": {
                        "key": "cat photos",
                        "mode": "clip",
                        "include_images": True,
                    },
                    "result": {
                        "mode": "clip",
                        "image_matches": [
                            {
                                "caption": "Orange cat sitting on the stairs",
                                "attachment": {
                                    "name": "cat.png",
                                    "type": "image/png",
                                    "url": "/api/attachments/hash-cat/cat.png",
                                    "content_hash": "hash-cat",
                                },
                            }
                        ],
                        "image_attachments": [
                            {
                                "name": "cat.png",
                                "type": "image/png",
                                "url": "/api/attachments/hash-cat/cat.png",
                                "content_hash": "hash-cat",
                            }
                        ],
                    },
                    "status": "invoked",
                }
            ],
        },
    )
    assert resp.status_code == 200
    assert captured["attachments"] == [
        {
            "name": "cat.png",
            "type": "image/png",
            "url": "/api/attachments/hash-cat/cat.png",
            "content_hash": "hash-cat",
        }
    ]


def test_chat_continue_extracts_nested_attachment_value_and_sets_signature(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    conv_store.save_conversation(
        "sess",
        [
            {"id": "m1:user", "role": "user", "text": "what does bails look like"},
            {"id": "m1", "role": "ai", "text": "Requested tool recall."},
        ],
    )

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
        captured["attachments"] = attachments
        return {
            "text": "Bails looks very fluffy.",
            "thought": "",
            "tools_used": [],
            "metadata": {},
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    client = TestClient(app)
    resp = client.post(
        "/chat/continue",
        json={
            "session_id": "sess",
            "message_id": "m1",
            "tools": [
                {
                    "id": "tool-1",
                    "name": "recall",
                    "args": {"key": "bails", "include_images": True},
                    "result": {
                        "mode": "hybrid",
                        "data": {
                            "value": {
                                "name": "bails.jpg",
                                "type": "image/jpeg",
                                "url": "/api/attachments/hash-bails/bails.jpg",
                                "content_hash": "hash-bails",
                            }
                        },
                    },
                    "status": "invoked",
                }
            ],
        },
    )

    assert resp.status_code == 200
    assert captured["attachments"] == [
        {
            "name": "bails.jpg",
            "type": "image/jpeg",
            "url": "/api/attachments/hash-bails/bails.jpg",
            "content_hash": "hash-bails",
        }
    ]
    assert resp.json()["metadata"]["tool_continue_signature"]


def test_chat_continue_unresolved_loop_summarizes_mixed_tool_states(
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
                "text": "Requested tools search_web, search_web.",
            },
        ],
    )

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
                {"name": "search_web", "args": {"query": "tea party menu"}},
                {"name": "search_web", "args": {"query": "tea desserts"}},
                {"name": "search_web", "args": {"query": "tea decor"}},
            ],
            "metadata": {},
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    client = TestClient(app)
    resp = client.post(
        "/chat/continue",
        json={
            "session_id": "sess",
            "message_id": "m1",
            "model": None,
            "tools": [
                {
                    "id": "tool-1",
                    "name": "search_web",
                    "args": {"query": "tea party menu"},
                    "result": {
                        "data": {
                            "query": "tea party menu",
                            "results": [{"title": "Menu ideas"}],
                        }
                    },
                    "status": "invoked",
                },
                {
                    "id": "tool-2",
                    "name": "search_web",
                    "args": {"query": "tea desserts"},
                    "result": {"message": "Denied by user."},
                    "status": "denied",
                },
                {
                    "id": "tool-3",
                    "name": "search_web",
                    "args": {"query": "tea decor"},
                    "result": {"message": "Stopped by user."},
                    "status": "cancelled",
                },
            ],
        },
    )
    assert resp.status_code == 200
    payload = resp.json()
    message = payload.get("message") or ""
    assert message.startswith("I couldn't finish the continuation from tool results.")
    assert '- search_web: "tea party menu" -> Menu ideas' in message
    assert "- search_web: denied - Denied by user." in message
    assert "- search_web: cancelled - Stopped by user." in message
    metadata = payload.get("metadata") or {}
    assert metadata.get("unresolved_tool_loop") is True


def test_chat_continue_uses_compact_tool_outcome_prompt(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    conv_store.save_conversation(
        "sess",
        [
            {"id": "m1:user", "role": "user", "text": "hello"},
            {"id": "m1", "role": "ai", "text": "Checking docs first.[[tool_call:0]]"},
        ],
    )

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
        captured["context"] = context
        return {
            "text": "Use computer.session.start first.",
            "thought": "",
            "tools_used": [],
            "metadata": {},
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    client = TestClient(app)
    resp = client.post(
        "/chat/continue",
        json={
            "session_id": "sess",
            "message_id": "m1",
            "model": None,
            "tools": [
                {
                    "id": "tool-1",
                    "name": "tool_help",
                    "args": {
                        "tool_name": "",
                        "detail": "rich",
                        "include_schema": True,
                        "max_tools": 20,
                    },
                    "result": {
                        "status": "invoked",
                        "ok": True,
                        "data": {
                            "count": 20,
                            "tools": [
                                {"name": "computer.session.start"},
                                {"name": "computer.navigate"},
                                {"name": "computer.observe"},
                                {"name": "computer.act"},
                                {"name": "computer.app.launch"},
                            ],
                        },
                    },
                    "status": "invoked",
                }
            ],
        },
    )
    assert resp.status_code == 200
    ctx = captured.get("context")
    assert ctx is not None
    tool_messages = [
        msg["content"]
        for msg in ctx.messages
        if isinstance(msg, dict)
        and msg.get("role") == "system"
        and "Tool outcomes (chronological)" in str(msg.get("content") or "")
    ]
    assert tool_messages
    tool_prompt = tool_messages[-1]
    assert "tool_help status=invoked" in tool_prompt
    assert (
        "returned 20 tool(s): computer.session.start, computer.navigate, computer.observe, computer.act"
        in tool_prompt
    )
    assert (
        "do not claim screenshots, browser navigation, image embeds, or picture-in-picture"
        in tool_prompt.lower()
    )
    assert '"tools"' not in tool_prompt
    assert "[[tool_call:" not in " ".join(
        str(msg.get("content") or "") for msg in ctx.messages if isinstance(msg, dict)
    )
