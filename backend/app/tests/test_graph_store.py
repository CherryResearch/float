import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.base_services import MemoryManager
from app.services.graph_payload_service import apply_graph_payload
from app.utils.graph_store import GraphStore

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_graph_store_supports_event_predictions_and_multi_role_claims(tmp_path):
    store = GraphStore(tmp_path / "memory.sqlite3")
    user = store.upsert_node(
        node_kind="entity",
        node_type="person",
        canonical_name="Kai",
    )
    friend = store.upsert_node(
        node_kind="entity",
        node_type="person",
        canonical_name="Maya",
    )
    karate = store.upsert_node(
        node_kind="event",
        node_type="class_session",
        canonical_name="Karate class 2026-03-24 18:00",
    )

    future_ts = (datetime.now(tz=timezone.utc) + timedelta(days=7, hours=2)).timestamp()
    claim = store.upsert_claim(
        predicate="participation",
        claim_type="prediction",
        epistemic_status="scheduled",
        confidence=0.92,
        valid_from=future_ts,
        source_kind="memory",
        source_ref="karate_plan",
        metadata={"memory_key": "karate_plan"},
        roles=[
            {"role": "event", "node_id": karate["node_id"]},
            {"role": "participant", "node_id": user["node_id"]},
            {"role": "participant", "node_id": friend["node_id"]},
            {"role": "location", "value": {"text": "Downtown dojo"}},
        ],
    )

    assert claim["claim_type"] == "prediction"
    assert claim["epistemic_status"] == "scheduled"
    assert claim["valid_from"] == future_ts
    assert len(claim["roles"]) == 4
    assert [role["role_name"] for role in claim["roles"]].count("participant") == 2
    assert any(role["value"] == {"text": "Downtown dojo"} for role in claim["roles"])

    claims_for_user = store.list_claims_for_node(user["node_id"])
    assert len(claims_for_user) == 1
    assert claims_for_user[0]["claim_id"] == claim["claim_id"]


def test_graph_store_allows_self_referential_claim_roles(tmp_path):
    store = GraphStore(tmp_path / "memory.sqlite3")
    node = store.upsert_node(
        node_kind="entity",
        node_type="person",
        canonical_name="Maya",
    )

    claim = store.upsert_claim(
        predicate="same_as",
        roles=[
            {"role": "subject", "node_id": node["node_id"]},
            {"role": "object", "node_id": node["node_id"]},
        ],
    )

    assert claim["predicate"] == "same_as"
    assert [role["node_id"] for role in claim["roles"]] == [
        node["node_id"],
        node["node_id"],
    ]

    claims_for_node = store.list_claims_for_node(node["node_id"])
    assert len(claims_for_node) == 1
    assert claims_for_node[0]["claim_id"] == claim["claim_id"]


def test_memory_manager_initializes_graph_tables_for_persistent_store(tmp_path):
    db_path = tmp_path / "memory.sqlite3"
    manager = MemoryManager({"memory_store_path": str(db_path)})
    assert manager._graph_store is not None

    with sqlite3.connect(str(db_path)) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert "graph_nodes" in tables
    assert "graph_claims" in tables
    assert "graph_claim_roles" in tables


def test_graph_store_projects_basic_social_network_fixture(tmp_path):
    fixture = json.loads((FIXTURES / "basic_social_graph.json").read_text())
    store = GraphStore(tmp_path / "memory.sqlite3")

    summary = apply_graph_payload(
        store,
        graph_nodes=fixture["nodes"],
        graph_claims=fixture["claims"],
        default_source_kind="fixture",
        default_source_ref="basic_social_graph",
    )

    assert summary["errors"] == []
    assert summary["node_count"] == 7
    assert summary["claim_count"] == 7

    self_node = store.get_node("person:self")
    assert self_node is not None
    assert self_node["attributes"]["relation_to_self"] == "self"
    assert self_node["attributes"]["interests"] == ["graph workflows", "tool traces"]

    projection = store.projection()
    assert projection["metadata"]["node_count"] == 7
    assert projection["metadata"]["claim_count"] == 7
    assert projection["metadata"]["link_count"] == 7
    assert all(node["attributes"] for node in projection["nodes"])
    assert {
        (link["source"], link["target"], link["predicate"])
        for link in projection["links"]
    } == {
        ("knowledge:person:self", "knowledge:person:friend-maya", "friend_of"),
        ("knowledge:person:self", "knowledge:person:friend-jules", "friend_of"),
        ("knowledge:person:self", "knowledge:person:coworker-ren", "works_with"),
        ("knowledge:person:self", "knowledge:person:family-lena", "family_of"),
        ("knowledge:person:friend-jules", "knowledge:org:computer-club", "member_of"),
        ("knowledge:person:self", "knowledge:org:job", "works_at"),
        ("knowledge:person:coworker-ren", "knowledge:org:job", "works_at"),
    }
