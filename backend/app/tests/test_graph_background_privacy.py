"""Synthetic graph custody checks at background provider transport boundaries."""

import itertools
import json
from types import SimpleNamespace

import pytest
from app import base_services
from app.base_services import LLMService
from app.models import Message
from app.services import reflection_service as reflection_module
from app.services.reflection_service import ReflectionService
from app.utils import conversation_store
from app.utils.privacy_policy import (
    DataPolicy,
    current_data_policy,
    data_policy_scope,
    local_only_transport_required,
)

SENTINEL = "SYNTHETIC_GRAPH_LOCAL_ONLY"
PUBLIC = "SYNTHETIC_UNRESTRICTED_CONTEXT"


@pytest.fixture
def environment(tmp_path, monkeypatch):
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
            json=lambda: {
                "choices": [{"message": {"content": content}}],
                "model": "synthetic-model",
            },
        )

    monkeypatch.setattr(base_services.http_session, "post", post)
    monkeypatch.setattr(
        reflection_module,
        "try_ingest_text",
        lambda text, *_a, **_k: mirrors.append(text),
    )
    return dispatches, mirrors


def provider(endpoint="https://synthetic.invalid/v1/chat/completions"):
    return LLMService(
        mode="api",
        config={
            "api_url": endpoint,
            "api_key": "synthetic-key",
            "api_model": "synthetic-model",
        },
    )


def save_thread(name, text, *, restricted=False, legacy_receipt=False):
    message = {"role": "ai", "text": text}
    if legacy_receipt:
        message["tools"] = [
            {"name": "graph.query", "status": "invoked", "result": {"text": text}}
        ]
    conversation_store.save_conversation(name, [message])
    if restricted:
        conversation_store.mark_graph_local_only(name)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://synthetic.invalid/v1/chat/completions",
        "http://127.0.0.1:8080/v1/chat/completions",
    ],
)
@pytest.mark.parametrize("legacy_receipt", [False, True])
def test_reflection_refuses_graph_source_before_scorer_provider_and_mirror(
    environment, tmp_path, endpoint, legacy_receipt
):
    dispatches, mirrors = environment
    save_thread(
        "graph-source",
        SENTINEL,
        restricted=not legacy_receipt,
        legacy_receipt=legacy_receipt,
    )
    if legacy_receipt:
        assert not conversation_store.graph_local_only("graph-source")
    service = ReflectionService(
        {"reflection_store_path": str(tmp_path / "reflection.sqlite3")},
        llm_generate=provider(endpoint).generate,
    )
    # Title/question themselves can be derived from the source. Even the initial
    # task scorer must wait until this workflow can retain graph custody.
    task = service.create_task(
        question=f"Review {SENTINEL}", source_thread_id="graph-source"
    )
    result = service.run_task(task["id"], force=True)
    assert result["status"] == "error"
    assert result["error"]["category"] == "local_only_context"
    assert result["run"]["output"] == ""
    assert SENTINEL not in json.dumps(result["run"])
    assert task["metadata"]["graph_local_only"] is True
    assert conversation_store.graph_local_only("graph-source")
    assert dispatches == []
    assert mirrors == []


def test_reflection_rechecks_existing_task_when_source_becomes_restricted(
    environment, tmp_path
):
    dispatches, mirrors = environment
    save_thread("source", SENTINEL)
    service = ReflectionService(
        {"reflection_store_path": str(tmp_path / "reflection.sqlite3")},
        llm_generate=provider().generate,
    )
    task = service.create_task(
        question="Review the source",
        source_thread_id="source",
        utility=0.5,
        uncertainty=0.5,
    )
    conversation_store.mark_graph_local_only("source")
    assert (
        service.run_task(task["id"], force=True)["error"]["category"]
        == "local_only_context"
    )
    assert dispatches == mirrors == []


@pytest.mark.parametrize(
    "marked_name,source_name", [("source", "source.json"), ("source.json", "source")]
)
def test_reflection_cannot_drop_sidecar_custody_via_filename_alias(
    environment, tmp_path, marked_name, source_name
):
    dispatches, mirrors = environment
    save_thread("source", SENTINEL)
    # Seed either historical sidecar spelling, with no raw tool receipt left.
    conversation_store.merge_metadata(marked_name, {"graph_local_only": True})
    service = ReflectionService(
        {"reflection_store_path": str(tmp_path / "reflection.sqlite3")},
        llm_generate=provider().generate,
    )
    task = service.create_task(
        question="Review the source", source_thread_id=source_name
    )
    result = service.run_task(task["id"], force=True)
    assert result["error"]["category"] == "local_only_context"
    assert dispatches == mirrors == []


@pytest.mark.parametrize("legacy_receipt", [False, True])
def test_recent_reflection_skips_marked_source_and_runs_unmarked_control(
    environment, tmp_path, legacy_receipt
):
    dispatches, mirrors = environment
    save_thread("safe-source", PUBLIC)
    save_thread(
        "graph-source",
        SENTINEL,
        restricted=not legacy_receipt,
        legacy_receipt=legacy_receipt,
    )
    conversation_store.merge_metadata("graph-source", {"updated_at": 9999999999})
    service = ReflectionService(
        {"reflection_store_path": str(tmp_path / "reflection.sqlite3")},
        llm_generate=provider().generate,
    )
    assert [item["name"] for item in service._recent_conversation_candidates()] == [
        "safe-source"
    ]
    task = service.create_task(
        question="Review a recent source",
        metadata={"source_mode": "recent"},
        utility=0.5,
        uncertainty=0.5,
    )
    result = service.run_task(task["id"], force=True)
    assert result["status"] == "ran"
    assert len(dispatches) == 2, "Real LLMService must reach fake HTTP for both calls"
    assert PUBLIC in json.dumps(dispatches)
    assert SENTINEL not in json.dumps(dispatches)
    assert mirrors
    assert SENTINEL not in json.dumps(mirrors)


def test_reflection_rejects_compaction_carried_graph_marker(
    environment, tmp_path, monkeypatch
):
    dispatches, mirrors = environment
    save_thread("source", PUBLIC)
    service = ReflectionService(
        {"reflection_store_path": str(tmp_path / "reflection.sqlite3")},
        llm_generate=provider().generate,
    )
    task = service.create_task(
        question="Review a source",
        source_thread_id="source",
        utility=0.5,
        uncertainty=0.5,
    )
    monkeypatch.setattr(
        reflection_module,
        "build_compaction",
        lambda *_a, **_k: {
            "summary_preview": SENTINEL,
            "messages": [
                {"role": "ai", "text": SENTINEL, "metadata": {"graph_local_only": True}}
            ],
        },
    )
    assert (
        service.run_task(task["id"], force=True)["error"]["category"]
        == "local_only_context"
    )
    assert dispatches == mirrors == []


def test_reflection_rejects_prior_note_derived_from_marked_source(
    environment, tmp_path
):
    dispatches, mirrors = environment
    save_thread("old-source", SENTINEL)
    service = ReflectionService(
        {"reflection_store_path": str(tmp_path / "reflection.sqlite3")},
        llm_generate=provider().generate,
    )
    task = service.create_task(
        question="Continue the prior reflection", utility=0.5, uncertainty=0.5
    )
    # Reproduce a historical derived note whose source reference survives even
    # when the current task selects a different recent conversation.
    service._save_run(
        task,
        SENTINEL,
        {},
        {"input_context_ids": ["conversation:old-source:tail"]},
    )
    mirrors.clear()  # The fixture was created before its source was restricted.
    conversation_store.mark_graph_local_only("old-source")
    result = service.run_task(task["id"], force=True)
    assert result["error"]["category"] == "local_only_context"
    assert dispatches == mirrors == []


def _lineage_service(tmp_path):
    clock = itertools.count(1_700_000_000)
    service = ReflectionService(
        {"reflection_store_path": str(tmp_path / "reflection.sqlite3")},
        llm_generate=provider().generate,
        now_fn=lambda: float(next(clock)),
    )
    task = service.create_task(
        question="Continue the prior reflection",
        utility=0.5,
        uncertainty=0.5,
        patience_budget={"max_reasoning_turns": 20},
    )
    return service, task


def _save_linked_history(service, task):
    save_thread("historical-source", SENTINEL)
    runs = [
        service._save_run(
            task,
            SENTINEL,
            {},
            {"input_context_ids": ["conversation:historical-source:tail"]},
        )
    ]
    for _ in range(4):
        context = service._retrieve_context(task)
        assert SENTINEL in context["text"]
        runs.append(service._save_run(task, SENTINEL, {}, context))
    prior = service.list_runs(task["id"], limit=4)
    assert len(prior) == 4
    assert all(
        any(item.startswith("reflection_run:") for item in run["input_context_ids"])
        for run in prior
    )
    assert not any(
        item.startswith("conversation:")
        for run in prior
        for item in run["input_context_ids"]
    )
    return runs


@pytest.mark.parametrize("restricted", [False, True])
@pytest.mark.parametrize("legacy_receipt", [False, True])
def test_reflection_lineage_rechecks_ancestors_beyond_four_notes(
    environment, tmp_path, monkeypatch, restricted, legacy_receipt
):
    dispatches, mirrors = environment
    service, task = _lineage_service(tmp_path)
    _save_linked_history(service, task)
    mirrors.clear()
    if restricted:
        save_thread(
            "historical-source",
            SENTINEL,
            restricted=not legacy_receipt,
            legacy_receipt=legacy_receipt,
        )
        for method in ("_memory_context", "_rag_context"):
            monkeypatch.setattr(
                service,
                method,
                lambda *_a: pytest.fail("Restricted ancestry reached retrieval"),
            )
    result = service.run_task(task["id"], force=True)
    if restricted:
        assert result["status"] == "error"
        assert result["error"]["category"] == "local_only_context"
        assert result["run"]["output"] == ""
        assert SENTINEL not in json.dumps(result["run"])
        assert service.get_task(task["id"])["metadata"]["graph_local_only"] is True
        assert dispatches == mirrors == []
    else:
        assert result["status"] == "ran"
        assert len(dispatches) == 2
        assert SENTINEL in json.dumps(dispatches)
        assert len(mirrors) == 1


def test_reflection_lineage_restriction_survives_repeated_failed_attempts(
    environment, tmp_path
):
    dispatches, mirrors = environment
    service, task = _lineage_service(tmp_path)
    _save_linked_history(service, task)
    mirrors.clear()
    conversation_store.mark_graph_local_only("historical-source")
    for _ in range(6):
        result = service.run_task(task["id"], force=True)
        assert result["error"]["category"] == "local_only_context"
        assert SENTINEL not in json.dumps(result["run"])
    # All four selected notes are now failure receipts with no source links.
    assert all(
        run["input_context_ids"] == [] for run in service.list_runs(task["id"], limit=4)
    )
    assert service.get_task(task["id"])["metadata"]["graph_local_only"] is True
    assert dispatches == mirrors == []


def test_reflection_lineage_shared_diamond_is_not_a_cycle(environment, tmp_path):
    dispatches, mirrors = environment
    service, task = _lineage_service(tmp_path)

    def save(refs):
        return service._save_run(task, PUBLIC, {}, {"input_context_ids": refs})["id"]

    leaf = save([])
    left = save([f"reflection_run:{leaf}"])
    right = save([f"reflection_run:{leaf}"])
    save([f"reflection_run:{left}", f"reflection_run:{right}"])
    mirrors.clear()
    result = service.run_task(task["id"], force=True)
    assert result["status"] == "ran"
    assert len(dispatches) == 2
    assert PUBLIC in json.dumps(dispatches)
    assert len(mirrors) == 1


def test_reflection_lineage_preserves_other_context_id_contracts(environment, tmp_path):
    dispatches, mirrors = environment
    service, task = _lineage_service(tmp_path)
    service._save_run(
        task,
        PUBLIC,
        {},
        {
            "input_context_ids": [
                "memory:ordinary",
                "calendar_event:event",
                "rag",
                "opaque-document-id",
                "https://synthetic.invalid/note",
            ]
        },
    )
    mirrors.clear()
    result = service.run_task(task["id"], force=True)
    assert result["status"] == "ran"
    assert len(dispatches) == 2
    assert PUBLIC in json.dumps(dispatches)
    assert len(mirrors) == 1


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "corrupt_json",
        "non_object",
        "wrong_id",
        "wrong_task",
        "missing_refs",
        "non_list_refs",
        "non_string_ref",
        "invalid_conversation_ref",
        "empty_reflection_ref",
        "bare_conversation_ref",
        "bare_reflection_ref",
        "padded_invalid_ref",
        "cycle",
        "root_identity_alias",
        "node_budget",
        "reference_budget",
    ],
)
def test_reflection_lineage_unverifiable_history_fails_before_retrieval(
    environment, tmp_path, monkeypatch, damage
):
    dispatches, mirrors = environment
    service, task = _lineage_service(tmp_path)
    runs = _save_linked_history(service, task)
    mirrors.clear()
    leaf = dict(runs[0])
    row_id = leaf["id"]
    if damage == "node_budget":
        monkeypatch.setattr(reflection_module, "MAX_REFLECTION_LINEAGE_RUNS", 3)
    elif damage == "reference_budget":
        # Duplicate references must count toward work even when nodes are cached.
        leaf["input_context_ids"] = ["memory:ordinary"] * (
            reflection_module.MAX_REFLECTION_LINEAGE_REFS + 1
        )
    elif damage == "wrong_id":
        leaf["id"] = "mismatched-row"
    elif damage == "wrong_task":
        leaf["task_id"] = "mismatched-task"
    elif damage == "missing_refs":
        del leaf["input_context_ids"]
    elif damage == "non_list_refs":
        leaf["input_context_ids"] = "conversation:historical-source:tail"
    elif damage == "non_string_ref":
        leaf["input_context_ids"] = [42]
    elif damage == "invalid_conversation_ref":
        leaf["input_context_ids"] = ["conversation:historical-source"]
    elif damage == "empty_reflection_ref":
        leaf["input_context_ids"] = ["reflection_run:"]
    elif damage == "bare_conversation_ref":
        leaf["input_context_ids"] = ["conversation"]
    elif damage == "bare_reflection_ref":
        leaf["input_context_ids"] = ["reflection_run"]
    elif damage == "padded_invalid_ref":
        leaf["input_context_ids"] = [" conversation:historical-source "]
    elif damage == "cycle":
        leaf["input_context_ids"] = [f"reflection_run:{runs[-1]['id']}"]
    elif damage == "root_identity_alias":
        row_id = runs[-1]["id"]
        leaf = dict(runs[-1], id=runs[0]["id"])
    payload = json.dumps(leaf)
    if damage == "corrupt_json":
        payload = "invalid JSON " + SENTINEL
    elif damage == "non_object":
        payload = json.dumps([SENTINEL])
    with service._connect() as conn:
        if damage == "missing":
            conn.execute("DELETE FROM reflection_runs WHERE id = ?", (row_id,))
        else:
            conn.execute(
                "UPDATE reflection_runs SET payload = ? WHERE id = ?",
                (payload, row_id),
            )
    for method in ("_memory_context", "_rag_context"):
        monkeypatch.setattr(
            service,
            method,
            lambda *_a: pytest.fail("Unverified ancestry reached retrieval"),
        )
    result = service.run_task(task["id"], force=True)
    assert result["status"] == "error"
    assert result["error"] == {
        "category": "context_provenance_unavailable",
        "message": "Reflection cannot verify the source history of its saved notes. "
        "No background generation was started.",
    }
    assert result["run"]["output"] == ""
    assert SENTINEL not in json.dumps(result["run"])
    assert not service.get_task(task["id"])["metadata"].get("graph_local_only")
    assert dispatches == mirrors == []


@pytest.mark.parametrize("local_first", [False, True])
def test_nested_policy_cannot_release_local_transport_obligation(
    environment, local_first
):
    dispatches, _ = environment
    local = DataPolicy(external=False, allow_remote_query=False)
    external = DataPolicy(external=True, allow_remote_query=True)
    outer, inner = (local, external) if local_first else (external, local)
    with data_policy_scope(outer):
        with pytest.raises(PermissionError, match="local-only context"):
            provider().generate(SENTINEL, _data_policy=inner)
        # The enclosing scope remains intact after the failing nested call.
        assert current_data_policy() == outer
    assert current_data_policy() is None
    assert dispatches == []


def test_nested_local_transport_disables_redirects_and_websocket(
    environment, monkeypatch
):
    dispatches, _ = environment
    service = provider("http://127.0.0.1:8080/v1/responses")
    service.config.update(openai_responses_ws_enabled=True)
    monkeypatch.setattr(
        service,
        "_get_openai_responses_ws_transport",
        lambda **_kwargs: pytest.fail(
            "local-only context must not enter websocket lane"
        ),
    )
    with data_policy_scope(DataPolicy(external=True, allow_remote_query=True)):
        with data_policy_scope(DataPolicy(external=False, allow_remote_query=False)):
            assert local_only_transport_required()
            service.generate(SENTINEL)
    assert dispatches
    assert all(options.get("allow_redirects") is False for _, options in dispatches)


@pytest.mark.parametrize(
    "message",
    [
        {"metadata": {"graph_local_only": True}},
        Message(
            role="assistant", content=SENTINEL, metadata={"graph_local_only": True}
        ),
        {"tools": [{"name": "graph.query", "status": "completed"}]},
        {"metadata": {"tool_results": True, "graph_result_refs": ["synthetic"]}},
        {"content": '{"name":"graph.query"}', "metadata": {"tool_results": True}},
    ],
)
def test_graph_custody_survives_supported_history_envelopes(environment, message):
    assert conversation_store.context_graph_local_only(None, [message])


def test_graph_request_or_plain_mention_is_not_a_completed_result(environment):
    assert not conversation_store.context_graph_local_only(
        None,
        [
            {"tools": [{"name": "graph.query", "status": "pending"}]},
            {"content": 'The tool is {"name":"graph.query"}'},
        ],
    )
