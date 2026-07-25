from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.graph_payload_service import apply_graph_payload
from app.utils import verify_signature

_GRAPH_STORE = None


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


__all__ = ["set_graph_store", "update_graph"]
