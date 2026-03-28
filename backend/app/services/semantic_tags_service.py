# isort: skip_file
from __future__ import annotations

"""Helpers for semantic-tagging operations.

This module vendors a subset of the ``semantic_tags`` package so that core
text chunking, embedding, clustering and tagging utilities are available
without requiring the external dependency.
"""

from dataclasses import dataclass
from math import fsum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import re
from collections import Counter, defaultdict

from app.services.text_chunks import chunk_text as shared_chunk_text
from app.services.text_chunks import split_into_nuggets as shared_split_into_nuggets


# ---------------------------------------------------------------------------
# Text chunking
# ---------------------------------------------------------------------------
def split_into_nuggets(text: str, max_tokens: int = 128) -> List[str]:
    """Split raw text into semantically coherent nuggets."""
    return shared_split_into_nuggets(text, max_tokens=max_tokens)


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------


class Embedder:
    def __init__(
        self,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        batch_size: int = 32,
        device: Optional[str] = None,
        model_dir: Optional[Path] = Path("models/embeddings"),
    ) -> None:
        """Load a sentence-transformer model for text embeddings.

        Parameters
        ----------
        model_name:
            Name or local path of a ``sentence-transformers`` model. Small
            models such as ``all-MiniLM-L6-v2`` are preferred for local topic
            inference.
        batch_size:
            Number of texts to encode per batch.
        device:
            Optional torch device string.
        model_dir:
            Directory containing cached embedding models. Defaults to the
            repository's ``models/embeddings`` folder.
        """

        try:
            from sentence_transformers import SentenceTransformer
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "sentence-transformers is required for embeddings"
            ) from exc

        if model_dir:
            local_path = Path(model_dir) / model_name.replace("/", "_")
            if local_path.exists():
                model_name = str(local_path)
        self.model = SentenceTransformer(
            model_name,
            device=device,
            cache_folder=str(model_dir) if model_dir else None,
        )
        self.batch_size = batch_size
        self.model_name = model_name
        self.device = device

    def embed(self, texts: List[str]):  # type: ignore[override]
        """Return embeddings for ``texts`` using the loaded model."""
        return self.model.encode(
            texts,
            batch_size=self.batch_size,
            show_progress_bar=True,
        )


def embed_image(
    path: Path,
    model_name: str = "ViT-B-32",
    model_dir: Path | None = Path("models/embeddings/clip"),
    device: str | None = None,
) -> List[float]:
    """Return a CLIP embedding for ``path``.

    Parameters
    ----------
    path:
        Location of the image file.
    model_name:
        CLIP model identifier passed to ``open_clip``.
    model_dir:
        Directory containing cached CLIP models.
    device:
        Optional torch device string.
    """

    try:
        import open_clip
        import torch
        from PIL import Image
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "open_clip, torch and Pillow are required for image embeddings"
        ) from exc

    model, _, preprocess = open_clip.create_model_and_transforms(
        model_name,
        pretrained="openai",
        cache_dir=str(model_dir) if model_dir else None,
    )
    model = model.to(device or "cpu")
    image = preprocess(Image.open(path)).unsqueeze(0).to(device or "cpu")
    with torch.no_grad():
        embedding = model.encode_image(image)[0]
    return embedding.detach().cpu().tolist()


def embed_audio(
    path: Path,
    model_dir: Path | None = None,
    embedder: Embedder | None = None,
) -> List[float]:
    """Embed an audio file by transcribing to text then encoding it.

    The function attempts to use a local Whisper model for transcription and
    then feeds the transcript through ``Embedder``. If Whisper is not
    available, a ``RuntimeError`` is raised.
    """

    try:
        import whisper
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("whisper is required for audio embeddings") from exc

    model = whisper.load_model(
        "small",
        download_root=str(model_dir) if model_dir else None,
    )
    transcript = model.transcribe(str(path))["text"]
    if embedder is None:
        embedder = Embedder(model_dir=model_dir)
    return embedder.embed([transcript])[0].tolist()


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------


DEFAULT_KMEANS_ALGORITHM = "elkan"
DEFAULT_KMEANS_RANDOM_STATE = 0
DEFAULT_SILHOUETTE_SAMPLE_SIZE = 512
DEFAULT_CLUSTER_BACKEND = "sklearn"
DEFAULT_CLUSTER_DEVICE = "auto"
DEFAULT_TORCH_KMEANS_MAX_ITER = 25
DEFAULT_TORCH_KMEANS_TOL = 1e-4


def _as_clustering_array(embeddings, np_module=None):
    try:
        import numpy as np
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("numpy is required for clustering") from exc

    np_ref = np_module or np
    arr = np_ref.asarray(embeddings, dtype=np_ref.float32)
    if arr.ndim == 1:
        if arr.size == 0:
            return arr.reshape(0, 0)
        arr = arr.reshape(1, -1)
    if arr.ndim != 2:
        raise ValueError("Embeddings must be a 2D array")
    if arr.size == 0:
        return arr
    norms = np_ref.linalg.norm(arr, axis=1, keepdims=True)
    safe_norms = np_ref.where(norms > 0.0, norms, 1.0)
    return (arr / safe_norms).astype(np_ref.float32, copy=False)


def _normalize_cluster_backend(value: Any) -> str:
    backend = str(value or "").strip().lower()
    if backend == "torch":
        return "torch"
    return DEFAULT_CLUSTER_BACKEND


def _normalize_cluster_device(value: Any) -> str:
    device = str(value or "").strip().lower()
    if device in {"cpu", "cuda"}:
        return device
    return DEFAULT_CLUSTER_DEVICE


def resolve_cluster_backend(
    cluster_backend: Any = None,
    cluster_device: Any = None,
) -> Dict[str, Any]:
    requested_backend = _normalize_cluster_backend(cluster_backend)
    requested_device = _normalize_cluster_device(cluster_device)
    state: Dict[str, Any] = {
        "requested_backend": requested_backend,
        "requested_device": requested_device,
        "backend": DEFAULT_CLUSTER_BACKEND,
        "device": "cpu",
        "fallback": False,
        "reason": None,
        "torch_available": False,
    }
    if requested_backend != "torch":
        return state

    try:
        import torch
    except Exception:
        state["fallback"] = True
        state["reason"] = "torch_unavailable"
        return state

    state["torch_available"] = True
    resolved_device = "cpu"
    if requested_device == "cuda":
        if bool(torch.cuda.is_available()):
            resolved_device = "cuda"
        else:
            state["fallback"] = True
            state["reason"] = "cuda_unavailable"
    elif requested_device == "auto":
        resolved_device = "cuda" if bool(torch.cuda.is_available()) else "cpu"
    else:
        resolved_device = requested_device

    state["backend"] = "torch"
    state["device"] = resolved_device
    return state


@dataclass
class TorchKMeansModel:
    cluster_centers_: List[List[float]]
    device: str
    n_iter_: int
    backend: str = "torch"


def _torch_initialize_centroids(data, k: int, torch_module, generator):
    sample_count = int(data.shape[0])
    first_idx = int(
        torch_module.randint(
            low=0,
            high=sample_count,
            size=(1,),
            generator=generator,
            device=data.device,
        ).item()
    )
    centroids = [data[first_idx].clone()]
    min_dist_sq = None
    for _ in range(1, k):
        latest = centroids[-1].unsqueeze(0)
        dist_sq = torch_module.sum((data - latest) ** 2, dim=1)
        if min_dist_sq is None:
            min_dist_sq = dist_sq
        else:
            min_dist_sq = torch_module.minimum(min_dist_sq, dist_sq)
        total = float(min_dist_sq.sum().item())
        if total <= 0.0:
            next_idx = int(
                torch_module.randint(
                    low=0,
                    high=sample_count,
                    size=(1,),
                    generator=generator,
                    device=data.device,
                ).item()
            )
        else:
            probs = min_dist_sq / min_dist_sq.sum()
            next_idx = int(
                torch_module.multinomial(
                    probs,
                    num_samples=1,
                    replacement=False,
                    generator=generator,
                ).item()
            )
        centroids.append(data[next_idx].clone())
    return torch_module.stack(centroids, dim=0)


def _torch_cluster_embeddings(
    embeddings,
    k: int,
    *,
    cluster_device: str = DEFAULT_CLUSTER_DEVICE,
    preprocessed: bool = False,
    max_iter: int = DEFAULT_TORCH_KMEANS_MAX_ITER,
    tol: float = DEFAULT_TORCH_KMEANS_TOL,
):
    try:
        import numpy as np
        import torch
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("torch and numpy are required for torch clustering") from exc

    backend_state = resolve_cluster_backend("torch", cluster_device)
    if backend_state.get("backend") != "torch":
        raise RuntimeError(str(backend_state.get("reason") or "torch backend unavailable"))

    arr = embeddings if preprocessed else _as_clustering_array(embeddings)
    data = torch.as_tensor(
        arr,
        dtype=torch.float32,
        device=str(backend_state.get("device") or "cpu"),
    )
    if data.ndim != 2:
        raise ValueError("Embeddings must be a 2D array")

    generator = torch.Generator(device=data.device)
    generator.manual_seed(DEFAULT_KMEANS_RANDOM_STATE)
    centroids = _torch_initialize_centroids(data, k, torch, generator)
    labels = torch.zeros(data.shape[0], dtype=torch.long, device=data.device)
    n_iter = 0
    for n_iter in range(1, max(1, int(max_iter)) + 1):
        distances = torch.cdist(data, centroids)
        labels = torch.argmin(distances, dim=1)
        min_distances = torch.min(distances, dim=1).values
        new_centroids = centroids.clone()
        for cluster_id in range(k):
            mask = labels == cluster_id
            if bool(mask.any()):
                new_centroids[cluster_id] = data[mask].mean(dim=0)
            else:
                farthest_idx = int(torch.argmax(min_distances).item())
                new_centroids[cluster_id] = data[farthest_idx]
        delta = float(torch.max(torch.linalg.norm(new_centroids - centroids, dim=1)).item())
        centroids = new_centroids
        if delta <= float(tol):
            break

    label_array = labels.detach().cpu().numpy().astype(np.int32, copy=False)
    model = TorchKMeansModel(
        cluster_centers_=centroids.detach().cpu().tolist(),
        device=str(backend_state.get("device") or "cpu"),
        n_iter_=int(n_iter),
    )
    return label_array, model


def choose_k(
    embeddings,
    k_min: int = 2,
    k_max: int | None = None,
    preferred_k: int | None = 16,
    preference_weight: float = 0.08,
    silhouette_sample_size: int | None = DEFAULT_SILHOUETTE_SAMPLE_SIZE,
    preprocessed: bool = False,
    cluster_backend: str | None = None,
    cluster_device: str | None = None,
) -> int:
    try:
        import numpy as np
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("numpy is required for clustering") from exc

    embeddings = (
        embeddings if preprocessed else _as_clustering_array(embeddings, np_module=np)
    )
    backend_state = resolve_cluster_backend(cluster_backend, cluster_device)
    n_samples = int(embeddings.shape[0])
    if n_samples <= 1:
        return 1
    if n_samples == 2:
        return 1
    if bool(np.allclose(embeddings, embeddings[:1], atol=1e-6)):
        return 1

    silhouette_score = None
    KMeans = None
    if str(backend_state.get("backend")) == "sklearn":
        try:
            from sklearn.cluster import KMeans
            from sklearn.metrics import silhouette_score
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("scikit-learn is required for clustering") from exc
    else:
        try:
            from sklearn.metrics import silhouette_score
        except Exception:
            silhouette_score = None

    if k_max is None:
        k_max = int(np.sqrt(n_samples)) + 1
    # Silhouette requires 2 <= k <= n_samples - 1
    k_upper = min(max(2, int(k_max)), n_samples - 1)
    if k_upper < 2:
        return 1

    k_lower = max(2, int(k_min))
    best_k = min(k_lower, k_upper)
    best_score = float("-inf")
    pref = None
    if isinstance(preferred_k, int) and preferred_k > 1:
        pref = int(preferred_k)
    sampled_silhouette = None
    if (
        isinstance(silhouette_sample_size, int)
        and silhouette_sample_size > 1
        and n_samples > silhouette_sample_size
    ):
        sampled_silhouette = int(silhouette_sample_size)
    if silhouette_score is None:
        if pref is not None:
            return max(k_lower, min(pref, k_upper))
        return k_lower
    for k in range(k_lower, k_upper + 1):
        if str(backend_state.get("backend")) == "torch":
            labels, _ = _torch_cluster_embeddings(
                embeddings,
                k,
                cluster_device=str(backend_state.get("device") or "cpu"),
                preprocessed=True,
            )
        else:
            km = KMeans(
                n_clusters=k,
                n_init="auto",
                algorithm=DEFAULT_KMEANS_ALGORITHM,
                random_state=DEFAULT_KMEANS_RANDOM_STATE,
            )
            labels = km.fit_predict(embeddings)
        if int(np.unique(labels).size) < 2:
            continue
        score = float(
            silhouette_score(
                embeddings,
                labels,
                sample_size=sampled_silhouette,
                random_state=DEFAULT_KMEANS_RANDOM_STATE,
            )
        )
        adjusted = score
        if pref is not None and preference_weight > 0:
            span = max(1, k_upper - k_lower)
            penalty = abs(k - pref) / span
            adjusted = score - (float(preference_weight) * penalty)
        if adjusted > best_score:
            best_score = adjusted
            best_k = k
    if best_score == float("-inf"):
        return 1
    return best_k


def cluster_embeddings(
    embeddings,
    k: int,
    *,
    preprocessed: bool = False,
    cluster_backend: str | None = None,
    cluster_device: str | None = None,
):
    backend_state = resolve_cluster_backend(cluster_backend, cluster_device)
    if str(backend_state.get("backend")) == "torch":
        return _torch_cluster_embeddings(
            embeddings,
            k,
            cluster_device=str(backend_state.get("device") or "cpu"),
            preprocessed=preprocessed,
        )

    try:
        import numpy as np
        from sklearn.cluster import KMeans
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "scikit-learn and numpy are required for clustering"
        ) from exc

    arr = embeddings if preprocessed else _as_clustering_array(embeddings, np_module=np)
    km = KMeans(
        n_clusters=k,
        n_init="auto",
        algorithm=DEFAULT_KMEANS_ALGORITHM,
        random_state=DEFAULT_KMEANS_RANDOM_STATE,
    )
    labels = km.fit_predict(arr)
    return labels, km


# ---------------------------------------------------------------------------
# Tagging and topic inference
# ---------------------------------------------------------------------------

DEFAULT_LABELS = {
    "recipe": [r"\brecipe\b", r"\bcook\b"],
    "anime": [r"\banime\b", r"\bmanga\b"],
}

TOPIC_STOPWORDS = {
    "about",
    "after",
    "again",
    "also",
    "among",
    "and",
    "any",
    "are",
    "around",
    "been",
    "being",
    "between",
    "both",
    "but",
    "can",
    "could",
    "did",
    "does",
    "doing",
    "done",
    "each",
    "even",
    "from",
    "for",
    "got",
    "had",
    "has",
    "have",
    "here",
    "how",
    "its",
    "just",
    "kind",
    "like",
    "maybe",
    "more",
    "most",
    "much",
    "need",
    "now",
    "off",
    "only",
    "other",
    "our",
    "out",
    "over",
    "really",
    "said",
    "same",
    "should",
    "some",
    "still",
    "than",
    "that",
    "the",
    "their",
    "them",
    "then",
    "there",
    "these",
    "they",
    "this",
    "those",
    "through",
    "time",
    "too",
    "use",
    "using",
    "very",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "while",
    "who",
    "with",
    "would",
    "you",
    "your",
}


class HeuristicTagger:
    def __init__(
        self,
        patterns: Dict[str, List[str]] | None = None,
        labels: List[str] | None = None,
    ):
        if labels is not None:
            patterns = {}
            for label in labels:
                patterns[label] = [rf"\b{re.escape(label)}\b"]
        self.patterns = {
            k: [re.compile(p, re.I) for p in v]
            for k, v in (patterns or DEFAULT_LABELS).items()
        }

    def tag(self, texts: Iterable[str]) -> List[List[str]]:
        results: List[List[str]] = []
        for text in texts:
            tags: List[str] = []
            for label, regexes in self.patterns.items():
                if any(r.search(text) for r in regexes):
                    tags.append(label)
            results.append(tags)
        return results


def _normalize_topic_label(value: str) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if not text:
        return ""
    # Strip common markdown/quote wrappers from model outputs.
    text = re.sub(r"^[`*_\"'\s]+|[`*_\"'\s]+$", "", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    if len(text) > 48:
        text = text[:48].rstrip()
    return text


def infer_cluster_tags(
    nuggets: List[str],
    labels: List[int],
    top_n: int = 2,
    api_key: str | None = None,
) -> Dict[int, str]:
    """Return a short label for each cluster.

    If ``api_key`` is provided, the OpenAI API will be queried. Otherwise a
    local topic model such as ``fastopic`` will be tried, falling back to a
    simple word-frequency heuristic.
    """
    n_clusters = max(labels) + 1 if labels else 0
    result: Dict[int, str] = {}
    for cid in range(n_clusters):
        texts = [t for t, l in zip(nuggets, labels) if l == cid]
        if not texts:
            continue
        if api_key:
            try:  # pragma: no cover - network call
                from openai import OpenAI

                client = OpenAI(api_key=api_key)
                prompt = (
                    "Provide a 1-2 word topic label for the following text:\n"
                    + " ".join(texts)
                )
                resp = client.responses.create(
                    model="gpt-4o-mini",
                    input=[
                        {
                            "role": "system",
                            "content": "Return only a concise 1-2 word topic label.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    max_output_tokens=16,
                )
                label = _normalize_topic_label(
                    str(getattr(resp, "output_text", "") or "")
                )
                if label:
                    result[cid] = label
                    continue
            except Exception:
                try:  # pragma: no cover - network call (legacy client fallback)
                    import openai

                    openai.api_key = api_key
                    prompt = (
                        "Provide a 1-2 word topic label for the following text:\n"
                        + " ".join(texts)
                    )
                    resp = openai.ChatCompletion.create(
                        model="gpt-3.5-turbo",
                        messages=[{"role": "user", "content": prompt}],
                    )
                    label = _normalize_topic_label(
                        str(resp["choices"][0]["message"]["content"] or "")
                    )
                    if label:
                        result[cid] = label
                        continue
                except Exception:
                    pass
        try:  # pragma: no cover - optional dependency
            from fastopic import FASTopic

            model = FASTopic()
            model.fit(texts)
            words = model.get_top_words(0, top_n)
            label = _normalize_topic_label(" ".join(words))
            if label:
                result[cid] = label
                continue
        except Exception:
            pass
        tokens = re.findall(r"\b[a-z][a-z0-9'-]{2,}\b", " ".join(texts).lower())
        filtered = [t for t in tokens if t not in TOPIC_STOPWORDS]
        counts = Counter(filtered)
        if counts:
            label = _normalize_topic_label(
                " ".join(t for t, _ in counts.most_common(max(1, top_n)))
            )
        else:
            label = f"cluster_{cid}"
        result[cid] = label
    return result


# ---------------------------------------------------------------------------
# Graph utilities
# ---------------------------------------------------------------------------


@dataclass
class Nugget:
    id: int
    text: str
    tags: List[str]
    cluster_id: int
    source: Path
    speaker: Optional[str] = None
    emotion: Optional[str] = None


class TagGraph:
    def __init__(self) -> None:
        try:
            import networkx as nx  # pragma: no cover - optional dependency
        except Exception as exc:  # pragma: no cover - optional dependency
            msg = "networkx is required for graph operations"  # th
            raise RuntimeError(msg) from exc

        self.graph = nx.Graph()

    def add_nuggets(self, nuggets: Iterable[Nugget]) -> None:
        for nugget in nuggets:
            self.graph.add_node(
                f"nugget_{nugget.id}",
                type="nugget",
                text=nugget.text,
                cluster=nugget.cluster_id,
                source=str(nugget.source),
                speaker=nugget.speaker,
                emotion=nugget.emotion,
            )
            for tag in nugget.tags:
                tag_node = f"tag_{tag}"
                self.graph.add_node(tag_node, type="tag")
                self.graph.add_edge(f"nugget_{nugget.id}", tag_node)
                self.graph.nodes[tag_node]["count"] = (
                    self.graph.nodes[tag_node].get("count", 0) + 1
                )

    def co_occurrence_edges(self) -> None:
        nodes = self.graph.nodes(data=True)
        tags = [n for n, d in nodes if d.get("type") == "tag"]
        for i, t1 in enumerate(tags):
            neighbors_t1 = set(self.graph.neighbors(t1))
            for t2 in tags[i + 1 :]:  # noqa: E203
                neighbors_t2 = self.graph.neighbors(t2)
                shared = len(neighbors_t1.intersection(neighbors_t2))
                if shared:
                    self.graph.add_edge(t1, t2, weight=shared)

    def to_networkx(self):  # pragma: no cover - simple accessor
        return self.graph

    def summary(
        self,
        metadata: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        tags = {
            n[4:]: self.graph.nodes[n].get("count", 0)
            for n, d in self.graph.nodes(data=True)
            if d.get("type") == "tag"
        }

        cluster_members: defaultdict[int, List[str]] = defaultdict(list)
        for node, data in self.graph.nodes(data=True):
            if data.get("type") == "nugget":
                cluster_members[data["cluster"]].append(node)

        cluster_labels: Dict[str, str | None] = {}
        for cid, nug_nodes in cluster_members.items():
            counter: Counter[str] = Counter()
            for n in nug_nodes:
                for neigh in self.graph.neighbors(n):
                    if self.graph.nodes[neigh].get("type") == "tag":
                        counter[neigh[4:]] += 1
            label = counter.most_common(1)[0][0] if counter else None
            cluster_labels[str(cid)] = label

        result = {
            "tag_counts": tags,
            "cluster_count": len(cluster_members),
            "clusters": cluster_labels,
        }
        if metadata:
            result["metadata"] = metadata
        return result

    def conversation_summary(self) -> Dict[str, Dict[str, Any]]:
        base_summary = self.summary()
        labels = base_summary.get("clusters", {})

        topic_counts: defaultdict[str, defaultdict[str, int]] = defaultdict(
            lambda: defaultdict(int)
        )
        nugget_totals: Counter[str] = Counter()

        for node, data in self.graph.nodes(data=True):
            if data.get("type") == "nugget":
                src = data["source"]
                cluster = str(data["cluster"])
                label = labels.get(cluster, f"cluster_{cluster}")
                topic_counts[src][label] += 1
                nugget_totals[src] += 1

        result: Dict[str, Dict[str, Any]] = {}
        for src, topics in topic_counts.items():
            result[src] = {
                "nugget_count": nugget_totals[src],
                "topics": dict(topics),
            }
        return result


# ---------------------------------------------------------------------------
# Convenience wrappers used by the service layer
# ---------------------------------------------------------------------------


def chunk_text(text: str) -> List[str]:
    """Split ``text`` into semantic nuggets.

    Parameters
    ----------
    text:
        Raw text to split.

    Returns
    -------
    List[str]
        List of chunks no longer than the max token threshold.
    """

    return shared_chunk_text(text)


def embed_texts(texts: List[str]) -> Tuple[List[List[float]], Any]:
    """Embed ``texts`` and return both embeddings and the embedder.

    Parameters
    ----------
    texts:
        Text snippets to embed.

    Returns
    -------
    Tuple[List[List[float]], Any]
        A tuple of embeddings and the embedder instance.
    """

    embedder = Embedder()
    embeddings = embedder.embed(texts).tolist()
    return embeddings, embedder


def cluster_texts(
    embeddings: List[List[float]],
    *,
    preferred_k: int | None = 16,
    max_k: int | None = None,
    cluster_backend: str | None = None,
    cluster_device: str | None = None,
) -> Tuple[List[int], int]:
    """Cluster ``embeddings`` and return labels plus chosen ``k``.

    Parameters
    ----------
    embeddings:
        Numeric vectors to cluster.

    Returns
    -------
    Tuple[List[int], int]
        Cluster labels and the number of clusters selected.
    """
    try:
        import numpy as np
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("numpy is required for clustering") from exc

    arr = _as_clustering_array(embeddings, np_module=np)
    n_samples = int(arr.shape[0]) if hasattr(arr, "shape") else len(embeddings)
    if n_samples <= 1:
        return [0] * max(0, n_samples), 1
    k = choose_k(
        arr,
        k_max=max_k,
        preferred_k=preferred_k,
        preprocessed=True,
        cluster_backend=cluster_backend,
        cluster_device=cluster_device,
    )
    if k <= 1:
        return [0] * n_samples, 1
    labels, _ = cluster_embeddings(
        arr,
        k,
        preprocessed=True,
        cluster_backend=cluster_backend,
        cluster_device=cluster_device,
    )
    return labels.tolist(), k


def summarize_clusters(
    nuggets_text: List[str],
    labels: List[int],
    embeddings: List[List[float]],
    embedder: Any,
    k: int,
    tags: Optional[List[str]] = None,
    infer_topics: bool = False,
    openai_key: Optional[str] = None,
    nug_sources: Optional[List[Path]] = None,
    nug_speakers: Optional[List[Optional[str]]] = None,
    nug_conversations: Optional[List[str]] = None,
    nug_msg_indices: Optional[List[int]] = None,
    nug_datestamps: Optional[List[str]] = None,
) -> Tuple[Dict[str, Any], Dict[str, List[float]]]:
    """Build a summary graph and threads mapping.

    Parameters
    ----------
    nuggets_text:
        Text nuggets produced from the conversation.
    labels:
        Cluster labels for each nugget.
    embeddings:
        Embedding vector for each nugget.
    embedder:
        Embedder instance used to create the embeddings.
    k:
        Number of clusters.
    tags:
        Optional list of heuristic tags to force when tagging nuggets.
    infer_topics:
        Whether to infer topic labels via the OpenAI API.
    openai_key:
        API key used when ``infer_topics`` is true.
    nug_sources, nug_speakers, nug_conversations, nug_msg_indices,
    nug_datestamps:
        Optional metadata for each nugget. When omitted, neutral defaults are
        substituted so the function is easy to call from tests.

    Returns
    -------
    Tuple[Dict[str, Any], Dict[str, List[float]]]
        A summary dictionary and cluster centroid mapping.
    """

    n = len(nuggets_text)
    if nug_sources is None:
        nug_sources = [Path("")] * n
    if nug_speakers is None:
        nug_speakers = [None] * n
    if nug_conversations is None:
        nug_conversations = ["conversation"] * n
    if nug_msg_indices is None:
        nug_msg_indices = list(range(n))
    if nug_datestamps is None:
        nug_datestamps = [""] * n

    tagger = HeuristicTagger(labels=tags)
    initial_tag_lists = tagger.tag(nuggets_text)
    tag_lists = initial_tag_lists
    if infer_topics:
        cluster_tags = infer_cluster_tags(
            nuggets_text,
            labels,
            api_key=openai_key,
        )
        tag_lists = [
            ts + [cluster_tags.get(int(lbl), f"cluster_{lbl}")]
            for ts, lbl in zip(initial_tag_lists, labels)
        ]

    tg = TagGraph()
    nug_objs = [
        Nugget(
            i,
            text,
            tgs or [],
            int(lbl),
            nug_sources[i],
            nug_speakers[i],
            None,
        )
        for i, (text, tgs, lbl) in enumerate(
            zip(nuggets_text, tag_lists, labels),
        )
    ]
    tg.add_nuggets(nug_objs)
    tg.co_occurrence_edges()

    model_obj = getattr(embedder, "model", None)
    dimension_getter = getattr(
        model_obj, "get_sentence_embedding_dimension", lambda: None
    )
    metadata = {
        "embedding_model": getattr(embedder, "model_name", None)
        or getattr(model_obj, "__class__", type("", (), {})).__name__,
        "embedding_dim": dimension_getter() if model_obj else None,
        "quantized": (
            bool(getattr(model_obj, "quantization_config", None))
            if model_obj
            else False
        ),
        "device": str(getattr(embedder, "device", "cpu")),
        "batch_size": getattr(embedder, "batch_size", None),
        "k": k,
    }

    base_summary = tg.summary(metadata)
    base_summary["conversations"] = tg.conversation_summary()

    # Compute cluster centroids
    centroids: Dict[str, List[float]] = {}
    by_cluster: Dict[str, List[int]] = {}
    for i, lbl in enumerate(labels):
        key = str(int(lbl))
        by_cluster.setdefault(key, []).append(i)
    for key, ids in by_cluster.items():
        if not ids:
            continue
        dim = len(embeddings[0])
        sums = [0.0] * dim
        for idx in ids:
            vec = embeddings[idx]
            for d in range(dim):
                sums[d] += float(vec[d])
        centroids[key] = [s / len(ids) for s in sums]

    def cosine(a: List[float], b: List[float]) -> float:
        ax = fsum(x * x for x in a) ** 0.5
        bx = fsum(x * x for x in b) ** 0.5
        if ax == 0 or bx == 0:
            return 0.0
        return fsum(x * y for x, y in zip(a, b)) / (ax * bx)

    clusters = base_summary.get("clusters", {})
    threads_map: Dict[str, List[Dict[str, Any]]] = {}
    seen_conv: set[Tuple[str, str]] = set()
    for i, lbl in enumerate(labels):
        tname = clusters.get(str(int(lbl)), f"cluster_{int(lbl)}")
        if not tname:
            tname = f"cluster_{int(lbl)}"
        conv = nug_conversations[i]
        key = (tname, conv)
        if key in seen_conv:
            continue
        seen_conv.add(key)
        score = cosine(
            embeddings[i],
            centroids.get(str(int(lbl)), embeddings[i]),
        )
        excerpt = "\n".join([ln for ln in nuggets_text[i].splitlines()][:4])
        threads_map.setdefault(tname, []).append(
            {
                "conversation": conv,
                "message_index": nug_msg_indices[i],
                "date": nug_datestamps[i],
                "score": round(float(score), 4),
                "excerpt": excerpt,
            }
        )
    base_summary["threads"] = threads_map

    return base_summary, centroids


# ---------------------------------------------------------------------------
# Public service interface
# ---------------------------------------------------------------------------


class SemanticTagsService:
    """High level interface for semantic-tagging operations."""

    def generate_tags(self, conversation: str) -> Dict[str, Any]:
        """Generate semantic tags for ``conversation``.

        Parameters
        ----------
        conversation:
            Raw conversation text to analyse.

        Returns
        -------
        Dict[str, Any]
            Summary data containing tag counts and thread information.
        """

        nuggets = chunk_text(conversation)
        embeddings, embedder = embed_texts(nuggets)
        labels, k = cluster_texts(embeddings)
        summary, _ = summarize_clusters(
            nuggets,
            labels,
            embeddings,
            embedder,
            k,
        )
        return summary

    def summarize_clusters(
        self, clusters: Dict[str, List[str]]
    ) -> Dict[str, Any]:  # noqa: E501
        """Summarize a mapping of cluster names to items.

        Parameters
        ----------
        clusters:
            Mapping of cluster label to list of items belonging to that
            cluster.



        Returns
        -------
        Dict[str, Any]
            Per-cluster counts and a representative sample string.
        """

        return {
            name: {
                "count": len(items),
                "sample": items[0] if items else "",
            }
            for name, items in clusters.items()
        }
