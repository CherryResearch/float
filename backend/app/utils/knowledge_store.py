from __future__ import annotations

import difflib
import hashlib
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.utils import memory_store

logger = logging.getLogger(__name__)

SQLITE_EXTENSIONS = {".db", ".sqlite", ".sqlite3"}


def resolve_path(path: Optional[str | Path] = None) -> Path:
    """Resolve the canonical knowledge SQLite path.

    By default this shares the same SQLite file used for memories so the durable
    store can grow into additional tables without spawning more small databases.
    """
    candidate = memory_store.resolve_path(path)
    if candidate.suffix.lower() not in SQLITE_EXTENSIONS:
        candidate = candidate.parent / "memory.sqlite3"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    return candidate


def _stable_id(seed: str) -> str:
    cleaned = str(seed or "").strip()
    if not cleaned:
        cleaned = f"knowledge:{time.time()}"
    return hashlib.md5(cleaned.encode("utf-8")).hexdigest()


def _serialize_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=memory_store._default_serializer)


def _safe_json_loads(value: Any) -> Dict[str, Any]:
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def _score_text(query: str, text: str) -> float:
    q = str(query or "").strip().lower()
    t = str(text or "").strip().lower()
    if not q or not t:
        return 0.0
    score = 0.0
    if q == t:
        return 1.0
    if q in t:
        score = max(score, 0.9)
    score = max(score, difflib.SequenceMatcher(None, q, t).ratio())
    q_terms = [term for term in q.split() if term]
    if q_terms:
        overlap = sum(1 for term in q_terms if term in t)
        score = max(score, min(0.89, overlap / max(1, len(q_terms))))
    return float(score)


class KnowledgeStore:
    def __init__(self, path: Optional[str | Path] = None):
        self.path = resolve_path(path)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_items (
                    knowledge_id TEXT PRIMARY KEY,
                    source TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL,
                    title TEXT,
                    text TEXT NOT NULL,
                    summary_text TEXT,
                    metadata_json TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_chunks (
                    chunk_id TEXT PRIMARY KEY,
                    knowledge_id TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    chunk_count INTEGER NOT NULL,
                    source TEXT NOT NULL UNIQUE,
                    root_source TEXT NOT NULL,
                    text TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    embedding_model TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY (knowledge_id) REFERENCES knowledge_items(knowledge_id)
                        ON DELETE CASCADE,
                    UNIQUE(knowledge_id, chunk_index)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_knowledge_items_updated_at ON knowledge_items(updated_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_root_source ON knowledge_chunks(root_source)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_knowledge_id ON knowledge_chunks(knowledge_id)"
            )
            conn.commit()

    def _load_item_row(self, row: sqlite3.Row | None) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        metadata = _safe_json_loads(row["metadata_json"])
        metadata.setdefault("source", row["source"])
        metadata.setdefault("kind", row["kind"])
        metadata.setdefault("type", metadata.get("kind"))
        metadata.setdefault("knowledge_id", row["knowledge_id"])
        return {
            "id": row["knowledge_id"],
            "knowledge_id": row["knowledge_id"],
            "source": row["source"],
            "text": row["text"],
            "summary_text": row["summary_text"] or "",
            "metadata": metadata,
            "version": int(row["version"] or 1),
            "created_at": float(row["created_at"]),
            "updated_at": float(row["updated_at"]),
        }

    def _load_chunk_row(self, row: sqlite3.Row | None) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        metadata = _safe_json_loads(row["metadata_json"])
        metadata.setdefault("source", row["source"])
        metadata.setdefault("root_source", row["root_source"])
        metadata.setdefault("knowledge_id", row["knowledge_id"])
        metadata.setdefault("chunk_index", int(row["chunk_index"]))
        metadata.setdefault("chunk_count", int(row["chunk_count"]))
        return {
            "id": row["chunk_id"],
            "chunk_id": row["chunk_id"],
            "knowledge_id": row["knowledge_id"],
            "source": row["source"],
            "root_source": row["root_source"],
            "text": row["text"],
            "metadata": metadata,
            "chunk_index": int(row["chunk_index"]),
            "chunk_count": int(row["chunk_count"]),
            "embedding_model": row["embedding_model"],
            "created_at": float(row["created_at"]),
            "updated_at": float(row["updated_at"]),
        }

    def get_item(self, knowledge_id: str) -> Optional[Dict[str, Any]]:
        if not knowledge_id:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT knowledge_id, source, kind, title, text, summary_text,
                       metadata_json, version, created_at, updated_at
                FROM knowledge_items
                WHERE knowledge_id = ?
                """,
                (str(knowledge_id),),
            ).fetchone()
        return self._load_item_row(row)

    def get_item_by_source(self, source: str) -> Optional[Dict[str, Any]]:
        if not source:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT knowledge_id, source, kind, title, text, summary_text,
                       metadata_json, version, created_at, updated_at
                FROM knowledge_items
                WHERE source = ?
                """,
                (str(source),),
            ).fetchone()
        return self._load_item_row(row)

    def get_chunk(self, chunk_id: str) -> Optional[Dict[str, Any]]:
        if not chunk_id:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT chunk_id, knowledge_id, chunk_index, chunk_count, source,
                       root_source, text, metadata_json, embedding_model,
                       created_at, updated_at
                FROM knowledge_chunks
                WHERE chunk_id = ?
                """,
                (str(chunk_id),),
            ).fetchone()
        return self._load_chunk_row(row)

    def resolve_identifier(self, identifier: str) -> Optional[Dict[str, Any]]:
        item = self.get_item(identifier)
        if item is not None:
            return {"type": "item", **item}
        chunk = self.get_chunk(identifier)
        if chunk is not None:
            return {"type": "chunk", **chunk}
        return None

    def upsert_document(
        self,
        *,
        source: str,
        text: str,
        metadata: Dict[str, Any],
        chunk_texts: List[str],
        embedding_model: Optional[str] = None,
        knowledge_id: Optional[str] = None,
        summary_text: Optional[str] = None,
    ) -> Dict[str, Any]:
        source_text = str(source or "").strip()
        if not source_text:
            raise ValueError("source is required")
        clean_text = str(text or "")
        clean_metadata = dict(metadata or {})
        now = float(clean_metadata.get("updated_at") or time.time())
        existing = None
        if knowledge_id:
            existing = self.get_item(str(knowledge_id))
        if existing is None:
            existing = self.get_item_by_source(source_text)
        resolved_id = str(
            knowledge_id
            or clean_metadata.get("knowledge_id")
            or (existing or {}).get("knowledge_id")
            or _stable_id(source_text)
        )
        created_at = float(
            clean_metadata.get("created_at")
            or (existing or {}).get("created_at")
            or now
        )
        version = int((existing or {}).get("version") or 0) + 1
        kind = str(
            clean_metadata.get("kind")
            or clean_metadata.get("type")
            or "document"
        ).strip() or "document"
        title = (
            clean_metadata.get("title")
            or clean_metadata.get("filename")
            or clean_metadata.get("memory_key")
            or source_text
        )
        clean_metadata["kind"] = kind
        clean_metadata.setdefault("type", kind)
        clean_metadata["source"] = source_text
        clean_metadata["knowledge_id"] = resolved_id
        summary_value = summary_text
        if summary_value is None and clean_metadata.get("summary_text") is not None:
            summary_value = _stringify(clean_metadata.get("summary_text"))
        if summary_value is None and clean_text:
            summary_value = clean_text[:400]
        cleaned_chunks = [str(chunk or "").strip() for chunk in chunk_texts if str(chunk or "").strip()]
        if not cleaned_chunks:
            cleaned_chunks = [clean_text]
        chunk_rows: List[Dict[str, Any]] = []
        chunk_count = len(cleaned_chunks)
        for idx, chunk_text in enumerate(cleaned_chunks):
            chunk_source = source_text if chunk_count == 1 else f"{source_text}#chunk:{idx + 1}"
            chunk_id = resolved_id if chunk_count == 1 else _stable_id(f"{resolved_id}:chunk:{idx}")
            chunk_metadata = dict(clean_metadata)
            chunk_metadata.update(
                {
                    "source": chunk_source,
                    "root_source": source_text,
                    "knowledge_id": resolved_id,
                    "chunk_index": idx,
                    "chunk_count": chunk_count,
                    "chunked": chunk_count > 1,
                }
            )
            chunk_rows.append(
                {
                    "chunk_id": chunk_id,
                    "source": chunk_source,
                    "root_source": source_text,
                    "text": chunk_text,
                    "metadata_json": _serialize_json(chunk_metadata),
                }
            )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO knowledge_items (
                    knowledge_id, source, kind, title, text, summary_text,
                    metadata_json, version, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(knowledge_id) DO UPDATE SET
                    source=excluded.source,
                    kind=excluded.kind,
                    title=excluded.title,
                    text=excluded.text,
                    summary_text=excluded.summary_text,
                    metadata_json=excluded.metadata_json,
                    version=excluded.version,
                    updated_at=excluded.updated_at
                """,
                (
                    resolved_id,
                    source_text,
                    kind,
                    _stringify(title),
                    clean_text,
                    _stringify(summary_value or ""),
                    _serialize_json(clean_metadata),
                    version,
                    created_at,
                    now,
                ),
            )
            conn.execute("DELETE FROM knowledge_chunks WHERE knowledge_id = ?", (resolved_id,))
            for idx, chunk in enumerate(chunk_rows):
                conn.execute(
                    """
                    INSERT INTO knowledge_chunks (
                        chunk_id, knowledge_id, chunk_index, chunk_count, source,
                        root_source, text, metadata_json, embedding_model,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk["chunk_id"],
                        resolved_id,
                        idx,
                        chunk_count,
                        chunk["source"],
                        chunk["root_source"],
                        chunk["text"],
                        chunk["metadata_json"],
                        embedding_model,
                        created_at,
                        now,
                    ),
                )
            conn.commit()
        return {
            "id": resolved_id,
            "knowledge_id": resolved_id,
            "source": source_text,
            "text": clean_text,
            "metadata": clean_metadata,
            "chunks": [
                {
                    "id": chunk["chunk_id"],
                    "source": chunk["source"],
                    "text": chunk["text"],
                    "metadata": _safe_json_loads(chunk["metadata_json"]),
                }
                for chunk in chunk_rows
            ],
        }

    def list_items(self) -> Dict[str, List[Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT knowledge_id, source, kind, title, text, summary_text,
                       metadata_json, version, created_at, updated_at
                FROM knowledge_items
                ORDER BY updated_at DESC, knowledge_id ASC
                """
            ).fetchall()
        ids: List[str] = []
        documents: List[str] = []
        metadatas: List[Dict[str, Any]] = []
        for row in rows:
            item = self._load_item_row(row)
            if item is None:
                continue
            ids.append(str(item["id"]))
            documents.append(str(item["text"]))
            metadatas.append(item["metadata"])
        return {"ids": ids, "documents": documents, "metadatas": metadatas}

    def trace(self, identifier: str) -> Optional[Dict[str, Any]]:
        resolved = self.resolve_identifier(identifier)
        if resolved is None:
            return None
        if resolved["type"] == "item":
            return {
                "id": resolved["id"],
                "text": resolved["text"],
                "metadata": resolved["metadata"],
            }
        return {
            "id": resolved["id"],
            "text": resolved["text"],
            "metadata": resolved["metadata"],
        }

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        q = str(query or "").strip()
        if not q:
            return []
        with self._connect() as conn:
            item_rows = conn.execute(
                """
                SELECT knowledge_id, source, kind, title, text, summary_text,
                       metadata_json, version, created_at, updated_at
                FROM knowledge_items
                """
            ).fetchall()
            chunk_rows = conn.execute(
                """
                SELECT chunk_id, knowledge_id, chunk_index, chunk_count, source,
                       root_source, text, metadata_json, embedding_model,
                       created_at, updated_at
                FROM knowledge_chunks
                """
            ).fetchall()
        best_by_knowledge: Dict[str, Dict[str, Any]] = {}
        item_lookup: Dict[str, Dict[str, Any]] = {}
        for row in item_rows:
            item = self._load_item_row(row)
            if item is None:
                continue
            item_lookup[item["knowledge_id"]] = item
            meta = item["metadata"]
            score = max(
                _score_text(q, item["source"]),
                _score_text(q, _stringify(meta.get("title") or "")),
                _score_text(q, _stringify(meta.get("memory_key") or "")),
                _score_text(q, _stringify(meta.get("filename") or "")),
                _score_text(q, item["summary_text"]),
                _score_text(q, item["text"]),
            )
            if score <= 0:
                continue
            best_by_knowledge[item["knowledge_id"]] = {
                "id": item["knowledge_id"],
                "text": item["summary_text"] or item["text"],
                "metadata": dict(meta),
                "score": round(float(score), 4),
                "retrieved_via": "canonical",
                "match_type": "item",
            }
        for row in chunk_rows:
            chunk = self._load_chunk_row(row)
            if chunk is None:
                continue
            parent = item_lookup.get(chunk["knowledge_id"])
            if parent is None:
                continue
            score = max(
                _score_text(q, chunk["text"]),
                _score_text(q, chunk["source"]),
                _score_text(q, chunk["root_source"]),
            )
            if score <= 0:
                continue
            existing = best_by_knowledge.get(chunk["knowledge_id"])
            if existing is not None and float(existing.get("score") or 0.0) >= score:
                continue
            merged_meta = dict(parent["metadata"])
            merged_meta.update(
                {
                    "chunk_index": chunk["chunk_index"],
                    "chunk_count": chunk["chunk_count"],
                    "chunked": chunk["chunk_count"] > 1,
                }
            )
            best_by_knowledge[chunk["knowledge_id"]] = {
                "id": parent["knowledge_id"],
                "text": chunk["text"],
                "metadata": merged_meta,
                "score": round(float(score), 4),
                "retrieved_via": "canonical",
                "match_type": "chunk",
            }
        ranked = list(best_by_knowledge.values())
        ranked.sort(
            key=lambda item: float(item.get("score") or 0.0),
            reverse=True,
        )
        return ranked[: max(1, int(top_k or 5))]

    def delete_identifier(self, identifier: str) -> bool:
        resolved = self.resolve_identifier(identifier)
        if resolved is None:
            return False
        knowledge_id = str(resolved["knowledge_id"])
        with self._connect() as conn:
            conn.execute("DELETE FROM knowledge_items WHERE knowledge_id = ?", (knowledge_id,))
            conn.commit()
        return True

    def delete_source(self, source: str) -> bool:
        item = self.get_item_by_source(source)
        if item is not None:
            return self.delete_identifier(item["knowledge_id"])
        with self._connect() as conn:
            row = conn.execute(
                "SELECT knowledge_id FROM knowledge_chunks WHERE root_source = ? LIMIT 1",
                (str(source or ""),),
            ).fetchone()
        if row is None:
            return False
        return self.delete_identifier(str(row["knowledge_id"]))
