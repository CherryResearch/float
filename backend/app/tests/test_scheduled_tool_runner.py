import sys
import time
from pathlib import Path

import pytest


@pytest.fixture
def app_with_temp_stores(tmp_path, monkeypatch):
    backend_dir = Path(__file__).resolve().parents[2]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    from app.main import app
    from app.utils import calendar_store, conversation_store

    monkeypatch.setattr(
        conversation_store, "CONV_DIR", tmp_path / "conversations", raising=False
    )
    conversation_store.CONV_DIR.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        calendar_store, "EVENTS_DIR", tmp_path / "calendar", raising=False
    )
    calendar_store.EVENTS_DIR.mkdir(parents=True, exist_ok=True)

    # Keep tests isolated from existing console state.
    app.state.agent_console_state = {"agents": {}}

    class DummyManager:
        def __init__(self):
            self.calls = []

        def invoke_tool(self, name, *, user=None, signature=None, **args):
            self.calls.append({"name": name, "user": user, "args": args})
            return "ok"

    original_manager = getattr(app.state, "memory_manager", None)
    dummy = DummyManager()
    app.state.memory_manager = dummy
    try:
        yield app, dummy
    finally:
        if original_manager is not None:
            app.state.memory_manager = original_manager


@pytest.mark.anyio
async def test_scheduled_tool_runner_executes_due_action(app_with_temp_stores):
    from app.utils import calendar_store, conversation_store
    from workers.scheduled_tool_runner import run_scheduled_tools_for_event

    app, dummy = app_with_temp_stores
    event_id = "ev-1"
    request_id = "rid-1"
    session_id = "s1"
    message_id = "m1"

    conversation_store.save_conversation(
        session_id,
        [
            {
                "id": message_id,
                "role": "ai",
                "text": "tool scheduled",
                "tools": [
                    {
                        "id": request_id,
                        "name": "remember",
                        "args": {"key": "k", "value": "v"},
                        "status": "scheduled",
                        "result": {"scheduled_event_id": event_id},
                    }
                ],
            }
        ],
    )

    calendar_store.save_event(
        event_id,
        {
            "id": event_id,
            "title": "Schedule tool: remember",
            "start_time": time.time() - 5,
            "timezone": "UTC",
            "status": "scheduled",
            "actions": [
                {
                    "id": request_id,
                    "request_id": request_id,
                    "kind": "tool",
                    "name": "remember",
                    "args": {"key": "k", "value": "v"},
                    "status": "scheduled",
                    "session_id": session_id,
                    "message_id": message_id,
                    "chain_id": message_id,
                }
            ],
        },
    )

    res = await run_scheduled_tools_for_event(app, event_id)
    assert res["status"] == "invoked"
    assert dummy.calls
    assert dummy.calls[-1]["name"] == "remember"

    stored_event = calendar_store.load_event(event_id)
    assert stored_event.get("status") == "prompted"
    action = stored_event.get("actions", [])[0]
    assert action.get("status") == "invoked"
    assert action.get("result") == "ok"

    stored_conv = conversation_store.load_conversation(session_id)
    tool = stored_conv[0]["tools"][0]
    assert tool["status"] == "invoked"
    assert tool["result"] == "ok"

    agents = (app.state.agent_console_state or {}).get("agents") or {}
    assert message_id in agents
    events = agents[message_id].get("events") or []
    tool_events = [
        e for e in events if e.get("type") == "tool" and e.get("id") == request_id
    ]
    assert tool_events
    assert tool_events[-1].get("status") == "invoked"


@pytest.mark.anyio
async def test_scheduled_tool_runner_runs_prompt_followup(
    app_with_temp_stores, monkeypatch
):
    from app import routes as routes_module
    from app.utils import calendar_store, conversation_store
    from workers.scheduled_tool_runner import run_scheduled_tools_for_event

    app, _dummy = app_with_temp_stores
    event_id = "ev-prompt"
    request_id = "rid-prompt"
    session_id = "s-prompt"
    message_id = "m-prompt"

    def fake_generate(*_args, **_kwargs):
        return {"text": "Follow-up response", "thought": ""}

    monkeypatch.setattr(routes_module.llm_service, "generate", fake_generate)

    conversation_store.save_conversation(
        session_id,
        [
            {
                "id": message_id,
                "role": "ai",
                "text": "tool scheduled",
                "tools": [
                    {
                        "id": request_id,
                        "name": "remember",
                        "args": {"key": "k", "value": "v"},
                        "status": "scheduled",
                        "result": {"scheduled_event_id": event_id},
                    }
                ],
            }
        ],
    )

    calendar_store.save_event(
        event_id,
        {
            "id": event_id,
            "title": "Schedule tool: remember",
            "start_time": time.time() - 5,
            "timezone": "UTC",
            "status": "scheduled",
            "actions": [
                {
                    "id": request_id,
                    "request_id": request_id,
                    "kind": "tool",
                    "name": "remember",
                    "args": {"key": "k", "value": "v"},
                    "status": "scheduled",
                    "session_id": session_id,
                    "message_id": message_id,
                    "chain_id": message_id,
                    "prompt": "Say something about the result.",
                }
            ],
        },
    )

    res = await run_scheduled_tools_for_event(app, event_id)
    assert res["status"] == "invoked"

    stored_conv = conversation_store.load_conversation(session_id)
    assert any(
        entry.get("role") == "user"
        and entry.get("text") == "Say something about the result."
        for entry in stored_conv
        if isinstance(entry, dict)
    )
    assert any(
        entry.get("role") == "ai" and entry.get("text") == "Follow-up response"
        for entry in stored_conv
        if isinstance(entry, dict)
    )

    agents = (app.state.agent_console_state or {}).get("agents") or {}
    assert message_id in agents
    events = agents[message_id].get("events") or []
    content_events = [e for e in events if e.get("type") == "content"]
    assert content_events
    assert content_events[-1].get("content") == "Follow-up response"


@pytest.mark.anyio
async def test_scheduled_tool_runner_routes_new_chat_followup_to_task_conversation(
    app_with_temp_stores, monkeypatch
):
    from app import routes as routes_module
    from app.utils import calendar_store, conversation_store
    from workers.scheduled_tool_runner import run_scheduled_tools_for_event

    app, _dummy = app_with_temp_stores
    event_id = "ev-new-chat"
    request_id = "rid-new-chat"
    session_id = "s-origin"
    message_id = "m-origin"

    def fake_generate(*_args, **_kwargs):
        return {"text": "New chat follow-up response", "thought": ""}

    monkeypatch.setattr(routes_module.llm_service, "generate", fake_generate)

    conversation_store.save_conversation(
        session_id,
        [
            {
                "id": message_id,
                "role": "ai",
                "text": "tool scheduled",
                "tools": [
                    {
                        "id": request_id,
                        "name": "remember",
                        "args": {"key": "k", "value": "v"},
                        "status": "scheduled",
                        "result": {"scheduled_event_id": event_id},
                    }
                ],
            }
        ],
    )

    calendar_store.save_event(
        event_id,
        {
            "id": event_id,
            "title": "Schedule tool: remember",
            "start_time": time.time() - 5,
            "timezone": "UTC",
            "status": "scheduled",
            "actions": [
                {
                    "id": request_id,
                    "request_id": request_id,
                    "kind": "tool",
                    "name": "remember",
                    "args": {"key": "k", "value": "v"},
                    "status": "scheduled",
                    "prompt": "Write the follow-up in a new task chat.",
                    "conversation_mode": "new_chat",
                    "session_id": session_id,
                    "message_id": message_id,
                    "chain_id": message_id,
                }
            ],
        },
    )

    res = await run_scheduled_tools_for_event(app, event_id)
    assert res["status"] == "invoked"

    stored_event = calendar_store.load_event(event_id)
    stored_action = stored_event.get("actions", [])[0]
    generated_session = stored_action.get("session_id")
    assert isinstance(generated_session, str)
    assert generated_session.startswith("task-")
    assert generated_session != session_id
    assert stored_action.get("conversation_mode") == "new_chat"

    original_conv = conversation_store.load_conversation(session_id)
    assert not any(
        entry.get("role") == "user"
        and entry.get("text") == "Write the follow-up in a new task chat."
        for entry in original_conv
        if isinstance(entry, dict)
    )
    generated_conv = conversation_store.load_conversation(generated_session)
    assert any(
        entry.get("role") == "user"
        and entry.get("text") == "Write the follow-up in a new task chat."
        for entry in generated_conv
        if isinstance(entry, dict)
    )
    assert any(
        entry.get("role") == "ai"
        and entry.get("text") == "New chat follow-up response"
        for entry in generated_conv
        if isinstance(entry, dict)
    )


@pytest.mark.anyio
async def test_scheduled_tool_runner_runs_prompt_action(
    app_with_temp_stores, monkeypatch
):
    from app import routes as routes_module
    from app.utils import calendar_store, conversation_store
    from workers.scheduled_tool_runner import run_scheduled_tools_for_event

    app, _dummy = app_with_temp_stores
    event_id = "ev-prompt-only"
    action_id = "act-prompt-only"
    session_id = "s-prompt-only"
    message_id = "m-prompt-only"

    def fake_generate(*_args, **_kwargs):
        return {"text": "Prompt-only response", "thought": ""}

    monkeypatch.setattr(routes_module.llm_service, "generate", fake_generate)

    conversation_store.save_conversation(
        session_id,
        [
            {
                "id": message_id,
                "role": "ai",
                "text": "ready",
            }
        ],
    )

    calendar_store.save_event(
        event_id,
        {
            "id": event_id,
            "title": "Prompt-only task",
            "start_time": time.time() - 5,
            "timezone": "UTC",
            "status": "scheduled",
            "actions": [
                {
                    "id": action_id,
                    "kind": "prompt",
                    "prompt": "Write a summary.",
                    "status": "scheduled",
                    "session_id": session_id,
                    "message_id": message_id,
                    "chain_id": message_id,
                }
            ],
        },
    )

    res = await run_scheduled_tools_for_event(app, event_id)
    assert res["status"] == "invoked"

    stored_event = calendar_store.load_event(event_id)
    assert stored_event.get("status") == "prompted"
    stored_action = stored_event.get("actions", [])[0]
    assert stored_action.get("status") == "prompted"
    assert stored_action.get("result") == "Prompt-only response"

    stored_conv = conversation_store.load_conversation(session_id)
    assert any(
        entry.get("role") == "user" and entry.get("text") == "Write a summary."
        for entry in stored_conv
        if isinstance(entry, dict)
    )
    assert any(
        entry.get("role") == "ai" and entry.get("text") == "Prompt-only response"
        for entry in stored_conv
        if isinstance(entry, dict)
    )

    agents = (app.state.agent_console_state or {}).get("agents") or {}
    assert message_id in agents
    events = agents[message_id].get("events") or []
    content_events = [e for e in events if e.get("type") == "content"]
    assert content_events
    assert content_events[-1].get("content") == "Prompt-only response"


@pytest.mark.anyio
async def test_scheduled_tool_runner_normalizes_legacy_continue_prompt_action(
    app_with_temp_stores, monkeypatch
):
    from app import routes as routes_module
    from app.utils import calendar_store, conversation_store
    from workers.scheduled_tool_runner import run_scheduled_tools_for_event

    app, _dummy = app_with_temp_stores
    event_id = "ev-legacy-prompt"

    def fake_generate(*_args, **_kwargs):
        return {"text": "Legacy prompt response", "thought": ""}

    monkeypatch.setattr(routes_module.llm_service, "generate", fake_generate)

    calendar_store.save_event(
        event_id,
        {
            "id": event_id,
            "title": "Legacy prompt task",
            "start_time": time.time() - 5,
            "timezone": "UTC",
            "status": "scheduled",
            "actions": [
                {
                    "id": "legacy-1",
                    "type": "continue_prompt",
                    "prompt": "Continue from the stored task.",
                }
            ],
        },
    )

    res = await run_scheduled_tools_for_event(app, event_id)
    assert res["status"] == "invoked"

    stored_event = calendar_store.load_event(event_id)
    stored_action = stored_event["actions"][0]
    assert stored_action.get("status") == "prompted"
    assert stored_action.get("result") == "Legacy prompt response"

    generated_session = stored_action.get("session_id")
    assert isinstance(generated_session, str) and generated_session.startswith("task-")
    stored_conv = conversation_store.load_conversation(generated_session)
    assert any(
        entry.get("role") == "ai" and entry.get("text") == "Legacy prompt response"
        for entry in stored_conv
        if isinstance(entry, dict)
    )
