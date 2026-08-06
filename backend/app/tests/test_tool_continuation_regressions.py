import asyncio
import importlib

import httpx
from fastapi.testclient import TestClient


def _prepare_chat_runtime(monkeypatch, tmp_path, messages, *, scope_messages=True):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext
    from app.main import app
    from app.utils import user_settings
    from app.workflow_scope import build_capability_scope

    monkeypatch.setattr(
        user_settings,
        "USER_SETTINGS_PATH",
        tmp_path / "user_settings.json",
        raising=False,
    )
    user_settings.save_settings(
        {
            "approval_level": "all",
            "default_workflow": "default",
            "enabled_workflow_modules": [],
        }
    )
    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}
    app.state.pending_tools = {}
    app.state.agent_console_state = {"agents": {}, "resources": {}}
    saved_messages = [dict(message) for message in messages]
    receipts = {}
    if scope_messages:
        scope = build_capability_scope(
            workflow="default",
            channel="text",
            modules=[],
            tool_definitions=routes._registered_prompt_tool_definitions(
                app,
                allow_computer_capture=True,
            ),
        )
        for message in saved_messages:
            if message.get("role") not in {"ai", "assistant"} or not message.get("id"):
                continue
            metadata = dict(message.get("metadata") or {})
            metadata.setdefault("capability_scope", scope)
            message["metadata"] = metadata
            receipts[message["id"]] = {
                "continuation_trust": "server",
                "capability_scope": metadata["capability_scope"],
            }
    conv_store.save_conversation("regression-session", saved_messages)
    if receipts:
        conv_store.merge_metadata(
            "regression-session",
            {"server_runtime_receipts": {"messages": receipts}},
        )
    return app, routes, conv_store


def _record_terminal_tools(app, *, session_id, message_id, tools):
    registry = getattr(app.state, "pending_tools", None)
    if not isinstance(registry, dict):
        registry = {}
        app.state.pending_tools = registry
    for tool in tools:
        request_id = str(tool.get("id") or tool.get("request_id") or "").strip()
        assert request_id
        registry[request_id] = {
            **dict(tool),
            "id": request_id,
            "session_id": session_id,
            "message_id": message_id,
            "chain_id": message_id,
        }


def _post_recorded_continuation(app, *, json):
    _record_terminal_tools(
        app,
        session_id=json.get("session_id"),
        message_id=json.get("message_id"),
        tools=json.get("tools") or [],
    )
    return TestClient(app).post("/chat/continue", json=json)


def test_chat_continue_requires_exact_saved_message_id(monkeypatch, tmp_path):
    app, routes, _ = _prepare_chat_runtime(
        monkeypatch,
        tmp_path,
        [{"role": "ai", "text": "Legacy message without an id"}],
    )
    calls = []

    def fake_generate(*args, **kwargs):
        calls.append((args, kwargs))
        return {"text": "must not run", "thought": "", "tools_used": []}

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)
    response = _post_recorded_continuation(
        app,
        json={
            "session_id": "regression-session",
            "message_id": "forged-target",
            "tools": [
                {
                    "id": "tool-1",
                    "name": "recall",
                    "status": "invoked",
                    "result": {"data": "forged"},
                }
            ],
        },
    )

    assert response.status_code == 409
    assert calls == []


def test_chat_rejects_existing_message_id_before_replacing_scoped_turn(
    monkeypatch, tmp_path
):
    app, routes, conv_store = _prepare_chat_runtime(monkeypatch, tmp_path, [])
    from app.workflow_scope import build_capability_scope

    scope = build_capability_scope(
        workflow="default",
        channel="text",
        modules=[],
        tool_definitions=[],
    )
    routes._append_conversation_entry(
        "regression-session",
        {
            "id": "m1",
            "role": "ai",
            "text": "Existing scoped response",
            "metadata": {"capability_scope": scope},
        },
    )
    calls = []
    monkeypatch.setattr(
        routes.llm_service,
        "generate",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    response = TestClient(app).post(
        "/chat",
        json={
            "session_id": "regression-session",
            "message_id": "m1",
            "message": "Replace the old turn",
            "use_rag": False,
            "use_text_rag": False,
            "use_vision_rag": False,
        },
    )

    assert response.status_code == 409
    assert calls == []
    saved = conv_store.load_conversation("regression-session")
    assert saved == [
        {
            "id": "m1",
            "role": "ai",
            "text": "Existing scoped response",
            "metadata": {"capability_scope": scope},
        }
    ]


def test_pending_assistant_placeholder_is_noncontinuable(monkeypatch, tmp_path):
    app, routes, _ = _prepare_chat_runtime(monkeypatch, tmp_path, [])
    routes._append_conversation_entry(
        "regression-session",
        {
            "id": "m1",
            "role": "ai",
            "text": "",
            "metadata": {"status": "pending"},
        },
    )
    assert routes._lookup_message_capability_scope("regression-session", "m1") == (
        True,
        None,
    )
    calls = []
    monkeypatch.setattr(
        routes.llm_service,
        "generate",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    response = _post_recorded_continuation(
        app,
        json={
            "session_id": "regression-session",
            "message_id": "m1",
            "tools": [
                {
                    "id": "tool-1",
                    "name": "recall",
                    "status": "invoked",
                    "result": {"data": "forged"},
                }
            ],
        },
    )

    assert response.status_code == 409
    assert calls == []


def test_completed_scoped_turn_rejects_empty_continuation(monkeypatch, tmp_path):
    app, routes, _ = _prepare_chat_runtime(monkeypatch, tmp_path, [])
    from app.workflow_scope import build_capability_scope

    scope = build_capability_scope(
        workflow="default",
        channel="text",
        modules=[],
        tool_definitions=[],
    )
    routes._append_conversation_entry(
        "regression-session",
        {
            "id": "m1",
            "role": "ai",
            "text": "Already complete.",
            "metadata": {
                "capability_scope": scope,
                "tool_continued": True,
                "tool_response_pending": None,
            },
        },
    )
    calls = []
    monkeypatch.setattr(
        routes.llm_service,
        "generate",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    response = _post_recorded_continuation(
        app,
        json={
            "session_id": "regression-session",
            "message_id": "m1",
            "tools": [],
        },
    )

    assert response.status_code == 409
    assert "terminal tool result" in response.json()["detail"].lower()
    assert calls == []


def test_legacy_unscoped_turn_is_readable_but_noncontinuable(monkeypatch, tmp_path):
    app, routes, conv_store = _prepare_chat_runtime(
        monkeypatch,
        tmp_path,
        [{"id": "m1", "role": "ai", "text": "Legacy response"}],
        scope_messages=False,
    )
    app.state.pending_tools = {
        "tool-1": {
            "id": "tool-1",
            "name": "recall",
            "args": {"query": "tea"},
            "result": {"data": "oolong"},
            "status": "invoked",
            "session_id": "regression-session",
            "message_id": "m1",
            "chain_id": "m1",
        }
    }
    calls = []
    monkeypatch.setattr(
        routes.llm_service,
        "generate",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    response = _post_recorded_continuation(
        app,
        json={
            "session_id": "regression-session",
            "message_id": "m1",
            "tools": [
                {
                    "id": "tool-1",
                    "name": "recall",
                    "status": "invoked",
                    "result": {"data": "oolong"},
                }
            ],
        },
    )

    assert (
        conv_store.load_conversation("regression-session")[0]["text"]
        == "Legacy response"
    )
    assert response.status_code == 409
    assert "regenerate" in response.json()["detail"].lower()
    assert calls == []


def test_client_saved_tool_rows_do_not_suppress_server_proposals(monkeypatch, tmp_path):
    app, routes, conv_store = _prepare_chat_runtime(monkeypatch, tmp_path, [])
    tool = {
        "id": "client-tool",
        "name": "recall",
        "args": {"query": "tea"},
        "status": "invoked",
        "client_saved_untrusted": True,
    }
    conv_store.save_conversation(
        "regression-session",
        [{"id": "m1", "role": "ai", "text": "Saved", "tools": [tool]}],
    )

    assert (
        routes._existing_tool_signatures_for_message(app, "regression-session", "m1")
        == set()
    )

    tool.pop("client_saved_untrusted")
    tool["server_recorded"] = True
    conv_store.save_conversation(
        "regression-session",
        [{"id": "m1", "role": "ai", "text": "Saved", "tools": [tool]}],
    )
    assert routes._tool_signature("recall", {"query": "tea"}) in (
        routes._existing_tool_signatures_for_message(app, "regression-session", "m1")
    )


def test_concurrent_chat_requests_cannot_claim_same_message_id(monkeypatch, tmp_path):
    app, routes, conv_store = _prepare_chat_runtime(monkeypatch, tmp_path, [])
    generation_count = 0

    def fake_generate(*args, **kwargs):
        nonlocal generation_count
        generation_count += 1
        return {
            "text": "One response",
            "thought": "",
            "tools_used": [],
            "metadata": {},
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)
    payload = {
        "session_id": "regression-session",
        "message_id": "shared-message",
        "message": "Only run this turn once.",
        "mode": "api",
        "model": "gpt-5.6-sol",
        "use_rag": False,
        "use_text_rag": False,
        "use_vision_rag": False,
    }

    async def send_both():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await asyncio.gather(
                client.post("/chat", json=payload),
                client.post("/chat", json=payload),
            )

    responses = asyncio.run(send_both())

    assert sorted(response.status_code for response in responses) == [200, 409]
    assert generation_count == 1
    saved = conv_store.load_conversation("regression-session")
    assert [entry["id"] for entry in saved] == [
        "shared-message:user",
        "shared-message",
    ]


def test_continuation_signatures_canonicalize_aliases_and_add_sha256():
    from app import routes

    legacy = [
        {
            "id": "tool-1",
            "name": "memory.read",
            "status": "invoked",
            "args": {"key": "tea"},
            "result": {"data": "oolong"},
        }
    ]
    canonical = [{**legacy[0], "name": "recall"}]

    assert routes._tool_continue_signature(legacy) == routes._tool_continue_signature(
        canonical
    )
    secure = routes._tool_continue_sha256_signature(legacy)
    assert secure == routes._tool_continue_sha256_signature(canonical)
    assert secure.startswith("sha256:")
    assert len(secure) == len("sha256:") + 64


def test_completed_replay_accepts_legacy_alias_signature_after_upgrade(
    monkeypatch, tmp_path
):
    _, routes, conv_store = _prepare_chat_runtime(monkeypatch, tmp_path, [])
    event = {
        "id": "tool-1",
        "name": "memory.read",
        "status": "invoked",
        "args": {"key": "tea"},
        "result": {"data": "oolong"},
    }
    legacy_payload = routes._normalized_tool_continue_events(
        [event], canonical_names=False
    )
    legacy_signature = routes._fnv1a_signature(
        routes._stable_tool_continue_json(legacy_payload)
    )
    conv_store.save_conversation(
        "regression-session",
        [
            {
                "id": "m1",
                "role": "ai",
                "text": "The tea is oolong.",
                "metadata": {
                    "tool_continued": True,
                    "tool_response_pending": None,
                    "tool_continue_signature": legacy_signature,
                },
            }
        ],
    )

    replay = routes._completed_tool_continuation_replay(
        session_id="regression-session",
        message_id="m1",
        tool_events=[{**event, "name": "recall"}],
        context=routes.llm_service.get_context("regression-session"),
    )

    assert replay is not None
    assert replay.message == "The tea is oolong."


def test_completed_sha256_replay_does_not_accept_different_request_id(
    monkeypatch, tmp_path
):
    _, routes, conv_store = _prepare_chat_runtime(monkeypatch, tmp_path, [])
    event = {
        "id": "tool-1",
        "name": "recall",
        "status": "invoked",
        "args": {"key": "tea"},
        "result": {"data": "oolong"},
    }
    conv_store.save_conversation(
        "regression-session",
        [
            {
                "id": "m1",
                "role": "ai",
                "text": "The tea is oolong.",
                "metadata": {
                    "tool_continued": True,
                    "tool_response_pending": None,
                    "tool_continue_signature_sha256": routes._tool_continue_sha256_signature(
                        [event]
                    ),
                },
            }
        ],
    )

    replay = routes._completed_tool_continuation_replay(
        session_id="regression-session",
        message_id="m1",
        tool_events=[{**event, "id": "tool-2"}],
        context=routes.llm_service.get_context("regression-session"),
    )

    assert replay is None


def test_api_chat_passes_registered_native_tools_without_provider_executor(
    monkeypatch, tmp_path
):
    app, routes, _ = _prepare_chat_runtime(monkeypatch, tmp_path, [])
    native_tool = {
        "name": "tool_info",
        "description": "Inspect one live Float tool definition.",
        "parameters": {
            "type": "object",
            "properties": {"tool_name": {"type": "string"}},
            "required": ["tool_name"],
        },
    }
    calls = []

    monkeypatch.setattr(
        routes,
        "_registered_prompt_tool_definitions",
        lambda *args, **kwargs: [native_tool],
    )

    def fake_generate(prompt, context=None, **kwargs):
        calls.append({"context": context, "kwargs": dict(kwargs)})
        return {
            "text": "Checked the live tool definition.",
            "thought": "",
            "tools_used": [],
            "metadata": {},
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    response = TestClient(app).post(
        "/chat",
        json={
            "session_id": "regression-session",
            "message_id": "assistant-api-1",
            "message": "Check the live read_file tool documentation.",
            "mode": "api",
            "model": "gpt-5.6-sol",
            "use_rag": False,
            "use_text_rag": False,
            "use_vision_rag": False,
        },
    )

    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0]["kwargs"]["native_tool_definitions"] == [native_tool]
    assert "tool_executor" not in calls[0]["kwargs"]
    system_prompt = calls[0]["context"].system_prompt
    assert "**available tools this turn (brief):" not in system_prompt
    assert "Tool call syntax for this turn:" not in system_prompt
    assert '{"tool":"<exact_tool_name>"' not in system_prompt
    assert "<|channel|>commentary to=" not in system_prompt


def test_provider_native_tool_names_are_valid_and_reversible():
    from app import routes

    definitions = [
        {
            "name": "read_file",
            "description": "Read a file.",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": "memory.save",
            "description": "Save a durable memory record.",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": "memory.read",
            "description": "Compatibility alias for recall.",
            "parameters": {"type": "object", "properties": {}},
        },
    ]

    prepared, canonical_by_provider = routes._provider_native_tool_definitions(
        definitions
    )

    assert prepared[0] == definitions[0]
    provider_name = prepared[1]["name"]
    assert provider_name == "memory_save"
    assert routes._PROVIDER_FUNCTION_NAME_RE.fullmatch(provider_name)
    assert len(provider_name) <= routes._PROVIDER_FUNCTION_NAME_MAX_LENGTH
    assert canonical_by_provider == {provider_name: "memory.save"}
    assert prepared[1]["description"] == definitions[1]["description"]
    assert prepared[2]["name"] == "recall"
    assert prepared[2]["name"] != definitions[2]["name"]


def test_provider_native_tool_names_hash_only_on_readable_slug_collision():
    from app import routes

    definitions = [
        {
            "name": "graph.update",
            "description": "Update the graph through the canonical dotted handle.",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": "graph_update",
            "description": "A distinct tool already using the readable safe name.",
            "parameters": {"type": "object", "properties": {}},
        },
    ]

    prepared, canonical_by_provider = routes._provider_native_tool_definitions(
        definitions
    )

    dotted_provider_name = prepared[0]["name"]
    assert dotted_provider_name.startswith("graph_update_")
    assert dotted_provider_name != "graph_update"
    assert prepared[1]["name"] == "graph_update"
    assert canonical_by_provider == {dotted_provider_name: "graph.update"}


def test_model_catalog_hides_compatibility_aliases_without_duplicate_entries():
    from app import routes
    from app.tool_specs import get_tool_specs
    from app.tools import BUILTIN_TOOLS

    advertised = routes._merge_prompt_tool_definitions(
        get_tool_specs(list(BUILTIN_TOOLS))
    )
    names = [routes._tool_definition_name(tool) for tool in advertised]
    descriptions = [tool["description"] for tool in advertised]
    provider_tools, canonical_by_provider = routes._provider_native_tool_definitions(
        advertised
    )
    provider_names = [routes._tool_definition_name(tool) for tool in provider_tools]
    provider_descriptions = [tool["description"] for tool in provider_tools]

    assert set(routes._UNADVERTISED_MODEL_TOOL_ALIASES).isdisjoint(names)
    assert {"remember", "help", "create_task", "computer.navigate"}.issubset(names)
    assert len(names) == len(set(names))
    assert len(descriptions) == len(set(descriptions))
    assert len(provider_names) == len(set(provider_names))
    assert len(provider_descriptions) == len(set(provider_descriptions))
    assert canonical_by_provider["computer_navigate"] == "computer.navigate"


def test_api_chat_restores_dotted_tool_name_after_provider_call(monkeypatch, tmp_path):
    app, routes, _ = _prepare_chat_runtime(monkeypatch, tmp_path, [])
    native_tool = {
        "name": "graph.update",
        "description": "Update a structured knowledge graph.",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "value": {"type": "string"},
            },
            "required": ["key", "value"],
        },
    }
    calls = []

    monkeypatch.setattr(
        routes,
        "_registered_prompt_tool_definitions",
        lambda *args, **kwargs: [native_tool],
    )

    def fake_generate(prompt, context=None, **kwargs):
        calls.append({"context": context, "kwargs": dict(kwargs)})
        provider_name = kwargs["native_tool_definitions"][0]["name"]
        return {
            "text": "[[tool_call:0]]",
            "thought": "",
            "tools_used": [
                {
                    "name": provider_name,
                    "args": {"key": "transport-test", "value": "safe"},
                }
            ],
            "metadata": {},
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    response = TestClient(app).post(
        "/chat",
        json={
            "session_id": "regression-session",
            "message_id": "assistant-api-dotted-1",
            "message": "Save a structured memory record.",
            "mode": "api",
            "model": "gpt-5.6-sol",
            "use_rag": False,
            "use_text_rag": False,
            "use_vision_rag": False,
        },
    )

    assert response.status_code == 200
    provider_name = calls[0]["kwargs"]["native_tool_definitions"][0]["name"]
    assert provider_name == "graph_update"
    assert routes._PROVIDER_FUNCTION_NAME_RE.fullmatch(provider_name)
    assert response.json()["tools_used"][0]["name"] == "graph.update"


def test_force_tool_review_prevents_initial_auto_decision(monkeypatch, tmp_path):
    app, routes, _ = _prepare_chat_runtime(monkeypatch, tmp_path, [])
    from app.utils import user_settings

    user_settings.save_settings(
        {
            "approval_level": "auto",
            "default_workflow": "default",
            "enabled_workflow_modules": [],
        }
    )
    decision_calls = []

    async def fake_decide(*args, **kwargs):
        decision_calls.append((args, kwargs))
        return {"status": "invoked", "result": {"ok": True}}

    monkeypatch.setattr(routes, "decide_tool", fake_decide)

    def fake_generate(prompt, context=None, **kwargs):
        assert "tool_executor" not in kwargs
        return {
            "text": "[[tool_call:0]]",
            "thought": "",
            "tools_used": [
                {
                    "name": "read_file",
                    "args": {"path": "workspace/note.txt"},
                }
            ],
            "metadata": {},
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    response = TestClient(app).post(
        "/chat",
        json={
            "session_id": "regression-session",
            "message_id": "assistant-force-review-1",
            "message": "Read workspace/note.txt.",
            "mode": "api",
            "model": "gpt-5.6-sol",
            "force_tool_review": True,
            "use_rag": False,
            "use_text_rag": False,
            "use_vision_rag": False,
        },
    )

    assert response.status_code == 200
    proposal = response.json()["tools_used"][0]
    assert proposal["status"] == "proposed"
    assert proposal.get("approval") != "auto"
    assert decision_calls == []
    assert app.state.pending_tools[proposal["id"]]["server_auto_decide"] is False


def test_api_chat_continue_keeps_native_tools_without_provider_executor(
    monkeypatch, tmp_path
):
    app, routes, _ = _prepare_chat_runtime(
        monkeypatch,
        tmp_path,
        [
            {"id": "user-1", "role": "user", "text": "Inspect read_file."},
            {
                "id": "assistant-1",
                "role": "ai",
                "text": "Requested tool_info.",
            },
        ],
    )
    native_tool = {
        "name": "read_file",
        "description": "Read a workspace file.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    }
    calls = []

    monkeypatch.setattr(
        routes,
        "_registered_prompt_tool_definitions",
        lambda *args, **kwargs: [native_tool],
    )

    def fake_generate(prompt, context=None, **kwargs):
        calls.append({"context": context, "kwargs": dict(kwargs)})
        return {
            "text": "The required input is path.",
            "thought": "",
            "tools_used": [],
            "metadata": {},
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    response = _post_recorded_continuation(
        app,
        json={
            "session_id": "regression-session",
            "message_id": "assistant-1",
            "mode": "api",
            "model": "gpt-5.6-sol",
            "tools": [
                {
                    "id": "tool-1",
                    "name": "tool_info",
                    "args": {"tool_name": "read_file"},
                    "result": {
                        "status": "invoked",
                        "ok": True,
                        "data": {"required": ["path"]},
                    },
                    "status": "invoked",
                }
            ],
        },
    )

    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0]["kwargs"]["native_tool_definitions"] == [native_tool]
    assert "tool_executor" not in calls[0]["kwargs"]
    system_prompt = calls[0]["context"].system_prompt
    assert "**available tools this turn (brief):" not in system_prompt
    assert "Tool call syntax for this turn:" not in system_prompt
    assert '{"tool":"<exact_tool_name>"' not in system_prompt
    assert "<|channel|>commentary to=" not in system_prompt


def test_force_tool_review_prevents_continuation_auto_decision(monkeypatch, tmp_path):
    app, routes, _ = _prepare_chat_runtime(
        monkeypatch,
        tmp_path,
        [
            {"id": "user-1", "role": "user", "text": "Read then save a note."},
            {"id": "assistant-1", "role": "ai", "text": "Read the source note."},
        ],
    )
    from app.utils import user_settings

    user_settings.save_settings(
        {
            "approval_level": "auto",
            "default_workflow": "default",
            "enabled_workflow_modules": [],
        }
    )
    decision_calls = []

    async def fake_decide(*args, **kwargs):
        decision_calls.append((args, kwargs))
        return {"status": "invoked", "result": {"ok": True}}

    monkeypatch.setattr(routes, "decide_tool", fake_decide)

    def fake_generate(prompt, context=None, **kwargs):
        assert "tool_executor" not in kwargs
        return {
            "text": "[[tool_call:0]]",
            "thought": "",
            "tools_used": [
                {
                    "name": "write_file",
                    "args": {
                        "path": "workspace/note-copy.txt",
                        "content": "copied note",
                    },
                }
            ],
            "metadata": {},
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    response = _post_recorded_continuation(
        app,
        json={
            "session_id": "regression-session",
            "message_id": "assistant-1",
            "mode": "api",
            "model": "gpt-5.6-sol",
            "force_tool_review": True,
            "tools": [
                {
                    "id": "tool-read-1",
                    "name": "read_file",
                    "args": {"path": "workspace/note.txt"},
                    "result": {
                        "status": "invoked",
                        "ok": True,
                        "data": {"text": "copied note"},
                    },
                    "status": "invoked",
                }
            ],
        },
    )

    assert response.status_code == 200
    proposal = response.json()["tools_used"][0]
    assert proposal["name"] == "write_file"
    assert proposal["status"] == "proposed"
    assert proposal.get("approval") != "auto"
    assert decision_calls == []
    assert app.state.pending_tools[proposal["id"]]["server_auto_decide"] is False


def test_post_discovery_retry_keeps_preferred_provider_tool_name(monkeypatch, tmp_path):
    app, routes, _ = _prepare_chat_runtime(
        monkeypatch,
        tmp_path,
        [
            {
                "id": "assistant-1:user",
                "role": "user",
                "text": "Save this in my food diary.",
            },
            {"id": "assistant-1", "role": "ai", "text": "[[tool_call:0]]"},
        ],
    )
    native_tool = {
        "name": "remember",
        "description": "Save a structured memory record.",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "value": {"type": "string"},
            },
            "required": ["key", "value"],
        },
    }
    calls = []

    monkeypatch.setattr(
        routes,
        "_registered_prompt_tool_definitions",
        lambda *args, **kwargs: [native_tool],
    )

    def fake_generate(prompt, context=None, **kwargs):
        calls.append(dict(kwargs))
        if len(calls) == 1:
            return {
                "text": "I found the memory schema.",
                "thought": "",
                "tools_used": [
                    {
                        "name": "help",
                        "status": "invoked",
                        "args": {"tool_name": "remember"},
                        "result": {"status": "invoked", "ok": True},
                    }
                ],
                "metadata": {},
            }
        provider_name = kwargs["native_tool_definitions"][0]["name"]
        assert provider_name == "remember"
        return {
            "text": "[[tool_call:0]]",
            "thought": "",
            "tools_used": [
                {
                    "name": provider_name,
                    "args": {"key": "food_diary", "value": "noodles"},
                }
            ],
            "metadata": {},
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    response = _post_recorded_continuation(
        app,
        json={
            "session_id": "regression-session",
            "message_id": "assistant-1",
            "mode": "api",
            "model": "gpt-5.6-sol",
            "tools": [
                {
                    "id": "help-1",
                    "name": "help",
                    "args": {"tool_name": "remember"},
                    "result": {
                        "status": "invoked",
                        "ok": True,
                        "data": {"tools": [{"name": "remember"}]},
                    },
                    "status": "invoked",
                }
            ],
        },
    )

    assert response.status_code == 200
    assert len(calls) == 2
    proposal = response.json()["tools_used"][0]
    assert proposal["name"] == "remember"
    assert proposal["status"] == "proposed"
    assert response.json()["metadata"]["post_discovery_persistence_retry"] is True


def test_tinker_repeat_retry_strips_native_tools_and_executor(monkeypatch, tmp_path):
    app, routes, _ = _prepare_chat_runtime(
        monkeypatch,
        tmp_path,
        [
            {"id": "user-1", "role": "user", "text": "Read my profile."},
            {
                "id": "assistant-1",
                "role": "ai",
                "text": "Requested tool read_file.",
            },
        ],
    )
    tool_args = {"path": "workspace/imports/profile.md"}
    native_tool = {
        "name": "read_file",
        "description": "Read a local workspace file.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    }
    executor = object()
    calls = []

    monkeypatch.setattr(
        routes,
        "_resolve_provider_inference_target_or_none",
        lambda *args, **kwargs: {
            "provider": "tinker",
            "model": "tinker://run:test/sampler_weights/inkling",
            "base_url": "https://tinker.example.test/oai/api/v1",
            "api_token": "test-token",
            "runtime": {},
        },
    )
    monkeypatch.setattr(
        routes,
        "_registered_prompt_tool_definitions",
        lambda *args, **kwargs: [native_tool],
    )
    # Exercise the retry sanitizer with both tool-bearing kwargs. Tinker supplies
    # native definitions naturally; the executor represents an API-style caller
    # reaching the same retry path.
    monkeypatch.setattr(
        routes,
        "_reasoning_generation_kwargs",
        lambda *args, **kwargs: {"tool_executor": executor},
    )

    def fake_generate(prompt, context=None, **kwargs):
        calls.append({"context": context, "kwargs": dict(kwargs)})
        return {
            "text": "",
            "thought": "",
            "tools_used": [{"name": "read_file", "args": dict(tool_args)}],
            "metadata": {},
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    response = _post_recorded_continuation(
        app,
        json={
            "session_id": "regression-session",
            "message_id": "assistant-1",
            "mode": "local",
            "model": "tinker://run:test/sampler_weights/inkling",
            "tools": [
                {
                    "id": "tool-1",
                    "name": "read_file",
                    "args": tool_args,
                    "result": {
                        "status": "invoked",
                        "ok": True,
                        "data": {"text": "Profile details"},
                    },
                    "status": "invoked",
                }
            ],
        },
    )

    assert response.status_code == 200
    assert len(calls) == 2
    assert calls[0]["kwargs"]["native_tool_definitions"] == [native_tool]
    assert calls[0]["kwargs"]["tool_executor"] is executor
    assert calls[1]["context"].tools == []
    assert "native_tool_definitions" not in calls[1]["kwargs"]
    assert "tool_executor" not in calls[1]["kwargs"]


def test_chat_continue_superseded_by_later_message_skips_generation(
    monkeypatch, tmp_path
):
    app, routes, conv_store = _prepare_chat_runtime(
        monkeypatch,
        tmp_path,
        [
            {"id": "user-1", "role": "user", "text": "Use the tool."},
            {
                "id": "assistant-1",
                "role": "ai",
                "text": "Requested tool recall.",
            },
            {"id": "user-2", "role": "user", "text": "Never mind; continue."},
            {
                "id": "assistant-2",
                "role": "ai",
                "text": "This is the newer answer.",
            },
        ],
    )
    generate_calls = 0

    def fake_generate(prompt, context=None, **kwargs):
        nonlocal generate_calls
        generate_calls += 1
        return {
            "text": "Stale continuation that must not be generated.",
            "thought": "",
            "tools_used": [],
            "metadata": {},
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    response = _post_recorded_continuation(
        app,
        json={
            "session_id": "regression-session",
            "message_id": "assistant-1",
            "mode": "api",
            "tools": [
                {
                    "id": "tool-1",
                    "name": "recall",
                    "args": {"key": "user_profile"},
                    "result": {
                        "status": "invoked",
                        "ok": True,
                        "data": {"value": "Profile details"},
                    },
                    "status": "invoked",
                }
            ],
        },
    )

    assert response.status_code == 200
    assert generate_calls == 0
    assert response.json()["metadata"].get("continuation_superseded") is True
    saved = conv_store.load_conversation("regression-session")
    newer = next(item for item in saved if item.get("id") == "assistant-2")
    assert newer["text"] == "This is the newer answer."


def test_chat_continue_round_limit_stops_before_generation(monkeypatch, tmp_path):
    app, routes, conv_store = _prepare_chat_runtime(
        monkeypatch,
        tmp_path,
        [
            {"id": "user-1", "role": "user", "text": "Use tools carefully."},
            {
                "id": "assistant-1",
                "role": "ai",
                "text": "Requested tool recall.",
                "metadata": {"tool_continuation_rounds": 12},
            },
        ],
    )
    generate_calls = 0

    def fake_generate(prompt, context=None, **kwargs):
        nonlocal generate_calls
        generate_calls += 1
        return {"text": "must not run", "tools_used": [], "metadata": {}}

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    response = _post_recorded_continuation(
        app,
        json={
            "session_id": "regression-session",
            "message_id": "assistant-1",
            "mode": "api",
            "tools": [
                {
                    "id": "tool-13",
                    "name": "recall",
                    "args": {"key": "profile"},
                    "result": {"status": "invoked", "ok": True, "data": {}},
                    "status": "invoked",
                }
            ],
        },
    )

    assert response.status_code == 200
    assert generate_calls == 0
    metadata = response.json()["metadata"]
    assert metadata["tool_continuation_limit_reached"] is True
    assert metadata["continuation_stop_reason"] == "tool_continuation_round_limit"
    assert metadata["tool_continuation_rounds"] == 12
    saved = conv_store.load_conversation("regression-session")
    target = next(item for item in saved if item.get("id") == "assistant-1")
    assert target["metadata"]["status"] == "partial"


def test_explicit_no_tools_turn_suppresses_stale_tool_intent(monkeypatch, tmp_path):
    app, routes, conv_store = _prepare_chat_runtime(
        monkeypatch,
        tmp_path,
        [
            {
                "id": "user-1",
                "role": "user",
                "text": "Read workspace/imports/profile.md.",
            },
            {
                "id": "assistant-1",
                "role": "ai",
                "text": "Requested tool read_file (workspace/imports/profile.md). Awaiting approval.",
                "metadata": {"tool_response_pending": True},
            },
        ],
    )
    routes.llm_service.contexts["default"].system_prompt = (
        routes.app_config.REPO_ROOT
        / "backend"
        / "app"
        / "prompts"
        / "system_prompt.txt"
    ).read_text(encoding="utf-8")
    native_tool = {
        "name": "read_file",
        "description": "Read a local workspace file.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    }
    calls = []

    monkeypatch.setattr(
        routes,
        "_resolve_provider_inference_target_or_none",
        lambda *args, **kwargs: {
            "provider": "tinker",
            "model": "tinker://run:test/sampler_weights/inkling",
            "base_url": "https://tinker.example.test/oai/api/v1",
            "api_token": "test-token",
            "runtime": {},
        },
    )
    monkeypatch.setattr(
        routes,
        "_registered_prompt_tool_definitions",
        lambda *args, **kwargs: [native_tool],
    )

    async def fail_register(*args, **kwargs):
        raise AssertionError("a no-tools turn must not register a tool proposal")

    monkeypatch.setattr(routes, "_register_tool_proposals", fail_register)

    def fake_generate(prompt, context=None, **kwargs):
        calls.append({"prompt": prompt, "context": context, "kwargs": dict(kwargs)})
        if len(calls) == 1:
            return {
                "text": "I'll read it now.",
                "thought": "",
                "tools_used": [
                    {
                        "name": "read_file",
                        "args": {"path": "workspace/imports/profile.md"},
                    }
                ],
                "metadata": {"finish_reason": "tool_calls"},
            }
        return {
            "text": "SAFE_STOP_ACK",
            "thought": "",
            "tools_used": [],
            "metadata": {},
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    response = TestClient(app).post(
        "/chat",
        json={
            "session_id": "regression-session",
            "message_id": "assistant-2",
            "message": "No more tools. Reply exactly SAFE_STOP_ACK and do nothing else.",
            "mode": "local",
            "model": "tinker://run:test/sampler_weights/inkling",
            "use_rag": False,
            "use_text_rag": False,
            "use_vision_rag": False,
        },
    )

    assert response.status_code == 200
    assert len(calls) == 2
    assert all(call["context"].tools == [] for call in calls)
    assert all("native_tool_definitions" not in call["kwargs"] for call in calls)
    assert all("tool_executor" not in call["kwargs"] for call in calls)
    assert all(
        routes._TOOL_DISCOVERY_PROMPT_HINT not in call["context"].system_prompt
        for call in calls
    )
    assert all(
        all(
            hint not in call["context"].system_prompt
            for hint in routes._TOOL_CALL_PROMPT_HINTS
        )
        for call in calls
    )
    assert all(
        routes._AVAILABLE_TOOLS_PROMPT_HEADER not in call["context"].system_prompt
        for call in calls
    )
    assert all(
        "**action loop:" not in call["context"].system_prompt.lower()
        and "**tool-use policy:" not in call["context"].system_prompt.lower()
        and "call the matching listed tool directly"
        not in call["context"].system_prompt.lower()
        and "**personality:" in call["context"].system_prompt.lower()
        for call in calls
    )
    assert any(
        message.get("metadata", {}).get("tools_disabled_for_turn") is True
        for message in calls[0]["context"].messages
        if isinstance(message, dict)
    )
    assert all(
        not any(
            message.get("metadata", {}).get(key)
            for key in ("turn_scope", "tool_approval", "workflow")
        )
        for call in calls
        for message in call["context"].messages
        if isinstance(message, dict)
    )
    payload = response.json()
    assert payload["message"] == "SAFE_STOP_ACK"
    assert payload["tools_used"] == []
    assert payload["metadata"]["status"] == "complete"
    assert payload["metadata"]["tools_disabled_for_turn"] is True
    assert payload["metadata"]["tool_requests_suppressed"] is True
    assert payload["metadata"]["tool_free_retry"] is True
    assert payload["metadata"]["capability_scope"]["tool_names"] == []
    assert "I'll read it now." not in payload["message"]
    assert app.state.pending_tools == {}

    saved = conv_store.load_conversation("regression-session")
    target = next(item for item in saved if item.get("id") == "assistant-2")
    assert target["text"] == "SAFE_STOP_ACK"
    assert target["metadata"]["status"] == "complete"


def test_no_tools_turn_detection_requires_a_standalone_directive():
    from app import routes

    assert routes._turn_explicitly_disallows_tools("No more tools. Reply in text.")
    assert routes._turn_explicitly_disallows_tools(
        "Please do not use any tools. Just answer."
    )
    assert not routes._turn_explicitly_disallows_tools(
        'Explain why the phrase "don\'t use tools" can be ambiguous.'
    )
    assert not routes._turn_explicitly_disallows_tools(
        "Don't use tool X; use recall instead."
    )


def test_no_tools_turn_keeps_privacy_route_preflight(monkeypatch, tmp_path):
    app, routes, _ = _prepare_chat_runtime(monkeypatch, tmp_path, [])
    generate_calls = 0

    monkeypatch.setattr(
        routes,
        "_privacy_route_check_for_message",
        lambda *args, **kwargs: {
            "tool": {
                "name": "route_to_local_model",
                "args": {"reason": "sensitive_content", "message": "private"},
            },
            "metadata": {"privacy_route_status": "proposed"},
        },
    )

    async def fake_register(*args, **kwargs):
        return [
            {
                "id": "privacy-route-1",
                "name": "route_to_local_model",
                "args": {"reason": "sensitive_content", "message": "private"},
                "status": "proposed",
            }
        ]

    monkeypatch.setattr(routes, "_register_tool_proposals", fake_register)

    def fail_generate(*args, **kwargs):
        nonlocal generate_calls
        generate_calls += 1
        raise AssertionError("privacy preflight must run before provider generation")

    monkeypatch.setattr(routes.llm_service, "generate", fail_generate)

    response = TestClient(app).post(
        "/chat",
        json={
            "session_id": "regression-session",
            "message_id": "assistant-privacy",
            "message": "No more tools. Keep this private.",
            "mode": "api",
            "use_rag": False,
            "use_text_rag": False,
            "use_vision_rag": False,
        },
    )

    assert response.status_code == 200
    assert generate_calls == 0
    payload = response.json()
    assert payload["tools_used"][0]["name"] == "route_to_local_model"
    assert payload["metadata"]["status"] == "pending"
    assert payload["metadata"]["tools_disabled_for_turn"] is True
    assert payload["metadata"]["privacy_route_safety_override"] is True
    assert payload["metadata"]["capability_scope"]["tool_names"] == []


def test_empty_tool_continuation_persists_recoverable_partial(monkeypatch, tmp_path):
    app, routes, conv_store = _prepare_chat_runtime(
        monkeypatch,
        tmp_path,
        [
            {"id": "user-1", "role": "user", "text": "Read the profile."},
            {
                "id": "assistant-1",
                "role": "ai",
                "text": "Requested tool read_file (workspace/imports/profile.md). Awaiting approval.",
                "metadata": {
                    "status": "pending",
                    "tool_response_pending": True,
                },
            },
        ],
    )

    generate_calls = []

    def fake_generate(prompt, context=None, **kwargs):
        generate_calls.append((prompt, context, kwargs))
        return {
            "text": " \n[[tool_call:0]]\n ",
            "thought": "",
            "tools_used": [],
            "metadata": {"finish_reason": "tool_calls"},
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    response = _post_recorded_continuation(
        app,
        json={
            "session_id": "regression-session",
            "message_id": "assistant-1",
            "mode": "api",
            "tools": [
                {
                    "id": "tool-1",
                    "name": "read_file",
                    "args": {"path": "workspace/imports/profile.md"},
                    "result": {
                        "status": "invoked",
                        "ok": True,
                        "data": {"text": "Profile details"},
                    },
                    "status": "invoked",
                }
            ],
        },
    )

    assert response.status_code == 200
    assert len(generate_calls) == 2
    retry_prompt, retry_context, retry_kwargs = generate_calls[1]
    assert isinstance(retry_prompt, str) and retry_prompt.strip()
    assert retry_context.tools == []
    assert "native_tool_definitions" not in retry_kwargs
    assert "tool_executor" not in retry_kwargs
    payload = response.json()
    assert payload["message"].startswith(
        "I couldn't finish the continuation from tool results."
    )
    assert "read_file" in payload["message"]
    assert "Awaiting approval" not in payload["message"]
    assert payload["tools_used"] == []
    assert payload["metadata"]["status"] == "partial"
    assert payload["metadata"]["empty_tool_continuation"] is True
    assert payload["metadata"]["unresolved_tool_loop"] is True
    assert payload["metadata"]["continuation_stop_reason"] == "empty_after_tool_results"
    assert payload["metadata"]["retry_without_tools"] is True
    assert payload["metadata"]["tool_result_text_retry"] is True
    assert payload["metadata"]["tool_result_text_retry_failed"] is True
    assert payload["metadata"].get("tool_response_pending") is None

    saved = conv_store.load_conversation("regression-session")
    target = next(item for item in saved if item.get("id") == "assistant-1")
    assert target["text"] == payload["message"]
    assert target["metadata"]["status"] == "partial"
    assert target["metadata"].get("tool_response_pending") is None


def test_empty_successful_tool_continuation_recovers_with_text_only_retry(
    monkeypatch, tmp_path
):
    app, routes, conv_store = _prepare_chat_runtime(
        monkeypatch,
        tmp_path,
        [
            {"id": "user-1", "role": "user", "text": "Read the profile."},
            {
                "id": "assistant-1",
                "role": "ai",
                "text": "Requested tool read_file (workspace/imports/profile.md). Awaiting approval.",
                "metadata": {
                    "status": "pending",
                    "tool_response_pending": True,
                },
            },
        ],
    )
    generate_calls = []
    expected_answer = "PROFILE_OK | name=Ada | role=engineer"

    def fake_generate(prompt, context=None, **kwargs):
        generate_calls.append((prompt, context, kwargs))
        if len(generate_calls) == 1:
            return {
                "text": " \n[[tool_call:0]]\n ",
                "thought": "The tool succeeded; I still need to write the final answer.",
                "tools_used": [],
                "metadata": {"finish_reason": "tool_calls"},
            }

        assert isinstance(prompt, str) and prompt.strip()
        assert "completed tool output" in prompt.lower()
        assert context.tools == []
        assert kwargs.get("attachments") == []
        assert "native_tool_definitions" not in kwargs
        assert "tool_executor" not in kwargs
        turn_keys = {
            str((message.get("metadata") or {}).get("turn_message_key") or "")
            for message in context.messages
            if isinstance(message, dict) and isinstance(message.get("metadata"), dict)
        }
        assert "tool_results" in turn_keys
        assert not turn_keys.intersection(
            {
                "capture_policy",
                "computer_use_disabled",
                "continuation",
                "tool_approval",
                "turn_scope",
                "workflow",
            }
        )
        return {
            "text": expected_answer,
            "thought": "",
            "tools_used": [],
            "metadata": {"finish_reason": "stop"},
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    response = _post_recorded_continuation(
        app,
        json={
            "session_id": "regression-session",
            "message_id": "assistant-1",
            "mode": "api",
            "tools": [
                {
                    "id": "tool-1",
                    "name": "read_file",
                    "args": {"path": "workspace/imports/profile.md"},
                    "result": {
                        "status": "invoked",
                        "ok": True,
                        "data": {"text": "name=Ada\nrole=engineer"},
                    },
                    "status": "invoked",
                }
            ],
        },
    )

    assert response.status_code == 200
    assert len(generate_calls) == 2
    payload = response.json()
    assert payload["message"] == expected_answer
    assert payload["tools_used"] == []
    assert payload["metadata"]["status"] == "complete"
    assert payload["metadata"]["retry_without_tools"] is True
    assert payload["metadata"]["tool_result_text_retry"] is True
    assert payload["metadata"].get("empty_tool_continuation") is not True
    assert payload["metadata"].get("unresolved_tool_loop") is not True

    saved = conv_store.load_conversation("regression-session")
    target = next(item for item in saved if item.get("id") == "assistant-1")
    assert target["text"] == expected_answer
    assert target["metadata"]["status"] == "complete"


def test_empty_failed_tool_continuation_does_not_retry(monkeypatch, tmp_path):
    app, routes, conv_store = _prepare_chat_runtime(
        monkeypatch,
        tmp_path,
        [
            {"id": "user-1", "role": "user", "text": "Read the profile."},
            {
                "id": "assistant-1",
                "role": "ai",
                "text": "Requested tool read_file. Awaiting approval.",
                "metadata": {"status": "pending", "tool_response_pending": True},
            },
        ],
    )
    generate_calls = 0

    def fake_generate(*args, **kwargs):
        nonlocal generate_calls
        generate_calls += 1
        return {
            "text": "",
            "thought": "",
            "tools_used": [],
            "metadata": {"finish_reason": "stop"},
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    response = _post_recorded_continuation(
        app,
        json={
            "session_id": "regression-session",
            "message_id": "assistant-1",
            "mode": "api",
            "tools": [
                {
                    "id": "tool-1",
                    "name": "read_file",
                    "args": {"path": "workspace/imports/missing.md"},
                    "result": {"status": "error", "ok": False, "error": "not_found"},
                    "status": "error",
                }
            ],
        },
    )

    assert response.status_code == 200
    assert generate_calls == 1
    payload = response.json()
    assert "read_file: error - not_found" in payload["message"]
    assert payload["tools_used"] == []
    assert payload["metadata"]["status"] == "partial"
    assert payload["metadata"]["empty_tool_continuation"] is True
    assert payload["metadata"].get("retry_without_tools") is not True

    saved = conv_store.load_conversation("regression-session")
    target = next(item for item in saved if item.get("id") == "assistant-1")
    assert target["text"] == payload["message"]
    assert target["metadata"]["status"] == "partial"


def test_error_tool_continuation_preserves_provider_error(monkeypatch, tmp_path):
    app, routes, conv_store = _prepare_chat_runtime(
        monkeypatch,
        tmp_path,
        [
            {"id": "user-1", "role": "user", "text": "Read the profile."},
            {
                "id": "assistant-1",
                "role": "ai",
                "text": "Requested tool read_file. Awaiting approval.",
                "metadata": {"status": "pending", "tool_response_pending": True},
            },
        ],
    )

    generate_calls = 0

    def fake_generate(*args, **kwargs):
        nonlocal generate_calls
        generate_calls += 1
        return {
            "text": "",
            "thought": "",
            "tools_used": [],
            "metadata": {"error": "provider unavailable"},
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    response = _post_recorded_continuation(
        app,
        json={
            "session_id": "regression-session",
            "message_id": "assistant-1",
            "mode": "api",
            "tools": [
                {
                    "id": "tool-1",
                    "name": "read_file",
                    "args": {"path": "workspace/imports/profile.md"},
                    "result": {
                        "status": "invoked",
                        "ok": True,
                        "data": {"text": "Profile details"},
                    },
                    "status": "invoked",
                }
            ],
        },
    )

    assert response.status_code == 200
    assert generate_calls == 1
    payload = response.json()
    assert "provider unavailable" in payload["message"]
    assert payload["metadata"]["status"] == "error"
    assert payload["metadata"].get("empty_tool_continuation") is not True
    assert payload["metadata"].get("retry_without_tools") is not True

    saved = conv_store.load_conversation("regression-session")
    target = next(item for item in saved if item.get("id") == "assistant-1")
    assert "provider unavailable" in target["text"]
    assert target["metadata"]["status"] == "error"


def test_concurrent_accepts_for_one_request_invoke_tool_once(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.main import app

    monkeypatch.setattr(
        routes.user_settings,
        "load_settings",
        lambda: {
            "approval_level": "all",
            "tool_resolution_notifications": True,
        },
    )

    request_id = "concurrent-accept-regression"
    decision_context = {
        "request_id": request_id,
        "name": "remember",
        "args": {"key": "one-shot", "value": "Only save this once."},
        "session_id": "concurrent-accept-session",
        "message_id": "concurrent-accept-message",
        "chain_id": "concurrent-accept-message",
    }
    conv_store.save_conversation(
        decision_context["session_id"],
        [
            {"id": "concurrent-user", "role": "user", "text": "Save this once."},
            {
                "id": decision_context["message_id"],
                "role": "ai",
                "text": "Requested tool remember.",
            },
        ],
    )
    app.state.pending_tools = {
        request_id: {
            "id": request_id,
            "name": decision_context["name"],
            "args": decision_context["args"],
            "session_id": decision_context["session_id"],
            "message_id": decision_context["message_id"],
            "chain_id": decision_context["chain_id"],
            "status": "proposed",
            "review_notification_emitted": True,
        }
    }
    app.state.agent_console_state = {"agents": {}, "resources": {}}
    invoke_count = 0
    first_invocation_started = asyncio.Event()
    release_invocation = asyncio.Event()
    notifications = []

    async def fake_invoke(*args, **kwargs):
        nonlocal invoke_count
        invoke_count += 1
        first_invocation_started.set()
        await release_invocation.wait()
        return {"saved": True}

    monkeypatch.setattr(routes, "_invoke_registered_tool_in_thread", fake_invoke)
    monkeypatch.setattr(
        routes,
        "emit_notification",
        lambda app, **kwargs: notifications.append(kwargs),
    )

    async def send_concurrent_decisions():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            first = asyncio.create_task(
                client.post(
                    "/api/tools/decision",
                    json={**decision_context, "decision": "accept"},
                )
            )
            await asyncio.wait_for(first_invocation_started.wait(), timeout=1)
            second = asyncio.create_task(
                client.post(
                    "/api/tools/decision",
                    json={**decision_context, "decision": "accept"},
                )
            )
            await asyncio.sleep(0.05)
            release_invocation.set()
            return await asyncio.gather(first, second)

    responses = asyncio.run(send_concurrent_decisions())

    assert [response.status_code for response in responses] == [200, 200]
    assert [response.json()["status"] for response in responses] == [
        "invoked",
        "invoked",
    ]
    assert invoke_count == 1
    assert len(notifications) == 1
    assert notifications[0]["category"] == "tool_resolution"
    assert notifications[0]["data"]["tool_ids"] == [request_id]
    assert notifications[0]["data"]["tool_statuses"] == ["invoked"]
    assert "result" not in notifications[0]["data"]
