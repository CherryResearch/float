from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services import graph_payload_service
from app.services.instance_sync_service import InstanceSyncService
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

router = APIRouter(tags=["Graph"])


class GraphRoleUpsert(BaseModel):
    role_name: Optional[str] = None
    role: Optional[str] = None
    ordinal: Optional[int] = None
    node_id: Optional[str] = None
    node_ref: Optional[str] = None
    value: Any = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class GraphNodeUpsert(BaseModel):
    node_id: Optional[str] = None
    id: Optional[str] = None
    ref: Optional[str] = None
    node_kind: str = "entity"
    kind: Optional[str] = None
    node_type: str = "unknown"
    type: Optional[str] = None
    canonical_name: Optional[str] = None
    name: Optional[str] = None
    label: Optional[str] = None
    summary_text: Optional[str] = None
    summary: Optional[str] = None
    attributes: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    status: str = "active"


class GraphClaimUpsert(BaseModel):
    claim_id: Optional[str] = None
    id: Optional[str] = None
    claim_type: str = "relation"
    type: Optional[str] = None
    predicate: str
    status: str = "active"
    epistemic_status: str = "asserted"
    confidence: float = 1.0
    valid_from: Any = None
    valid_to: Any = None
    occurred_at: Any = None
    source_kind: Optional[str] = None
    source_ref: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    roles: List[GraphRoleUpsert] = Field(default_factory=list)


class GraphUpsertRequest(BaseModel):
    nodes: List[GraphNodeUpsert] = Field(default_factory=list)
    claims: List[GraphClaimUpsert] = Field(default_factory=list)
    source_kind: Optional[str] = "manual"
    source_ref: Optional[str] = None


def _request_graph_store(request: Request) -> Any:
    manager = getattr(request.app.state, "memory_manager", None)
    return getattr(manager, "_graph_store", None)


@router.get("/graph/schema")
async def graph_schema():
    return graph_payload_service.graph_schema_payload()


@router.get("/graph")
async def graph_projection(
    request: Request,
    limit_nodes: int = Query(default=120, ge=1, le=500),
    limit_claims: int = Query(default=240, ge=1, le=1000),
):
    graph_store = _request_graph_store(request)
    if graph_store is None:
        return {
            "graph": {
                "schema_version": graph_payload_service.graph_schema_payload()[
                    "schema_version"
                ],
                "nodes": [],
                "links": [],
                "claims": [],
                "metadata": {
                    "available": False,
                    "node_count": 0,
                    "claim_count": 0,
                    "link_count": 0,
                    "source": "graph_store",
                },
            }
        }
    graph = graph_store.projection(
        node_limit=limit_nodes,
        claim_limit=limit_claims,
    )
    graph.setdefault("metadata", {})["available"] = True
    return {"graph": graph}


@router.post("/graph")
async def graph_upsert(request: Request, payload: GraphUpsertRequest):
    graph_store = _request_graph_store(request)
    if graph_store is None:
        raise HTTPException(status_code=503, detail="graph store unavailable")
    action_history = getattr(request.app.state, "action_history_service", None)
    before_snapshot = (
        InstanceSyncService().build_snapshot(["graph"])
        if action_history is not None
        else None
    )
    summary = graph_payload_service.apply_graph_payload(
        graph_store,
        graph_nodes=[node.model_dump(exclude_none=True) for node in payload.nodes],
        graph_claims=[claim.model_dump(exclude_none=True) for claim in payload.claims],
        default_source_kind=payload.source_kind or "manual",
        default_source_ref=payload.source_ref,
    )
    graph = graph_store.projection()
    graph.setdefault("metadata", {})["available"] = True
    revision = None
    if action_history is not None and before_snapshot is not None:
        after_snapshot = InstanceSyncService().build_snapshot(["graph"])
        action = action_history.record_snapshot_action(
            kind="manual",
            name="graph.update",
            before_snapshot=before_snapshot,
            after_snapshot=after_snapshot,
            sections=["graph"],
            args=payload.model_dump(exclude_none=True),
            result=summary,
            summary=(
                "graph.update applied: "
                f"{summary.get('node_count', 0)} nodes, "
                f"{summary.get('claim_count', 0)} claims"
            ),
        )
        if isinstance(action, dict):
            revision = {
                "action_id": action.get("id"),
                "summary": action.get("summary"),
                "item_count": len(action.get("items") or []),
            }
    return {
        "status": "partial" if summary.get("errors") else "ok",
        "graph_update": summary,
        "revision": revision,
        "graph": graph,
    }


__all__ = ["router"]
