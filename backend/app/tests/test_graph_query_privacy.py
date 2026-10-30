"""Offline graph dispatch and destination-replay checks using synthetic data."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from app import routes
from app.base_services import MemoryManager
from app.main import app
from app.tools import graph as graph_tools
from app.utils import conversation_store, user_settings
from app.utils.privacy_policy import current_data_policy
from app.workflow_scope import build_capability_scope
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

PRIVATE = "SYNTHETIC_GRAPH_PRIVATE_a25e"
SESSION = "synthetic-graph-privacy"


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(conversation_store, "CONV_DIR", tmp_path / "conversations")
    monkeypatch.setattr(user_settings, "USER_SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setitem(
        app.state._state, "config", {"mode": "api", "rag_enabled": False}
    )
    for key in ("pending_tools", "tool_data_policies", "agent_console_state"):
        monkeypatch.setitem(app.state._state, key, {})
    manager = MemoryManager({"memory_store_path": str(tmp_path / "synthetic.sqlite3")})
    manager.register_tool("graph.query", graph_tools.query_graph)
    monkeypatch.setattr(app.state, "memory_manager", manager)
    monkeypatch.setattr(graph_tools, "_GRAPH_STORE", manager._graph_store)
    manager._graph_store.upsert_claim(
        claim_id="private",
        predicate="synthetic_relation",
        roles=[{"role_name": "value", "value": PRIVATE}],
        metadata={"sensitivity": "secret"},
    )
    monkeypatch.setattr(routes, "tool_allowed_in_workflow", lambda *_args: True)
    monkeypatch.setattr(routes, "approval_allows_auto_for_tool", lambda *_args: True)
    monkeypatch.setattr(routes, "_get_rag_service", lambda: None)
    monkeypatch.setattr(routes, "_get_clip_rag_service", lambda **_kwargs: None)
    monkeypatch.setattr(
        routes, "_effective_provider_for_runtime", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        routes,
        "_resolve_provider_inference_target_or_none",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(routes.llm_service, "contexts", {}, raising=False)
    monkeypatch.setattr(routes.llm_service, "mode", "api")
    monkeypatch.setattr(
        routes.llm_service,
        "generate",
        lambda *_args, **_kwargs: pytest.fail("No model may run in a denied request"),
    )
    return TestClient(app), manager


def seed_turn(*, args=None, save_result=False):
    scope = build_capability_scope(
        workflow="default",
        channel="text",
        modules=[],
        tool_definitions=[{"name": "graph.query"}],
    )
    routes._append_conversation_entry(
        SESSION, {"id": "m1:user", "role": "user", "text": "Read the synthetic graph"}
    )
    routes._append_conversation_entry(
        SESSION,
        {
            "id": "m1",
            "role": "ai",
            "text": "",
            "metadata": {"mode": "local", "capability_scope": scope},
        },
    )
    event = {
        "id": "graph-1",
        "name": "graph.query",
        "args": args or {},
        "status": "invoked",
        "result": {"ok": True, "data": {"claims": [{"literal": PRIVATE}]}},
        "session_id": SESSION,
        "message_id": "m1",
        "mode": "local",
    }
    app.state.pending_tools[event["id"]] = event
    if save_result:
        routes._append_tool_event_to_conversation(
            SESSION,
            "m1",
            "graph.query",
            event["args"],
            event["result"],
            "invoked",
            request_id=event["id"],
            mode="local",
        )
    return event


def forbid_store(monkeypatch):
    reads = []

    def forbidden():
        reads.append(True)
        pytest.fail("Denied graph.query must not access the graph store")

    monkeypatch.setattr(graph_tools, "_store", forbidden)
    return reads


@pytest.mark.parametrize("query_mode", ["current", "audit"])
def test_direct_http_graph_invoke_denies_unverified_local_hints(
    runtime, monkeypatch, query_mode
):
    client, _manager = runtime
    reads = forbid_store(monkeypatch)
    monkeypatch.setattr(
        routes, "_lookup_message_runtime_hints", lambda *_args: ("synthetic", "local")
    )
    response = client.post(
        "/tools/invoke", json={"name": "graph.query", "args": {"mode": query_mode}}
    )
    assert response.status_code == 403
    assert PRIVATE not in response.text
    assert reads == []
    assert current_data_policy() is None


@pytest.mark.parametrize("mode", [None, "", "unknown", "api", "dynamic", "server"])
@pytest.mark.parametrize("query_mode", ["current", "audit"])
def test_provider_graph_executor_denies_external_or_unknown_before_read(
    runtime, monkeypatch, mode, query_mode
):
    reads = forbid_store(monkeypatch)
    execute = routes._build_provider_tool_executor(
        app,
        session_id=None,
        message_id=None,
        workflow_name="text",
        model="synthetic",
        mode=mode,
    )
    result = execute(
        {"name": "graph.query", "args": {"mode": query_mode}, "call_id": "synthetic"}
    )
    assert result["ok"] is False
    assert "local-only" in json.dumps(result)
    assert PRIVATE not in json.dumps(result)
    assert reads == []
    assert current_data_policy() is None


@pytest.mark.parametrize("query_mode", ["current", "audit"])
def test_declared_local_provider_with_remote_endpoint_denies_graph(
    runtime, monkeypatch, query_mode
):
    reads = forbid_store(monkeypatch)
    monkeypatch.setattr(
        routes, "_effective_provider_for_runtime", lambda *_args, **_kwargs: "synthetic"
    )
    monkeypatch.setattr(
        routes.provider_manager, "_settings_for_provider", lambda *_args: {}
    )
    monkeypatch.setattr(
        routes.provider_manager,
        "_adapter",
        lambda *_args: SimpleNamespace(
            resolve_base_url=lambda *_args, **_kwargs: "https://synthetic.invalid/v1"
        ),
    )
    execute = routes._build_provider_tool_executor(
        app,
        session_id=None,
        message_id=None,
        workflow_name="text",
        model="synthetic",
        mode="local",
    )
    result = execute({"name": "graph.query", "args": {"mode": query_mode}})
    assert result["ok"] is False
    assert "local-only" in json.dumps(result)
    assert reads == []


@pytest.mark.parametrize("action_context", [None, {}, {"agent_label": "scheduler"}])
@pytest.mark.parametrize("query_mode", ["current", "audit"])
def test_scheduled_dispatch_without_trusted_destination_denies_before_read(
    runtime, monkeypatch, action_context, query_mode
):
    from workers.scheduled_tool_runner import _invoke_tool

    reads = forbid_store(monkeypatch)
    with pytest.raises(PermissionError, match="local-only"):
        asyncio.run(
            _invoke_tool(
                app,
                name="graph.query",
                args={"mode": query_mode},
                user="synthetic",
                action_context=action_context,
            )
        )
    assert reads == []
    assert current_data_policy() is None


@pytest.mark.parametrize(
    "mode,allowed", [("local", True), ("api", False), (None, False)]
)
@pytest.mark.parametrize("query_mode", ["current", "audit"])
def test_graph_proposal_dispatch_requires_host_local_destination(
    runtime, monkeypatch, mode, allowed, query_mode
):
    client, _manager = runtime
    seed_turn()
    if not allowed:
        reads = forbid_store(monkeypatch)
    proposals = asyncio.run(
        routes._register_tool_proposals(
            Request({"type": "http", "app": app, "headers": []}),
            tools=[{"name": "graph.query", "args": {"mode": query_mode}}],
            session_id=SESSION,
            message_id="m1",
            model="synthetic",
            mode=mode,
            default_agent="m1",
            force_tool_review=True,
            ignore_existing_signatures=True,
        )
    )
    response = client.post(
        "/tools/decision",
        json={
            "request_id": proposals[0]["id"],
            "decision": "accept",
            "name": "graph.query",
            "session_id": SESSION,
            "message_id": "m1",
        },
    )
    assert response.status_code == 200
    assert (PRIVATE in response.text) is allowed
    assert response.json()["status"] == ("invoked" if allowed else "error")
    if allowed:
        receipt = routes._trusted_tool_data_policy(
            app, proposals[0]["id"], SESSION, "m1", completed=True
        )
        assert receipt["external"] is False
        assert conversation_store.graph_local_only(SESSION)
    else:
        assert reads == []
    assert current_data_policy() is None


@pytest.mark.parametrize("receipt_mode", [None, "local", "api"])
@pytest.mark.parametrize("query_mode", ["current", "audit"])
def test_external_graph_continuation_rejects_all_result_receipts(
    runtime, receipt_mode, query_mode
):
    client, _manager = runtime
    event = seed_turn(args={"mode": query_mode})
    if receipt_mode:
        routes._record_tool_data_policy(
            app,
            event["id"],
            SESSION,
            "m1",
            routes._request_data_destination(app, receipt_mode),
            completed=True,
        )
    event["result_data_policy"] = routes._request_data_destination(app, "api")
    response = client.post(
        "/chat/continue",
        json={
            "session_id": SESSION,
            "message_id": "m1",
            "mode": "api",
            "tools": [event],
        },
    )
    assert response.status_code == 409
    assert "local-only graph.query" in response.json()["detail"]
    assert PRIVATE not in response.text


def test_new_external_chat_denies_saved_local_graph_history(runtime):
    client, _manager = runtime
    seed_turn(save_result=True)
    response = client.post(
        "/chat",
        json={
            "session_id": SESSION,
            "mode": "api",
            "message": "Continue",
            "use_rag": False,
        },
    )
    assert response.status_code == 409
    assert "local-only graph.query" in response.json()["detail"]


def test_new_external_chat_denies_in_memory_graph_reference(runtime):
    client, _manager = runtime
    context = routes.llm_service.get_context(SESSION)
    context.add_message(
        "system",
        PRIVATE,
        metadata={"tool_results": True, "graph_result_refs": [{"id": "synthetic"}]},
    )
    response = client.post(
        "/chat",
        json={
            "session_id": SESSION,
            "mode": "api",
            "message": "Continue",
            "use_rag": False,
        },
    )
    assert response.status_code == 409
    assert "local-only graph.query" in response.json()["detail"]


def test_local_graph_continuation_retains_reference_and_external_reuse_denied(
    runtime, monkeypatch
):
    client, _manager = runtime
    event = seed_turn()
    seen = {}

    def synthetic_generate(*_args, context=None, **kwargs):
        seen["context"] = context
        seen["policy"] = kwargs["_data_policy"]
        return {"text": "Synthetic local answer", "tools_used": [], "metadata": {}}

    monkeypatch.setattr(routes.llm_service, "generate", synthetic_generate)
    response = client.post(
        "/chat/continue",
        json={
            "session_id": SESSION,
            "message_id": "m1",
            "mode": "local",
            "tools": [event],
        },
    )
    assert response.status_code == 200, response.text
    assert seen["policy"].external is False
    assert conversation_store.graph_local_only(SESSION)
    assert PRIVATE in json.dumps(seen["context"].messages)
    messages = seen["context"].messages
    references = [
        message["metadata"]["graph_result_refs"]
        for message in messages
        if message.get("metadata", {}).get("graph_result_refs")
    ]
    assert references == [
        [{"id": event["id"], "session_id": SESSION, "message_id": "m1"}]
    ]
    with pytest.raises(HTTPException, match="local-only graph.query"):
        routes._require_local_graph_history(messages, {"external": True})


@pytest.mark.parametrize("destination", [{}, {"external": None}, {"external": True}])
@pytest.mark.parametrize("representation", ["event", "reference", "legacy"])
def test_raw_graph_history_denies_ambiguous_and_external_destinations(
    destination, representation
):
    event = {"name": "graph.query", "status": "invoked", "result": PRIVATE}
    if representation == "event":
        message = {"tools": [event]}
    else:
        message = {"content": json.dumps([event]), "metadata": {"tool_results": True}}
        if representation == "reference":
            message["content"] = PRIVATE
            message["metadata"]["graph_result_refs"] = [{"id": "synthetic"}]
    with pytest.raises(HTTPException, match="local-only graph.query"):
        routes._require_local_graph_history([message], destination)
    routes._require_local_graph_history([message], {"external": False})


def test_graph_guard_leaves_recall_and_failed_graph_events_unchanged():
    events = [
        {"name": "recall", "status": "invoked", "result": "synthetic recall"},
        {"name": "graph.query", "status": "error", "result": "local-only"},
        {"name": "graph.query", "status": "denied", "result": "Denied"},
    ]
    routes._require_local_graph_results(events, {"external": True})


def test_graph_restriction_survives_prose_replacement_and_false_client_metadata(
    runtime,
):
    client, _manager = runtime
    seed_turn()
    conversation_store.mark_graph_local_only(SESSION)
    conversation_store.merge_metadata(SESSION, {"graph_local_only": False})
    conversation_store.replace_conversation_content(
        SESSION,
        [
            {
                "id": "summary",
                "role": "system",
                "text": PRIVATE,
                "metadata": {"graph_local_only": False},
            }
        ],
    )
    assert conversation_store.graph_local_only(SESSION)
    # Neither missing tool events nor a cold in-memory runtime can remove the restriction.
    app.state.tool_data_policies = {}
    response = client.post(
        "/chat",
        json={
            "session_id": SESSION,
            "mode": "api",
            "message": "Continue",
            "use_rag": False,
        },
    )
    assert response.status_code == 409
    assert "local-only graph.query" in response.json()["detail"]


@pytest.mark.parametrize("keep_last", [1, 40])
def test_deterministic_compaction_preserves_graph_restriction_in_copy_and_preview(
    runtime, keep_last
):
    from app.services.conversation_compaction import build_compaction, write_compaction

    seed_turn()
    conversation_store.mark_graph_local_only(SESSION)
    preview = build_compaction(SESSION, keep_last=keep_last)
    assert preview["graph_local_only"] is True
    assert preview["messages"][0]["metadata"]["graph_local_only"] is True
    conversation_store.replace_conversation_content(
        "imported-preview", preview["messages"]
    )
    assert conversation_store.graph_local_only("imported-preview")
    result = write_compaction(
        SESSION, keep_last=keep_last, target_conversation_id="synthetic-copy"
    )
    assert result["status"] == "written"
    assert conversation_store.graph_local_only("synthetic-copy")
    with pytest.raises(HTTPException, match="local-only graph.query"):
        routes._require_local_graph_history(
            [], {"external": True}, session_id="synthetic-copy"
        )


def test_graph_llm_compaction_fails_before_summarizer_or_model(runtime):
    from app.services.conversation_compaction import build_compaction

    client, _manager = runtime
    seed_turn()
    conversation_store.mark_graph_local_only(SESSION)
    with pytest.raises(ValueError, match="deterministic compaction"):
        build_compaction(
            SESSION,
            keep_last=1,
            summary_mode="llm",
            llm_summarizer=lambda _request: pytest.fail("LLM summarizer must not run"),
        )
    response = client.post(
        "/conversations/compact/preview",
        json={"conversation_id": SESSION, "keep_last": 1, "summary_mode": "llm"},
    )
    assert response.status_code == 400
    assert "deterministic compaction" in response.json()["detail"]


@pytest.mark.parametrize("mode", [None, "api", "local"])
def test_compaction_tool_respects_graph_source_and_marks_destination(runtime, mode):
    from app.tools import conversations as conversation_tools

    _client, manager = runtime
    seed_turn()
    conversation_store.mark_graph_local_only(SESSION)
    manager.register_tool(
        "compact_conversation_preview", conversation_tools.compact_conversation_preview
    )
    execute = routes._build_provider_tool_executor(
        app,
        session_id="synthetic-destination",
        message_id=None,
        workflow_name="text",
        model="synthetic",
        mode=mode,
    )
    result = execute(
        {
            "name": "compact_conversation_preview",
            # Use non-default optional fields to respect the existing compaction
            # signature payload, which omits empty/default optional values.
            "args": {
                "conversation_id": SESSION,
                "keep_last": 1,
                "summary_workflow": "decision_focus",
                "summary_format_notes": "Synthetic format",
                "summary_model": "synthetic",
            },
        }
    )
    assert result["ok"] is (mode == "local")
    assert conversation_store.graph_local_only("synthetic-destination") is (
        mode == "local"
    )
    if mode != "local":
        assert "local-only graph.query" in json.dumps(result)


def test_graph_marker_persistence_failure_prevents_successful_result(
    runtime, monkeypatch
):
    def cannot_persist(_session):
        raise OSError("synthetic metadata write failure")

    monkeypatch.setattr(conversation_store, "mark_graph_local_only", cannot_persist)
    execute = routes._build_provider_tool_executor(
        app,
        session_id=SESSION,
        message_id=None,
        workflow_name="text",
        model="synthetic",
        mode="local",
    )
    result = execute({"name": "graph.query", "args": {}})
    assert result["ok"] is False
    assert "synthetic metadata write failure" in json.dumps(result)
    assert PRIVATE not in json.dumps(result)


def test_branch_preserves_graph_restriction_for_prose_only_context(runtime):
    client, _manager = runtime
    context = routes.llm_service.get_context(SESSION)
    context.add_message("assistant", PRIVATE)
    conversation_store.mark_graph_local_only(SESSION)
    response = client.post(
        f"/context/{SESSION}/branch", json={"new_id": "synthetic-branch"}
    )
    assert response.status_code == 200
    assert conversation_store.graph_local_only("synthetic-branch")
    response = client.post(
        "/chat",
        json={
            "session_id": "synthetic-branch",
            "mode": "api",
            "message": "Continue",
            "use_rag": False,
        },
    )
    assert response.status_code == 409
    assert "local-only graph.query" in response.json()["detail"]


def test_branch_marker_failure_prevents_context_copy(runtime, monkeypatch):
    client, _manager = runtime
    routes.llm_service.get_context(SESSION).add_message("assistant", PRIVATE)
    conversation_store.mark_graph_local_only(SESSION)

    def cannot_persist(_name):
        raise OSError("synthetic marker write failure")

    monkeypatch.setattr(conversation_store, "mark_graph_local_only", cannot_persist)
    with pytest.raises(OSError, match="synthetic marker write failure"):
        client.post(
            f"/context/{SESSION}/branch", json={"new_id": "synthetic-blocked-branch"}
        )
    assert "synthetic-blocked-branch" not in routes.llm_service.contexts


@pytest.mark.parametrize("mode", ["api", "dynamic", "server", "unknown"])
def test_llm_generate_denies_marked_graph_session_before_model(runtime, mode):
    client, _manager = runtime
    routes.llm_service.get_context(SESSION).add_message("assistant", PRIVATE)
    conversation_store.mark_graph_local_only(SESSION)
    response = client.post(
        "/llm/generate",
        json={"session_id": SESSION, "mode": mode, "prompt": "Continue"},
    )
    assert response.status_code == 409
    assert "local-only graph.query" in response.json()["detail"]


def test_local_llm_generate_carries_graph_egress_policy(runtime, monkeypatch):
    client, _manager = runtime
    routes.llm_service.get_context(SESSION).add_message("assistant", PRIVATE)
    conversation_store.mark_graph_local_only(SESSION)
    seen = {}

    def synthetic_generate(*_args, **kwargs):
        seen["policy"] = kwargs["_data_policy"]
        return {"text": "Synthetic local response", "metadata": {}}

    monkeypatch.setattr(routes.llm_service, "generate", synthetic_generate)
    response = client.post(
        "/llm/generate",
        json={"session_id": SESSION, "mode": "local", "prompt": "Continue"},
    )
    assert response.status_code == 200
    assert seen["policy"].external is False
    assert seen["policy"].allow_remote_query is False


def test_json_export_import_preserves_graph_scope_without_tool_payloads(runtime):
    client, _manager = runtime
    conversation_store.save_conversation(
        SESSION, [{"role": "assistant", "text": PRIVATE}]
    )
    conversation_store.mark_graph_local_only(SESSION)
    exported = client.get(
        f"/conversations/{SESSION}/export",
        params={"format": "json", "include_tools": False, "include_thoughts": False},
    )
    assert exported.status_code == 200
    data = exported.json()
    assert data["messages"][0]["metadata"]["graph_local_only"] is True
    response = client.post(
        "/conversations/import",
        json={
            "name": "synthetic-json-import",
            "format": "json",
            "content": json.dumps(data),
        },
    )
    assert response.status_code == 200, response.text
    assert conversation_store.graph_local_only("synthetic-json-import")
    response = client.post(
        "/chat",
        json={
            "session_id": "synthetic-json-import",
            "mode": "api",
            "message": "Continue",
            "use_rag": False,
        },
    )
    assert response.status_code == 409


def test_json_import_restores_restrictive_top_level_marker_without_mutating_input():
    from app.utils.conversation_io import (
        import_conversation_json_raw,
        import_openai_conversation_json,
    )

    original = {
        "messages": [{"role": "assistant", "text": PRIVATE}],
        "metadata": {"graph_local_only": True},
    }
    imported = import_conversation_json_raw(original)
    assert imported[0]["metadata"]["graph_local_only"] is True
    normalized = import_openai_conversation_json(original)
    assert normalized[0]["metadata"]["graph_local_only"] is True
    assert "metadata" not in original["messages"][0]
