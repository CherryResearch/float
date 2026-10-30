from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.graph_payload_service import apply_graph_payload
from app.utils import privacy_policy, verify_signature

_GRAPH_STORE = None
_DEFAULT = object()


def set_graph_store(store) -> None:
    global _GRAPH_STORE
    _GRAPH_STORE = store


def _store():
    if _GRAPH_STORE is None:
        raise RuntimeError("graph store not available")
    return _GRAPH_STORE


def update_graph(
    nodes: Optional[List[Dict[str, Any]]] = None,
    claims: Optional[List[Dict[str, Any]]] = None,
    source_kind: str = "tool",
    source_ref: str = "",
    *,
    user: str,
    signature: str,
) -> Dict[str, Any]:
    payload = {
        "nodes": list(nodes or []),
        "claims": list(claims or []),
        "source_kind": source_kind or "tool",
        "source_ref": source_ref or "",
    }
    verify_signature(signature, user, "graph.update", payload)
    if not payload["nodes"] and not payload["claims"]:
        raise ValueError("graph.update requires at least one node or claim")
    summary = apply_graph_payload(
        _store(),
        graph_nodes=payload["nodes"],
        graph_claims=payload["claims"],
        default_source_kind=payload["source_kind"],
        default_source_ref=payload["source_ref"],
    )
    graph = _store().projection()
    graph.setdefault("metadata", {})["available"] = True
    return {
        "status": "partial" if summary.get("errors") else "ok",
        "graph_update": summary,
        "graph": graph,
    }


def query_graph(
    mode: str | object = _DEFAULT,
    node_id: Optional[str] | object = _DEFAULT,
    predicate: Optional[str] | object = _DEFAULT,
    role_nodes: Optional[Dict[str, str]] | object = _DEFAULT,
    epistemic_status: Optional[str] | object = _DEFAULT,
    valid_at: Optional[float] | object = _DEFAULT,
    occurred_from: Optional[float] | object = _DEFAULT,
    occurred_before: Optional[float] | object = _DEFAULT,
    known_at: Optional[float] | object = _DEFAULT,
    limit: int | object = _DEFAULT,
    *,
    user: str,
    signature: str,
) -> Dict[str, Any]:
    """Query retained graph claims without changing state or resolving conflicts."""
    payload = {
        key: value
        for key, value in {
            "mode": mode,
            "node_id": node_id,
            "predicate": predicate,
            "role_nodes": role_nodes,
            "epistemic_status": epistemic_status,
            "valid_at": valid_at,
            "occurred_from": occurred_from,
            "occurred_before": occurred_before,
            "known_at": known_at,
            "limit": limit,
        }.items()
        if value is not _DEFAULT
    }
    verify_signature(signature, user, "graph.query", payload)
    policy = privacy_policy.current_data_policy()
    if policy is None or policy.external is not False:
        raise PermissionError(
            "graph.query is local-only: sensitivity inheritance is not enforced; "
            "an explicit local data-policy destination is required"
        )
    return {"status": "ok", **_store().query_claims(**payload)}


__all__ = ["set_graph_store", "update_graph", "query_graph"]
