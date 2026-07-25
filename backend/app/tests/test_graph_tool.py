import json
from pathlib import Path

from app.tools import graph as graph_tools
from app.utils import generate_signature
from app.utils.graph_store import GraphStore

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_graph_update_tool_applies_five_person_social_fixture(tmp_path):
    fixture = json.loads((FIXTURES / "basic_social_graph.json").read_text())
    store = GraphStore(tmp_path / "memory.sqlite3")
    graph_tools.set_graph_store(store)

    user = "alice"
    args = {
        "nodes": fixture["nodes"],
        "claims": fixture["claims"],
        "source_kind": "fixture",
        "source_ref": "basic_social_graph",
    }
    signature = generate_signature(user, "graph.update", args)

    result = graph_tools.update_graph(user=user, signature=signature, **args)

    assert result["status"] == "ok"
    assert result["graph_update"]["node_count"] == 7
    assert result["graph_update"]["claim_count"] == 7
    assert result["graph"]["metadata"]["node_count"] == 7
    assert {link["predicate"] for link in result["graph"]["links"]} >= {
        "friend_of",
        "works_with",
        "family_of",
        "member_of",
        "works_at",
    }
