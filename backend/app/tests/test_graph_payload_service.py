import pytest
from app.services.graph_payload_service import apply_graph_payload
from app.services.instance_sync_service import InstanceSyncService
from app.utils.graph_store import GraphStore


@pytest.mark.parametrize("updating", [False, True])
def test_unresolved_reference_rejects_entire_claim(tmp_path, updating):
    store = GraphStore(tmp_path / "memory.sqlite3")
    store.upsert_node(node_id="person:one", node_kind="entity", node_type="person")
    original = None
    if updating:
        original = store.upsert_claim(
            claim_id="claim:one",
            predicate="likes",
            roles=[
                {"role_name": "subject", "node_id": "person:one"},
                {"role_name": "object", "value": "tea"},
            ],
        )

    result = apply_graph_payload(
        store,
        graph_claims=[
            {
                "claim_id": "claim:one",
                "predicate": "knows",
                "roles": [
                    {"role_name": "subject", "node_id": "person:one"},
                    {"role_name": "object", "node_ref": "previous-call:friend"},
                ],
            }
        ],
    )

    assert result["claim_count"] == 0
    assert "unresolved node_ref" in result["errors"][0]
    assert store.get_claim("claim:one") == original


@pytest.mark.parametrize("confidence", [0, 0.25, None])
def test_confidence_and_false_values_survive_write_and_sync(
    tmp_path, monkeypatch, confidence
):
    store = GraphStore(tmp_path / "source.sqlite3")
    result = apply_graph_payload(
        store,
        graph_nodes=[{"ref": "one", "node_id": "person:one", "node_type": "person"}],
        graph_claims=[
            {
                "claim_id": "claim:one",
                "predicate": "subscribed",
                "confidence": confidence,
                "roles": [
                    {"role_name": "subject", "node_ref": "one"},
                    {"role_name": "object", "value": False},
                ],
            }
        ],
    )
    assert result["errors"] == []
    expected = 1.0 if confidence is None else confidence
    claim = store.get_claim("claim:one")
    assert claim["confidence"] == expected
    assert len(claim["roles"]) == 2
    assert claim["roles"][1]["value"] is False

    target = tmp_path / "target.sqlite3"
    monkeypatch.setenv("FLOAT_MEMORY_FILE", str(target))
    sync = InstanceSyncService()
    sync._merge_graph({"nodes": [store.get_node("person:one")], "claims": [claim]})
    restored = GraphStore(target).get_claim("claim:one")
    assert restored["confidence"] == expected
    assert restored["roles"] == claim["roles"]
