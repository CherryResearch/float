"""Helpers for applying structured graph updates from tools and API routes."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.utils.graph_store import (
    GRAPH_CLAIM_EPISTEMIC_STATUSES,
    GRAPH_NODE_KINDS,
    GRAPH_STORE_SCHEMA_VERSION,
)


def graph_schema_payload() -> Dict[str, Any]:
    return {
        "schema_version": GRAPH_STORE_SCHEMA_VERSION,
        "node_kinds": sorted(GRAPH_NODE_KINDS),
        "epistemic_statuses": sorted(GRAPH_CLAIM_EPISTEMIC_STATUSES),
        "node": {
            "required": ["node_type"],
            "optional": [
                "node_id",
                "ref",
                "node_kind",
                "canonical_name",
                "summary_text",
                "attributes",
                "status",
            ],
        },
        "claim": {
            "required": ["predicate", "roles"],
            "optional": [
                "claim_id",
                "claim_type",
                "status",
                "epistemic_status",
                "confidence",
                "valid_from",
                "valid_to",
                "occurred_at",
                "source_kind",
                "source_ref",
                "metadata",
            ],
            "role": {
                "required": ["role_name"],
                "one_of": ["node_id", "node_ref", "value"],
            },
        },
    }


def _dict_list(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _metadata(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _text(value: Any, default: str = "") -> str:
    text = str(value if value is not None else default).strip()
    return text or default


def _node_ref(raw: Dict[str, Any], index: int) -> str:
    return _text(
        raw.get("ref")
        or raw.get("node_ref")
        or raw.get("node_id")
        or raw.get("id")
        or raw.get("canonical_name")
        or raw.get("name")
        or raw.get("label")
        or f"node-{index}"
    )


def _role_node_id(role: Dict[str, Any], node_refs: Dict[str, str]) -> Optional[str]:
    node_id = _text(role.get("node_id") or role.get("id") or role.get("node"))
    if node_id:
        return node_id
    node_ref = _text(role.get("node_ref") or role.get("ref"))
    if node_ref:
        return node_refs.get(node_ref)
    return None


def apply_graph_payload(
    graph_store: Any,
    *,
    graph_nodes: Any = None,
    graph_claims: Any = None,
    default_source_kind: Optional[str] = None,
    default_source_ref: Optional[str] = None,
    memory_key: Optional[str] = None,
) -> Dict[str, Any]:
    nodes = _dict_list(graph_nodes)
    claims = _dict_list(graph_claims)
    summary: Dict[str, Any] = {
        "available": graph_store is not None,
        "node_count": 0,
        "claim_count": 0,
        "nodes": [],
        "claims": [],
        "errors": [],
    }
    if graph_store is None:
        if nodes or claims:
            summary["errors"].append("graph store is not available")
        return summary

    node_refs: Dict[str, str] = {}
    for index, raw_node in enumerate(nodes):
        try:
            attributes = _metadata(raw_node.get("attributes"))
            if not attributes and isinstance(raw_node.get("metadata"), dict):
                attributes = _metadata(raw_node.get("metadata"))
            node = graph_store.upsert_node(
                node_id=_text(raw_node.get("node_id") or raw_node.get("id")) or None,
                node_kind=_text(
                    raw_node.get("node_kind") or raw_node.get("kind"), "entity"
                ),
                node_type=_text(
                    raw_node.get("node_type") or raw_node.get("type"), "unknown"
                ),
                canonical_name=_text(
                    raw_node.get("canonical_name")
                    or raw_node.get("name")
                    or raw_node.get("label")
                ),
                summary_text=_text(
                    raw_node.get("summary_text") or raw_node.get("summary")
                ),
                attributes=attributes,
                status=_text(raw_node.get("status"), "active"),
            )
            ref = _node_ref(raw_node, index)
            node_refs[ref] = node["node_id"]
            node_refs[node["node_id"]] = node["node_id"]
            if node.get("canonical_name"):
                node_refs[str(node["canonical_name"])] = node["node_id"]
            summary["nodes"].append(node["node_id"])
            summary["node_count"] += 1
        except Exception as exc:
            summary["errors"].append(f"node[{index}]: {exc}")

    for index, raw_claim in enumerate(claims):
        try:
            roles: List[Dict[str, Any]] = []
            for role_index, raw_role in enumerate(_dict_list(raw_claim.get("roles"))):
                role_name = _text(
                    raw_role.get("role_name") or raw_role.get("role"),
                    f"role_{role_index}",
                )
                node_id = _role_node_id(raw_role, node_refs)
                value = raw_role.get("value")
                roles.append(
                    {
                        "role_name": role_name,
                        "ordinal": raw_role.get("ordinal", role_index),
                        "node_id": node_id,
                        "value": value,
                        "metadata": _metadata(raw_role.get("metadata")),
                    }
                )
            metadata = _metadata(raw_claim.get("metadata"))
            if memory_key and "memory_key" not in metadata:
                metadata["memory_key"] = memory_key
            claim = graph_store.upsert_claim(
                claim_id=_text(raw_claim.get("claim_id") or raw_claim.get("id"))
                or None,
                predicate=_text(raw_claim.get("predicate") or raw_claim.get("label")),
                roles=roles,
                claim_type=_text(
                    raw_claim.get("claim_type") or raw_claim.get("type"), "relation"
                ),
                status=_text(raw_claim.get("status"), "active"),
                epistemic_status=_text(raw_claim.get("epistemic_status"), "asserted"),
                confidence=float(raw_claim.get("confidence", 1.0) or 1.0),
                valid_from=raw_claim.get("valid_from"),
                valid_to=raw_claim.get("valid_to"),
                occurred_at=raw_claim.get("occurred_at"),
                source_kind=_text(raw_claim.get("source_kind") or default_source_kind)
                or None,
                source_ref=_text(raw_claim.get("source_ref") or default_source_ref)
                or None,
                metadata=metadata,
            )
            summary["claims"].append(claim["claim_id"])
            summary["claim_count"] += 1
        except Exception as exc:
            summary["errors"].append(f"claim[{index}]: {exc}")

    return summary
