"""Synthetic route checks for retrieval and tool-result destination boundaries."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from app import routes
from app.base_services import MemoryManager
from app.main import app
from app.tools import memory as memory_tools
from app.utils import conversation_store, user_settings
from app.utils.privacy_policy import current_data_policy
from app.workflow_scope import build_capability_scope
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from starlette.requests import Request

PRIVATE = "SYNTHETIC_ROUTE_PRIVATE_d31a"


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(conversation_store, "CONV_DIR", tmp_path / "conversations")
    monkeypatch.setattr(user_settings, "USER_SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setitem(
        app.state._state, "config", {"mode": "api", "rag_enabled": True}
    )
    monkeypatch.setitem(app.state._state, "pending_tools", {})
    monkeypatch.setitem(app.state._state, "tool_data_policies", {})
    monkeypatch.setitem(app.state._state, "agent_console_state", {})
    manager = MemoryManager({"mem_key": Fernet.generate_key().decode()})
    monkeypatch.setattr(manager, "_drop_vector_record", lambda *_args, **_kwargs: None)
    manager.register_tool("recall", memory_tools.recall)
    manager.upsert_item("private", PRIVATE, sensitivity="protected")
    manager.upsert_item("ordinary", "SYNTHETIC_PUBLIC_VALUE", sensitivity="mundane")
    monkeypatch.setattr(app.state, "memory_manager", manager)
    monkeypatch.setattr(memory_tools, "_MANAGER", manager)
    monkeypatch.setattr(routes, "tool_allowed_in_workflow", lambda *_args: True)
    monkeypatch.setattr(routes, "_get_clip_rag_service", lambda **_kwargs: None)
    monkeypatch.setattr(
        routes,
        "_resolve_provider_inference_target_or_none",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        routes, "_effective_provider_for_runtime", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(routes.llm_service, "contexts", {}, raising=False)
    monkeypatch.setattr(routes.llm_service, "mode", "api")
    return TestClient(app), manager


def request():
    return Request({"type": "http", "app": app, "headers": []})


def seed_turn(*, mode="local", result=PRIVATE, tool_id="recall-1"):
    scope = build_capability_scope(
        workflow="default",
        channel="text",
        modules=[],
        tool_definitions=[{"name": "recall"}],
    )
    routes._append_conversation_entry(
        "privacy-route",
        {
            "id": "m1:user",
            "role": "user",
            "text": "Read my saved note",
        },
    )
    routes._append_conversation_entry(
        "privacy-route",
        {
            "id": "m1",
            "role": "ai",
            "text": "",
            "metadata": {
                "mode": mode,
                "capability_scope": scope,
            },
        },
    )
    tool = {
        "id": tool_id,
        "name": "recall",
        "args": {"key": "private"},
        "status": "invoked",
        "result": {"ok": True, "data": result},
        "session_id": "privacy-route",
        "message_id": "m1",
        "mode": mode,
    }
    app.state.pending_tools[tool_id] = tool
    return tool


@pytest.mark.parametrize("sensitivity", ["protected", "secret"])
def test_direct_invoke_cannot_opt_out_of_external_privacy(
    runtime, monkeypatch, sensitivity
):
    client, manager = runtime
    manager.upsert_item("private", PRIVATE, sensitivity=sensitivity)
    monkeypatch.setattr(
        routes, "_lookup_message_runtime_hints", lambda *_args: ("model", "local")
    )
    response = client.post(
        "/tools/invoke",
        json={
            "name": "recall",
            "args": {"key": "private", "for_external": False, "allow_protected": True},
            "session_id": "fake-client-local",
            "message_id": "fake",
        },
    )
    assert response.status_code == 200
    assert PRIVATE not in response.text
    assert manager.get_item("private", touch=False)["value"] == PRIVATE


def test_generated_local_proposal_preserves_local_recall_and_stamps_result(runtime):
    client, manager = runtime
    seed_turn()
    proposals = asyncio.run(
        routes._register_tool_proposals(
            request(),
            tools=[{"name": "recall", "args": {"key": "private"}}],
            session_id="privacy-route",
            message_id="m1",
            model="local-test",
            mode="local",
            default_agent="m1",
            force_tool_review=True,
            ignore_existing_signatures=True,
        )
    )
    proposal_id = proposals[0]["id"]
    response = client.post(
        "/tools/decision",
        json={
            "request_id": proposal_id,
            "decision": "accept",
            "name": "recall",
            "session_id": "privacy-route",
            "message_id": "m1",
        },
    )
    assert response.status_code == 200
    assert PRIVATE in response.text
    receipt = routes._trusted_tool_data_policy(
        app, proposal_id, "privacy-route", "m1", completed=True
    )
    assert receipt is not None and receipt["external"] is False
    # A process restart must recover the same host-owned result policy.
    app.state.tool_data_policies = {}
    canonical = routes._saved_terminal_tool_event(
        app,
        {"id": proposal_id, "name": "recall"},
        session_id="privacy-route",
        message_id="m1",
    )
    assert canonical["result_data_policy"]["external"] is False


@pytest.mark.parametrize("receipt_mode", [None, "local"])
def test_external_continuation_rejects_local_and_legacy_recall(
    runtime, monkeypatch, receipt_mode
):
    client, _manager = runtime
    tool = seed_turn()
    if receipt_mode:
        routes._record_tool_data_policy(
            app,
            tool["id"],
            "privacy-route",
            "m1",
            routes._request_data_destination(app, receipt_mode),
            completed=True,
        )
    # A client-added external-safe field must never manufacture a host receipt.
    tool["result_data_policy"] = routes._request_data_destination(app, "api")
    monkeypatch.setattr(
        routes.llm_service,
        "generate",
        lambda *_args, **_kwargs: pytest.fail("provider must not run"),
    )
    response = client.post(
        "/chat/continue",
        json={
            "session_id": "privacy-route",
            "message_id": "m1",
            "mode": "api",
            "tools": [tool],
        },
    )
    assert response.status_code == 409
    assert "not prepared for an external model" in response.json()["detail"]


def test_external_safe_result_receipt_survives_canonicalization(runtime):
    _client, _manager = runtime
    tool = seed_turn(mode="api", result="SYNTHETIC_PUBLIC_VALUE")
    routes._record_tool_data_policy(
        app,
        tool["id"],
        "privacy-route",
        "m1",
        routes._request_data_destination(app, "api"),
        completed=True,
    )
    canonical = routes._saved_terminal_tool_event(
        app, tool, session_id="privacy-route", message_id="m1"
    )
    routes._require_external_safe_recall_results(
        [canonical], routes._request_data_destination(app, "api")
    )
    assert canonical["result"]["data"] == "SYNTHETIC_PUBLIC_VALUE"
    assert (
        routes._trusted_tool_data_policy(
            app, tool["id"], "different", "m1", completed=True
        )
        is None
    )


def test_new_external_chat_rejects_prior_local_recall_history(runtime, monkeypatch):
    client, _manager = runtime
    tool = seed_turn()
    routes._append_tool_event_to_conversation(
        "privacy-route",
        "m1",
        "recall",
        tool["args"],
        tool["result"],
        "invoked",
        request_id=tool["id"],
        mode="local",
    )
    routes._record_tool_data_policy(
        app,
        tool["id"],
        "privacy-route",
        "m1",
        routes._request_data_destination(app, "local"),
        completed=True,
    )
    monkeypatch.setattr(
        routes.llm_service,
        "generate",
        lambda *_args, **_kwargs: pytest.fail("provider must not run"),
    )
    response = client.post(
        "/chat",
        json={
            "session_id": "privacy-route",
            "mode": "api",
            "message": "Continue our discussion",
            "use_rag": False,
        },
    )
    assert response.status_code == 409
    assert "not prepared for an external model" in response.json()["detail"]


def test_in_memory_tool_context_checks_host_receipt(runtime):
    _client, _manager = runtime
    tool = seed_turn()
    routes._record_tool_data_policy(
        app,
        tool["id"],
        "privacy-route",
        "m1",
        routes._request_data_destination(app, "local"),
        completed=True,
    )
    messages = [
        {
            "role": "system",
            "content": PRIVATE,
            "metadata": {
                "tool_results": True,
                "recall_result_refs": [
                    {
                        "id": tool["id"],
                        "session_id": "privacy-route",
                        "message_id": "m1",
                    }
                ],
            },
        }
    ]
    with pytest.raises(Exception, match="not prepared for an external model"):
        routes._require_external_safe_recall_history(
            app, messages, "privacy-route", routes._request_data_destination(app, "api")
        )


@pytest.mark.parametrize(
    "request_mode,shared_mode,expected",
    [("api", "local", False), ("local", "api", True)],
)
def test_automatic_rag_uses_request_destination(
    runtime, monkeypatch, request_mode, shared_mode, expected
):
    client, _manager = runtime
    observed = {}

    class Retrieval:
        def query(self, text, top_k=3):
            observed["policy"] = current_data_policy()
            return [
                {
                    "id": "protected-doc",
                    "text": PRIVATE,
                    "metadata": {"source": "local-note", "sensitivity": "protected"},
                    "score": 0.98,
                }
            ]

        def trace(self, doc_id):
            return None

    def generate(*_args, context=None, **kwargs):
        observed["context"] = context
        observed["egress_policy"] = kwargs.get("_data_policy")
        return {
            "text": "Synthetic answer",
            "thought": "",
            "tools_used": [],
            "metadata": {},
        }

    monkeypatch.setattr(routes, "_get_rag_service", lambda: Retrieval())
    monkeypatch.setattr(routes.llm_service, "generate", generate)
    monkeypatch.setattr(routes.llm_service, "mode", shared_mode)
    response = client.post(
        "/chat",
        json={
            "session_id": f"rag-{request_mode}",
            "mode": request_mode,
            "message": "Find my note",
            "use_rag": True,
        },
    )
    assert response.status_code == 200
    assert (PRIVATE in json.dumps(observed["context"].messages)) is expected
    assert observed["policy"].external is not expected
    assert observed["policy"].allow_remote_query is not expected
    assert observed["egress_policy"].external is not expected
    assert current_data_policy() is None


def test_provider_endpoint_resolution_does_not_probe_and_remote_is_external(
    runtime, monkeypatch
):
    _client, _manager = runtime
    monkeypatch.setattr(
        routes, "_effective_provider_for_runtime", lambda *_args, **_kwargs: "lmstudio"
    )
    monkeypatch.setattr(
        routes.provider_manager, "_settings_for_provider", lambda *_args: {}
    )
    monkeypatch.setattr(
        routes.provider_manager,
        "_adapter",
        lambda *_args: SimpleNamespace(
            resolve_base_url=lambda *_args, **_kwargs: "https://remote.example/v1"
        ),
    )
    assert (
        routes._request_data_destination(app, "local", "lmstudio")["external"] is True
    )
    monkeypatch.setattr(
        routes.provider_manager,
        "_adapter",
        lambda *_args: SimpleNamespace(
            resolve_base_url=lambda *_args, **_kwargs: "http://127.0.0.1:1234/v1"
        ),
    )
    assert (
        routes._request_data_destination(app, "local", "lmstudio")["external"] is False
    )


def test_private_route_preflight_suppresses_remote_query_before_retrieval(
    runtime, monkeypatch
):
    client, _manager = runtime
    observed = []

    class Retrieval:
        def query(self, text, top_k=3):
            observed.append(current_data_policy())
            return []

    def preflight(*_args, **_kwargs):
        observed.append("preflight")
        return {
            "tool": {"name": "route_to_local_model", "args": {"target_mode": "local"}},
            "metadata": {},
        }

    async def proposals(*_args, **_kwargs):
        return []

    monkeypatch.setattr(routes, "_privacy_route_check_for_message", preflight)
    monkeypatch.setattr(routes, "_get_rag_service", lambda: Retrieval())
    monkeypatch.setattr(routes, "_register_tool_proposals", proposals)
    monkeypatch.setattr(
        routes.llm_service,
        "generate",
        lambda *_args, **_kwargs: {
            "text": "Synthetic",
            "tools_used": [],
            "metadata": {},
        },
    )
    response = client.post(
        "/chat",
        json={
            "session_id": "private-preflight",
            "mode": "api",
            "message": "synthetic private query",
            "use_rag": True,
        },
    )
    assert response.status_code == 200
    assert observed[0] == "preflight"
    assert observed[1].allow_remote_query is False


def test_reflection_error_result_publishes_error(runtime, monkeypatch):
    _client, _manager = runtime
    task = {"id": "reflection-synthetic", "title": "Synthetic", "status": "failed"}
    result = {
        "status": "error",
        "task": task,
        "run": {
            "error": {"category": "provider", "message": "Synthetic provider failure"}
        },
    }
    monkeypatch.setattr(
        routes,
        "_get_reflection_service",
        lambda *_args: SimpleNamespace(
            get_task=lambda *_args: task, run_task=lambda *_args, **_kwargs: result
        ),
    )
    events = []

    async def publish(*_args, **kwargs):
        events.append(kwargs)

    monkeypatch.setattr(routes, "_publish_reflection_event", publish)
    actual = asyncio.run(
        routes.run_reflection_task(task["id"], request(), payload=None)
    )
    assert actual == result
    assert events[-1]["status"] == "error"
    assert events[-1]["content"] == "Synthetic provider failure"


def test_rehydrate_reports_private_api_mirror_skipped_and_keeps_canonical(
    runtime, monkeypatch, tmp_path
):
    from app.services import rag_provider, rag_service

    client, manager = runtime
    monkeypatch.setattr(
        rag_service.RAGService,
        "_init_backend",
        lambda self, *_args: rag_service._InMemoryBackend(
            self.class_name, self._embed_text
        ),
    )
    service = rag_service.RAGService(
        backend="memory",
        embedding_model="api:synthetic",
        api_key="synthetic-only",
        sqlite_path=str(tmp_path / "knowledge.db"),
    )
    monkeypatch.setattr(
        rag_provider, "get_rag_service", lambda *_args, **_kwargs: service
    )
    monkeypatch.setattr(routes, "_get_rag_service", lambda: service)
    monkeypatch.setattr(
        rag_service.http_session,
        "post",
        lambda *_args, **_kwargs: pytest.fail(
            "private embedding must not leave the process"
        ),
    )
    monkeypatch.setattr(
        manager,
        "_drop_vector_record",
        MemoryManager._drop_vector_record.__get__(manager),
    )
    manager.delete_item("ordinary")
    response = client.post("/memory/rag/rehydrate", json={"allow_protected": True})
    assert response.status_code == 200
    assert response.json()["reindexed"] == 0
    assert response.json()["skipped"] == 1
    assert manager.get_item("private", touch=False)["vectorize"] is False
    assert service.canonical_store.get_item_by_source("memory:private")[
        "text"
    ].endswith(PRIVATE)
    assert service.backend.list_docs()["ids"] == []


@pytest.mark.parametrize("sensitivity", ["secret", "protected"])
def test_http_private_promotion_replaces_public_projection(
    runtime, monkeypatch, tmp_path, sensitivity
):
    from app.services import rag_provider, rag_service

    client, manager = runtime
    monkeypatch.setattr(
        rag_service.RAGService,
        "_init_backend",
        lambda self, *_args: rag_service._InMemoryBackend(
            self.class_name, self._embed_text
        ),
    )
    service = rag_service.RAGService(
        backend="memory",
        embedding_model="simple",
        sqlite_path=str(tmp_path / "promotion.db"),
    )
    monkeypatch.setattr(
        rag_provider, "get_rag_service", lambda *_args, **_kwargs: service
    )
    monkeypatch.setattr(routes, "_get_rag_service", lambda: service)
    monkeypatch.setattr(
        manager,
        "_drop_vector_record",
        MemoryManager._drop_vector_record.__get__(manager),
    )
    created = client.post(
        "/memory/promoted",
        json={"value": PRIVATE, "sensitivity": "mundane", "vectorize": True},
    )
    assert created.status_code == 200
    assert service.backend.list_docs()["ids"]
    changed = client.post(
        "/memory/promoted", json={"value": PRIVATE, "sensitivity": sensitivity}
    )
    assert changed.status_code == 200
    assert service.backend.list_docs()["ids"] == []
    assert manager.get_item("promoted", touch=False)["value"] == PRIVATE
    assert manager.get_item("promoted", touch=False)["vectorize"] is False
    canonical = service.canonical_store.get_item_by_source("memory:promoted")
    assert canonical is not None
    if sensitivity == "secret":
        assert manager.store["promoted"]["encrypted"] is True
        with service.canonical_store._connect() as conn:
            values = conn.execute(
                "SELECT text, summary_text, metadata_json FROM knowledge_items"
            ).fetchall()
            chunks = conn.execute(
                "SELECT text, metadata_json FROM knowledge_chunks"
            ).fetchall()
        assert PRIVATE not in str([tuple(row) for row in values + chunks])
    else:
        assert canonical["text"].endswith(PRIVATE)
        assert routes._vectorize_memory_key(request(), "promoted", allow_protected=True)
        assert service.backend.list_docs()["ids"]


def test_knowledge_rehydrate_fails_closed_when_private_cleanup_fails(
    runtime, monkeypatch
):
    from app.utils.privacy_policy import PrivacyCleanupError

    client, _manager = runtime

    def failed_cleanup(*_args, **_kwargs):
        raise PrivacyCleanupError(PRIVATE)

    service = SimpleNamespace(
        canonical_store=SimpleNamespace(
            list_items=lambda: {
                "ids": ["private-doc"],
                "documents": [PRIVATE],
                "metadatas": [{"source": "memory:private", "sensitivity": "secret"}],
            }
        ),
        rehydrate_canonical_document=failed_cleanup,
    )
    monkeypatch.setattr(routes, "_get_rag_service", lambda: service)
    response = client.post("/knowledge/rag/rehydrate", json={})
    assert response.status_code == 503
    assert "cleanup failed" in response.json()["detail"]
    assert PRIVATE not in response.text
