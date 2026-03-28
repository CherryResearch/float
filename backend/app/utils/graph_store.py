import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.utils import memory_store

GRAPH_NODE_KINDS = {"entity", "event"}
GRAPH_CLAIM_EPISTEMIC_STATUSES = {
    "observed",
    "asserted",
    "scheduled",
    "predicted",
    "hypothesized",
    "cancelled",
    "superseded",
}


def resolve_path(path: Optional[str | Path] = None) -> Path:
    """Resolve the shared SQLite path used for memory, knowledge, and graph state."""
    candidate = memory_store.resolve_path(path)
    if candidate.suffix.lower() not in memory_store.SQLITE_EXTENSIONS:
        candidate = candidate.parent / "memory.sqlite3"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    return candidate


def _stable_id(seed: str) -> str:
    cleaned = str(seed or "").strip()
    if not cleaned:
        cleaned = f"graph:{time.time()}"
    return hashlib.sha1(cleaned.encode("utf-8")).hexdigest()


def _json_dumps(value: Any) -> str:
    return json.dumps(
        value if value is not None else {},
        ensure_ascii=False,
        default=memory_store._default_serializer,
    )


def _json_loads(value: Any) -> Dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_loads_any(value: Any) -> Any:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def _normalize_node_kind(value: Any) -> str:
    candidate = str(value or "").strip().lower()
    return candidate if candidate in GRAPH_NODE_KINDS else "entity"


def _normalize_epistemic_status(value: Any) -> str:
    candidate = str(value or "").strip().lower()
    if candidate in GRAPH_CLAIM_EPISTEMIC_STATUSES:
        return candidate
    return "asserted"


def _safe_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


class GraphStore:
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
                CREATE TABLE IF NOT EXISTS graph_nodes (
                    node_id TEXT PRIMARY KEY,
                    node_kind TEXT NOT NULL,
                    node_type TEXT NOT NULL,
                    canonical_name TEXT,
                    summary_text TEXT,
                    attributes_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS graph_claims (
                    claim_id TEXT PRIMARY KEY,
                    claim_type TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    epistemic_status TEXT NOT NULL DEFAULT 'asserted',
                    confidence REAL NOT NULL DEFAULT 1.0,
                    valid_from REAL,
                    valid_to REAL,
                    occurred_at REAL,
                    source_kind TEXT,
                    source_ref TEXT,
                    metadata_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS graph_claim_roles (
                    claim_id TEXT NOT NULL,
                    role_name TEXT NOT NULL,
                    ordinal INTEGER NOT NULL DEFAULT 0,
                    node_id TEXT,
                    value_json TEXT,
                    metadata_json TEXT NOT NULL,
                    PRIMARY KEY (claim_id, role_name, ordinal),
                    FOREIGN KEY (claim_id) REFERENCES graph_claims(claim_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (node_id) REFERENCES graph_nodes(node_id)
                        ON DELETE CASCADE,
                    CHECK (node_id IS NOT NULL OR value_json IS NOT NULL)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_graph_nodes_kind ON graph_nodes(node_kind, node_type)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_graph_claims_predicate ON graph_claims(predicate, epistemic_status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_graph_claim_roles_node ON graph_claim_roles(node_id)"
            )
            conn.commit()

    def _load_node(self, row: sqlite3.Row | None) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        return {
            "node_id": row["node_id"],
            "node_kind": row["node_kind"],
            "node_type": row["node_type"],
            "canonical_name": row["canonical_name"] or "",
            "summary_text": row["summary_text"] or "",
            "attributes": _json_loads(row["attributes_json"]),
            "status": row["status"],
            "created_at": float(row["created_at"]),
            "updated_at": float(row["updated_at"]),
        }

    def _load_claim(self, row: sqlite3.Row | None) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        with self._connect() as conn:
            role_rows = conn.execute(
                """
                SELECT claim_id, role_name, ordinal, node_id, value_json, metadata_json
                FROM graph_claim_roles
                WHERE claim_id = ?
                ORDER BY ordinal ASC, role_name ASC
                """,
                (row["claim_id"],),
            ).fetchall()
        roles: List[Dict[str, Any]] = []
        for role in role_rows:
            roles.append(
                {
                    "role_name": role["role_name"],
                    "ordinal": int(role["ordinal"]),
                    "node_id": role["node_id"],
                    "value": _json_loads_any(role["value_json"]),
                    "metadata": _json_loads(role["metadata_json"]),
                }
            )
        return {
            "claim_id": row["claim_id"],
            "claim_type": row["claim_type"],
            "predicate": row["predicate"],
            "status": row["status"],
            "epistemic_status": row["epistemic_status"],
            "confidence": float(row["confidence"]),
            "valid_from": _safe_float(row["valid_from"]),
            "valid_to": _safe_float(row["valid_to"]),
            "occurred_at": _safe_float(row["occurred_at"]),
            "source_kind": row["source_kind"],
            "source_ref": row["source_ref"],
            "metadata": _json_loads(row["metadata_json"]),
            "created_at": float(row["created_at"]),
            "updated_at": float(row["updated_at"]),
            "roles": roles,
        }

    def upsert_node(
        self,
        *,
        node_kind: str,
        node_type: str,
        canonical_name: Optional[str] = None,
        summary_text: Optional[str] = None,
        attributes: Optional[Dict[str, Any]] = None,
        status: str = "active",
        node_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        now = time.time()
        resolved_kind = _normalize_node_kind(node_kind)
        resolved_type = str(node_type or "").strip() or "unknown"
        resolved_name = str(canonical_name or "").strip()
        resolved_id = str(
            node_id
            or _stable_id(f"{resolved_kind}:{resolved_type}:{resolved_name or now}")
        )
        existing = self.get_node(resolved_id)
        created_at = float((existing or {}).get("created_at") or now)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO graph_nodes (
                    node_id, node_kind, node_type, canonical_name, summary_text,
                    attributes_json, status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    node_kind=excluded.node_kind,
                    node_type=excluded.node_type,
                    canonical_name=excluded.canonical_name,
                    summary_text=excluded.summary_text,
                    attributes_json=excluded.attributes_json,
                    status=excluded.status,
                    updated_at=excluded.updated_at
                """,
                (
                    resolved_id,
                    resolved_kind,
                    resolved_type,
                    resolved_name,
                    str(summary_text or ""),
                    _json_dumps(attributes),
                    str(status or "active"),
                    created_at,
                    now,
                ),
            )
            conn.commit()
        return self.get_node(resolved_id) or {
            "node_id": resolved_id,
            "node_kind": resolved_kind,
            "node_type": resolved_type,
            "canonical_name": resolved_name,
            "summary_text": str(summary_text or ""),
            "attributes": dict(attributes or {}),
            "status": str(status or "active"),
            "created_at": created_at,
            "updated_at": now,
        }

    def get_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        if not node_id:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT node_id, node_kind, node_type, canonical_name, summary_text,
                       attributes_json, status, created_at, updated_at
                FROM graph_nodes
                WHERE node_id = ?
                """,
                (str(node_id),),
            ).fetchone()
        return self._load_node(row)

    def list_nodes(self, *, limit: int = 100) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT node_id, node_kind, node_type, canonical_name, summary_text,
                       attributes_json, status, created_at, updated_at
                FROM graph_nodes
                ORDER BY updated_at DESC, node_id ASC
                LIMIT ?
                """,
                (max(1, int(limit or 100)),),
            ).fetchall()
        return [node for row in rows if (node := self._load_node(row)) is not None]

    def upsert_claim(
        self,
        *,
        predicate: str,
        roles: List[Dict[str, Any]],
        claim_type: str = "relation",
        status: str = "active",
        epistemic_status: str = "asserted",
        confidence: float = 1.0,
        valid_from: Any = None,
        valid_to: Any = None,
        occurred_at: Any = None,
        source_kind: Optional[str] = None,
        source_ref: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        claim_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        resolved_predicate = str(predicate or "").strip()
        if not resolved_predicate:
            raise ValueError("predicate is required")
        role_rows = []
        for index, raw_role in enumerate(list(roles or [])):
            if not isinstance(raw_role, dict):
                continue
            role_name = str(
                raw_role.get("role_name") or raw_role.get("role") or ""
            ).strip()
            node_id = str(raw_role.get("node_id") or "").strip() or None
            value = raw_role.get("value")
            if not role_name or (node_id is None and value is None):
                continue
            role_rows.append(
                {
                    "role_name": role_name,
                    "ordinal": int(raw_role.get("ordinal", index)),
                    "node_id": node_id,
                    "value_json": _json_dumps(value) if value is not None else None,
                    "metadata_json": _json_dumps(raw_role.get("metadata") or {}),
                }
            )
        if not role_rows:
            raise ValueError("at least one claim role is required")
        seed = f"{resolved_predicate}:{_json_dumps(role_rows)}:{_json_dumps(metadata)}"
        resolved_claim_id = str(claim_id or _stable_id(seed))
        existing = self.get_claim(resolved_claim_id)
        now = time.time()
        created_at = float((existing or {}).get("created_at") or now)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO graph_claims (
                    claim_id, claim_type, predicate, status, epistemic_status,
                    confidence, valid_from, valid_to, occurred_at, source_kind,
                    source_ref, metadata_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(claim_id) DO UPDATE SET
                    claim_type=excluded.claim_type,
                    predicate=excluded.predicate,
                    status=excluded.status,
                    epistemic_status=excluded.epistemic_status,
                    confidence=excluded.confidence,
                    valid_from=excluded.valid_from,
                    valid_to=excluded.valid_to,
                    occurred_at=excluded.occurred_at,
                    source_kind=excluded.source_kind,
                    source_ref=excluded.source_ref,
                    metadata_json=excluded.metadata_json,
                    updated_at=excluded.updated_at
                """,
                (
                    resolved_claim_id,
                    str(claim_type or "relation"),
                    resolved_predicate,
                    str(status or "active"),
                    _normalize_epistemic_status(epistemic_status),
                    float(confidence),
                    _safe_float(valid_from),
                    _safe_float(valid_to),
                    _safe_float(occurred_at),
                    str(source_kind or "").strip() or None,
                    str(source_ref or "").strip() or None,
                    _json_dumps(metadata),
                    created_at,
                    now,
                ),
            )
            conn.execute(
                "DELETE FROM graph_claim_roles WHERE claim_id = ?",
                (resolved_claim_id,),
            )
            for role in role_rows:
                conn.execute(
                    """
                    INSERT INTO graph_claim_roles (
                        claim_id, role_name, ordinal, node_id, value_json, metadata_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        resolved_claim_id,
                        role["role_name"],
                        role["ordinal"],
                        role["node_id"],
                        role["value_json"],
                        role["metadata_json"],
                    ),
                )
            conn.commit()
        return self.get_claim(resolved_claim_id) or {
            "claim_id": resolved_claim_id,
            "predicate": resolved_predicate,
            "claim_type": str(claim_type or "relation"),
            "roles": [],
        }

    def get_claim(self, claim_id: str) -> Optional[Dict[str, Any]]:
        if not claim_id:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT claim_id, claim_type, predicate, status, epistemic_status,
                       confidence, valid_from, valid_to, occurred_at, source_kind,
                       source_ref, metadata_json, created_at, updated_at
                FROM graph_claims
                WHERE claim_id = ?
                """,
                (str(claim_id),),
            ).fetchone()
        return self._load_claim(row)

    def delete_node(self, node_id: str) -> bool:
        if not node_id:
            return False
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM graph_nodes WHERE node_id = ?",
                (str(node_id),),
            )
            conn.commit()
        return bool(cursor.rowcount)

    def delete_claim(self, claim_id: str) -> bool:
        if not claim_id:
            return False
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM graph_claims WHERE claim_id = ?",
                (str(claim_id),),
            )
            conn.commit()
        return bool(cursor.rowcount)

    def list_claims_for_node(
        self, node_id: str, *, limit: int = 100
    ) -> List[Dict[str, Any]]:
        if not node_id:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT c.claim_id, c.claim_type, c.predicate, c.status,
                       c.epistemic_status, c.confidence, c.valid_from, c.valid_to,
                       c.occurred_at, c.source_kind, c.source_ref, c.metadata_json,
                       c.created_at, c.updated_at
                FROM graph_claims c
                JOIN graph_claim_roles r ON r.claim_id = c.claim_id
                WHERE r.node_id = ?
                ORDER BY c.updated_at DESC, c.claim_id ASC
                LIMIT ?
                """,
                (str(node_id), max(1, int(limit or 100))),
            ).fetchall()
        return [claim for row in rows if (claim := self._load_claim(row)) is not None]

    def summary(self) -> Dict[str, int]:
        with self._connect() as conn:
            node_count = conn.execute("SELECT COUNT(*) FROM graph_nodes").fetchone()[0]
            claim_count = conn.execute("SELECT COUNT(*) FROM graph_claims").fetchone()[
                0
            ]
            role_count = conn.execute(
                "SELECT COUNT(*) FROM graph_claim_roles"
            ).fetchone()[0]
        return {
            "node_count": int(node_count or 0),
            "claim_count": int(claim_count or 0),
            "role_count": int(role_count or 0),
        }


__all__ = ["GraphStore", "resolve_path"]
