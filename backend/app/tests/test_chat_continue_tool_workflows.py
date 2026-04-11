import importlib

from fastapi.testclient import TestClient


def _pin_default_workflow_settings(monkeypatch, tmp_path):
    from app.utils import user_settings

    monkeypatch.setattr(
        user_settings,
        "USER_SETTINGS_PATH",
        tmp_path / "user_settings.json",
        raising=False,
    )
    user_settings.save_settings(
        {"default_workflow": "default", "enabled_workflow_modules": []}
    )


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


def test_chat_continue_missing_mode_defaults_to_configured_api_not_service_mode(
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

    def fake_generate(
        prompt, session_id=None, model=None, attachments=None, context=None, **kwargs
    ):
        captured["metadata"] = kwargs.get("metadata")
        captured["capture_raw_api"] = kwargs.get("capture_raw_api")
        return {"text": "Resolved.", "thought": "", "tools_used": [], "metadata": {}}

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
        "/chat/continue",
        json={
            "session_id": "sess",
            "message_id": "m1",
            "tools": [
                {
                    "id": "tool-1",
                    "name": "help",
                    "args": {},
                    "result": {"message": "ok"},
                    "status": "invoked",
                }
            ],
        },
    )
    assert resp.status_code == 200
    assert captured["metadata"]["mode"] == "api"
    assert captured["capture_raw_api"] is True
    assert routes.llm_service.mode == "local"
    routes.llm_service.mode = original_mode


def test_chat_continue_missing_mode_prefers_saved_message_mode_hint(
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
                "metadata": {"status": "pending", "mode": "local"},
            },
        ],
    )

    from app import routes
    from app.base_services import ModelContext

    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}
    captured = {"resolved": False, "server_url": None}

    def fake_resolve(*, provider, requested_model, allow_auto_start=True):
        captured["resolved"] = True
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
        captured["server_url"] = kwargs.get("server_url")
        return {"text": "Resolved.", "thought": "", "tools_used": [], "metadata": {}}

    monkeypatch.setattr(
        routes.provider_manager,
        "resolve_inference_target",
        fake_resolve,
    )
    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    app.state.pending_tools = {}
    app.state.config["mode"] = "api"
    client = TestClient(app)
    resp = client.post(
        "/chat/continue",
        json={
            "session_id": "sess",
            "message_id": "m1",
            "model": "ollama",
            "tools": [
                {
                    "id": "tool-1",
                    "name": "help",
                    "args": {},
                    "result": {"message": "ok"},
                    "status": "invoked",
                }
            ],
        },
    )
    assert resp.status_code == 200
    assert captured["resolved"] is True
    assert captured["server_url"] == "http://127.0.0.1:11434/v1"


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


def test_chat_continue_local_provider_resolution_error_updates_message(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    conv_store.save_conversation(
        "sess",
        [
            {"id": "m1:user", "role": "user", "text": "hello"},
            {"id": "m1", "role": "ai", "text": "", "metadata": {"status": "pending"}},
        ],
    )

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
        "/chat/continue",
        json={
            "session_id": "sess",
            "message_id": "m1",
            "mode": "local",
            "model": "lmstudio",
            "tools": [],
        },
    )

    assert resp.status_code == 409
    assert "No model is loaded for lmstudio" in resp.json()["detail"]

    messages = conv_store.load_conversation("sess")
    ai = next(m for m in messages if m.get("id") == "m1")
    metadata = ai.get("metadata") or {}
    assert metadata.get("status") == "error"
    assert metadata.get("status_code") == 409
    assert metadata.get("category") == "provider_resolution_error"
    assert "No model is loaded for lmstudio" in (metadata.get("error") or "")
    assert "No model is loaded for lmstudio" in (ai.get("text") or "")

    registry = getattr(client.app.state, "pending_tools", {})
    assert registry == {}


def test_chat_continue_local_provider_target_skips_reasoning_controls(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    conv_store.save_conversation(
        "sess",
        [
            {"id": "m1:user", "role": "user", "text": "remember this"},
            {"id": "m1", "role": "ai", "text": "Requested tool remember."},
        ],
    )

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
        prompt,
        session_id=None,
        model=None,
        attachments=None,
        context=None,
        **kwargs,
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
        "/chat/continue",
        json={
            "session_id": "sess",
            "message_id": "m1",
            "mode": "local",
            "model": "ollama",
            "thinking": "high",
            "tools": [
                {
                    "id": "tool-1",
                    "name": "remember",
                    "args": {"value": "temporary key"},
                    "result": {"status": "invoked", "ok": True},
                    "status": "invoked",
                }
            ],
        },
    )

    assert resp.status_code == 200
    assert captured["model"] == "gemma4:e4b"
    assert captured["reasoning"] is None
    metadata = resp.json().get("metadata") or {}
    assert metadata.get("model") == "gemma4:e4b"
    assert metadata.get("model_requested") == "ollama"
    assert metadata.get("model_resolved") == "gemma4:e4b"


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


def test_chat_continue_includes_structured_tool_outcome_prompt(monkeypatch, tmp_path):
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
                        "detail": "brief",
                        "include_schema": False,
                        "max_tools": 20,
                    },
                    "result": {
                        "status": "invoked",
                        "ok": True,
                        "data": {
                            "count": 20,
                            "total_count": 34,
                            "tools": [
                                "help",
                                "tool_help",
                                "tool_info",
                                "list_actions",
                                "read_action_diff",
                                "revert_actions",
                                "crawl",
                                "search_web",
                                "open_url",
                                "computer.session.start",
                                "read_file",
                                "list_dir",
                                "write_file",
                                "generate_threads",
                                "read_threads_summary",
                                "create_event",
                                "create_task",
                                "memory.save",
                                "remember",
                                "recall",
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
    scoped_messages = [
        msg
        for msg in ctx.messages
        if isinstance(msg, dict)
        and msg.get("role") == "system"
        and (msg.get("metadata") or {}).get("turn_message_key") == "tool_results"
    ]
    assert scoped_messages
    tool_messages = [
        msg["content"]
        for msg in ctx.messages
        if isinstance(msg, dict)
        and msg.get("role") == "system"
        and (msg.get("metadata") or {}).get("turn_message_key") == "tool_results"
    ]
    assert tool_messages
    tool_prompt = tool_messages[-1]
    assert tool_prompt.startswith("Use these tool results to continue your response.")
    assert "Summary:" in tool_prompt
    assert "Tool result data:" in tool_prompt
    assert '"write_file"' in tool_prompt
    assert '"total_count": 34' in tool_prompt
    assert "picture-in-picture" not in tool_prompt.lower()
    assert "[[tool_call:" not in " ".join(
        str(msg.get("content") or "") for msg in ctx.messages if isinstance(msg, dict)
    )


def test_chat_continue_strips_inline_tool_markers_from_saved_reply(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    conv_store.save_conversation(
        "sess",
        [
            {"id": "m1:user", "role": "user", "text": "hello"},
            {"id": "m1", "role": "ai", "text": "Checking memory now.[[tool_call:0]]"},
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
            "text": "I checked memory and found one note.",
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
                    "name": "recall",
                    "args": {"key": "user_recall_difficulty"},
                    "result": {
                        "status": "invoked",
                        "ok": True,
                        "data": {"text": "Sometimes recall is difficult."},
                    },
                    "status": "invoked",
                }
            ],
        },
    )
    assert resp.status_code == 200
    payload = resp.json()
    message = payload.get("message") or ""
    assert "[[tool_call:" not in message
    assert "I checked memory and found one note." in message

    saved = conv_store.load_conversation("sess")
    saved_entry = next(
        item for item in saved if isinstance(item, dict) and item.get("id") == "m1"
    )
    saved_message = saved_entry.get("text")
    assert "Checking memory now." in str(saved_message or "")
    assert "I checked memory and found one note." in str(saved_message or "")
    assert "[[tool_call:" not in str(saved_message or "")
    metadata = saved_entry.get("metadata") if isinstance(saved_entry, dict) else {}
    assert isinstance(metadata, dict)
    assert metadata.get("tool_response_pending") is None
    assert metadata.get("tool_continued") is True


def test_chat_continue_uses_history_only_through_target_message(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    conv_store.save_conversation(
        "sess",
        [
            {"id": "m1:user", "role": "user", "text": "first request"},
            {"id": "m1", "role": "ai", "text": "Checking tools.[[tool_call:0]]"},
            {"id": "m2:user", "role": "user", "text": "later request"},
            {"id": "m2", "role": "ai", "text": "Later response placeholder."},
        ],
    )

    from app import routes
    from app.base_services import ModelContext

    routes.llm_service.contexts = {
        "default": ModelContext(system_prompt=""),
        "sess": ModelContext(
            system_prompt="",
            messages=[
                {"role": "user", "content": "first request"},
                {"role": "assistant", "content": "Checking tools."},
                {"role": "user", "content": "later request"},
            ],
        ),
    }
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
            "text": "Finished the first request.",
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
                    "name": "tool_info",
                    "args": {"tool_name": "write_file"},
                    "result": {"message": "ok"},
                    "status": "invoked",
                }
            ],
        },
    )
    assert resp.status_code == 200

    ctx = captured.get("context")
    assert ctx is not None
    ctx_text = " ".join(
        str(msg.get("content") or "") for msg in ctx.messages if isinstance(msg, dict)
    )
    assert "first request" in ctx_text
    assert "later request" not in ctx_text

    saved = conv_store.load_conversation("sess")
    first_ai = next(
        item for item in saved if isinstance(item, dict) and item.get("id") == "m1"
    )
    later_user = next(
        item for item in saved if isinstance(item, dict) and item.get("id") == "m2:user"
    )
    assert "Finished the first request." in str(first_ai.get("text") or "")
    assert later_user.get("text") == "later request"


def test_chat_continue_text_turn_filters_computer_capture_scope(monkeypatch, tmp_path):
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

    _pin_default_workflow_settings(monkeypatch, tmp_path)
    tool_defs = [
        {"name": "help", "description": "help", "parameters": {}},
        {"name": "recall", "description": "recall", "parameters": {}},
        {"name": "open_url", "description": "open", "parameters": {}},
        {"name": "computer.observe", "description": "observe", "parameters": {}},
        {"name": "camera.capture", "description": "capture", "parameters": {}},
    ]
    routes.llm_service.contexts = {
        "default": ModelContext(system_prompt=""),
        "sess": ModelContext(system_prompt="", tools=tool_defs),
    }
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
            "text": "Resolved.",
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
                    "args": {"key": "tea"},
                    "result": {"message": "found tea"},
                    "status": "invoked",
                }
            ],
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
    assert tool_names == ["help", "recall"]
    assert (
        "Do not propose or call open_url, computer.*, camera.capture, or capture.*"
        not in (ctx.system_prompt)
    )
    scope_messages = [
        msg
        for msg in ctx.messages
        if isinstance(msg, dict)
        and msg.get("role") == "system"
        and (msg.get("metadata") or {}).get("turn_message_key") == "turn_scope"
    ]
    assert scope_messages
    assert (
        "Do not propose or call open_url, computer.*, camera.capture, or capture.*"
        in str(scope_messages[-1].get("content") or "")
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
    assert (ctx.metadata.get("workflow") or {}).get("modules") == []
    continuation_messages = [
        msg
        for msg in ctx.messages
        if isinstance(msg, dict)
        and msg.get("role") == "system"
        and (msg.get("metadata") or {}).get("turn_message_key") == "continuation"
    ]
    assert continuation_messages
    assert "Continue your response to the user's last message." in str(
        continuation_messages[-1].get("content") or ""
    )
    assert not any(
        isinstance(msg, dict)
        and msg.get("role") == "user"
        and (msg.get("metadata") or {}).get("continuation")
        for msg in ctx.messages
    )


def test_chat_continue_computer_results_keep_computer_capture_scope(
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
                "text": "Requested tools computer.session.start and computer.observe.",
            },
        ],
    )

    from app import routes
    from app.base_services import ModelContext

    _pin_default_workflow_settings(monkeypatch, tmp_path)
    tool_defs = [
        {"name": "help", "description": "help", "parameters": {}},
        {"name": "open_url", "description": "open", "parameters": {}},
        {"name": "computer.observe", "description": "observe", "parameters": {}},
        {"name": "camera.capture", "description": "capture", "parameters": {}},
    ]
    routes.llm_service.contexts = {
        "default": ModelContext(system_prompt=""),
        "sess": ModelContext(system_prompt="", tools=tool_defs),
    }
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
            "text": "Resolved.",
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
                    "name": "computer.observe",
                    "args": {"session_id": "desktop"},
                    "result": {"message": "Captured desktop view."},
                    "status": "invoked",
                }
            ],
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
    assert (
        "browser, desktop, or capture tools are in scope"
        not in ctx.system_prompt.lower()
    )
    scope_messages = [
        msg
        for msg in ctx.messages
        if isinstance(msg, dict)
        and msg.get("role") == "system"
        and (msg.get("metadata") or {}).get("turn_message_key") == "turn_scope"
    ]
    assert scope_messages
    assert "Browser, desktop, and capture tools are in scope for this turn." in str(
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
    assert (ctx.metadata.get("workflow") or {}).get("modules") == [
        "camera_capture",
        "computer_use",
        "memory_promotion",
    ]


def test_turn_system_messages_dedupe_by_key():
    from app import routes
    from app.base_services import ModelContext

    ctx = ModelContext(system_prompt="", messages=[], tools=[], metadata={})

    first = routes._add_turn_system_message(
        ctx,
        "turn_scope",
        "Treat this as a normal text or knowledge turn.",
        metadata={"turn_scope": True},
    )
    second = routes._add_turn_system_message(
        ctx,
        "turn_scope",
        "Treat this as a normal text or knowledge turn.",
        metadata={"turn_scope": True},
    )

    assert first is True
    assert second is False
    assert [
        msg
        for msg in ctx.messages
        if isinstance(msg, dict) and msg.get("role") == "system"
    ] == [
        {
            "role": "system",
            "content": "Treat this as a normal text or knowledge turn.",
            "metadata": {
                "turn_scope": True,
                "ephemeral": True,
                "turn_message_key": "turn_scope",
            },
        }
    ]


def test_effective_system_prompt_appends_json_tool_call_hint():
    from app import routes

    prompt = routes._effective_system_prompt(
        "Base prompt.",
        response_format=None,
    )

    assert (
        'Tool call syntax for this turn: emit direct JSON only in the form {"tool":"tool_help","args":{}}.'
        in prompt
    )
    assert "<|channel|>commentary to=tool_help" not in prompt


def test_effective_system_prompt_appends_harmony_tool_call_hint():
    from app import routes

    prompt = routes._effective_system_prompt(
        "Base prompt.",
        response_format="harmony",
    )

    assert (
        "Tool call syntax for this turn: emit Harmony tool calls only in the form "
        "<|channel|>commentary to=tool_help <|constrain|>json <|message|>{}."
    ) in prompt
    assert '{"tool":"tool_help","args":{}}' not in prompt


def test_effective_system_prompt_dedupes_existing_tool_call_hints():
    from app import routes

    base = (
        "Base prompt.\n\n"
        'Tool call syntax for this turn: emit direct JSON only in the form {"tool":"tool_help","args":{}}. '
        "Use exact tool identifiers and valid JSON only. "
        "Do not wrap JSON calls in Harmony markers.\n\n"
        "Tool call syntax for this turn: emit Harmony tool calls only in the form "
        "<|channel|>commentary to=tool_help <|constrain|>json <|message|>{}. "
        "Use exact tool identifiers and valid JSON in the message body only. "
        "Do not prepend standalone JSON tool calls outside the Harmony wrapper."
    )

    prompt = routes._effective_system_prompt(
        base,
        response_format=None,
    )

    assert prompt.count("Tool call syntax for this turn:") == 1
    assert '{"tool":"tool_help","args":{}}' in prompt
    assert "<|channel|>commentary to=tool_help" not in prompt


def test_route_response_format_uses_harmony_only_for_gpt_oss():
    from app import routes

    assert (
        routes._resolve_route_response_format(
            None,
            harmony_enabled=True,
            model_name="openai/gpt-oss-20b",
        )
        == "harmony"
    )
    assert (
        routes._resolve_route_response_format(
            None,
            harmony_enabled=True,
            model_name="gpt-5.4",
        )
        is None
    )
    assert (
        routes._resolve_route_response_format(
            "harmony",
            harmony_enabled=True,
            model_name="gpt-5.4",
        )
        is None
    )


def test_chat_continue_api_forwards_openai_metadata(monkeypatch, tmp_path):
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
    from app.utils import user_settings

    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}
    monkeypatch.setattr(
        user_settings,
        "USER_SETTINGS_PATH",
        tmp_path / "user_settings.json",
        raising=False,
    )
    user_settings.save_settings({"approval_level": "all"})
    captured = {}

    def fake_generate(
        prompt, session_id=None, model=None, attachments=None, context=None, **kwargs
    ):
        captured["metadata"] = kwargs.get("metadata")
        captured["capture_raw_api"] = kwargs.get("capture_raw_api")
        return {
            "text": "Resolved.",
            "thought": "",
            "tools_used": [],
            "metadata": {
                "response_id": "resp_continue",
                "previous_response_id": "resp_before",
                "output_ids": ["out_continue"],
            },
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
            "mode": "api",
            "tools": [
                {
                    "id": "tool-1",
                    "name": "help",
                    "args": {},
                    "result": {"message": "ok"},
                    "status": "invoked",
                }
            ],
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

    updated = conv_store.load_conversation("sess")
    ai = next(m for m in updated if m.get("id") == "m1")
    metadata = ai.get("metadata") or {}
    assert metadata.get("response_id") == "resp_continue"
    assert metadata.get("previous_response_id") == "resp_before"
    assert metadata.get("output_ids") == ["out_continue"]
    assert metadata.get("message_id") == "m1"
