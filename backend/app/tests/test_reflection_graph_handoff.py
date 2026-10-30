"""Keep graph custody when a model queues reflection without a source argument."""

import json
from types import SimpleNamespace

import pytest
from app import base_services
from app.base_services import LLMService, MemoryManager
from app.services import reflection_service as reflection_module
from app.services.reflection_service import ReflectionService
from app.tools import reflections
from app.utils import conversation_store, generate_signature


@pytest.fixture
def handoff(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path / "conversations"))
    monkeypatch.setenv("FLOAT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(conversation_store, "CONV_DIR", tmp_path / "conversations")
    conversation_store.CONV_DIR.mkdir()
    monkeypatch.setattr(reflection_module, "get_rag_service", lambda *_a, **_k: None)
    dispatches, mirrors = [], []

    def post(url, **options):
        dispatches.append((url, options))
        content = json.dumps({"utility": 0.5, "uncertainty": 0.5, "continue": False})
        return SimpleNamespace(
            status_code=200,
            headers={},
            text="synthetic response",
            raise_for_status=lambda: None,
            json=lambda: {"choices": [{"message": {"content": content}}]},
        )

    monkeypatch.setattr(base_services.http_session, "post", post)
    monkeypatch.setattr(
        reflection_module,
        "try_ingest_text",
        lambda text, *_a, **_k: mirrors.append(text),
    )
    provider = LLMService(
        mode="api",
        config={
            "api_url": "http://127.0.0.1:8080/v1/chat/completions",
            "api_key": "synthetic-key",
            "api_model": "synthetic-model",
        },
    )
    service = ReflectionService(
        {"reflection_store_path": str(tmp_path / "reflection.sqlite3")},
        llm_generate=provider.generate,
    )
    monkeypatch.setattr(reflections, "_SERVICE", service)
    manager = MemoryManager({"memory_store_path": str(tmp_path / "memory.sqlite3")})
    manager.register_tool("reflect", reflections.reflect)
    return manager, service, dispatches, mirrors


@pytest.mark.parametrize("source", [None, "", "unrelated-unmarked-thread"])
@pytest.mark.parametrize("legacy_receipt", [False, True])
def test_reflect_cannot_drop_host_graph_custody(handoff, source, legacy_receipt):
    manager, service, dispatches, mirrors = handoff
    message = {"role": "ai", "text": "SYNTHETIC_GRAPH_LOCAL_ONLY"}
    if legacy_receipt:
        message["tools"] = [{"name": "graph.query", "status": "invoked"}]
    conversation_store.save_conversation("graph-origin", [message])
    if not legacy_receipt:
        conversation_store.mark_graph_local_only("graph-origin")
    args = {"question": "Review SYNTHETIC_GRAPH_LOCAL_ONLY", "run_now": True}
    if source is not None:
        args["source_thread_id"] = source
    with pytest.raises(ValueError, match="local-only graph context"):
        manager.invoke_tool(
            "reflect",
            user="synthetic",
            signature=generate_signature("synthetic", "reflect", args),
            _action_context={"session_id": "graph-origin", "mode": "local"},
            **args,
        )
    assert service.list_tasks() == []
    assert dispatches == mirrors == []
    assert conversation_store.graph_local_only("graph-origin")


def test_unmarked_local_reflect_handoff_still_runs_real_provider_adapter(handoff):
    manager, service, dispatches, mirrors = handoff
    conversation_store.save_conversation(
        "safe-origin", [{"role": "ai", "text": "safe"}]
    )
    args = {"question": "Review SYNTHETIC_PUBLIC_CONTEXT", "run_now": True}
    result = manager.invoke_tool(
        "reflect",
        user="synthetic",
        signature=generate_signature("synthetic", "reflect", args),
        _action_context={"session_id": "safe-origin", "mode": "local"},
        **args,
    )
    assert result["status"] == "ran"
    assert result["run"]["status"] == "ran"
    assert len(service.list_tasks()) == 1
    assert len(dispatches) >= 2
    assert "SYNTHETIC_PUBLIC_CONTEXT" in json.dumps(dispatches)
    assert mirrors
