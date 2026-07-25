import asyncio
import importlib

import httpx
from fastapi.testclient import TestClient


def _prepare_chat_runtime(monkeypatch, tmp_path, messages):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)
    conv_store.save_conversation("regression-session", messages)

    from app import routes
    from app.base_services import ModelContext
    from app.main import app
    from app.utils import user_settings

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
    return app, routes, conv_store


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

    response = TestClient(app).post(
        "/chat/continue",
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

    response = TestClient(app).post(
        "/chat/continue",
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

    response = TestClient(app).post(
        "/chat/continue",
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

    response = TestClient(app).post(
        "/chat/continue",
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

    response = TestClient(app).post(
        "/chat/continue",
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

    response = TestClient(app).post(
        "/chat/continue",
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

    response = TestClient(app).post(
        "/chat/continue",
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
