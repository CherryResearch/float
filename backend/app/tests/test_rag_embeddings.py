import sys
import types

from app.services import rag_service


def _init_memory_backend(self, backend, url, api_key):
    return rag_service._InMemoryBackend(self.class_name, self._embed_text)


def test_rag_embedding_local_uses_sentence_transformer(monkeypatch):
    class DummyEncoder:
        def encode(self, text):
            return [0.1, 0.2, 0.3]

    fake_module = types.SimpleNamespace(
        SentenceTransformer=lambda model, trust_remote_code=False: DummyEncoder()
    )
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    monkeypatch.setattr(rag_service.RAGService, "_init_backend", _init_memory_backend)

    service = rag_service.RAGService(backend="chroma", embedding_model="local:dummy")
    vector = service._embed_text("hello")
    assert vector == [0.1, 0.2, 0.3]


def test_rag_embedding_local_initializes_lazily(monkeypatch):
    observed = {"init_calls": 0}

    class DummyEncoder:
        def encode(self, text):
            return [0.4, 0.5]

    def fake_init_encoder(self, model_name):
        observed["init_calls"] += 1
        return DummyEncoder()

    monkeypatch.setattr(rag_service.RAGService, "_init_backend", _init_memory_backend)
    monkeypatch.setattr(
        rag_service.RAGService,
        "_init_embedding_encoder",
        fake_init_encoder,
    )

    service = rag_service.RAGService(backend="chroma", embedding_model="local:dummy")
    assert observed["init_calls"] == 0
    assert service.embedding_runtime_status()["state"] == "idle"

    vector = service._embed_text("hello")

    assert vector == [0.4, 0.5]
    assert observed["init_calls"] == 1
    assert service.embedding_runtime_status()["state"] == "loaded"


def test_rag_embedding_local_falls_back_on_load_error(monkeypatch):
    class DummyEncoder:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("load failed")

    fake_module = types.SimpleNamespace(SentenceTransformer=DummyEncoder)
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    monkeypatch.setattr(rag_service.RAGService, "_init_backend", _init_memory_backend)

    service = rag_service.RAGService(backend="chroma", embedding_model="local:dummy")
    vector = service._embed_text("hello")
    assert len(vector) == 32


def test_rag_embedding_api_falls_back_to_hash(monkeypatch):
    monkeypatch.setattr(rag_service.RAGService, "_init_backend", _init_memory_backend)

    service = rag_service.RAGService(backend="chroma", embedding_model="api:test")
    vector = service._embed_text("hello")
    assert len(vector) == 32
