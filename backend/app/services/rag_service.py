import hashlib
import json
import logging
import math
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from app import config as app_config
from app import hooks
from app.services.text_chunks import chunk_text as shared_chunk_text
from app.utils.knowledge_store import KnowledgeStore
from services.weaviate_client import create_client
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

try:
    from bs4 import BeautifulSoup

    _bs4_error = None
except Exception as e:  # pragma: no cover - missing dep
    BeautifulSoup = None
    _bs4_error = e

http_session = requests.Session()
logger = logging.getLogger(__name__)


def _is_fatal_base_exception(exc: BaseException) -> bool:
    return isinstance(exc, (KeyboardInterrupt, SystemExit))


def _simple_embed(text: str) -> list[float]:
    """Return a deterministic embedding from text."""
    digest = hashlib.sha256(text.encode()).digest()
    return [b / 255 for b in digest[:32]]


def _dot(a: List[float], b: List[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _norm(vec: List[float]) -> float:
    return math.sqrt(sum(x * x for x in vec))


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    denom = _norm(a) * _norm(b)
    if denom == 0:
        return 0.0
    return _dot(a, b) / denom


def _distance_to_score(distance: Any) -> Optional[float]:
    """Convert distance-like values to a bounded similarity score in [0, 1]."""
    try:
        dist = float(distance)
    except Exception:
        return None
    if not math.isfinite(dist) or dist < 0:
        return None
    if dist <= 1.0:
        return 1.0 - dist
    return 1.0 / (1.0 + dist)


def _derive_embeddings_url(api_url: Optional[str]) -> str:
    """Derive an embeddings endpoint from a base or responses/chat URL."""
    candidate = (
        (api_url or "").strip()
        or os.getenv("EXTERNAL_API_URL", "").strip()
        or app_config.DEFAULT_OPENAI_API_URL
    )
    raw = candidate.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    if raw.endswith("/v1/embeddings"):
        return raw
    if "/v1/" in raw:
        return raw.split("/v1/", 1)[0] + "/v1/embeddings"
    if raw.endswith("/v1"):
        return raw + "/embeddings"
    return raw + "/v1/embeddings"


def _doc_id_for_source(source: str, *, fallback_text: str = "") -> str:
    """Return a stable ID for a given source string.

    Schema decision:
    - We treat `metadata.source` as the stable knowledge key.
    - IDs are derived from `source` so updates to the text do not create a new row.

    Alternate approach:
    - Use UUIDv5 for a "real" UUID. We keep MD5 here because historical data and
      Weaviate integrations already relied on 32-hex IDs.
    """
    seed = (source or "").strip()
    if not seed:
        seed = (fallback_text or "").strip()
    if not seed:
        seed = str(uuid.uuid4())
    return hashlib.md5(seed.encode("utf-8")).hexdigest()


def _pop_embedding_override(meta: Dict[str, Any]) -> Optional[list[float]]:
    """Remove and return an embedding override from metadata, if present.

    Callers can pass a precomputed embedding via metadata to avoid recomputing
    vectors (e.g., CLIP image embeddings). The vector is intentionally *not*
    persisted into metadata to avoid bloating the stored document payload.
    """

    for key in ("__embedding", "embedding"):
        value = meta.pop(key, None)
        if value is None:
            continue
        if isinstance(value, (tuple, list)):
            try:
                vec = [float(v) for v in value]
            except Exception:
                continue
            if vec:
                return vec
    return None


def _sanitize_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Return metadata compatible with strict vector stores (e.g., Chroma).

    Chroma metadata values must be scalar (str/int/float/bool). We stringify
    nested objects via JSON to avoid runtime failures when users attach rich
    metadata (lists, dicts, etc.).

    Alternate approach:
    - Maintain a separate metadata store with full JSON support and keep the
      vector DB metadata intentionally minimal. For now, we keep a pragmatic
      one-store strategy and coerce types at write time.
    """

    sanitized: Dict[str, Any] = {}
    if not isinstance(metadata, dict):
        return sanitized
    for raw_key, value in metadata.items():
        key = str(raw_key)
        if value is None:
            # Chroma rejects nulls; omit them.
            continue
        if isinstance(value, (str, int, float, bool)):
            sanitized[key] = value
            continue
        if isinstance(value, (Path, uuid.UUID)):
            sanitized[key] = str(value)
            continue
        # Best-effort datetime/date handling without importing datetime types.
        iso = getattr(value, "isoformat", None)
        if callable(iso):
            try:
                sanitized[key] = str(iso())
                continue
            except Exception:
                pass
        try:
            sanitized[key] = json.dumps(value, ensure_ascii=False)
        except Exception:
            sanitized[key] = str(value)
    return sanitized


class _VectorBackendError(RuntimeError):
    """Raised when the configured vector backend cannot be initialized."""


class _WeaviateBackend:
    def __init__(
        self,
        class_name: str,
        url: str | None,
        api_key: str | None,
        embed_fn,
    ):
        resolved_url = (
            url
            or os.getenv("WEAVIATE_URL")
            or os.getenv("FLOAT_WEAVIATE_URL")
            or "http://localhost:8080"
        )
        self.class_name = class_name
        self.url = resolved_url
        self.client = create_client(resolved_url, api_key)
        self._embed_text = embed_fn or _simple_embed
        self._uses_collections_api = hasattr(self.client, "collections")

    def _collection_definition(self) -> Dict[str, Any]:
        return {
            "class": self.class_name,
            "vectorizer": "none",
            "properties": [
                {"name": "text", "dataType": ["text"]},
                {"name": "source", "dataType": ["text"]},
            ],
        }

    def _v4_collection(self):
        collections = getattr(self.client, "collections", None)
        if collections is None:
            raise RuntimeError("Weaviate collections API unavailable")
        use = getattr(collections, "use", None)
        if callable(use):
            return use(self.class_name)
        get = getattr(collections, "get", None)
        if callable(get):
            return get(self.class_name)
        raise RuntimeError("Weaviate collections API missing use/get helpers")

    def _ensure_v4_collection(self):
        collections = getattr(self.client, "collections", None)
        if collections is None:
            raise RuntimeError("Weaviate collections API unavailable")
        exists = getattr(collections, "exists", None)
        has_collection = bool(exists(self.class_name)) if callable(exists) else False
        if not has_collection:
            create_from_dict = getattr(collections, "create_from_dict", None)
            if callable(create_from_dict):
                create_from_dict(self._collection_definition())
            else:
                create = getattr(collections, "create", None)
                if not callable(create):
                    raise RuntimeError(
                        "Weaviate collections API cannot create collections"
                    )
                from weaviate.classes.config import Configure, DataType, Property

                create(
                    self.class_name,
                    vector_config=Configure.Vectors.self_provided(),
                    properties=[
                        Property(name="text", data_type=DataType.TEXT),
                        Property(name="source", data_type=DataType.TEXT),
                    ],
                )
        return self._v4_collection()

    @staticmethod
    def _v4_object_payload(item: Any) -> tuple[str, Dict[str, Any]]:
        properties = getattr(item, "properties", None) or {}
        doc_id = str(getattr(item, "uuid", "") or "")
        return doc_id, dict(properties or {})

    def ensure_schema(self) -> None:
        if self._uses_collections_api:
            self._ensure_v4_collection()
            return
        schema = self.client.schema.get()
        classes = schema.get("classes", [])
        if not any(c.get("class") == self.class_name for c in classes):
            self.client.schema.create_class(self._collection_definition())

    def add_text(
        self, text: str, source: str, metadata: Optional[Dict[str, Any]]
    ) -> str:
        meta = dict(metadata or {})
        embedding_override = _pop_embedding_override(meta)
        meta = _sanitize_metadata(meta)
        source_val = str(meta.get("source") or source or "").strip()
        doc_id = _doc_id_for_source(source_val, fallback_text=text)
        embedding = embedding_override or self._embed_text(text)
        data = {"text": text, "source": source_val, **meta}
        if self._uses_collections_api:
            collection = self._ensure_v4_collection()
            try:
                collection.data.delete_by_id(str(doc_id))
            except Exception:
                pass
            collection.data.insert(
                properties=data,
                uuid=str(doc_id),
                vector=embedding,
            )
            return doc_id
        try:
            self.client.data_object.create(
                data, self.class_name, uuid=doc_id, vector=embedding
            )
        except Exception:
            # Best-effort "upsert": try deleting the old ID and retry.
            try:
                self.client.data_object.delete(doc_id)
            except Exception:
                pass
            self.client.data_object.create(
                data, self.class_name, uuid=doc_id, vector=embedding
            )
        return doc_id

    def list_docs(self) -> Dict[str, list]:
        if self._uses_collections_api:
            ids: list[str] = []
            metadatas: list[Dict[str, Any]] = []
            collection = self._ensure_v4_collection()
            iterator = getattr(collection, "iterator", None)
            if callable(iterator):
                for item in iterator():
                    doc_id, props = self._v4_object_payload(item)
                    ids.append(doc_id)
                    metadatas.append(props)
                return {"ids": ids, "metadatas": metadatas}
            fetch_objects = getattr(
                getattr(collection, "query", None), "fetch_objects", None
            )
            if callable(fetch_objects):
                response = fetch_objects(limit=1000)
                for item in getattr(response, "objects", None) or []:
                    doc_id, props = self._v4_object_payload(item)
                    ids.append(doc_id)
                    metadatas.append(props)
            return {"ids": ids, "metadatas": metadatas}
        res = self.client.data_object.get(class_name=self.class_name)
        objs = res.get("objects", [])
        return {
            "ids": [o["id"] for o in objs],
            "metadatas": [o.get("properties", {}) for o in objs],
        }

    def get_doc(self, doc_id: str):
        if self._uses_collections_api:
            collection = self._ensure_v4_collection()
            fetch_object_by_id = getattr(
                getattr(collection, "query", None),
                "fetch_object_by_id",
                None,
            )
            item = (
                fetch_object_by_id(str(doc_id))
                if callable(fetch_object_by_id)
                else None
            )
            if item is None:
                iterator = getattr(collection, "iterator", None)
                if callable(iterator):
                    for candidate in iterator():
                        candidate_id, props = self._v4_object_payload(candidate)
                        if candidate_id == str(doc_id):
                            return {"id": candidate_id, "properties": props}
                return None
            candidate_id, props = self._v4_object_payload(item)
            return {"id": candidate_id or str(doc_id), "properties": props}
        return self.client.data_object.get_by_id(doc_id)

    def delete_doc(self, doc_id: str) -> None:
        if self._uses_collections_api:
            self._ensure_v4_collection().data.delete_by_id(str(doc_id))
            return
        self.client.data_object.delete(doc_id)

    def delete_source(self, source: str) -> None:
        if not source:
            return
        if self._uses_collections_api:
            try:
                self.delete_doc(_doc_id_for_source(source))
            except Exception:
                pass
            docs = self.list_docs()
            ids = docs.get("ids") or []
            metas = docs.get("metadatas") or []
            for doc_id, meta in zip(ids, metas):
                if (
                    isinstance(meta, dict)
                    and str(meta.get("source") or "").strip() == source
                ):
                    try:
                        self.delete_doc(str(doc_id))
                    except Exception:
                        pass
            return
        try:
            self.delete_doc(_doc_id_for_source(source))
        except Exception:
            pass
        # Best-effort: clean up any legacy rows sharing the same `source`.
        try:
            docs = self.list_docs()
        except Exception:
            return
        ids = docs.get("ids") or []
        metas = docs.get("metadatas") or []
        for doc_id, meta in zip(ids, metas):
            if (
                isinstance(meta, dict)
                and str(meta.get("source") or "").strip() == source
            ):
                try:
                    self.delete_doc(doc_id)
                except Exception:
                    pass

    def update_doc(
        self, doc_id: str, text: str, metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        embedding = self._embed_text(text)
        meta = _sanitize_metadata(metadata or {"updated": True})
        data = {"text": text, **meta}
        if self._uses_collections_api:
            collection = self._ensure_v4_collection()
            try:
                collection.data.delete_by_id(str(doc_id))
            except Exception:
                pass
            collection.data.insert(
                properties=data,
                uuid=str(doc_id),
                vector=embedding,
            )
            return
        self.client.data_object.create(
            data, self.class_name, uuid=doc_id, vector=embedding
        )

    def query(self, text: str, top_k: int = 5) -> list[Dict[str, Any]]:
        if self._uses_collections_api:
            try:
                from weaviate.classes.query import MetadataQuery

                vector = self._embed_text(text)
                response = self._ensure_v4_collection().query.near_vector(
                    vector,
                    limit=int(top_k),
                    return_metadata=MetadataQuery(distance=True),
                )
                payload = getattr(response, "objects", None) or []
            except Exception:
                return []
            matches: list[Dict[str, Any]] = []
            for entry in payload:
                doc_id, props = self._v4_object_payload(entry)
                text_val = props.get("text") or ""
                metadata = {k: v for k, v in props.items() if k not in {"text"}}
                meta_obj = getattr(entry, "metadata", None)
                distance = (
                    getattr(meta_obj, "distance", None)
                    if meta_obj is not None
                    else None
                )
                score = _distance_to_score(distance)
                matches.append(
                    {
                        "id": doc_id,
                        "text": text_val,
                        "metadata": metadata,
                        "score": score,
                    }
                )
            return matches[:top_k]
        try:
            vector = self._embed_text(text)
            result = (
                self.client.query.get(self.class_name, ["text", "source"])
                .with_near_vector({"vector": vector})
                .with_limit(int(top_k))
                .with_additional(["distance", "id"])
                .do()
            )
            payload = result.get("data", {}).get("Get", {}).get(self.class_name, [])
        except Exception:
            return []
        matches: list[Dict[str, Any]] = []
        for entry in payload or []:
            props = dict(entry or {})
            additional = props.pop("_additional", {}) if isinstance(props, dict) else {}
            text_val = props.get("text") or ""
            metadata = {k: v for k, v in props.items() if k not in {"text"}}
            doc_id = additional.get("id") or props.get("id")
            distance = additional.get("distance")
            score = _distance_to_score(distance)
            matches.append(
                {
                    "id": doc_id,
                    "text": text_val,
                    "metadata": metadata,
                    "score": score,
                }
            )
        return matches[:top_k]


class _ChromaBackend:
    def __init__(
        self,
        collection_name: str,
        persist_dir: str | None,
        embed_fn,
        preferred_dimension: Optional[int] = None,
    ):
        try:
            import chromadb
        except Exception as exc:  # pragma: no cover - optional dep
            raise _VectorBackendError(
                "Chroma backend requested but chromadb is not installed"
            ) from exc

        path = (
            persist_dir
            or os.getenv("CHROMA_PERSIST_DIR")
            or os.getenv("FLOAT_CHROMA_PATH")
            or str(app_config.DEFAULT_CHROMA_DIR)
        )
        os.makedirs(path, exist_ok=True)
        self.collection_name = collection_name
        self._root_collection_name = collection_name
        try:
            self.client = chromadb.PersistentClient(path=path)
        except BaseException as exc:
            if _is_fatal_base_exception(exc):
                raise
            raise _VectorBackendError(
                f"Failed to open Chroma client at {path}: {exc}"
            ) from exc
        self._embed_text = embed_fn or _simple_embed
        try:
            self.collection = self.client.get_or_create_collection(collection_name)
        except BaseException:
            try:
                # Existing collections may carry a persisted embedding function config;
                # open them without providing a new embedding function and rely on
                # client-side embeddings instead of falling back to in-memory.
                self.collection = self.client.get_collection(collection_name)
                logger.info(
                    "Opened existing Chroma collection %s without injecting an embedding_function "
                    "due to prior configuration; using client-side embeddings.",
                    collection_name,
                )
            except BaseException as inner_exc:  # pragma: no cover - defensive fallback
                if _is_fatal_base_exception(inner_exc):
                    raise
                raise _VectorBackendError(
                    f"Failed to initialize Chroma collection {collection_name}"
                ) from inner_exc
        if self.collection is None:
            raise _VectorBackendError(
                f"Failed to initialize Chroma collection {collection_name}"
            )
        self._select_dimension_collection(preferred_dimension)

    def _is_dimension_mismatch(self, exc: Exception) -> bool:
        text = str(exc).lower()
        return "dimension" in text and "embedding" in text and "expect" in text

    def _switch_collection_for_dim(self, dim: int) -> bool:
        if not dim:
            return False
        target_name = f"{self._root_collection_name}_dim{dim}"
        try:
            self.collection = self.client.get_or_create_collection(target_name)
            self.collection_name = target_name
            logger.warning(
                "Chroma collection dimension mismatch; switching to %s for %d-dim embeddings.",
                target_name,
                dim,
            )
            return True
        except BaseException as exc:  # pragma: no cover - defensive fallback
            if _is_fatal_base_exception(exc):
                raise
            logger.error(
                "Failed to switch Chroma collection to %s: %s", target_name, exc
            )
            return False

    def _get_collection_if_exists(self, name: str):
        try:
            return self.client.get_collection(name)
        except BaseException:
            return None

    def _all_collection_names(self) -> list[str]:
        names: list[str] = []
        try:
            listed = self.client.list_collections()
        except BaseException:
            listed = []
        for entry in listed or []:
            name = None
            if isinstance(entry, str):
                name = entry
            elif isinstance(entry, dict):
                raw = entry.get("name")
                if isinstance(raw, str):
                    name = raw
            else:
                raw = getattr(entry, "name", None)
                if isinstance(raw, str):
                    name = raw
            if isinstance(name, str) and name and name not in names:
                names.append(name)
        return names

    def _iter_candidate_collections(self) -> list:
        collections = []
        seen_names: set[str] = set()
        preferred_names = [self.collection_name, self._root_collection_name]
        dim_prefix = f"{self._root_collection_name}_dim"
        for name in self._all_collection_names():
            if name.startswith(dim_prefix):
                preferred_names.append(name)
        for name in preferred_names:
            if not name or name in seen_names:
                continue
            coll = self._get_collection_if_exists(name)
            if coll is None:
                continue
            collections.append(coll)
            seen_names.add(name)
        if not collections:
            collections.append(self.collection)
        return collections

    def _select_dimension_collection(self, dim: Optional[int]) -> None:
        if not isinstance(dim, int) or dim <= 0:
            return
        target_name = f"{self._root_collection_name}_dim{dim}"
        if target_name == self.collection_name:
            return
        target = self._get_collection_if_exists(target_name)
        if target is None:
            return
        try:
            target_count = int(target.count())
        except BaseException:
            target_count = 0
        try:
            current_count = int(self.collection.count())
        except BaseException:
            current_count = 0
        if target_count > 0 or current_count == 0:
            self.collection = target
            self.collection_name = target_name
            logger.info(
                "Using existing Chroma collection %s for %d-dim embeddings.",
                target_name,
                dim,
            )

    def ensure_schema(self) -> None:
        # Collections are created lazily via get_or_create_collection.
        return None

    def add_text(
        self, text: str, source: str, metadata: Optional[Dict[str, Any]]
    ) -> str:
        meta = dict(metadata or {})
        embedding_override = _pop_embedding_override(meta)
        source_val = str(meta.get("source") or source or "").strip()
        doc_id = _doc_id_for_source(source_val, fallback_text=text)
        embedding = embedding_override or self._embed_text(text)
        meta["source"] = source_val
        meta.setdefault("text", text)
        meta = _sanitize_metadata(meta)
        is_chunked = (
            meta.get("chunk_index") is not None or int(meta.get("chunk_count") or 0) > 1
        )
        # Migrate away from legacy ID generation (source + text length) by
        # removing any rows that share the same stable identifier.
        for key in ("memory_key", "event_id"):
            if meta.get(key) and not is_chunked:
                for collection in self._iter_candidate_collections():
                    try:
                        collection.delete(where={key: meta[key]})
                    except BaseException:
                        pass
        for collection in self._iter_candidate_collections():
            try:
                collection.delete(where={"source": source_val})
            except BaseException:
                pass

        def _write_to_collection(collection) -> None:
            if hasattr(collection, "upsert"):
                collection.upsert(
                    ids=[doc_id],
                    documents=[text],
                    metadatas=[meta],
                    embeddings=[embedding],
                )
            else:  # pragma: no cover - older chromadb fallback
                try:
                    collection.delete(ids=[doc_id])
                except BaseException:
                    pass
                collection.add(
                    ids=[doc_id],
                    documents=[text],
                    metadatas=[meta],
                    embeddings=[embedding],
                )

        try:
            _write_to_collection(self.collection)
        except BaseException as exc:
            if _is_fatal_base_exception(exc):
                raise
            if self._is_dimension_mismatch(exc) and self._switch_collection_for_dim(
                len(embedding)
            ):
                _write_to_collection(self.collection)
            else:
                raise
        return doc_id

    def list_docs(self) -> Dict[str, list]:
        combined_ids: list[str] = []
        combined_meta: list[Dict[str, Any]] = []
        seen: set[str] = set()
        for collection in self._iter_candidate_collections():
            try:
                data = collection.get(include=["metadatas", "documents"])
            except BaseException:
                continue
            ids = data.get("ids", [])
            documents = data.get("documents") or [""] * len(ids)
            metadatas = data.get("metadatas") or [{} for _ in ids]
            for idx, doc_id in enumerate(ids):
                meta = dict((metadatas[idx] if idx < len(metadatas) else {}) or {})
                if "text" not in meta:
                    meta["text"] = documents[idx] if idx < len(documents) else ""
                if "source" not in meta:
                    meta["source"] = ""
                source = str(meta.get("source") or "").strip()
                dedupe_key = source or f"id:{doc_id}"
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                combined_ids.append(doc_id)
                combined_meta.append(meta)
        return {"ids": combined_ids, "metadatas": combined_meta}

    def get_doc(self, doc_id: str):
        for collection in self._iter_candidate_collections():
            try:
                data = collection.get(ids=[doc_id], include=["metadatas", "documents"])
            except BaseException:
                continue
            if not data.get("ids"):
                continue
            meta = dict((data.get("metadatas") or [{}])[0] or {})
            documents = data.get("documents") or [""]
            if "text" not in meta:
                meta["text"] = documents[0] if documents else ""
            if "source" not in meta:
                meta["source"] = ""
            return {"id": doc_id, "properties": meta}
        return None

    def delete_doc(self, doc_id: str) -> None:
        if doc_id:
            self.collection.delete(ids=[doc_id])

    def delete_source(self, source: str) -> None:
        if not source:
            return
        for collection in self._iter_candidate_collections():
            try:
                collection.delete(where={"source": str(source)})
            except BaseException:
                pass
            try:
                collection.delete(ids=[_doc_id_for_source(str(source))])
            except BaseException:
                pass

    def update_doc(
        self, doc_id: str, text: str, metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        meta = dict(metadata or {"updated": True})
        meta.setdefault("text", text)
        meta = _sanitize_metadata(meta)
        self.collection.delete(ids=[doc_id])
        self.collection.add(
            ids=[doc_id],
            documents=[text],
            metadatas=[meta],
            embeddings=[self._embed_text(text)],
        )

    def query(self, text: str, top_k: int = 5) -> list[Dict[str, Any]]:
        query_embedding = None
        try:
            query_embedding = self._embed_text(text)
        except Exception:
            query_embedding = None

        merged: Dict[str, Dict[str, Any]] = {}
        for collection in self._iter_candidate_collections():
            res = None
            if isinstance(query_embedding, list) and query_embedding:
                try:
                    res = collection.query(
                        query_embeddings=[query_embedding],
                        n_results=int(top_k),
                        include=["metadatas", "documents", "distances"],
                    )
                except BaseException:
                    res = None
            if res is None:
                try:
                    res = collection.query(
                        query_texts=[text],
                        n_results=int(top_k),
                        include=["metadatas", "documents", "distances"],
                    )
                except BaseException:
                    continue
            ids_matrix = res.get("ids") or []
            if not ids_matrix:
                continue
            ids = ids_matrix[0] or []
            documents = (
                (res.get("documents") or [[]])[0] if res.get("documents") else []
            )
            metadatas = (
                (res.get("metadatas") or [[]])[0] if res.get("metadatas") else []
            )
            distances = (
                (res.get("distances") or [[]])[0] if res.get("distances") else []
            )
            for idx, doc_id in enumerate(ids):
                text_val = ""
                if idx < len(documents):
                    text_val = documents[idx] or ""
                metadata = {}
                if idx < len(metadatas):
                    metadata = dict(metadatas[idx] or {})
                score = None
                if idx < len(distances):
                    score = _distance_to_score(distances[idx])
                source = str(metadata.get("source") or "").strip()
                dedupe_key = source or f"id:{doc_id}"
                existing = merged.get(dedupe_key)
                if existing is not None:
                    prev_score = existing.get("score")
                    if isinstance(prev_score, (int, float)) and isinstance(
                        score, (int, float)
                    ):
                        if prev_score >= score:
                            continue
                merged[dedupe_key] = {
                    "id": doc_id,
                    "text": metadata.get("text", text_val),
                    "metadata": {k: v for k, v in metadata.items() if k != "text"},
                    "score": score,
                }

        matches = list(merged.values())
        matches.sort(
            key=lambda item: float(item.get("score"))
            if isinstance(item.get("score"), (int, float))
            else float("-inf"),
            reverse=True,
        )
        return matches[:top_k]


class _InMemoryBackend:
    def __init__(self, class_name: str, embed_fn):
        self.class_name = class_name
        self._store: Dict[str, Dict[str, Any]] = {}
        self._embed_text = embed_fn or _simple_embed

    def ensure_schema(self) -> None:
        return None

    def add_text(
        self, text: str, source: str, metadata: Optional[Dict[str, Any]]
    ) -> str:
        meta = dict(metadata or {})
        embedding_override = _pop_embedding_override(meta)
        source_val = str(meta.get("source") or source or "").strip()
        doc_id = _doc_id_for_source(source_val, fallback_text=text)
        meta = _sanitize_metadata({"text": text, "source": source_val, **meta})
        self._store[doc_id] = {
            "id": doc_id,
            "properties": meta,
            "embedding": embedding_override or self._embed_text(text),
        }
        return doc_id

    def list_docs(self) -> Dict[str, list]:
        ids = list(self._store.keys())
        metadatas = [self._store[i]["properties"] for i in ids]
        return {"ids": ids, "metadatas": metadatas}

    def get_doc(self, doc_id: str):
        return self._store.get(doc_id)

    def delete_doc(self, doc_id: str) -> None:
        self._store.pop(doc_id, None)

    def delete_source(self, source: str) -> None:
        if not source:
            return
        to_delete = [
            doc_id
            for doc_id, payload in self._store.items()
            if isinstance(payload, dict)
            and isinstance(payload.get("properties"), dict)
            and str(payload["properties"].get("source") or "").strip() == source
        ]
        for doc_id in to_delete:
            self._store.pop(doc_id, None)

    def update_doc(
        self, doc_id: str, text: str, metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        meta = _sanitize_metadata(dict(metadata or {"updated": True}))
        meta.setdefault("text", text)
        self._store[doc_id] = {
            "id": doc_id,
            "properties": meta,
            "embedding": self._embed_text(text),
        }

    def query(self, text: str, top_k: int = 5) -> list[Dict[str, Any]]:
        if not self._store:
            return []
        query_vec = self._embed_text(text)
        matches: list[Dict[str, Any]] = []
        for doc_id, payload in self._store.items():
            props = dict(payload.get("properties") or {})
            embedding = payload.get("embedding")
            if embedding is None:
                embedding = self._embed_text(props.get("text", "") or "")
                payload["embedding"] = embedding
            score = _cosine_similarity(query_vec, embedding)
            text_val = props.get("text", "")
            metadata = {k: v for k, v in props.items() if k != "text"}
            matches.append(
                {
                    "id": doc_id,
                    "text": text_val,
                    "metadata": metadata,
                    "score": score,
                }
            )
        matches.sort(key=lambda item: item.get("score", 0), reverse=True)
        return matches[:top_k]


class RAGService:
    def __init__(
        self,
        class_name: str = "Knowledge",
        url: Optional[str] = None,
        api_key: Optional[str] = None,
        persist_dir: Optional[str] = None,
        backend: Optional[str] = None,
        embedding_model: Optional[str] = None,
        api_url: Optional[str] = None,
        sqlite_path: Optional[str] = None,
        enable_canonical_store: Optional[bool] = None,
    ):
        self.class_name = class_name
        self.persist_dir = persist_dir
        self.embedding_model = (
            embedding_model or os.getenv("RAG_EMBEDDING_MODEL") or "simple"
        ).strip() or "simple"
        self.embedding_api_url = (api_url or "").strip()
        self.embedding_api_key = (api_key or "").strip()
        self._embedding_api_endpoint = _derive_embeddings_url(self.embedding_api_url)
        self._embedding_encoder = None
        self._embedding_encoder_init_attempted = False
        self._embedding_encoder_error: Optional[str] = None
        self._embedding_encoder_lock = threading.Lock()
        self._embedding_dimension_hint = self._resolve_embedding_dimension_hint()
        requested_backend = (
            backend or os.getenv("FLOAT_RAG_BACKEND") or "chroma"
        ).lower()
        self.backend = self._init_backend(requested_backend, url, api_key)
        self.backend.ensure_schema()
        canonical_enabled = (
            class_name == "Knowledge"
            if enable_canonical_store is None
            else bool(enable_canonical_store)
        )
        self.canonical_store = (
            KnowledgeStore(sqlite_path) if canonical_enabled else None
        )
        self.watchers: list[Any] = []

    def _init_embedding_encoder(self, model_name: str | None):
        """Best-effort loader for local embedding models."""
        if not model_name or model_name.lower() in {"simple", "hash"}:
            return None
        name = model_name.strip()
        target = name
        if name.lower().startswith("clip:"):
            target = name.split(":", 1)[1] or "ViT-B-32"
            try:  # pragma: no cover - optional dependency
                from app.services.clip_embeddings import ClipTextEmbedder

                embedder = ClipTextEmbedder(target)
                logger.info("Loaded CLIP text embeddings: %s", target)
                return embedder
            except Exception as exc:
                logger.warning(
                    "CLIP embedding model %s unavailable (%s); using hash-based fallback.",
                    target,
                    exc,
                )
                return None
        if name.lower().startswith("local:"):
            target = name.split(":", 1)[1] or "all-MiniLM-L6-v2"
        if name.lower().startswith("api:"):
            logger.info(
                "API embedding model %s configured; embeddings will be requested from the provider.",
                name,
            )
            return None
        try:  # pragma: no cover - optional dependency
            from sentence_transformers import SentenceTransformer

            model = SentenceTransformer(target, trust_remote_code=False)
            logger.info("Loaded SentenceTransformer embeddings: %s", target)
            return model
        except Exception as exc:
            logger.warning(
                "Embedding model %s unavailable (%s); using hash-based fallback.",
                target,
                exc,
            )
            return None

    def _ensure_embedding_encoder(self):
        model_name = (self.embedding_model or "").strip().lower()
        if model_name in {"", "simple", "hash"}:
            return None
        if model_name.startswith("api:"):
            return None
        if self._embedding_encoder is not None:
            return self._embedding_encoder
        if self._embedding_encoder_init_attempted:
            return None
        with self._embedding_encoder_lock:
            if self._embedding_encoder is not None:
                return self._embedding_encoder
            if self._embedding_encoder_init_attempted:
                return None
            self._embedding_encoder_init_attempted = True
            logger.info(
                "Initializing local embedding model on first use: %s",
                self.embedding_model,
            )
            try:
                self._embedding_encoder = self._init_embedding_encoder(
                    self.embedding_model
                )
                self._embedding_encoder_error = None
                if self._embedding_encoder is not None:
                    logger.info(
                        "Local embedding model ready: %s",
                        self.embedding_model,
                    )
                if (
                    self._embedding_dimension_hint is None
                    and self._embedding_encoder is not None
                ):
                    self._embedding_dimension_hint = (
                        self._resolve_embedding_dimension_hint()
                    )
            except Exception as exc:
                self._embedding_encoder = None
                self._embedding_encoder_error = str(exc)
                logger.warning(
                    "Local embedding model failed to initialize: %s (%s)",
                    self.embedding_model,
                    exc,
                )
        return self._embedding_encoder

    def embedding_runtime_status(self) -> Dict[str, Any]:
        model_name = (self.embedding_model or "").strip() or "simple"
        lowered = model_name.lower()
        if lowered in {"simple", "hash"}:
            mode = "hash"
            state = "ready"
        elif lowered.startswith("api:"):
            mode = "api"
            state = "remote"
        elif lowered.startswith("clip:"):
            mode = "clip"
            if self._embedding_encoder is not None:
                state = "loaded"
            elif (
                self._embedding_encoder_init_attempted and self._embedding_encoder_error
            ):
                state = "error"
            else:
                state = "idle"
        else:
            mode = "sentence_transformer"
            if self._embedding_encoder is not None:
                state = "loaded"
            elif (
                self._embedding_encoder_init_attempted and self._embedding_encoder_error
            ):
                state = "error"
            else:
                state = "idle"
        return {
            "model": model_name,
            "mode": mode,
            "state": state,
            "loaded": self._embedding_encoder is not None,
            "init_attempted": bool(self._embedding_encoder_init_attempted),
            "error": self._embedding_encoder_error,
        }

    def load_embedding_runtime(self) -> Dict[str, Any]:
        lowered = (self.embedding_model or "").strip().lower()
        if lowered in {"", "simple", "hash"} or lowered.startswith("api:"):
            return self.embedding_runtime_status()
        with self._embedding_encoder_lock:
            if self._embedding_encoder is not None:
                return self.embedding_runtime_status()
            self._embedding_encoder_error = None
            self._embedding_encoder_init_attempted = False
        self._ensure_embedding_encoder()
        return self.embedding_runtime_status()

    def unload_embedding_runtime(self) -> Dict[str, Any]:
        with self._embedding_encoder_lock:
            self._embedding_encoder = None
            self._embedding_encoder_error = None
            self._embedding_encoder_init_attempted = False
        return self.embedding_runtime_status()

    def _embed_text(self, text: str) -> list[float]:
        if self._embedding_encoder is None:
            self._ensure_embedding_encoder()
        if self._embedding_encoder is not None:
            try:
                vector = self._embedding_encoder.encode(text)
                if hasattr(vector, "tolist"):
                    vector = vector.tolist()
                return [float(v) for v in vector]
            except Exception as exc:
                logger.warning(
                    "Embedding encode failed for %s; reverting to fallback (%s).",
                    self.embedding_model,
                    exc,
                )
                self._embedding_encoder = None
                self._embedding_encoder_error = str(exc)
        if self.embedding_model.lower().startswith("api:"):
            model = (
                self.embedding_model.split(":", 1)[1].strip()
                or "text-embedding-3-large"
            )
            api_key = self._resolve_embedding_api_key()
            if not api_key:
                logger.warning(
                    "API embedding model %s configured but no API key is set; using hash fallback.",
                    model,
                )
                return _simple_embed(text)
            endpoint = self._resolve_embedding_api_endpoint()
            payload = {"model": model, "input": text}
            try:
                resp = http_session.post(
                    endpoint,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    timeout=30,
                )
                resp.raise_for_status()
                body = resp.json()
                data = body.get("data") if isinstance(body, dict) else None
                if not data or not isinstance(data, list):
                    raise ValueError("missing embeddings data")
                embedding = (
                    data[0].get("embedding") if isinstance(data[0], dict) else None
                )
                if not isinstance(embedding, list):
                    raise ValueError("invalid embedding payload")
                return [float(v) for v in embedding]
            except Exception as exc:
                logger.warning(
                    "Embedding API call failed for model %s (%s); using hash fallback.",
                    model,
                    exc,
                )
        return _simple_embed(text)

    def update_api_settings(
        self, *, api_url: Optional[str] = None, api_key: Optional[str] = None
    ) -> None:
        if api_url is not None:
            self.embedding_api_url = (api_url or "").strip()
            self._embedding_api_endpoint = _derive_embeddings_url(
                self.embedding_api_url
            )
        if api_key is not None:
            self.embedding_api_key = (api_key or "").strip()

    def _resolve_embedding_api_key(self) -> str:
        if self.embedding_api_key:
            return self.embedding_api_key
        return os.getenv("OPENAI_API_KEY") or os.getenv("API_KEY", "")

    def _resolve_embedding_api_endpoint(self) -> str:
        endpoint = (self._embedding_api_endpoint or "").strip()
        if endpoint:
            return endpoint
        return _derive_embeddings_url(self.embedding_api_url)

    def _resolve_embedding_dimension_hint(self) -> Optional[int]:
        encoder = self._embedding_encoder
        if encoder is not None:
            getter = getattr(encoder, "get_sentence_embedding_dimension", None)
            if callable(getter):
                try:
                    dim = int(getter())
                    if dim > 0:
                        return dim
                except Exception:
                    pass
        model_name = (self.embedding_model or "").strip().lower()
        if model_name in {"simple", "hash"}:
            return len(_simple_embed(""))
        return None

    def _init_backend(self, backend: str, url: Optional[str], api_key: Optional[str]):
        embed_fn = self._embed_text
        choice = backend.strip().lower()
        if choice == "weaviate":
            try:
                return _WeaviateBackend(self.class_name, url, api_key, embed_fn)
            except BaseException as exc:
                if _is_fatal_base_exception(exc):
                    raise
                logger.warning(
                    "Weaviate backend unavailable (%s); falling back to Chroma store",
                    exc,
                )
                try:
                    return _ChromaBackend(
                        self.class_name,
                        self.persist_dir,
                        embed_fn,
                        self._embedding_dimension_hint,
                    )
                except BaseException as inner_exc:
                    if _is_fatal_base_exception(inner_exc):
                        raise
                    logger.warning(
                        "Chroma fallback unavailable after Weaviate failure (%s); using in-memory store",
                        inner_exc,
                    )
                    return _InMemoryBackend(self.class_name, embed_fn)
        if choice == "chroma":
            try:
                return _ChromaBackend(
                    self.class_name,
                    self.persist_dir,
                    embed_fn,
                    self._embedding_dimension_hint,
                )
            except BaseException as exc:
                if _is_fatal_base_exception(exc):
                    raise
                logger.warning(
                    "Chroma backend unavailable (%s); falling back to in-memory store",
                    exc,
                )
                return _InMemoryBackend(self.class_name, embed_fn)
        if choice == "auto":
            try:
                return _WeaviateBackend(self.class_name, url, api_key, embed_fn)
            except BaseException as exc:
                if _is_fatal_base_exception(exc):
                    raise
                try:
                    return _ChromaBackend(
                        self.class_name,
                        self.persist_dir,
                        embed_fn,
                        self._embedding_dimension_hint,
                    )
                except BaseException as inner_exc:
                    if _is_fatal_base_exception(inner_exc):
                        raise
                    return _InMemoryBackend(self.class_name, embed_fn)
        # Fallback: try Chroma then Weaviate, then in-memory
        for factory in (
            lambda: _ChromaBackend(
                self.class_name,
                self.persist_dir,
                embed_fn,
                self._embedding_dimension_hint,
            ),
            lambda: _WeaviateBackend(self.class_name, url, api_key, embed_fn),
        ):
            try:
                return factory()
            except BaseException as exc:
                if _is_fatal_base_exception(exc):
                    raise
                continue
        return _InMemoryBackend(self.class_name, embed_fn)

    def _emit_ingestion_hook(
        self,
        metadata: Optional[Dict[str, Any]],
        source: Optional[str],
        text: str,
    ) -> None:
        if not isinstance(text, str):
            return
        try:
            meta = dict(metadata or {})
        except Exception:
            meta = {}
        kind = str(meta.get("kind") or "document")
        preview = text.strip().replace("\n", " ")
        if len(preview) > 240:
            preview = preview[:237].rstrip() + "..."
        event = hooks.IngestionEvent(
            kind=kind,
            source=source,
            metadata=meta,
            preview=preview or None,
            size=len(text),
        )
        try:
            hooks.emit(hooks.INGESTION_EVENT, event)
        except Exception:
            logger.debug("Failed to emit ingestion hook", exc_info=True)

    def _normalize_metadata(
        self,
        metadata: Optional[Dict[str, Any]],
        *,
        default_kind: str,
        inferred_source: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return a sanitized metadata dict with stable keys.

        Schema decision:
        - `kind` is the primary discriminator used throughout the codebase today.
          We also keep `type` in sync as a forward-compatible alias (docs/UI).
        - `source` is required for stable upserts/deduping. If missing, we
          derive it from known identifiers (`memory_key`, `event_id`) or fall
          back to a generated `"<kind>:<uuid>"` value.

        Alternate approach:
        - Maintain a separate "knowledge table" (SQLite/Postgres) and let the
          vector store keep only embeddings + `knowledge_id`. Today we persist
          both text and metadata alongside the vector row for simplicity.
        """
        try:
            meta = dict(metadata or {})
        except Exception:
            meta = {}
        if "kind" not in meta and meta.get("type") is not None:
            meta["kind"] = meta["type"]
        if "type" not in meta and meta.get("kind") is not None:
            meta["type"] = meta["kind"]
        meta.setdefault("kind", default_kind)
        meta.setdefault("type", meta["kind"])
        meta.setdefault("created_at", time.time())
        source = meta.get("source")
        source_text = source if isinstance(source, str) else str(source or "")
        if not source_text.strip():
            if meta.get("memory_key"):
                source_text = f"memory:{meta['memory_key']}"
            elif meta.get("event_id"):
                source_text = f"calendar_event:{meta['event_id']}"
            elif inferred_source:
                source_text = inferred_source
            else:
                source_text = f"{meta['kind']}:{uuid.uuid4()}"
            meta["source"] = source_text
        else:
            meta["source"] = source_text.strip()
        return meta

    def _chunk_texts(self, text: str) -> list[str]:
        chunks = shared_chunk_text(text)
        if chunks:
            return chunks
        return [str(text or "")]

    def _delete_vector_only_by_source(self, source: str) -> None:
        if not source:
            return
        delete_by_source = getattr(self.backend, "delete_source", None)
        if callable(delete_by_source):
            try:
                delete_by_source(str(source))
            except Exception:
                pass
        try:
            docs = self.backend.list_docs() or {}
        except Exception:
            docs = {}
        ids = docs.get("ids") or []
        metas = docs.get("metadatas") or []
        for doc_id, meta in zip(ids, metas):
            if not isinstance(meta, dict):
                continue
            meta_source = str(meta.get("source") or "").strip()
            root_source = str(meta.get("root_source") or "").strip()
            if meta_source != str(source) and root_source != str(source):
                continue
            try:
                self.backend.delete_doc(str(doc_id))
            except Exception:
                pass

    def rehydrate_canonical_document(
        self,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
        *,
        knowledge_id: Optional[str] = None,
    ) -> bool:
        if self.canonical_store is None:
            return False
        source = str((metadata or {}).get("source") or "").strip()
        clean_text = str(text or "")
        if not source or not clean_text.strip():
            return False
        base_meta = self._normalize_metadata(
            metadata or {},
            default_kind=str(
                (metadata or {}).get("kind")
                or (metadata or {}).get("type")
                or "document"
            ),
            inferred_source=source,
        )
        base_meta["source"] = source
        if knowledge_id:
            base_meta["knowledge_id"] = str(knowledge_id)
        chunk_texts = self._chunk_texts(clean_text)
        chunk_count = len(chunk_texts)
        self._delete_vector_only_by_source(source)
        for idx, chunk_text in enumerate(chunk_texts):
            chunk_source = source if chunk_count == 1 else f"{source}#chunk:{idx + 1}"
            chunk_meta = dict(base_meta)
            chunk_meta.update(
                {
                    "source": chunk_source,
                    "root_source": source,
                    "knowledge_id": base_meta.get("knowledge_id") or knowledge_id,
                    "chunk_index": idx,
                    "chunk_count": chunk_count,
                    "chunked": chunk_count > 1,
                }
            )
            self._add_text(chunk_text, chunk_source, chunk_meta)
        self._emit_ingestion_hook(base_meta, source, clean_text)
        return True

    def _store_text(
        self,
        text: str,
        metadata: Dict[str, Any],
        *,
        mirror_vector: bool,
        knowledge_id: Optional[str] = None,
    ) -> str:
        source = str(metadata.get("source") or "").strip()
        if self.canonical_store is None:
            doc_id = self._add_text(text, source, metadata)
            self._emit_ingestion_hook(metadata, source, text)
            return doc_id
        stored = self.canonical_store.upsert_document(
            source=source,
            text=text,
            metadata=metadata,
            chunk_texts=self._chunk_texts(text),
            embedding_model=self.embedding_model,
            knowledge_id=knowledge_id,
        )
        self._delete_vector_only_by_source(source)
        if mirror_vector:
            for chunk in stored.get("chunks", []):
                if not isinstance(chunk, dict):
                    continue
                chunk_source = str(chunk.get("source") or "").strip()
                chunk_text = str(chunk.get("text") or "")
                chunk_meta = chunk.get("metadata") or {}
                self._add_text(
                    chunk_text,
                    chunk_source,
                    chunk_meta if isinstance(chunk_meta, dict) else {},
                )
        self._emit_ingestion_hook(metadata, source, text)
        return str(stored.get("id") or source)

    def ingest_text(
        self,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
        *,
        mirror_vector: bool = True,
    ) -> str:
        meta = self._normalize_metadata(metadata, default_kind="document")
        return self._store_text(text, meta, mirror_vector=mirror_vector)

    def ingest_file(
        self,
        path: str,
        metadata: Optional[Dict[str, Any]] = None,
        *,
        mirror_vector: bool = True,
    ) -> str:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
        meta = self._normalize_metadata(
            metadata,
            default_kind="document",
            inferred_source=os.path.abspath(path),
        )
        return self._store_text(text, meta, mirror_vector=mirror_vector)

    def ingest_pdf(
        self,
        path: str,
        metadata: Optional[Dict[str, Any]] = None,
        *,
        mirror_vector: bool = True,
    ) -> str:
        try:
            import PyPDF2
        except Exception as exc:  # pragma: no cover - optional dep
            raise RuntimeError("PyPDF2 is required to ingest PDFs") from exc

        reader = PyPDF2.PdfReader(path)
        text = "".join(page.extract_text() or "" for page in reader.pages)
        meta = self._normalize_metadata(
            metadata,
            default_kind="document",
            inferred_source=os.path.abspath(path),
        )
        return self._store_text(text, meta, mirror_vector=mirror_vector)

    def ingest_url(
        self,
        url: str,
        metadata: Optional[Dict[str, Any]] = None,
        *,
        mirror_vector: bool = True,
    ) -> str:
        resp = http_session.get(url, timeout=5)
        resp.raise_for_status()
        if BeautifulSoup is None:
            raise RuntimeError(
                "BeautifulSoup is required to ingest URLs"
            ) from _bs4_error
        soup = BeautifulSoup(resp.text, "html.parser")
        text = soup.get_text(" ")
        meta = self._normalize_metadata(
            metadata,
            default_kind="document",
            inferred_source=url,
        )
        return self._store_text(text, meta, mirror_vector=mirror_vector)

    def ingest_markdown(
        self,
        path: str,
        metadata: Optional[Dict[str, Any]] = None,
        *,
        mirror_vector: bool = True,
    ) -> str:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        meta = self._normalize_metadata(
            metadata,
            default_kind="document",
            inferred_source=os.path.abspath(path),
        )
        return self._store_text(text, meta, mirror_vector=mirror_vector)

    def _add_text(
        self, text: str, source: str, metadata: Optional[Dict[str, Any]]
    ) -> str:
        return self.backend.add_text(text, source, metadata)

    # ------------------------ folder watching -----------------------------
    class _Handler(FileSystemEventHandler):
        def __init__(self, service: "RAGService"):
            super().__init__()
            self.service = service

        def on_created(self, event):  # pragma: no cover - async side effect
            if not event.is_directory:
                try:
                    self.service.ingest_file(event.src_path)
                except Exception:
                    pass

        on_modified = on_created

    def watch_folder(self, path: str):
        observer = Observer()
        handler = self._Handler(self)
        observer.schedule(handler, path, recursive=False)
        observer.start()
        try:
            for name in os.listdir(path):
                p = os.path.join(path, name)
                if os.path.isfile(p):
                    try:
                        self.ingest_file(p)
                    except Exception:
                        pass
        except Exception:
            pass
        stop_event = threading.Event()

        class _Poller:
            def __init__(
                self,
                service: "RAGService",
                p: str,
                ev: threading.Event,
            ):
                self.service = service
                self.path = p
                self.ev = ev
                self.thread = threading.Thread(target=self.run, daemon=True)
                self.seen: set[str] = set()

            def start(self):
                self.thread.start()

            def stop(self):  # mimic Observer API
                self.ev.set()

            def join(self, timeout=None):  # mimic Observer API
                self.thread.join(timeout)

            def run(self):
                while not self.ev.is_set():
                    try:
                        for name in os.listdir(self.path):
                            p = os.path.join(self.path, name)
                            if os.path.isfile(p) and p not in self.seen:
                                try:
                                    self.service.ingest_file(p)
                                    self.seen.add(p)
                                except Exception:
                                    pass
                    except Exception:
                        pass
                    time.sleep(0.2)

        poller = _Poller(self, path, stop_event)
        poller.start()
        self.watchers.append(observer)
        self.watchers.append(poller)
        return observer

    def stop_watchers(self):
        for w in self.watchers:
            try:
                if hasattr(w, "stop"):
                    w.stop()
                if hasattr(w, "join"):
                    w.join()
            except Exception:
                pass
        self.watchers.clear()

    # ------------------------ edit / browse -----------------------------
    def list_docs(self):
        if self.canonical_store is not None:
            payload = self.canonical_store.list_items()
            if payload.get("ids"):
                return payload
        return self.backend.list_docs()

    def get_doc(self, doc_id: str):
        if self.canonical_store is not None:
            trace = self.canonical_store.trace(doc_id)
            if trace is not None:
                return trace
        return self.backend.get_doc(doc_id)

    def delete_doc(self, doc_id: str):
        if self.canonical_store is not None:
            resolved = self.canonical_store.resolve_identifier(doc_id)
            if resolved is not None:
                root_source = str(
                    resolved.get("source") or resolved.get("root_source") or ""
                ).split("#chunk:", 1)[0]
                self._delete_vector_only_by_source(root_source)
                self.canonical_store.delete_identifier(doc_id)
                return
        self.backend.delete_doc(doc_id)

    def delete_source(self, source: str) -> None:
        """Delete all documents associated with a stable `metadata.source` key."""
        if not source:
            return
        if self.canonical_store is not None:
            try:
                self.canonical_store.delete_source(str(source))
            except Exception:
                logger.debug(
                    "Canonical knowledge delete failed for %s", source, exc_info=True
                )
        self._delete_vector_only_by_source(str(source))

    def update_doc(
        self, doc_id: str, text: str, metadata: Optional[Dict[str, Any]] = None
    ):
        if self.canonical_store is None:
            self.backend.update_doc(doc_id, text, metadata)
            return
        existing = self.canonical_store.resolve_identifier(doc_id)
        merged = dict(metadata or {})
        if existing is not None and isinstance(existing.get("metadata"), dict):
            preserved = dict(existing["metadata"])
            preserved.update(merged)
            merged = preserved
        source = str(
            merged.get("source")
            or (existing or {}).get("source")
            or (existing or {}).get("root_source")
            or ""
        ).strip()
        if not source:
            self.backend.update_doc(doc_id, text, metadata)
            return
        merged["source"] = source
        self._store_text(
            text,
            self._normalize_metadata(
                merged,
                default_kind=str(
                    merged.get("kind") or merged.get("type") or "document"
                ),
                inferred_source=source,
            ),
            mirror_vector=True,
            knowledge_id=str((existing or {}).get("knowledge_id") or doc_id),
        )

    def search_canonical(self, text: str, top_k: int = 5) -> list[Dict[str, Any]]:
        if self.canonical_store is None:
            return []
        try:
            return self.canonical_store.search(text, top_k=top_k)
        except Exception as exc:
            logger.warning("Canonical knowledge search failed: %s", exc)
            return []

    def query(self, text: str, top_k: int = 5) -> list[Dict[str, Any]]:
        try:
            raw_matches = self.backend.query(text, top_k)
        except AttributeError:
            logger.warning(
                "RAG backend %s lacks query(); returning no matches",
                type(self.backend).__name__,
            )
            return []
        except Exception as exc:
            logger.warning("RAG query failed: %s", exc)
            return []
        normalized: list[Dict[str, Any]] = []
        for item in raw_matches or []:
            if not isinstance(item, dict):
                normalized.append(
                    {
                        "id": None,
                        "text": str(item),
                        "metadata": {},
                        "score": None,
                    }
                )
                continue
            metadata = item.get("metadata") or {}
            if self.canonical_store is not None and item.get("id"):
                trace = self.canonical_store.trace(str(item.get("id")))
                if isinstance(trace, dict):
                    trace_meta = trace.get("metadata")
                    if isinstance(trace_meta, dict):
                        merged_meta = dict(trace_meta)
                        if isinstance(metadata, dict):
                            merged_meta.update(metadata)
                        metadata = merged_meta
            normalized.append(
                {
                    "id": item.get("id"),
                    "text": item.get("text", ""),
                    "metadata": metadata if isinstance(metadata, dict) else {},
                    "score": item.get("score"),
                }
            )
        return normalized

    def trace(self, doc_id: str) -> Optional[Dict[str, Any]]:
        if self.canonical_store is not None:
            try:
                trace = self.canonical_store.trace(doc_id)
            except Exception as exc:
                logger.warning("Canonical trace failed for %s: %s", doc_id, exc)
                trace = None
            if trace is not None:
                return trace
        try:
            doc = self.backend.get_doc(doc_id)
        except Exception as exc:
            logger.warning("RAG trace failed for %s: %s", doc_id, exc)
            return None
        if not doc:
            return None
        if isinstance(doc, dict):
            if "text" in doc and "metadata" in doc:
                text_val = doc.get("text", "")
                metadata = doc.get("metadata") or {}
                identifier = doc.get("id", doc_id)
            elif "properties" in doc:
                props = dict(doc.get("properties") or {})
                text_val = props.get("text", "")
                metadata = {k: v for k, v in props.items() if k != "text"}
                identifier = doc.get("id", doc_id)
            else:
                text_val = doc.get("text", "")
                metadata = {k: v for k, v in doc.items() if k not in {"id", "text"}}
                identifier = doc.get("id", doc_id)
            return {
                "id": identifier,
                "text": text_val,
                "metadata": metadata if isinstance(metadata, dict) else {},
            }
        return {"id": doc_id, "text": str(doc), "metadata": {}}

    # ------------------------ weaviate import -----------------------------
    def import_from_weaviate(
        self, url: str, class_name: str, api_key: Optional[str] = None
    ) -> list[str]:
        client = create_client(url, api_key)
        if hasattr(client, "collections"):
            collection = client.collections.use(class_name)
            imported = []
            for obj in collection.iterator():
                props = dict(getattr(obj, "properties", {}) or {})
                object_id = str(getattr(obj, "uuid", "") or "")
                text = props.get("text", "")
                source = props.get("source", f"{url}:{object_id}")
                meta = {k: v for k, v in props.items() if k not in {"text", "source"}}
                doc_id = self._add_text(text, source, meta)
                imported.append(doc_id)
            return imported
        objs = client.data_object.get(class_name=class_name)
        imported = []
        for obj in objs.get("objects", []):
            props = obj.get("properties", {})
            text = props.get("text", "")
            source = props.get("source", f"{url}:{obj['id']}")
            meta = {k: v for k, v in props.items() if k not in {"text", "source"}}
            doc_id = self._add_text(text, source, meta)
            imported.append(doc_id)
        return imported
