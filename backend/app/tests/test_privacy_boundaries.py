"""Synthetic checks at the memory, retrieval, and tool-dispatch boundaries."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from app.base_services import MemoryManager
from app.services import rag_service
from app.services.rag_service import RAGService, _InMemoryBackend
from app.tools import memory as memory_tools
from app.utils import generate_signature
from app.utils.knowledge_store import KnowledgeStore
from cryptography.fernet import Fernet

SECRET = "SYNTHETIC_SECRET_9b2a"  # gitleaks:allow -- synthetic privacy-test marker
PROTECTED = "SYNTHETIC_PROTECTED_5c81"


@pytest.fixture
def services(tmp_path, monkeypatch):
    from app.services import rag_provider
    from app.utils import user_settings

    monkeypatch.setattr(user_settings, "USER_SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setenv("FLOAT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        RAGService,
        "_init_backend",
        lambda self, *_args: _InMemoryBackend(self.class_name, self._embed_text),
    )
    service = RAGService(
        backend="memory", embedding_model="simple", sqlite_path=str(tmp_path / "kb.db")
    )
    manager = MemoryManager(
        {
            "mem_key": Fernet.generate_key().decode(),
            "memory_store_path": str(tmp_path / "memory.db"),
        }
    )
    monkeypatch.setattr(memory_tools, "_MANAGER", manager)
    monkeypatch.setattr(
        rag_provider, "get_rag_service", lambda *_args, **_kwargs: service
    )
    monkeypatch.setattr(
        memory_tools, "get_rag_service", lambda *_args, **_kwargs: service
    )
    monkeypatch.setattr(
        memory_tools,
        "try_ingest_text",
        lambda text, metadata, **kwargs: service.ingest_text(text, metadata, **kwargs),
    )
    return manager, service


def remember(manager, **args):
    return memory_tools.remember(
        user="review", signature=generate_signature("review", "remember", args), **args
    )


def test_secret_memory_projects_no_value_or_summary_into_knowledge(services):
    manager, service = services
    remember(
        manager, key="private-note", value=SECRET, sensitivity="secret", vectorize=True
    )
    raw = manager.store["private-note"]
    assert raw["encrypted"] is True
    assert SECRET not in json.dumps(raw)
    assert manager.get_item("private-note", touch=False)["value"] == SECRET
    doc = service.canonical_store.get_item_by_source("memory:private-note")
    assert doc is not None, "Keep a discoverable reference to the authoritative memory"
    assert SECRET not in json.dumps(doc)
    assert service.backend.list_docs()["ids"] == []
    assert manager.get_item("private-note", touch=False)["vectorize"] is False


def test_secret_projection_replaces_previous_plaintext_chunks(services):
    manager, service = services
    remember(manager, key="changing-note", value=SECRET, sensitivity="mundane")
    assert service.backend.list_docs()["ids"]
    remember(manager, key="changing-note", value=SECRET, sensitivity="secret")
    with service.canonical_store._connect() as conn:
        items = conn.execute(
            "SELECT text, summary_text, metadata_json FROM knowledge_items"
        ).fetchall()
        chunks = conn.execute(
            "SELECT text, metadata_json FROM knowledge_chunks"
        ).fetchall()
    assert SECRET not in str([tuple(row) for row in items + chunks])
    assert service.backend.list_docs()["ids"] == []
    remember(
        manager,
        key="changing-note",
        value="ordinary replacement",
        sensitivity="mundane",
    )
    assert service.backend.list_docs()["ids"]
    assert (
        "ordinary replacement"
        in service.canonical_store.get_item_by_source("memory:changing-note")["text"]
    )


def test_canonical_store_guards_direct_secret_memory_summary_and_chunks(tmp_path):
    store = KnowledgeStore(str(tmp_path / "canonical.db"))
    store.upsert_document(
        source="memory:direct",
        text=SECRET,
        metadata={"sensitivity": " SECRET ", "kind": "memory", "summary_text": SECRET},
        chunk_texts=[SECRET, SECRET],
        summary_text=SECRET,
    )
    with store._connect() as conn:
        values = conn.execute(
            "SELECT text, summary_text, metadata_json FROM knowledge_items"
        ).fetchall()
        chunks = conn.execute(
            "SELECT text, metadata_json FROM knowledge_chunks"
        ).fetchall()
    assert SECRET not in str([tuple(row) for row in values + chunks])


@pytest.fixture
def api_service(services, tmp_path, monkeypatch):
    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"embedding": [1.0] * 32}]}

    def post(url, **kwargs):
        calls.append(kwargs["json"])
        return Response()

    monkeypatch.setattr(rag_service.http_session, "post", post)
    service = RAGService(
        backend="memory",
        embedding_model="api:synthetic-embed",
        api_url="https://synthetic.invalid/v1/responses",
        api_key="synthetic-key",
        sqlite_path=str(tmp_path / "api.db"),
    )
    return service, calls


@pytest.mark.parametrize("sensitivity", ["protected", " Protected ", "secret"])
def test_private_ingest_never_calls_api_embedding(api_service, sensitivity):
    service, calls = api_service
    service.ingest_text(
        PROTECTED, {"source": "private-doc", "sensitivity": sensitivity}
    )
    assert calls == []
    assert service.backend.list_docs()["ids"] == []
    doc = service.canonical_store.get_item_by_source("private-doc")
    assert doc is not None
    assert doc["metadata"]["retrieval_mirror_status"] == "skipped_privacy"


def test_public_api_embedding_and_protected_local_embedding_remain_available(
    api_service, services
):
    service, calls = api_service
    service.ingest_text(
        "ordinary public text", {"source": "public", "sensitivity": "public"}
    )
    assert calls[0]["input"] == "ordinary public text"
    local = services[1]
    local.ingest_text(
        PROTECTED, {"source": "local-private", "sensitivity": "protected"}
    )
    assert local.backend.list_docs()["ids"]


def test_rehydrate_and_update_cannot_bypass_private_embedding_guard(api_service):
    service, calls = api_service
    doc_id = service.ingest_text("public first", {"source": "changing"})
    calls.clear()
    service.update_doc(doc_id, PROTECTED, {"sensitivity": "protected"})
    assert calls == []
    assert service.backend.list_docs()["ids"] == []
    assert (
        service.rehydrate_canonical_document(
            PROTECTED, {"source": "changing", "sensitivity": "protected"}
        )
        is False
    )
    assert calls == []


def test_vector_only_update_preserves_existing_private_classification(api_service):
    service, calls = api_service
    service.canonical_store = None
    # A legacy local mirror can predate changing the configured encoder to API.
    service.backend._store["legacy"] = {
        "id": "legacy",
        "properties": {
            "source": "legacy",
            "text": PROTECTED,
            "sensitivity": "protected",
        },
        "embedding": [1.0] * 32,
    }
    service.update_doc("legacy", PROTECTED)
    assert calls == []
    assert service.backend.list_docs()["ids"] == []


@pytest.mark.parametrize("mode", ["api", "server", "unknown"])
@pytest.mark.parametrize("args", [{}, {"for_external": False, "allow_protected": True}])
def test_agent_cannot_relax_external_recall_policy(services, mode, args):
    manager, _ = services
    manager.upsert_item("private-note", SECRET, sensitivity="secret")
    manager.register_tool("recall", memory_tools.recall)
    payload = {"key": "private-note", **args}
    result = manager.invoke_tool(
        "recall",
        user="review",
        signature=generate_signature("review", "recall", payload),
        _action_context={"mode": mode},
        **payload,
    )
    assert SECRET not in str(result)


def test_dispatch_policy_is_scoped_and_local_direct_access_survives(services):
    manager, _ = services
    manager.upsert_item("private-note", SECRET, sensitivity="secret")
    manager.register_tool("recall", memory_tools.recall)
    payload = {"key": "private-note"}
    signature = generate_signature("review", "recall", payload)
    barrier = Barrier(2)

    def read(mode):
        barrier.wait()
        return manager.invoke_tool(
            "recall",
            user="review",
            signature=signature,
            _action_context={"mode": mode},
            **payload,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        external, local = list(pool.map(read, ["api", "local"]))
    assert SECRET not in str(external)
    assert local == SECRET
    assert memory_tools.recall(user="review", signature=signature, **payload) == SECRET


def test_legacy_secret_save_uses_an_opaque_generated_identifier(services):
    manager, service = services
    payload = {"text": SECRET, "privacy": "secret", "vectorize": True}
    result = memory_tools.legacy_memory_save(
        user="review",
        signature=generate_signature("review", "memory.save", payload),
        **payload,
    )
    key = result["key"]
    assert key.startswith("secret-")
    assert SECRET.lower().replace("_", "-") not in key
    assert SECRET in str(manager.get_item(key, touch=False)["value"])
    assert SECRET not in json.dumps(
        service.canonical_store.get_item_by_source(f"memory:{key}")
    )


def test_private_reclassification_reports_failed_mirror_cleanup(services, monkeypatch):
    from app.services import rag_provider
    from app.utils.privacy_policy import PrivacyCleanupError

    manager, service = services
    remember(manager, key="cleanup", value=SECRET)

    def unavailable(*_args):
        raise RuntimeError("synthetic unavailable store")

    monkeypatch.setattr(service.backend, "delete_doc", unavailable)
    monkeypatch.setattr(service.backend, "delete_source", unavailable)
    monkeypatch.setattr(rag_provider, "get_rag_service", lambda *_a, **_kw: service)
    monkeypatch.setattr(memory_tools, "try_ingest_text", rag_provider.try_ingest_text)
    with pytest.raises(PrivacyCleanupError, match="could not be verified"):
        remember(manager, key="cleanup", value=SECRET, sensitivity="secret")
    assert manager.store["cleanup"]["encrypted"] is True
    assert SECRET not in json.dumps(
        service.canonical_store.get_item_by_source("memory:cleanup")
    )


def test_sync_import_and_export_project_secret_memory_content(services, monkeypatch):
    from app.services.instance_sync_service import InstanceSyncService
    from app.utils import knowledge_store

    _, service = services
    store = service.canonical_store
    monkeypatch.setattr(knowledge_store, "resolve_path", lambda *_args: store.path)
    record = {
        "knowledge_id": "synthetic-sync",
        "source": "memory:sync",
        "kind": "memory",
        "text": SECRET,
        "summary_text": SECRET,
        "title": SECRET,
        "metadata": {"sensitivity": "secret", "hint": SECRET},
        "updated_at": 2.0,
        "chunks": [
            {
                "chunk_id": "synthetic-sync",
                "text": SECRET,
                "metadata": {"sensitivity": "secret", "summary_text": SECRET},
            }
        ],
    }
    sync = InstanceSyncService()
    assert sync._merge_knowledge([record])["applied"] == 1
    assert SECRET not in json.dumps(store.get_item("synthetic-sync"))
    assert SECRET not in json.dumps(store.get_chunk("synthetic-sync"))
    monkeypatch.setattr(sync, "_knowledge_records", lambda: [record])
    assert SECRET not in json.dumps(sync._knowledge_snapshot())


def test_rehydrating_legacy_secret_memory_repairs_its_canonical_copy(services):
    _, service = services
    store = service.canonical_store
    doc = store.upsert_document(
        source="memory:legacy",
        text=SECRET,
        metadata={"kind": "memory"},
        chunk_texts=[SECRET],
    )
    assert (
        service.rehydrate_canonical_document(
            SECRET,
            {"source": "memory:legacy", "kind": "memory", "sensitivity": "secret"},
            knowledge_id=doc["id"],
        )
        is False
    )
    assert SECRET not in json.dumps(store.get_item(doc["id"]))
    assert service.backend.list_docs()["ids"] == []


def test_local_query_uses_canonical_search_without_remote_embedding(api_service):
    from app.utils.privacy_policy import data_policy_scope, policy_for_destination

    service, calls = api_service
    service.ingest_text(
        PROTECTED, {"source": "private-query", "sensitivity": "protected"}
    )
    with data_policy_scope(policy_for_destination("local")):
        result = service.query(PROTECTED)
    assert calls == []
    assert PROTECTED in str(result)


def test_external_query_obeys_current_canonical_sensitivity(services, monkeypatch):
    from app.utils.privacy_policy import data_policy_scope, policy_for_destination

    _, service = services
    doc_id = service.ingest_text(
        PROTECTED, {"source": "old-mirror", "sensitivity": "protected"}
    )
    monkeypatch.setattr(
        service.backend,
        "query",
        lambda *_args: [
            {"id": doc_id, "text": PROTECTED, "metadata": {"sensitivity": "mundane"}},
            {
                "id": "public",
                "text": "public result",
                "metadata": {"sensitivity": "public"},
            },
        ],
    )
    with data_policy_scope(policy_for_destination("api")):
        result = service.query("query")
    assert PROTECTED not in str(result)
    assert "public result" in str(result)


def test_disabling_a_memory_mirror_preserves_durable_knowledge(services):
    manager, service = services
    remember(manager, key="durable", value="ordinary durable knowledge")
    manager.update_item_fields("durable", {"vectorize": False})
    assert service.backend.list_docs()["ids"] == []
    assert service.canonical_store.get_item_by_source("memory:durable")["text"]
    assert manager.delete_item("durable") is True
    assert service.canonical_store.get_item_by_source("memory:durable") is None


def test_protected_memory_reports_skipped_api_mirror(
    api_service, services, monkeypatch
):
    from app.services import rag_provider

    manager, _ = services
    service, calls = api_service
    monkeypatch.setattr(rag_provider, "get_rag_service", lambda *_a, **_kw: service)
    monkeypatch.setattr(memory_tools, "get_rag_service", lambda *_a, **_kw: service)
    monkeypatch.setattr(memory_tools, "try_ingest_text", rag_provider.try_ingest_text)
    remember(
        manager,
        key="protected",
        value=PROTECTED,
        sensitivity="protected",
        vectorize=True,
    )
    assert calls == []
    item = manager.get_item("protected", touch=False)
    assert item["vectorize"] is False
    assert item["vectorized_at"] is None
    assert (
        PROTECTED
        in service.canonical_store.get_item_by_source("memory:protected")["text"]
    )


@pytest.mark.parametrize(
    "status,level",
    [("matched", "protected"), ("matched", "secret"), ("unavailable", None)],
)
def test_remote_query_preflight_blocks_private_or_unchecked_query(
    api_service, monkeypatch, status, level
):
    from types import SimpleNamespace

    service, calls = api_service
    monkeypatch.setattr(
        rag_service.privacy_filter_service,
        "decide_sensitivity",
        lambda *_a, **_kw: SimpleNamespace(status=status, applied_sensitivity=level),
    )
    service.query(PROTECTED)
    assert calls == []


def test_sync_chunk_policy_cannot_be_downgraded_by_parent_metadata():
    from app.utils.privacy_policy import project_knowledge_record

    record = {
        "source": "memory:chunk",
        "kind": "memory",
        "text": SECRET,
        "metadata": {"sensitivity": "mundane"},
        "chunks": [{"text": SECRET, "metadata": {"sensitivity": "secret"}}],
    }
    projected = project_knowledge_record(record)
    assert SECRET not in json.dumps(projected)
    assert projected["metadata"]["sensitivity"] == "secret"


@pytest.mark.parametrize(
    "mode,endpoint",
    [
        ("api", "https://synthetic.invalid/v1"),
        ("server", "http://192.0.2.1:8080/v1"),
        ("dynamic", "https://synthetic.invalid/generate"),
    ],
)
def test_actual_model_transport_rejects_local_context_after_mode_drift(
    services, monkeypatch, mode, endpoint
):
    from app import base_services
    from app.base_services import LLMService
    from app.utils.privacy_policy import current_data_policy, policy_for_destination

    calls = []
    monkeypatch.setattr(
        base_services.http_session, "post", lambda *_a, **_kw: calls.append("post")
    )
    service = LLMService(
        mode=mode,
        config={
            "api_url": endpoint,
            "server_url": endpoint,
            "dynamic_url": endpoint,
            "api_key": "synthetic-key",
        },
    )
    service.dynamic_process = object()
    # A concurrent request may have changed the shared mode since local context was assembled.
    with pytest.raises(PermissionError, match="local-only context"):
        service.generate(SECRET, _data_policy=policy_for_destination("local"))
    assert calls == []
    assert current_data_policy() is None


@pytest.mark.parametrize(
    "endpoint",
    ["http://localhost:8080/v1", "http://127.0.0.1:1234/v1", "http://[::1]:8080/v1"],
)
def test_local_transport_policy_accepts_loopback_endpoints(endpoint):
    from app.utils.privacy_policy import (
        data_policy_scope,
        policy_for_destination,
        require_allowed_endpoint,
    )

    with data_policy_scope(policy_for_destination("local")):
        require_allowed_endpoint(endpoint)


def test_local_transport_cannot_redirect_or_use_an_external_websocket(
    services, monkeypatch
):
    from types import SimpleNamespace

    from app import base_services
    from app.base_services import LLMService
    from app.utils.privacy_policy import policy_for_destination

    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(
            status_code=200,
            headers={},
            text="synthetic",
            json=lambda: {"output": [], "output_text": "okay"},
            raise_for_status=lambda: None,
        )

    monkeypatch.setattr(base_services.http_session, "post", post)
    service = LLMService(
        mode="api",
        config={
            "api_url": "http://127.0.0.1:8080/v1/responses",
            "api_key": "synthetic-key",
            "openai_responses_ws_enabled": True,
            "openai_responses_ws_url": "wss://synthetic.invalid/v1/responses",
        },
    )
    monkeypatch.setattr(
        service,
        "_get_openai_responses_ws_transport",
        lambda **_kwargs: pytest.fail(
            "local context must not enter the websocket lane"
        ),
    )
    response = service.generate(
        SECRET, model="synthetic-model", _data_policy=policy_for_destination("local")
    )
    assert calls, response
    assert all(options.get("allow_redirects") is False for _, options in calls)
    assert "_data_policy" not in str(calls)


def test_external_query_uses_current_parent_policy_for_real_chunk_ids(
    services, monkeypatch
):
    from app.utils.privacy_policy import data_policy_scope, policy_for_destination

    _, service = services
    monkeypatch.setattr(
        service, "_chunk_texts", lambda _text: [PROTECTED, "second chunk"]
    )
    doc_id = service.ingest_text(
        PROTECTED, {"source": "chunked", "sensitivity": "mundane"}
    )
    vector_id = service.backend.list_docs()["ids"][0]
    assert service.canonical_store.trace(vector_id) is None
    # Sync can update canonical policy independently of an older vector mirror.
    service.canonical_store.upsert_document(
        source="chunked",
        text=PROTECTED,
        metadata={"sensitivity": "protected"},
        chunk_texts=[PROTECTED, "second chunk"],
        knowledge_id=doc_id,
    )
    with data_policy_scope(policy_for_destination("api")):
        assert service.query(PROTECTED) == []


@pytest.mark.parametrize(
    "failure", [None, "read", "delete", "enumerate", "open", "retained"]
)
def test_chroma_private_cleanup_covers_all_collections_and_propagates_failures(
    services, failure
):
    from types import SimpleNamespace

    from app.utils.privacy_policy import PrivacyCleanupError

    class Collection:
        def __init__(self):
            self.rows = {
                "chunk-id": {"source": "private#chunk:1", "root_source": "private"},
                "public-id": {"source": "public"},
            }

        def get(self, ids=None, **_kwargs):
            if failure == "read":
                raise RuntimeError("synthetic read failure")
            selected = [key for key in self.rows if ids is None or key in ids]
            return {"ids": selected, "metadatas": [self.rows[key] for key in selected]}

        def delete(self, *, ids):
            if failure == "delete":
                raise RuntimeError("synthetic deletion failure")
            if failure != "retained":
                for key in ids:
                    self.rows.pop(key, None)

    collections = {"Knowledge": Collection(), "Knowledge_dim64": Collection()}

    def list_collections():
        if failure == "enumerate":
            raise RuntimeError("synthetic enumeration failure")
        return list(collections)

    def get_collection(name):
        if failure == "open":
            raise RuntimeError("synthetic open failure")
        return collections[name]

    backend = object.__new__(rag_service._ChromaBackend)
    backend.collection_name = backend._root_collection_name = "Knowledge"
    backend.collection = collections["Knowledge"]
    backend.client = SimpleNamespace(
        list_collections=list_collections, get_collection=get_collection
    )
    service = services[1]
    service.backend = backend
    if failure:
        with pytest.raises(PrivacyCleanupError, match="could not be verified"):
            service.delete_retrieval_mirror("private")
    else:
        service.delete_retrieval_mirror("private")
        assert all(
            set(collection.rows) == {"public-id"} for collection in collections.values()
        )


def test_weaviate_adapter_without_full_enumeration_cannot_claim_private_cleanup(
    services,
):
    from app.utils.privacy_policy import PrivacyCleanupError

    backend = object.__new__(rag_service._WeaviateBackend)
    backend._uses_collections_api = False
    service = services[1]
    service.backend = backend
    with pytest.raises(PrivacyCleanupError, match="could not be verified"):
        service.delete_retrieval_mirror("private")


def test_weaviate_complete_iterator_removes_only_the_private_source(services):
    from types import SimpleNamespace

    rows = {
        "private-id": {"source": "private#chunk:1", "root_source": "private"},
        "public-id": {"source": "public"},
    }
    collection = SimpleNamespace(
        iterator=lambda: iter(
            [SimpleNamespace(uuid=key, properties=value) for key, value in rows.items()]
        ),
        data=SimpleNamespace(delete_by_id=lambda key: rows.pop(key)),
    )
    backend = object.__new__(rag_service._WeaviateBackend)
    backend._uses_collections_api = True
    backend._ensure_v4_collection = lambda: collection
    service = services[1]
    service.backend = backend
    service.delete_retrieval_mirror("private")
    assert set(rows) == {"public-id"}
