import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))
from services.graph_metadata import GraphMetadataStore  # noqa: E402


def test_graph_metadata_store_crud(tmp_path):
    db_path = tmp_path / "meta.db"
    store = GraphMetadataStore(str(db_path))

    store.create_node("n1", "Node1", {"a": 1})
    node = store.get_node("n1")
    assert node["label"] == "Node1"
    assert node["properties"]["a"] == 1

    store.update_node("n1", properties={"b": 2})
    node = store.get_node("n1")
    assert node["properties"]["b"] == 2

    edge_id = store.create_edge("n1", "n1", "self", {"w": 0.5})
    neighbors = store.get_neighbors("n1")
    assert neighbors and neighbors[0]["id"] == edge_id

    store.delete_edge(edge_id)
    assert store.get_neighbors("n1") == []

    store.delete_node("n1")
    assert store.get_node("n1") is None
