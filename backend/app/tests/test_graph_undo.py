"""Offline synthetic graph undo regressions; no user facts involved.

Run against a complete Float checkout with PYTHONPATH=backend and pytest.
"""

import copy
import socket
import sqlite3
import time

import pytest
from app.services.action_history_service import ActionHistoryService
from app.services.instance_sync_service import InstanceSyncService
from app.utils import calendar_store, conversation_store, user_settings
from app.utils.graph_store import GraphStore


def test_synthetic_graph_rename_undo_restores_older_value(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOAT_MEMORY_FILE", str(tmp_path / "synthetic.sqlite3"))
    monkeypatch.setattr(user_settings, "USER_SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(conversation_store, "CONV_DIR", tmp_path / "conversations")
    monkeypatch.setattr(calendar_store, "EVENTS_DIR", tmp_path / "calendar")
    graph = GraphStore()
    service = ActionHistoryService({"data_dir": str(tmp_path / "data")})
    sync = InstanceSyncService()
    node_id = "synthetic:rename-regression"
    graph.upsert_node(
        node_id=node_id,
        node_kind="entity",
        node_type="synthetic_example",
        canonical_name="SYNTHETIC Before",
    )
    before = {"sections": {"graph": sync._graph_snapshot()}}
    time.sleep(0.01)
    graph.upsert_node(
        node_id=node_id,
        node_kind="entity",
        node_type="synthetic_example",
        canonical_name="SYNTHETIC After",
    )
    after = {"sections": {"graph": sync._graph_snapshot()}}
    action = service.record_snapshot_action(
        kind="tool",
        name="graph.update",
        before_snapshot=before,
        after_snapshot=after,
        sections=["graph"],
        summary="Synthetic rename only",
    )
    assert action is not None
    result = service.revert_actions(action_ids=[action["id"]])
    assert result["status"] == "reverted"
    assert graph.get_node(node_id)["canonical_name"] == "SYNTHETIC Before"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("Graph undo tests must not use the network")

    monkeypatch.setattr(socket.socket, "connect", reject)
    monkeypatch.setattr(socket.socket, "connect_ex", reject)
    monkeypatch.setattr(socket, "create_connection", reject)


@pytest.fixture
def history(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOAT_MEMORY_FILE", str(tmp_path / "graph.sqlite3"))
    monkeypatch.setattr(user_settings, "USER_SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(conversation_store, "CONV_DIR", tmp_path / "conversations")
    monkeypatch.setattr(calendar_store, "EVENTS_DIR", tmp_path / "calendar")
    graph = GraphStore()
    service = ActionHistoryService({"data_dir": str(tmp_path / "data")})
    sync = InstanceSyncService()
    return graph, service, sync


def node(graph, name, node_id="node:a"):
    return graph.upsert_node(
        node_id=node_id,
        node_kind="entity",
        node_type="synthetic_example",
        canonical_name=name,
        attributes={"source": "synthetic-test"},
    )


def snapshot(sync):
    return {"sections": {"graph": sync._graph_snapshot()}}


def record(service, before, sync):
    action = service.record_snapshot_action(
        kind="tool",
        name="graph.update",
        before_snapshot=before,
        after_snapshot=snapshot(sync),
        sections=["graph"],
        summary="Synthetic test only",
    )
    assert action is not None
    return action


def content(snapshot):
    result = copy.deepcopy(snapshot["sections"]["graph"])
    for kind, id_key in (("nodes", "node_id"), ("claims", "claim_id")):
        for item in result[kind]:
            item.pop("updated_at")
        result[kind].sort(key=lambda item: item[id_key])
    return result


def relation(graph, *, claim_id="claim:relation", roles=None):
    return graph.upsert_claim(
        claim_id=claim_id,
        predicate="synthetic_relation",
        confidence=0,
        metadata={"source_date": "2026-10-01", "origin": "synthetic"},
        source_kind="synthetic",
        source_ref="test-source",
        roles=roles
        or [
            {
                "role_name": "subject",
                "node_id": "node:a",
                "ordinal": 0,
                "metadata": {"why": "synthetic"},
            },
            {"role_name": "object", "node_id": "node:b", "ordinal": 1},
            {"role_name": "detail", "value": {"example": "old"}, "ordinal": 2},
        ],
    )


def test_delete_node_undo_and_redo_preserve_full_captured_topology(history):
    graph, service, sync = history
    node(graph, "Before")
    node(graph, "Other", "node:b")
    relation(graph)
    before = snapshot(sync)
    graph.delete_node("node:a")
    after = snapshot(sync)
    assert len(after["sections"]["graph"]["claims"][0]["roles"]) == 2
    action = record(service, before, sync)
    # Deliberately reverse history order: restore must not rely on node-first ordering.
    action["items"].reverse()
    service._persist_action(action)
    result = service.revert_actions(action_ids=[action["id"]])
    assert result["status"] == "reverted"
    assert content(snapshot(sync)) == content(before)
    assert result["graph_integrity"] == {
        "integrity_check": "ok",
        "unresolved_endpoint_count": 0,
        "node_count": 2,
        "claim_count": 1,
        "role_count": 3,
        "roleless_claim_count": 0,
        "projectable_claim_count": 1,
    }
    undo = service.get_action_detail(result["action"]["id"])
    assert any(len(item["after"].get("roles", [])) == 3 for item in undo["items"])
    redo = service.revert_actions(action_ids=[undo["id"]])
    assert redo["status"] == "reverted"
    assert content(snapshot(sync)) == content(after)
    assert service.get_action_detail(action["id"])["reverted_at"] is None


def test_creation_undo_and_revert_of_revert_recreate_all_records(history):
    graph, service, sync = history
    before = snapshot(sync)
    node(graph, "A")
    node(graph, "B", "node:b")
    relation(graph)
    after = snapshot(sync)
    action = record(service, before, sync)
    undo = service.revert_actions(action_ids=[action["id"]])
    assert graph.summary() == {"node_count": 0, "claim_count": 0, "role_count": 0}
    redo = service.revert_actions(action_ids=[undo["action"]["id"]])
    assert redo["status"] == "reverted"
    assert content(snapshot(sync)) == content(after)


@pytest.mark.parametrize("value", [False, 0, "", [], {}, {"nested": [False, 0, ""]}])
def test_deleted_literal_claim_restores_value_roles_and_provenance(history, value):
    graph, service, sync = history
    node(graph, "A")
    relation(
        graph,
        roles=[
            {"role_name": "subject", "node_id": "node:a", "ordinal": 0},
            {
                "role_name": "value",
                "value": value,
                "ordinal": 1,
                "metadata": {"source": "fixture"},
            },
            {"role_name": "both", "node_id": "node:a", "value": value, "ordinal": 2},
        ],
    )
    before = snapshot(sync)
    old_claim = graph.get_claim("claim:relation")
    graph.delete_claim("claim:relation")
    action = record(service, before, sync)
    result = service.revert_actions(action_ids=[action["id"]])
    assert result["status"] == "reverted"
    restored = graph.get_claim("claim:relation")
    assert restored["roles"] == old_claim["roles"]
    assert restored["confidence"] == 0
    assert restored["source_ref"] == "test-source"
    assert restored["metadata"] == old_claim["metadata"]
    assert restored["created_at"] == old_claim["created_at"]
    assert restored["updated_at"] > old_claim["updated_at"]
    assert result["graph_integrity"]["role_count"] == 3
    assert content(snapshot(sync)) == content(before)


def test_explicit_json_null_literal_survives_snapshot_undo_and_sync(history):
    graph, service, sync = history
    node(graph, "A")
    relation(graph, roles=[{"role_name": "value", "value": 0, "ordinal": 0}])
    with graph._connect() as conn:
        conn.execute(
            "UPDATE graph_claim_roles SET value_json = 'null' WHERE claim_id = 'claim:relation'"
        )
    before = snapshot(sync)
    role = before["sections"]["graph"]["claims"][0]["roles"][0]
    assert role["value"] is None and role["value_present"] is True
    graph.delete_claim("claim:relation")
    action = record(service, before, sync)
    assert service.revert_actions(action_ids=[action["id"]])["status"] == "reverted"
    assert graph.get_claim("claim:relation")["roles"] == [role]
    graph.delete_claim("claim:relation")
    assert sync._merge_graph(before["sections"]["graph"])["applied"] == 2
    assert graph.get_claim("claim:relation")["roles"] == [role]


def test_explicit_empty_captured_roles_survive_revert_of_revert(history):
    graph, service, sync = history
    node(graph, "A")
    relation(graph, roles=[{"role_name": "subject", "node_id": "node:a", "ordinal": 0}])
    before = snapshot(sync)
    graph.delete_node("node:a")
    after = snapshot(sync)
    assert after["sections"]["graph"]["claims"][0]["roles"] == []
    action = record(service, before, sync)
    undo = service.revert_actions(action_ids=[action["id"]])
    redo = service.revert_actions(action_ids=[undo["action"]["id"]])
    assert content(snapshot(sync)) == content(after)
    assert redo["graph_integrity"]["roleless_claim_count"] == 1
    assert redo["graph_integrity"]["projectable_claim_count"] == 0


def test_multi_action_undo_uses_earliest_before_and_keeps_intermediate_roles(history):
    graph, service, sync = history
    node(graph, "Original")
    node(graph, "Other", "node:b")
    relation(graph)
    initial = snapshot(sync)
    graph.delete_node("node:a")
    first = record(service, initial, sync)
    middle = snapshot(sync)
    node(graph, "Recreated")
    relation(graph)
    second = record(service, middle, sync)
    result = service.revert_actions(action_ids=[first["id"], second["id"]])
    assert result["status"] == "reverted"
    assert content(snapshot(sync)) == content(initial)
    assert len(graph.get_claim("claim:relation")["roles"]) == 3


def test_newer_logged_action_conflict_is_preserved_and_force_is_explicit(history):
    graph, service, sync = history
    node(graph, "Original")
    before = snapshot(sync)
    node(graph, "First")
    first = record(service, before, sync)
    middle = snapshot(sync)
    node(graph, "Newer")
    record(service, middle, sync)
    with pytest.raises(ValueError, match="newer actions"):
        service.revert_actions(action_ids=[first["id"]])
    assert graph.get_node("node:a")["canonical_name"] == "Newer"
    assert service.get_action_detail(first["id"])["reverted_at"] is None
    assert (
        service.revert_actions(action_ids=[first["id"]], force=True)["status"]
        == "reverted"
    )
    assert graph.get_node("node:a")["canonical_name"] == "Original"


def test_unrecorded_current_change_fails_closed_unless_forced(history):
    graph, service, sync = history
    node(graph, "Original")
    before = snapshot(sync)
    node(graph, "Recorded")
    action = record(service, before, sync)
    node(graph, "Unrecorded")
    with pytest.raises(ValueError, match="current state differs"):
        service.revert_actions(action_ids=[action["id"]])
    assert graph.get_node("node:a")["canonical_name"] == "Unrecorded"
    assert service.get_action_detail(action["id"])["reverted_at"] is None
    assert (
        service.revert_actions(action_ids=[action["id"]], force=True)["status"]
        == "reverted"
    )


def test_unselected_attached_claim_cannot_be_silently_cascaded(history):
    graph, service, sync = history
    before = snapshot(sync)
    node(graph, "Created")
    action = record(service, before, sync)
    relation(graph, roles=[{"role_name": "subject", "node_id": "node:a", "ordinal": 0}])
    current = snapshot(sync)
    with pytest.raises(ValueError, match="unselected claim"):
        service.revert_actions(action_ids=[action["id"]], force=True)
    assert snapshot(sync) == current
    assert service.get_action_detail(action["id"])["reverted_at"] is None


@pytest.mark.parametrize(
    "defect", ["missing_roles", "duplicate_role", "missing_endpoint", "identity"]
)
def test_invalid_snapshot_rolls_back_every_selected_graph_record(history, defect):
    graph, service, sync = history
    node(graph, "Before")
    node(graph, "Other", "node:b")
    relation(graph)
    before = snapshot(sync)
    node(graph, "After")
    relation(graph, roles=[{"role_name": "subject", "node_id": "node:b", "ordinal": 0}])
    action = record(service, before, sync)
    claim_item = next(
        item for item in action["items"] if item["resource_type"] == "graph_claim"
    )
    if defect == "missing_roles":
        claim_item["before"].pop("roles")
    elif defect == "duplicate_role":
        claim_item["before"]["roles"].append(
            copy.deepcopy(claim_item["before"]["roles"][0])
        )
    elif defect == "missing_endpoint":
        claim_item["before"]["roles"][0]["node_id"] = "node:missing"
    else:
        claim_item["before"]["claim_id"] = "claim:wrong"
    service._persist_action(action)
    current = snapshot(sync)
    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        service.revert_actions(action_ids=[action["id"]])
    assert snapshot(sync) == current
    assert service.get_action_detail(action["id"])["reverted_at"] is None
    assert not any(row["kind"] == "revert" for row in service.list_actions())


def test_postcondition_failure_rolls_back_and_does_not_mark_history(history):
    graph, service, sync = history
    node(graph, "Before")
    before = snapshot(sync)
    node(graph, "After")
    action = record(service, before, sync)
    with graph._connect() as conn:
        conn.execute(
            "CREATE TRIGGER corrupt_restore AFTER UPDATE ON graph_nodes BEGIN UPDATE graph_nodes SET canonical_name='WRONG' WHERE node_id=NEW.node_id; END"
        )
    current = snapshot(sync)
    with pytest.raises(ValueError, match="postcondition"):
        service.revert_actions(action_ids=[action["id"]])
    assert snapshot(sync) == current
    assert service.get_action_detail(action["id"])["reverted_at"] is None


def test_nullable_sync_node_fields_restore_exactly(history):
    graph, service, sync = history
    node(graph, "Placeholder")
    with graph._connect() as conn:
        conn.execute("UPDATE graph_nodes SET canonical_name=NULL, summary_text=NULL")
    before = snapshot(sync)
    node(graph, "After")
    action = record(service, before, sync)
    assert service.revert_actions(action_ids=[action["id"]])["status"] == "reverted"
    assert content(snapshot(sync)) == content(before)


def test_normal_sync_keeps_newer_wins_after_undo_and_preserves_zero(history):
    graph, service, sync = history
    node(graph, "Before")
    node(graph, "Other", "node:b")
    relation(graph)
    before = snapshot(sync)
    node(graph, "After")
    relation(graph, roles=[{"role_name": "value", "value": 0, "ordinal": 0}])
    stale_after = snapshot(sync)["sections"]["graph"]
    action = record(service, before, sync)
    assert service.revert_actions(action_ids=[action["id"]])["status"] == "reverted"
    restored = snapshot(sync)
    result = sync._merge_graph(stale_after)
    assert result == {"applied": 1, "skipped": 2}  # unchanged other node can merge
    assert content(snapshot(sync)) == content(restored)
    assert graph.get_claim("claim:relation")["confidence"] == 0


def test_restore_expected_current_guard_and_noop(history):
    graph, _, sync = history
    old = node(graph, "Before")
    node(graph, "After")
    current = snapshot(sync)
    with pytest.raises(ValueError, match="current state differs"):
        graph.restore_records(
            nodes={"node:a": old}, claims={}, expected_nodes={"node:a": old}
        )
    assert snapshot(sync) == current
    desired = current["sections"]["graph"]["nodes"][0]
    result = graph.restore_records(
        nodes={"node:a": desired}, claims={}, expected_nodes={"node:a": desired}
    )
    assert result["changes"] == []
    assert snapshot(sync) == current


def test_forced_restore_rejects_change_after_its_current_snapshot(history, monkeypatch):
    graph, service, sync = history
    node(graph, "Before")
    before = snapshot(sync)
    node(graph, "After")
    action = record(service, before, sync)
    original_restore = GraphStore.restore_records

    def raced_restore(self, **kwargs):
        node(graph, "Concurrent change")
        return original_restore(self, **kwargs)

    monkeypatch.setattr(GraphStore, "restore_records", raced_restore)
    with pytest.raises(ValueError, match="current state differs"):
        service.revert_actions(action_ids=[action["id"]], force=True)
    assert graph.get_node("node:a")["canonical_name"] == "Concurrent change"
    assert service.get_action_detail(action["id"])["reverted_at"] is None


def test_legacy_role_snapshots_infer_present_values(history):
    graph, service, sync = history
    node(graph, "A")
    relation(graph, roles=[{"role_name": "value", "value": False, "ordinal": 0}])
    before = snapshot(sync)
    graph.delete_claim("claim:relation")
    action = record(service, before, sync)
    for item in action["items"]:
        for state in (item["before"], item["after"]):
            if state:
                for role in state.get("roles", []):
                    role.pop("value_present")
    service._persist_action(action)
    assert service.revert_actions(action_ids=[action["id"]])["status"] == "reverted"
    assert graph.get_claim("claim:relation")["roles"][0]["value"] is False


def test_legacy_ambiguous_null_only_role_fails_without_changes(history):
    graph, service, sync = history
    relation(graph, roles=[{"role_name": "value", "value": 0, "ordinal": 0}])
    with graph._connect() as conn:
        conn.execute("UPDATE graph_claim_roles SET value_json='null'")
    before = snapshot(sync)
    graph.delete_claim("claim:relation")
    action = record(service, before, sync)
    action["items"][0]["before"]["roles"][0].pop("value_present")
    service._persist_action(action)
    current = snapshot(sync)
    with pytest.raises(ValueError, match="no endpoint or literal"):
        service.revert_actions(action_ids=[action["id"]])
    assert snapshot(sync) == current
    assert service.get_action_detail(action["id"])["reverted_at"] is None


def test_sync_literal_serializer_keeps_existing_non_json_native_support(history):
    from pathlib import Path

    graph, _, sync = history
    claim = relation(graph, roles=[{"role_name": "value", "value": 0, "ordinal": 0}])
    graph.delete_claim(claim["claim_id"])
    claim["roles"][0]["value"] = Path("synthetic-path")
    assert sync._merge_graph({"nodes": [], "claims": [claim]})["applied"] == 1
    assert graph.get_claim(claim["claim_id"])["roles"][0]["value"] == "synthetic-path"


@pytest.mark.parametrize(
    "before_value,after_value", [(False, 0), (True, 1), (0, False)]
)
def test_bool_and_numeric_literals_are_distinct_restored_changes(
    history, before_value, after_value
):
    graph, service, sync = history
    relation(graph, roles=[{"role_name": "value", "value": before_value, "ordinal": 0}])
    before = snapshot(sync)
    relation(graph, roles=[{"role_name": "value", "value": after_value, "ordinal": 0}])
    action = record(service, before, sync)
    undo = service.revert_actions(action_ids=[action["id"]])
    assert undo["status"] == "reverted"
    value = graph.get_claim("claim:relation")["roles"][0]["value"]
    assert value == before_value and type(value) is type(before_value)
    assert (
        service.revert_actions(action_ids=[undo["action"]["id"]])["status"]
        == "reverted"
    )
    value = graph.get_claim("claim:relation")["roles"][0]["value"]
    assert value == after_value and type(value) is type(after_value)


def test_typed_literal_postcondition_failure_rolls_back(history):
    graph, service, sync = history
    node(graph, "Before")
    relation(graph, roles=[{"role_name": "value", "value": False, "ordinal": 0}])
    before = snapshot(sync)
    node(graph, "After")
    relation(graph, roles=[{"role_name": "value", "value": "new", "ordinal": 0}])
    action = record(service, before, sync)
    with graph._connect() as conn:
        conn.execute(
            "CREATE TRIGGER corrupt_literal AFTER INSERT ON graph_claim_roles BEGIN UPDATE graph_claim_roles SET value_json='0' WHERE claim_id=NEW.claim_id; END"
        )
    current = snapshot(sync)
    with pytest.raises(ValueError, match="postcondition"):
        service.revert_actions(action_ids=[action["id"]])
    assert snapshot(sync) == current
    assert service.get_action_detail(action["id"])["reverted_at"] is None


def test_typed_literal_current_guard_rejects_unrecorded_numeric_change(history):
    graph, service, sync = history
    relation(graph, roles=[{"role_name": "value", "value": "old", "ordinal": 0}])
    before = snapshot(sync)
    relation(graph, roles=[{"role_name": "value", "value": False, "ordinal": 0}])
    action = record(service, before, sync)
    with graph._connect() as conn:
        conn.execute("UPDATE graph_claim_roles SET value_json='0'")
    with pytest.raises(ValueError, match="current state differs"):
        service.revert_actions(action_ids=[action["id"]])
    assert type(graph.get_claim("claim:relation")["roles"][0]["value"]) is int
