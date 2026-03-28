import sys
import types
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))
from services import weaviate_client  # noqa: E402


def test_export_chroma_to_weaviate(monkeypatch):
    # Stub Chroma
    collection = types.SimpleNamespace(
        get=lambda: {
            "ids": ["1"],
            "documents": ["hello"],
            "metadatas": [{"tag": "test"}],
            "embeddings": [[0.1, 0.2]],
        }
    )
    chroma_client = types.SimpleNamespace(
        get_or_create_collection=lambda name: collection
    )
    chroma_module = types.SimpleNamespace(
        PersistentClient=lambda path: chroma_client,
    )
    sys.modules["chromadb"] = chroma_module

    # Stub Weaviate
    class FakeBatch:
        def __init__(self):
            self.objects = []

        def add_data_object(self, properties, class_name, vector, uuid):
            self.objects.append((properties, class_name, vector, uuid))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    fake_batch = FakeBatch()

    schema = types.SimpleNamespace(
        get=lambda: {"classes": []},
        create_class=lambda cls: None,
    )
    client = types.SimpleNamespace(schema=schema, batch=fake_batch)

    weaviate_module = types.SimpleNamespace(
        connect_to_custom=lambda **kwargs: client,
        auth=types.SimpleNamespace(AuthApiKey=lambda key: key),
    )
    sys.modules["weaviate"] = weaviate_module

    count = weaviate_client.export_chroma_to_weaviate(
        "knowledge",
        "TestClass",
        str(weaviate_client.DEFAULT_CHROMA_DIR),
        "http://fake",
    )
    assert count == 1
    assert fake_batch.objects[0][0]["text"] == "hello"
    assert fake_batch.objects[0][0]["metadata"]
