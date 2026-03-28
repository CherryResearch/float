import sys
import types
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

BACKEND_ROOT = Path(__file__).resolve().parents[2]
BACKEND_STR = str(BACKEND_ROOT)
if BACKEND_STR not in sys.path:
    sys.path.insert(0, BACKEND_STR)

from app import routes  # noqa: E402
from app.main import app  # noqa: E402
from app.services.rag_service import RAGService  # noqa: E402


@pytest.fixture
def client():
    return TestClient(app)


def test_rag_service_weaviate_backend_falls_back_to_chroma(monkeypatch, tmp_path):
    from app.services import rag_service

    class BrokenWeaviateBackend:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("weaviate unavailable")

    class DummyChromaBackend:
        def __init__(self, *_args, **_kwargs):
            self._docs = {}

        def ensure_schema(self):
            return None

        def add_text(self, text, source, metadata):
            doc_id = source or f"doc:{len(self._docs)}"
            self._docs[doc_id] = {
                "id": doc_id,
                "properties": {"text": text, **(metadata or {})},
            }
            return doc_id

        def get_doc(self, doc_id):
            return self._docs.get(doc_id)

    monkeypatch.setattr(rag_service, "_WeaviateBackend", BrokenWeaviateBackend)
    monkeypatch.setattr(rag_service, "_ChromaBackend", DummyChromaBackend)

    service = rag_service.RAGService(
        backend="weaviate",
        persist_dir=str(tmp_path),
        sqlite_path=str(tmp_path / "memory.sqlite3"),
        enable_canonical_store=False,
    )

    assert type(service.backend).__name__ == "DummyChromaBackend"
    doc_id = service.ingest_text(
        "Fallback storage stays persistent.", {"source": "fallback"}
    )
    trace = service.trace(doc_id)
    assert trace is not None
    assert "persistent" in trace["text"]


def test_weaviate_backend_supports_v4_collections_api(monkeypatch):
    from app.services import rag_service

    class DummyMetadata:
        def __init__(self, distance):
            self.distance = distance

    class DummyObject:
        def __init__(self, uuid, properties, distance=0.1):
            self.uuid = uuid
            self.properties = dict(properties)
            self.metadata = DummyMetadata(distance)

    class DummyDataOps:
        def __init__(self, store):
            self._store = store

        def insert(self, properties, uuid, vector):
            self._store[str(uuid)] = {
                "uuid": str(uuid),
                "properties": dict(properties),
                "vector": list(vector),
            }

        def delete_by_id(self, uuid):
            self._store.pop(str(uuid), None)

    class DummyQueryOps:
        def __init__(self, store):
            self._store = store

        def fetch_object_by_id(self, uuid):
            record = self._store.get(str(uuid))
            if record is None:
                return None
            return DummyObject(record["uuid"], record["properties"])

        def near_vector(self, _vector, limit=5, return_metadata=None):
            objects = [
                DummyObject(record["uuid"], record["properties"], 0.05)
                for record in self._store.values()
            ]
            return type("DummyResponse", (), {"objects": objects[:limit]})()

    class DummyCollection:
        def __init__(self):
            self._store = {}
            self.data = DummyDataOps(self._store)
            self.query = DummyQueryOps(self._store)

        def iterator(self):
            for record in self._store.values():
                yield DummyObject(record["uuid"], record["properties"])

    class DummyCollections:
        def __init__(self):
            self._collections = {}

        def exists(self, name):
            return name in self._collections

        def create_from_dict(self, schema):
            self._collections[schema["class"]] = DummyCollection()

        def use(self, name):
            return self._collections[name]

    class DummyClient:
        def __init__(self):
            self.collections = DummyCollections()

    monkeypatch.setattr(
        rag_service, "create_client", lambda *_args, **_kwargs: DummyClient()
    )
    monkeypatch.setitem(sys.modules, "weaviate", types.ModuleType("weaviate"))
    monkeypatch.setitem(
        sys.modules, "weaviate.classes", types.ModuleType("weaviate.classes")
    )
    query_module = types.ModuleType("weaviate.classes.query")
    query_module.MetadataQuery = lambda **_kwargs: {"distance": True}
    monkeypatch.setitem(sys.modules, "weaviate.classes.query", query_module)

    backend = rag_service._WeaviateBackend(
        "Knowledge",
        "http://localhost:8080",
        None,
        lambda text: [float(len(text)), 1.0, 0.5],
    )
    backend.ensure_schema()

    doc_id = backend.add_text("hello from v4", "doc:v4", {"kind": "document"})
    listing = backend.list_docs()
    assert doc_id in listing["ids"]

    match = backend.query("hello", top_k=1)[0]
    assert match["id"] == doc_id
    assert match["metadata"]["source"] == "doc:v4"

    trace = backend.get_doc(doc_id)
    assert trace is not None
    assert trace["properties"]["text"] == "hello from v4"

    backend.delete_source("doc:v4")
    assert backend.list_docs()["ids"] == []


def test_import_from_weaviate_supports_v4_collections_api(monkeypatch, tmp_path):
    from app.services import rag_service

    class DummyObject:
        def __init__(self, uuid, properties):
            self.uuid = uuid
            self.properties = dict(properties)

    class DummyCollection:
        def iterator(self):
            yield DummyObject(
                "w1",
                {"text": "from weaviate", "source": "doc:weaviate", "kind": "document"},
            )

    class DummyCollections:
        def use(self, _name):
            return DummyCollection()

    class DummyClient:
        def __init__(self):
            self.collections = DummyCollections()

    monkeypatch.setattr(
        rag_service, "create_client", lambda *_args, **_kwargs: DummyClient()
    )

    service = rag_service.RAGService(
        backend="chroma",
        persist_dir=str(tmp_path),
        sqlite_path=str(tmp_path / "memory.sqlite3"),
    )

    ids = service.import_from_weaviate("http://localhost:8080", "Knowledge")

    assert len(ids) == 1
    trace = service.trace(ids[0])
    assert trace is not None
    assert trace["metadata"]["source"] == "doc:weaviate"
    assert trace["text"] == "from weaviate"


def test_rag_service_query_returns_match(tmp_path):
    # Use an isolated persist dir so the assertion isn't polluted by
    # previously-ingested knowledge in the developer's local Chroma store.
    service = RAGService(
        backend="chroma",
        persist_dir=str(tmp_path),
        sqlite_path=str(tmp_path / "memory.sqlite3"),
    )
    doc_id = service.ingest_text(
        "The capital of France is Paris.",
        {"source": "test-note"},
    )
    results = service.query("What is the capital of France?", top_k=3)
    assert results, "RAG query returned no results"
    assert any(match.get("id") == doc_id for match in results)
    assert any("Paris" in (match.get("text") or "") for match in results)
    trace = service.trace(doc_id)
    assert trace is not None
    assert "Paris" in trace["text"]


def test_chat_includes_rag_context(monkeypatch, client):
    class DummyRAG:
        def query(self, text, top_k=3):
            return [
                {
                    "id": "doc1",
                    "text": "Paris is the capital of France.",
                    "metadata": {"source": "notes"},
                    "score": 0.99,
                }
            ]

        def trace(self, doc_id):
            return None

    monkeypatch.setattr(routes, "_get_rag_service", lambda: DummyRAG())

    captured = {}

    def fake_generate(
        prompt,
        session_id="default",
        model=None,
        attachments=None,
        context=None,
        **kwargs,
    ):
        captured["context"] = context
        return {
            "text": "Paris is the capital of France.",
            "thought": "",
            "tools_used": [],
            "metadata": {},
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    response = client.post(
        "/chat",
        json={
            "message": "What is the capital of France?",
            "session_id": "sess",
            "use_rag": True,
        },
    )
    assert response.status_code == 200
    ctx = captured.get("context")
    assert ctx is not None, "Generation context was not captured"
    rag_messages = [
        msg
        for msg in ctx.messages
        if isinstance(msg.get("metadata"), dict) and msg["metadata"].get("rag")
    ]
    assert rag_messages, "No RAG context message injected"
    rag_match = rag_messages[0]["metadata"]["rag"]["matches"][0]
    assert rag_match["source"] == "notes"
    rag_prompt = rag_messages[0].get("content", "")
    assert "[notes]" not in rag_prompt
    assert "score:" not in rag_prompt
    body = response.json()
    rag_meta = body["metadata"].get("rag")
    assert rag_meta and rag_meta["matches"][0]["source"] == "notes"


def test_chat_rag_filters_excluded_and_sensitive(monkeypatch, client):
    class DummyRAG:
        def query(self, text, top_k=3):
            return [
                {
                    "id": "doc-secret",
                    "text": "secret payload",
                    "metadata": {"source": "secret", "sensitivity": "secret"},
                    "score": 0.99,
                },
                {
                    "id": "doc-excluded",
                    "text": "excluded payload",
                    "metadata": {"source": "excluded", "rag_excluded": True},
                    "score": 0.98,
                },
                {
                    "id": "doc-ok",
                    "text": "ok payload",
                    "metadata": {"source": "ok"},
                    "score": 0.97,
                },
            ]

        def trace(self, doc_id):
            return None

    monkeypatch.setattr(routes, "_get_rag_service", lambda: DummyRAG())
    monkeypatch.setattr(
        routes, "_get_clip_rag_service", lambda *, raise_http=False: None
    )
    monkeypatch.setattr(routes.llm_service, "mode", "api", raising=False)

    def fake_generate(
        prompt,
        session_id="default",
        model=None,
        attachments=None,
        context=None,
        **kwargs,
    ):
        return {
            "text": "ok",
            "thought": "",
            "tools_used": [],
            "metadata": {},
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    response = client.post(
        "/chat",
        json={
            "message": "test",
            "session_id": "sess",
            "use_rag": True,
        },
    )
    assert response.status_code == 200
    body = response.json()
    rag_section = body["metadata"].get("rag") or {}
    matches = rag_section.get("matches") or []
    sources = {m.get("source") for m in matches if isinstance(m, dict)}
    assert "ok" in sources
    assert "excluded" not in sources
    assert "secret" not in sources


def test_chat_rag_truncates_metadata_text(monkeypatch, client):
    long_text = "Paris " * 2000

    class DummyRAG:
        def query(self, text, top_k=3):
            return [
                {
                    "id": "doc1",
                    "text": long_text,
                    "metadata": {"source": "notes"},
                    "score": 0.99,
                }
            ]

        def trace(self, doc_id):
            return None

    monkeypatch.setattr(routes, "_get_rag_service", lambda: DummyRAG())
    monkeypatch.setattr(
        routes, "_get_clip_rag_service", lambda *, raise_http=False: None
    )

    def fake_generate(
        prompt,
        session_id="default",
        model=None,
        attachments=None,
        context=None,
        **kwargs,
    ):
        return {
            "text": "ok",
            "thought": "",
            "tools_used": [],
            "metadata": {},
        }

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    client.app.state.config["rag_chat_match_chars"] = 180
    client.app.state.config["rag_chat_prompt_snippet_chars"] = 50

    response = client.post(
        "/chat",
        json={
            "message": "What is the capital of France?",
            "session_id": "sess",
            "use_rag": True,
        },
    )
    assert response.status_code == 200
    body = response.json()
    rag_section = body["metadata"].get("rag") or {}
    matches = rag_section.get("matches") or []
    assert matches, "Expected RAG matches in response metadata"
    match = matches[0]
    assert isinstance(match.get("text"), str)
    assert len(match["text"]) <= 180


def test_caption_image_stores_caption_and_clip_embedding(monkeypatch, client, tmp_path):
    sqlite_path = str(tmp_path / "memory.sqlite3")
    text_service = RAGService(
        backend="chroma",
        persist_dir=str(tmp_path),
        sqlite_path=sqlite_path,
    )
    clip_service = RAGService(
        class_name="KnowledgeClip",
        backend="chroma",
        persist_dir=str(tmp_path),
        embedding_model="clip:ViT-B-32",
        sqlite_path=sqlite_path,
    )

    monkeypatch.setattr(routes, "_get_rag_service", lambda: text_service)
    monkeypatch.setattr(
        routes,
        "_get_clip_rag_service",
        lambda *, raise_http=True: clip_service,
    )

    class DummyCaptioner:
        model = "dummy"

        def run(self, data):
            return {"image_caption": "a test image", "placeholder": False}

    monkeypatch.setattr(routes, "VisionCaptioner", DummyCaptioner)

    from app.services import clip_embeddings  # noqa: E402

    monkeypatch.setattr(
        clip_embeddings,
        "embed_clip_image_bytes",
        lambda *_args, **_kwargs: [0.1, 0.2, 0.3],
    )

    res = client.post(
        "/knowledge/caption-image",
        files={"file": ("pic.png", b"not-a-real-png", "image/png")},
    )
    assert res.status_code == 200
    payload = res.json()
    assert payload["caption"] == "a test image"
    assert payload["clip"]["saved"] is True
    assert payload["clip"]["dim"] == 3

    # Caption stored in the text index
    trace = text_service.trace(payload["id"])
    assert trace is not None
    assert "a test image" in trace["text"]

    # Image embedding stored in the CLIP index
    clip_trace = clip_service.trace(payload["clip"]["id"])
    assert clip_trace is not None
    assert clip_trace["metadata"].get("kind") == "image_embedding"


def test_attachment_caption_updates_reindex_text_and_clip(
    monkeypatch, client, tmp_path
):
    sqlite_path = str(tmp_path / "memory.sqlite3")
    text_service = RAGService(
        backend="chroma",
        persist_dir=str(tmp_path),
        sqlite_path=sqlite_path,
    )
    clip_service = RAGService(
        class_name="KnowledgeClip",
        backend="chroma",
        persist_dir=str(tmp_path),
        embedding_model="clip:ViT-B-32",
        sqlite_path=sqlite_path,
    )

    monkeypatch.setattr(routes, "_get_rag_service", lambda: text_service)
    monkeypatch.setattr(
        routes,
        "_get_clip_rag_service",
        lambda *, raise_http=True: clip_service,
    )

    class DummyCaptioner:
        model = "dummy"

        def run(self, data):
            return {"image_caption": "auto caption", "placeholder": False}

    monkeypatch.setattr(routes, "VisionCaptioner", DummyCaptioner)

    from app.services import clip_embeddings  # noqa: E402

    monkeypatch.setattr(
        clip_embeddings,
        "embed_clip_image_bytes",
        lambda *_args, **_kwargs: [0.1, 0.2, 0.3],
    )

    upload_resp = client.post(
        "/attachments/upload",
        files={"file": ("sample.png", b"fake-image", "image/png")},
    )
    assert upload_resp.status_code == 200
    content_hash = upload_resp.json()["content_hash"]

    put_resp = client.put(
        f"/attachments/caption/{content_hash}",
        json={"caption": "manual caption"},
    )
    assert put_resp.status_code == 200

    text_docs = text_service.list_docs()
    doc_id = next(
        doc_id
        for doc_id, meta in zip(
            text_docs.get("ids") or [],
            text_docs.get("metadatas") or [],
        )
        if isinstance(meta, dict) and meta.get("content_hash") == content_hash
    )
    assert text_service.trace(doc_id)["text"] == "manual caption"

    clip_docs = clip_service.list_docs()
    clip_doc_id = next(
        doc_id
        for doc_id, meta in zip(
            clip_docs.get("ids") or [],
            clip_docs.get("metadatas") or [],
        )
        if isinstance(meta, dict) and meta.get("content_hash") == content_hash
    )
    assert clip_service.trace(clip_doc_id)["metadata"].get("kind") == "image_embedding"

    delete_caption_resp = client.delete(f"/attachments/caption/{content_hash}")
    assert delete_caption_resp.status_code == 200
    refreshed = text_service.trace(doc_id)
    assert refreshed is not None
    assert refreshed["text"] == "auto caption"


def test_attachment_delete_cleans_up_rag_mirrors(monkeypatch, client, tmp_path):
    sqlite_path = str(tmp_path / "memory.sqlite3")
    text_service = RAGService(
        backend="chroma",
        persist_dir=str(tmp_path),
        sqlite_path=sqlite_path,
    )
    clip_service = RAGService(
        class_name="KnowledgeClip",
        backend="chroma",
        persist_dir=str(tmp_path),
        embedding_model="clip:ViT-B-32",
        sqlite_path=sqlite_path,
    )

    monkeypatch.setattr(routes, "_get_rag_service", lambda: text_service)
    monkeypatch.setattr(
        routes,
        "_get_clip_rag_service",
        lambda *, raise_http=True: clip_service,
    )

    class DummyCaptioner:
        model = "dummy"

        def run(self, data):
            return {"image_caption": "auto caption", "placeholder": False}

    monkeypatch.setattr(routes, "VisionCaptioner", DummyCaptioner)

    from app.services import clip_embeddings  # noqa: E402

    monkeypatch.setattr(
        clip_embeddings,
        "embed_clip_image_bytes",
        lambda *_args, **_kwargs: [0.1, 0.2, 0.3],
    )

    upload_resp = client.post(
        "/attachments/upload",
        files={"file": ("sample.png", b"fake-image", "image/png")},
    )
    assert upload_resp.status_code == 200
    content_hash = upload_resp.json()["content_hash"]

    put_resp = client.put(
        f"/attachments/caption/{content_hash}",
        json={"caption": "manual caption"},
    )
    assert put_resp.status_code == 200
    assert any(
        isinstance(meta, dict) and meta.get("content_hash") == content_hash
        for meta in (text_service.list_docs().get("metadatas") or [])
    )
    assert any(
        isinstance(meta, dict) and meta.get("content_hash") == content_hash
        for meta in (clip_service.list_docs().get("metadatas") or [])
    )

    delete_resp = client.delete(f"/attachments/{content_hash}")
    assert delete_resp.status_code == 200
    assert not any(
        isinstance(meta, dict) and meta.get("content_hash") == content_hash
        for meta in (text_service.list_docs().get("metadatas") or [])
    )
    assert not any(
        isinstance(meta, dict) and meta.get("content_hash") == content_hash
        for meta in (clip_service.list_docs().get("metadatas") or [])
    )
