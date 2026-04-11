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
    app.state.pending_tools = {}
    original_cfg_mode = app.state.config.get("mode")
    original_service_mode = getattr(routes.llm_service, "mode", "api")
    app.state.config["mode"] = "api"
    routes.llm_service.mode = "local"

    try:
        client = TestClient(app)
        resp = client.post(
            "/chat/continue",
            json={"session_id": "sess", "message_id": "m1", "tools": []},
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
    assert captured["calls"][-1]["attachments"][0]["content_hash"] == "hash-two"

    rehydrated_context = captured["calls"][-1]["context"]
    rehydrated_user = next(
        entry
        for entry in rehydrated_context.messages
        if isinstance(entry, dict) and entry.get("content") == "describe this image"
    )
    assert rehydrated_user.get("metadata", {}).get("attachments")[0][
        "content_hash"
    ] == ("hash-two")


def test_chat_attachment_only_turn_restores_session_context_after_generate(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

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
                    "url": "/api/attachments/hash-empty/camera.png",
                    "content_hash": "hash-empty",
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
        == "hash-empty"
    )


def test_chat_reuses_recent_image_for_attachment_only_follow_up_after_rehydrate(
    monkeypatch, tmp_path
):
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
            "message": "",
            "session_id": "sess",
            "message_id": "m-empty",
            "use_rag": False,
            "attachments": [
                {
                    "name": "camera.png",
                    "type": "image/png",
                    "url": "/api/attachments/hash-empty/camera.png",
                    "content_hash": "hash-empty",
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
    assert captured["calls"][-1]["attachments"][0]["content_hash"] == "hash-empty"


def test_chat_reuses_recent_image_for_direct_follow_up(monkeypatch, tmp_path):
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
            "message": "what about this?",
            "session_id": "sess",
            "message_id": "m2",
            "use_rag": False,
        },
    )
    assert second.status_code == 200
    assert captured["calls"][-1]["attachments"][0]["content_hash"] == "hash-two"


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
    app.state.memory_manager = DummyMemoryManager()
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
    app.state.memory_manager = DummyMemoryManager()
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
    assert "Computer Use" not in system_text
    assert "Camera Capture" not in system_text
    assert (ctx.metadata.get("workflow") or {}).get("modules") == []


def test_chat_computer_turn_keeps_computer_capture_scope(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

    _pin_default_workflow_settings(monkeypatch, tmp_path)
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
    assert "Computer Use" in system_text
    assert (ctx.metadata.get("workflow") or {}).get("modules") == [
        "camera_capture",
        "computer_use",
        "memory_promotion",
    ]
