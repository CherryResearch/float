import copy
import hashlib
import json
import math
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.utils import memory_store

GRAPH_STORE_SCHEMA_VERSION = 1
GRAPH_QUERY_MAX_RESPONSE_BYTES = 256 * 1024
GRAPH_QUERY_MAX_ROLES = 1000
GRAPH_QUERY_MAX_FILTER_CHARS = 512
_QUERY_DEFAULT = object()
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


def validate_graph_query_filters(filters: Dict[str, Any]) -> None:
    """Keep signed tool normalization and storage query validation identical."""
    allowed = {
        "mode",
        "node_id",
        "predicate",
        "role_nodes",
        "epistemic_status",
        "valid_at",
        "occurred_from",
        "occurred_before",
        "known_at",
        "limit",
    }
    if filters.keys() - allowed:
        raise ValueError(
            "Unsupported graph.query filter(s): "
            + ", ".join(sorted(filters.keys() - allowed))
        )
    mode = filters.get("mode", "current")
    if mode not in ("current", "audit"):
        raise ValueError("mode must be current or audit")
    if mode == "current" and filters.get("valid_at") is not None:
        raise ValueError(
            "valid_at requires mode='audit'; current mode evaluates eligibility now"
        )
    node_id = filters.get("node_id")
    predicate = filters.get("predicate")
    role_nodes = filters.get("role_nodes")
    epistemic_status = filters.get("epistemic_status")
    valid_at = filters.get("valid_at")
    occurred_from = filters.get("occurred_from")
    occurred_before = filters.get("occurred_before")
    limit = filters.get("limit", 50)
    if "known_at" in filters:
        raise ValueError(
            "known_at is unsupported: graph upsert overwrites same-ID records; "
            "created_at/updated_at cannot reconstruct database knowledge history"
        )
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer from 1 to 100")
    for name, value in (
        ("valid_at", valid_at),
        ("occurred_from", occurred_from),
        ("occurred_before", occurred_before),
    ):
        if value is None:
            continue
        try:
            finite = (
                not isinstance(value, bool)
                and isinstance(value, (int, float))
                and math.isfinite(value)
            )
        except OverflowError:
            finite = False
        if not finite:
            raise ValueError(f"{name} must be finite Unix seconds")
    if (
        occurred_from is not None
        and occurred_before is not None
        and occurred_from >= occurred_before
    ):
        raise ValueError("occurred_from must be earlier than occurred_before")
    for name, value in (
        ("node_id", node_id),
        ("predicate", predicate),
        ("epistemic_status", epistemic_status),
    ):
        if value is not None and (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > GRAPH_QUERY_MAX_FILTER_CHARS
        ):
            raise ValueError(
                f"{name} must be a non-empty string of at most {GRAPH_QUERY_MAX_FILTER_CHARS} characters"
            )
    if (
        epistemic_status is not None
        and epistemic_status not in GRAPH_CLAIM_EPISTEMIC_STATUSES
    ):
        raise ValueError("epistemic_status is not supported")
    if role_nodes is not None and (
        not isinstance(role_nodes, dict)
        or len(role_nodes) > 8
        or any(
            not isinstance(k, str)
            or not k.strip()
            or len(k) > GRAPH_QUERY_MAX_FILTER_CHARS
            or not isinstance(v, str)
            or not v.strip()
            or len(v) > GRAPH_QUERY_MAX_FILTER_CHARS
            for k, v in role_nodes.items()
        )
    ):
        raise ValueError(
            "role_nodes must map up to 8 non-empty role names to node IDs, each at most 512 characters"
        )


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

    def _load_claim(
        self,
        row: sqlite3.Row | None,
        *,
        role_rows: Optional[List[sqlite3.Row]] = None,
    ) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        if role_rows is None:
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
                    "value_present": role["value_json"] is not None,
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

    def list_claims(self, *, limit: int = 100) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT claim_id, claim_type, predicate, status, epistemic_status,
                       confidence, valid_from, valid_to, occurred_at, source_kind,
                       source_ref, metadata_json, created_at, updated_at
                FROM graph_claims
                ORDER BY updated_at DESC, claim_id ASC
                LIMIT ?
                """,
                (max(1, int(limit or 100)),),
            ).fetchall()
        return [claim for row in rows if (claim := self._load_claim(row)) is not None]

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

    def query_claims(
        self,
        *,
        mode: str = "current",
        node_id: Optional[str] = None,
        predicate: Optional[str] = None,
        role_nodes: Optional[Dict[str, str]] = None,
        epistemic_status: Optional[str] = None,
        valid_at: Optional[float] = None,
        occurred_from: Optional[float] = None,
        occurred_before: Optional[float] = None,
        known_at: Optional[float] | object = _QUERY_DEFAULT,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """Read complete retained claims; this is not database-as-of history.

        Exact structural and time filters run before a stable, bounded LIMIT.
        A single read transaction keeps claims, their roles, and nodes consistent.
        """
        filters = {
            "mode": mode,
            "node_id": node_id,
            "predicate": predicate,
            "role_nodes": role_nodes,
            "epistemic_status": epistemic_status,
            "valid_at": valid_at,
            "occurred_from": occurred_from,
            "occurred_before": occurred_before,
            "limit": limit,
        }
        if known_at is not _QUERY_DEFAULT:
            filters["known_at"] = known_at
        validate_graph_query_filters(filters)
        # SQLite binds Python ints as signed 64-bit values. Dates use REAL so
        # every validated finite number behaves consistently for direct callers.
        valid_at = float(valid_at) if valid_at is not None else None
        occurred_from = float(occurred_from) if occurred_from is not None else None
        occurred_before = (
            float(occurred_before) if occurred_before is not None else None
        )

        clauses = ["1 = 1"]
        params: List[Any] = []
        if node_id is not None:
            clauses.append(
                "EXISTS (SELECT 1 FROM graph_claim_roles r "
                "WHERE r.claim_id = c.claim_id AND r.node_id = ?)"
            )
            params.append(node_id)
        for column, value in (
            ("predicate", predicate),
            ("epistemic_status", epistemic_status),
        ):
            if value is not None:
                clauses.append(f"c.{column} = ?")
                params.append(value)
        for role_name, role_node_id in sorted((role_nodes or {}).items()):
            clauses.append(
                "EXISTS (SELECT 1 FROM graph_claim_roles r WHERE r.claim_id = c.claim_id "
                "AND r.role_name = ? AND r.node_id = ?)"
            )
            params.extend((role_name, role_node_id))
        current_at = time.time() if mode == "current" else None
        if mode == "current":
            # Eligibility is not truth or user adoption. Keep all matching
            # alternatives, but never revive a withdrawn or out-of-date row.
            clauses.extend(
                [
                    "LOWER(TRIM(c.status)) = 'active'",
                    "c.epistemic_status IN ('observed', 'asserted', 'scheduled', 'predicted', 'hypothesized')",
                    "(c.valid_from IS NULL OR c.valid_from <= ?)",
                    "(c.valid_to IS NULL OR ? < c.valid_to)",
                ]
            )
            params.extend((current_at, current_at))
        structural_where = " AND ".join(clauses)
        structural_params = list(params)
        unknown_time = []
        if valid_at is not None:
            # An absent start cannot support a dated answer. Superseded rows also
            # require an explicit end; never guess that end from updated_at.
            unknown_time.append(
                "(c.valid_from IS NULL OR "
                "((c.epistemic_status = 'superseded' OR LOWER(TRIM(c.status)) = 'superseded') AND c.valid_to IS NULL))"
            )
            clauses.extend(
                [
                    "c.valid_from IS NOT NULL AND c.valid_from <= ?",
                    "(c.valid_to IS NULL OR ? < c.valid_to)",
                    "((c.epistemic_status != 'superseded' AND LOWER(TRIM(c.status)) != 'superseded') OR c.valid_to IS NOT NULL)",
                ]
            )
            params.extend((valid_at, valid_at))
        if occurred_from is not None or occurred_before is not None:
            unknown_time.append("c.occurred_at IS NULL")
        if occurred_from is not None:
            clauses.append("c.occurred_at >= ?")
            params.append(occurred_from)
        if occurred_before is not None:
            clauses.append("c.occurred_at < ?")
            params.append(occurred_before)
        where = " AND ".join(clauses)
        with self._connect() as conn:
            conn.execute("BEGIN")
            unknown_count = 0
            if unknown_time:
                unknown_count = conn.execute(
                    "SELECT COUNT(*) FROM graph_claims c WHERE "
                    + structural_where
                    + " AND ("
                    + " OR ".join(unknown_time)
                    + ")",
                    structural_params,
                ).fetchone()[0]
            matched_count = conn.execute(
                "SELECT COUNT(*) FROM graph_claims c WHERE " + where, params
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT c.* FROM graph_claims c WHERE "
                + where
                + " ORDER BY c.claim_id ASC LIMIT ?",
                [*params, limit],
            ).fetchall()
            claims = []
            nodes = {}
            role_count = 0
            for row in rows:
                role_rows = conn.execute(
                    "SELECT * FROM graph_claim_roles WHERE claim_id = ? "
                    "ORDER BY ordinal ASC, role_name ASC LIMIT ?",
                    (row["claim_id"], GRAPH_QUERY_MAX_ROLES - role_count + 1),
                ).fetchall()
                role_count += len(role_rows)
                if role_count > GRAPH_QUERY_MAX_ROLES:
                    raise ValueError(
                        "graph.query result too large: more than 1000 roles; "
                        "narrow the query or lower limit; no incomplete claims returned"
                    )
                claims.append(self._load_claim(row, role_rows=role_rows))
                for role in role_rows:
                    target = role["node_id"]
                    if target is not None and target not in nodes:
                        node = self._load_node(
                            conn.execute(
                                "SELECT * FROM graph_nodes WHERE node_id = ?", (target,)
                            ).fetchone()
                        )
                        if node is not None:
                            nodes[target] = node
        result = {
            "claims": claims,
            "nodes": [nodes[key] for key in sorted(nodes)],
            "metadata": {
                "available": True,
                "mode": mode,
                "current_at": current_at,
                "current_claims_contract": "active, non-cancelled/non-superseded retained claims whose supplied validity bounds include now; not verified facts or user decisions",
                "claim_count": len(claims),
                "matched_claim_count": matched_count,
                "limit": limit,
                "truncated": matched_count > limit,
                "excluded_unknown_time_count": unknown_count,
                "order": "claim_id_ascending",
                "conflict_resolution": "none; all matching claims retained",
                "knowledge_as_of_supported": False,
                "max_response_bytes": GRAPH_QUERY_MAX_RESPONSE_BYTES,
                "max_roles": GRAPH_QUERY_MAX_ROLES,
                "time_contract": {
                    "valid_at": "explicit valid_from <= t < valid_to; absent end is open except for superseded claims",
                    "occurred_range": "occurred_from <= occurred_at < occurred_before; unknown occurred_at excluded",
                    "occurred_at": "stored event/source time, not a recorded-at guarantee",
                },
                "provenance_contract": "metadata is unvalidated attribution; proposals are not user adoption",
            },
        }
        encoded = json.dumps(
            {"status": "ok", **result}, ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
        if len(encoded) > GRAPH_QUERY_MAX_RESPONSE_BYTES:
            raise ValueError(
                "graph.query result too large: exceeds 262144 UTF-8 JSON bytes; "
                "narrow the query or lower limit; no incomplete claims returned"
            )
        return result

    def _restore_snapshot(self, conn: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
        """Read records and complete roles on the active restore transaction."""
        nodes = {}
        for row in conn.execute("SELECT * FROM graph_nodes"):
            record = dict(row)
            record["attributes"] = _json_loads(record.pop("attributes_json"))
            nodes[record["node_id"]] = record
        claims = {}
        for row in conn.execute("SELECT * FROM graph_claims"):
            record = dict(row)
            record["metadata"] = _json_loads(record.pop("metadata_json"))
            record["roles"] = []
            claims[record["claim_id"]] = record
        for row in conn.execute(
            "SELECT * FROM graph_claim_roles ORDER BY ordinal ASC, role_name ASC"
        ):
            claims[row["claim_id"]]["roles"].append(
                {
                    "role_name": row["role_name"],
                    "ordinal": row["ordinal"],
                    "node_id": row["node_id"],
                    "value": _json_loads_any(row["value_json"]),
                    "value_present": row["value_json"] is not None,
                    "metadata": _json_loads(row["metadata_json"]),
                }
            )
        return {"nodes": nodes, "claims": claims}

    @staticmethod
    def _restore_record(record: Any, record_id: str, *, kind: str) -> Any:
        if record is None:
            return None
        fields = (
            "node_id node_kind node_type canonical_name summary_text attributes "
            "status created_at updated_at"
            if kind == "nodes"
            else "claim_id claim_type predicate status epistemic_status confidence "
            "valid_from valid_to occurred_at source_kind source_ref metadata "
            "created_at updated_at roles"
        ).split()
        if not isinstance(record, dict) or any(key not in record for key in fields):
            raise ValueError("Graph restore requires complete record snapshots")
        result = {key: copy.deepcopy(record[key]) for key in fields}
        id_key = "node_id" if kind == "nodes" else "claim_id"
        if result[id_key] != record_id or not record_id:
            raise ValueError("Graph restore snapshot identity mismatch")
        json_key = "attributes" if kind == "nodes" else "metadata"
        if not isinstance(result[json_key], dict):
            raise ValueError("Graph restore metadata must be an object")
        if kind == "claims":
            if not isinstance(result["roles"], list):
                raise ValueError("Graph restore requires complete claim roles")
            roles = []
            for role in result["roles"]:
                if not isinstance(role, dict) or any(
                    key not in role
                    for key in ("role_name", "ordinal", "node_id", "value", "metadata")
                ):
                    raise ValueError("Graph restore requires complete claim roles")
                if (
                    not isinstance(role["role_name"], str)
                    or not role["role_name"].strip()
                    or not isinstance(role["ordinal"], int)
                    or not isinstance(role["metadata"], dict)
                ):
                    raise ValueError("Graph restore has an invalid claim role")
                restored_role = {
                    key: copy.deepcopy(role[key])
                    for key in ("role_name", "ordinal", "node_id", "value", "metadata")
                }
                restored_role["value_present"] = role.get(
                    "value_present", role["value"] is not None
                )
                if not isinstance(restored_role["value_present"], bool):
                    raise ValueError("Graph restore has an invalid value presence flag")
                if (
                    restored_role["node_id"] is None
                    and not restored_role["value_present"]
                ):
                    raise ValueError(
                        "Graph restore role has no endpoint or literal value"
                    )
                if (
                    not restored_role["value_present"]
                    and restored_role["value"] is not None
                ):
                    raise ValueError("Graph restore has an inconsistent literal value")
                roles.append(restored_role)
            result["roles"] = sorted(
                roles, key=lambda role: (role["ordinal"], role["role_name"])
            )
        return result

    @staticmethod
    def _restore_equal(left: Any, right: Any) -> bool:
        # Python equality conflates JSON false/0 and true/1 inside literal roles.
        def encoded(value: Any) -> str:
            return json.dumps(
                value, ensure_ascii=False, sort_keys=True, allow_nan=False
            )

        return encoded(left) == encoded(right)

    def restore_records(
        self,
        *,
        nodes: Dict[str, Any],
        claims: Dict[str, Any],
        expected_nodes: Optional[Dict[str, Any]] = None,
        expected_claims: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Restore selected history records atomically; this is not a sync merge.

        None deletes a selected record. Every supplied claim includes its full role
        set, even an explicitly empty historical set. Unselected records stay intact.
        """
        desired = {
            kind: {
                key: self._restore_record(value, key, kind=kind)
                for key, value in records.items()
            }
            for kind, records in (("nodes", nodes), ("claims", claims))
        }
        expected = {
            kind: None
            if records is None
            else {
                key: self._restore_record(value, key, kind=kind)
                for key, value in records.items()
            }
            for kind, records in (
                ("nodes", expected_nodes),
                ("claims", expected_claims),
            )
        }
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            before = self._restore_snapshot(conn)
            for kind in ("nodes", "claims"):
                for key, value in (expected[kind] or {}).items():
                    if not self._restore_equal(before[kind].get(key), value):
                        raise ValueError(
                            "Cannot restore graph because current state differs from the recorded action"
                        )
            deleted_nodes = {
                key for key, value in desired["nodes"].items() if value is None
            }
            for claim_id, claim in before["claims"].items():
                if claim_id not in desired["claims"] and any(
                    role["node_id"] in deleted_nodes for role in claim["roles"]
                ):
                    raise ValueError(
                        "Graph restore would remove roles from an unselected claim"
                    )
            now = time.time()
            for kind in ("nodes", "claims"):
                for key, value in desired[kind].items():
                    if value is not None:
                        current = before[kind].get(key)
                        if current is not None and self._restore_equal(
                            {
                                field: item
                                for field, item in current.items()
                                if field != "updated_at"
                            },
                            {
                                field: item
                                for field, item in value.items()
                                if field != "updated_at"
                            },
                        ):
                            value["updated_at"] = current["updated_at"]
                        else:
                            value["updated_at"] = max(
                                now,
                                math.nextafter(
                                    float((current or {}).get("updated_at", 0)),
                                    math.inf,
                                ),
                            )
            # Remove selected old roles before deleting nodes, then restore nodes
            # before inserting any endpoint roles. Item ordering cannot lose roles.
            for claim_id in desired["claims"]:
                conn.execute(
                    "DELETE FROM graph_claim_roles WHERE claim_id = ?", (claim_id,)
                )
            for kind, table, id_key in (
                ("claims", "graph_claims", "claim_id"),
                ("nodes", "graph_nodes", "node_id"),
            ):
                for key, value in desired[kind].items():
                    if value is None:
                        conn.execute(f"DELETE FROM {table} WHERE {id_key} = ?", (key,))
            for kind, table, id_key, json_key in (
                ("nodes", "graph_nodes", "node_id", "attributes"),
                ("claims", "graph_claims", "claim_id", "metadata"),
            ):
                for key, value in desired[kind].items():
                    if value is None:
                        continue
                    row = {
                        field: item for field, item in value.items() if field != "roles"
                    }
                    row[f"{json_key}_json"] = _json_dumps(row.pop(json_key))
                    columns = list(row)
                    updates = ", ".join(
                        f"{field}=excluded.{field}"
                        for field in columns
                        if field != id_key
                    )
                    conn.execute(
                        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)}) "
                        f"ON CONFLICT({id_key}) DO UPDATE SET {updates}",
                        tuple(row[field] for field in columns),
                    )
                    if kind == "claims":
                        for role in value["roles"]:
                            conn.execute(
                                "INSERT INTO graph_claim_roles (claim_id, role_name, ordinal, node_id, value_json, metadata_json) VALUES (?, ?, ?, ?, ?, ?)",
                                (
                                    key,
                                    role["role_name"],
                                    role["ordinal"],
                                    role["node_id"],
                                    json.dumps(role["value"], ensure_ascii=False)
                                    if role["value_present"]
                                    else None,
                                    _json_dumps(role["metadata"]),
                                ),
                            )
            after = self._restore_snapshot(conn)
            for kind in ("nodes", "claims"):
                for key, value in desired[kind].items():
                    if not self._restore_equal(after[kind].get(key), value):
                        raise ValueError("Graph restore postcondition failed")
                for key in set(before[kind]) | set(after[kind]):
                    if key not in desired[kind] and not self._restore_equal(
                        before[kind].get(key), after[kind].get(key)
                    ):
                        raise ValueError("Graph restore changed an unselected record")
            foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
            integrity = [row[0] for row in conn.execute("PRAGMA integrity_check")]
            if foreign_keys or integrity != ["ok"]:
                raise ValueError("Graph restore integrity check failed")
            all_claims = list(after["claims"].values())
            integrity_result = {
                "integrity_check": "ok",
                "unresolved_endpoint_count": 0,
                "node_count": len(after["nodes"]),
                "claim_count": len(all_claims),
                "role_count": sum(len(claim["roles"]) for claim in all_claims),
                "roleless_claim_count": sum(not claim["roles"] for claim in all_claims),
                "projectable_claim_count": sum(
                    len(
                        roles := [
                            role
                            for role in claim["roles"]
                            if role["node_id"] is not None
                        ]
                    )
                    >= 2
                    and bool(
                        self._claim_projection_links(claim, roles, prefix="knowledge")
                    )
                    for claim in all_claims
                ),
            }
            changes = [
                {
                    "kind": kind,
                    "record_id": key,
                    "before": before[kind].get(key),
                    "after": after[kind].get(key),
                }
                for kind in ("nodes", "claims")
                for key in desired[kind]
                if not self._restore_equal(before[kind].get(key), after[kind].get(key))
            ]
            return {"changes": changes, "integrity": integrity_result}

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

    def projection(
        self,
        *,
        node_limit: int = 120,
        claim_limit: int = 240,
    ) -> Dict[str, Any]:
        """Return a visualization-ready projection of durable graph state."""

        store_summary = self.summary()
        raw_nodes = self.list_nodes(limit=node_limit)
        raw_claims = self.list_claims(limit=claim_limit)
        node_ids = {str(node.get("node_id") or "") for node in raw_nodes}
        node_ids.discard("")
        prefix = "knowledge"
        links: List[Dict[str, Any]] = []
        visible_claims: List[Dict[str, Any]] = []
        degree_counts: Dict[str, int] = {}

        for claim in raw_claims:
            roles = [
                role
                for role in claim.get("roles", [])
                if str(role.get("node_id") or "") in node_ids
            ]
            if len(roles) < 2:
                continue
            claim_links = self._claim_projection_links(claim, roles, prefix=prefix)
            if not claim_links:
                continue
            visible_claims.append(claim)
            for link in claim_links:
                links.append(link)
                source = str(link.get("source") or "")
                target = str(link.get("target") or "")
                degree_counts[source] = degree_counts.get(source, 0) + 1
                degree_counts[target] = degree_counts.get(target, 0) + 1

        nodes: List[Dict[str, Any]] = []
        for raw_node in raw_nodes:
            node_id = str(raw_node.get("node_id") or "")
            projected_id = f"{prefix}:{node_id}"
            attributes = raw_node.get("attributes")
            attributes = attributes if isinstance(attributes, dict) else {}
            nodes.append(
                {
                    "id": projected_id,
                    "node_id": node_id,
                    "label": raw_node.get("canonical_name") or node_id,
                    "type": raw_node.get("node_type") or raw_node.get("node_kind"),
                    "node_kind": raw_node.get("node_kind"),
                    "node_type": raw_node.get("node_type"),
                    "graph_key": "knowledge",
                    "level": 0
                    if str(raw_node.get("node_kind") or "") == "entity"
                    else 1,
                    "weight": max(1, degree_counts.get(projected_id, 0)),
                    "summary_text": raw_node.get("summary_text") or "",
                    "attributes": attributes,
                    "status": raw_node.get("status") or "active",
                    "match_key": attributes.get("match_key") or "",
                    "updated_at": raw_node.get("updated_at"),
                }
            )

        return {
            "schema_version": GRAPH_STORE_SCHEMA_VERSION,
            "nodes": nodes,
            "links": links,
            "claims": visible_claims,
            "metadata": {
                "schema_version": GRAPH_STORE_SCHEMA_VERSION,
                "node_count": len(nodes),
                "claim_count": len(visible_claims),
                "link_count": len(links),
                "total_node_count": store_summary["node_count"],
                "total_claim_count": store_summary["claim_count"],
                "nodes_truncated": store_summary["node_count"] > len(raw_nodes),
                "claims_truncated": store_summary["claim_count"] > len(raw_claims),
                "truncated": (
                    store_summary["node_count"] > len(raw_nodes)
                    or store_summary["claim_count"] > len(raw_claims)
                ),
                "source": "graph_store",
                "maxLevel": 1 if any(node.get("level") == 1 for node in nodes) else 0,
            },
        }

    @staticmethod
    def _claim_projection_links(
        claim: Dict[str, Any],
        roles: List[Dict[str, Any]],
        *,
        prefix: str,
    ) -> List[Dict[str, Any]]:
        def projected_id(role: Dict[str, Any]) -> str:
            return f"{prefix}:{role.get('node_id')}"

        subject_roles = [
            role
            for role in roles
            if str(role.get("role_name") or "").lower()
            in {"subject", "source", "from", "actor", "owner", "person"}
        ]
        object_roles = [
            role
            for role in roles
            if str(role.get("role_name") or "").lower()
            in {"object", "target", "to", "friend", "member"}
        ]
        if subject_roles and object_roles:
            pairs = [
                (source, target)
                for source in subject_roles
                for target in object_roles
                if projected_id(source) != projected_id(target)
            ]
        else:
            anchor = roles[0]
            pairs = [
                (anchor, role)
                for role in roles[1:]
                if projected_id(anchor) != projected_id(role)
            ]

        links: List[Dict[str, Any]] = []
        for source, target in pairs:
            links.append(
                {
                    "source": projected_id(source),
                    "target": projected_id(target),
                    "type": "claim",
                    "graph_key": "knowledge",
                    "predicate": claim.get("predicate") or "",
                    "claim_id": claim.get("claim_id") or "",
                    "claim_type": claim.get("claim_type") or "relation",
                    "epistemic_status": claim.get("epistemic_status") or "asserted",
                    "status": claim.get("status") or "active",
                    "confidence": claim.get("confidence"),
                    "weight": max(0.1, min(float(claim.get("confidence") or 1.0), 2.0)),
                    "source_role": source.get("role_name"),
                    "target_role": target.get("role_name"),
                    "metadata": claim.get("metadata") or {},
                }
            )
        return links


__all__ = ["GraphStore", "GRAPH_STORE_SCHEMA_VERSION", "resolve_path"]
