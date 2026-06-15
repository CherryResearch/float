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


def test_rag_embedding_api_privacy_preflight_blocks_remote_call(monkeypatch):
    observed = {}
    monkeypatch.setattr(rag_service.RAGService, "_init_backend", _init_memory_backend)
    monkeypatch.setattr(
        rag_service.privacy_filter_service.user_settings,
        "load_settings",
        lambda: {
            "privacy_filter_mode": "always",
            "privacy_filter_route_private_mode": "ask",
            "privacy_filter_model": "privacy-filter",
        },
    )

    def fake_classifier(model):
        observed["model"] = model
        return lambda _text, aggregation_strategy="simple": [
            {"entity_group": "private_email", "score": 0.99}
        ]

    def fail_post(*_args, **_kwargs):
        raise AssertionError("sensitive text should not reach the embedding API")

    monkeypatch.setattr(
        rag_service.privacy_filter_service, "_get_classifier", fake_classifier
    )
    monkeypatch.setattr(rag_service.http_session, "post", fail_post)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    service = rag_service.RAGService(
        backend="chroma", embedding_model="api:text-embedding-3-small"
    )
    vector = service._embed_text("email me at person@example.com")

    assert len(vector) == 32
    assert observed["model"] == "openai/privacy-filter"


def test_rag_embedding_api_privacy_detector_off_allows_remote_call(monkeypatch):
    observed = {}
    monkeypatch.setattr(rag_service.RAGService, "_init_backend", _init_memory_backend)
    monkeypatch.setattr(
        rag_service.privacy_filter_service.user_settings,
        "load_settings",
        lambda: {
            "privacy_filter_mode": "off",
            "privacy_filter_route_private_mode": "ask",
        },
    )

    def fail_classifier(_model):
        raise AssertionError("disabled privacy detector should not load classifier")

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"embedding": [0.1, 0.2, 0.3]}]}

    def fake_post(url, json=None, headers=None, timeout=None):
        observed["url"] = url
        observed["payload"] = json
        observed["headers"] = headers
        observed["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(
        rag_service.privacy_filter_service, "_get_classifier", fail_classifier
    )
    monkeypatch.setattr(rag_service.http_session, "post", fake_post)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    service = rag_service.RAGService(
        backend="chroma", embedding_model="api:text-embedding-3-small"
    )
    vector = service._embed_text("email me at person@example.com")

    assert vector == [0.1, 0.2, 0.3]
    assert observed["payload"] == {
        "model": "text-embedding-3-small",
        "input": "email me at person@example.com",
    }
