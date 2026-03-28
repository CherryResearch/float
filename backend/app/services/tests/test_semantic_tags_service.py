import importlib.util
import sys
import types
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[4]
SERVICES = ROOT / "backend" / "app" / "services"
UTILS = ROOT / "backend" / "app" / "utils"

app_pkg = types.ModuleType("app")
app_pkg.__path__ = [str(ROOT / "backend" / "app")]
sys.modules.setdefault("app", app_pkg)

services_pkg = types.ModuleType("app.services")
services_pkg.__path__ = [str(SERVICES)]
services_pkg.RAG_IMPORT_ERROR = RuntimeError("stub services init for isolated test loading")
sys.modules.setdefault("app.services", services_pkg)

utils_pkg = types.ModuleType("app.utils")
utils_pkg.__path__ = [str(UTILS)]
sys.modules.setdefault("app.utils", utils_pkg)

module_path = SERVICES / "semantic_tags_service.py"
spec = importlib.util.spec_from_file_location(
    "semantic_tags_service",
    module_path,
)
sts = importlib.util.module_from_spec(spec)
sys.modules["semantic_tags_service"] = sts
spec.loader.exec_module(sts)
SemanticTagsService = sts.SemanticTagsService


def test_generate_tags_happy_path(monkeypatch):
    called = {}

    def fake_chunk_text(text):
        called["chunk_text"] = text
        return ["hi there"]

    def fake_embed_texts(texts):
        called["embed_texts"] = texts
        return [[0.1, 0.2, 0.3]], object()

    def fake_cluster_texts(embeddings):
        called["cluster_texts"] = embeddings
        return [0], 1

    def fake_summarize(nuggets, labels, embeddings, embedder, k, **kwargs):
        called["summarize"] = (nuggets, labels)
        summary = {
            "tag_counts": {"hi": 1},
            "cluster_count": 1,
            "clusters": {"0": "hi"},
        }
        return summary, {}

    monkeypatch.setattr(sts, "chunk_text", fake_chunk_text)
    monkeypatch.setattr(sts, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(sts, "cluster_texts", fake_cluster_texts)
    monkeypatch.setattr(sts, "summarize_clusters", fake_summarize)

    svc = SemanticTagsService()
    result = svc.generate_tags("sample conversation")
    assert result["tag_counts"] == {"hi": 1}
    assert called["chunk_text"] == "sample conversation"
    assert called["embed_texts"] == ["hi there"]
    assert called["cluster_texts"] == [[0.1, 0.2, 0.3]]
    assert called["summarize"] == (["hi there"], [0])


def test_infer_topics_without_key_uses_local_fallback():
    nuggets = ["hello world"]
    labels = [0]
    embeddings = [[0.0]]
    embedder = object()
    summary, _ = sts.summarize_clusters(
        nuggets,
        labels,
        embeddings,
        embedder,
        1,
        infer_topics=True,
        openai_key=None,
    )
    assert summary.get("cluster_count") == 1
    assert isinstance(summary.get("clusters"), dict)
    assert summary.get("clusters", {}).get("0")


def test_cluster_texts_single_vector_returns_single_cluster():
    labels, k = sts.cluster_texts([[0.2, 0.4]], preferred_k=16, max_k=30)
    assert labels == [0]
    assert k == 1


def test_cluster_embeddings_normalizes_inputs_and_uses_elkan(monkeypatch):
    captured = {}

    class FakeKMeans:
        def __init__(self, n_clusters, **kwargs):
            captured["n_clusters"] = n_clusters
            captured["kwargs"] = kwargs

        def fit_predict(self, embeddings):
            captured["embeddings"] = np.asarray(embeddings, dtype=np.float32)
            return np.asarray([0, 1], dtype=np.int32)

    monkeypatch.setitem(sys.modules, "sklearn", types.ModuleType("sklearn"))
    fake_cluster = types.ModuleType("sklearn.cluster")
    fake_cluster.KMeans = FakeKMeans
    monkeypatch.setitem(sys.modules, "sklearn.cluster", fake_cluster)

    labels, _ = sts.cluster_embeddings([[3.0, 4.0], [0.0, 5.0]], 2)

    assert labels.tolist() == [0, 1]
    assert captured["n_clusters"] == 2
    assert captured["kwargs"]["algorithm"] == sts.DEFAULT_KMEANS_ALGORITHM
    assert captured["kwargs"]["random_state"] == sts.DEFAULT_KMEANS_RANDOM_STATE
    norms = np.linalg.norm(captured["embeddings"], axis=1)
    assert np.allclose(norms, np.asarray([1.0, 1.0], dtype=np.float32))


def test_choose_k_samples_silhouette_for_large_inputs(monkeypatch):
    captured = {"sample_sizes": [], "kwargs": []}

    class FakeKMeans:
        def __init__(self, n_clusters, **kwargs):
            self.n_clusters = n_clusters
            captured["kwargs"].append(kwargs)

        def fit_predict(self, embeddings):
            count = len(embeddings)
            return np.asarray(
                [index % self.n_clusters for index in range(count)],
                dtype=np.int32,
            )

    def fake_silhouette_score(
        embeddings,
        labels,
        *,
        sample_size=None,
        random_state=None,
    ):
        captured["sample_sizes"].append(sample_size)
        captured["random_state"] = random_state
        return 0.5

    monkeypatch.setitem(sys.modules, "sklearn", types.ModuleType("sklearn"))
    fake_cluster = types.ModuleType("sklearn.cluster")
    fake_cluster.KMeans = FakeKMeans
    fake_metrics = types.ModuleType("sklearn.metrics")
    fake_metrics.silhouette_score = fake_silhouette_score
    monkeypatch.setitem(sys.modules, "sklearn.cluster", fake_cluster)
    monkeypatch.setitem(sys.modules, "sklearn.metrics", fake_metrics)

    embeddings = np.random.default_rng(0).normal(size=(600, 8)).astype(np.float32)
    selected_k = sts.choose_k(embeddings, preferred_k=8, k_max=10)

    assert selected_k == 8
    assert captured["sample_sizes"]
    assert all(
        sample_size == sts.DEFAULT_SILHOUETTE_SAMPLE_SIZE
        for sample_size in captured["sample_sizes"]
    )
    assert captured["random_state"] == sts.DEFAULT_KMEANS_RANDOM_STATE
    assert all(
        kwargs["algorithm"] == sts.DEFAULT_KMEANS_ALGORITHM
        for kwargs in captured["kwargs"]
    )


def test_cluster_texts_passes_torch_backend_options(monkeypatch):
    observed = {}

    def fake_choose_k(embeddings, **kwargs):
        observed["choose_k"] = kwargs
        return 2

    def fake_cluster_embeddings(embeddings, k, **kwargs):
        observed["cluster_embeddings"] = {"k": k, **kwargs}
        return np.asarray([0, 1], dtype=np.int32), object()

    monkeypatch.setattr(sts, "choose_k", fake_choose_k)
    monkeypatch.setattr(sts, "cluster_embeddings", fake_cluster_embeddings)

    labels, k = sts.cluster_texts(
        [[1.0, 0.0], [0.0, 1.0]],
        preferred_k=8,
        max_k=10,
        cluster_backend="torch",
        cluster_device="cuda",
    )

    assert labels == [0, 1]
    assert k == 2
    assert observed["choose_k"]["cluster_backend"] == "torch"
    assert observed["choose_k"]["cluster_device"] == "cuda"
    assert observed["cluster_embeddings"]["cluster_backend"] == "torch"
    assert observed["cluster_embeddings"]["cluster_device"] == "cuda"
