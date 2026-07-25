import json
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _make_client(tmp_path, monkeypatch=None, with_action_history=False):
    backend_dir = Path(__file__).resolve().parents[2]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    from app import routes
    from app.base_services import MemoryManager
    from app.services.action_history_service import ActionHistoryService
    from app.utils import user_settings

    db_path = tmp_path / "memory.sqlite3"
    if monkeypatch is not None:
        monkeypatch.setenv("FLOAT_MEMORY_FILE", str(db_path))
        monkeypatch.setenv("FLOAT_DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setattr(
            user_settings,
            "USER_SETTINGS_PATH",
            tmp_path / "user_settings.json",
            raising=False,
        )
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    app.state.memory_manager = MemoryManager({"memory_store_path": str(db_path)})
    if with_action_history:
        app.state.action_history_service = ActionHistoryService(
            {"data_dir": str(tmp_path / "data")}
        )
    return TestClient(app)


def test_graph_routes_expose_schema_and_project_social_fixture(tmp_path):
    client = _make_client(tmp_path)
    fixture = json.loads((FIXTURES / "basic_social_graph.json").read_text())

    schema_resp = client.get("/api/graph/schema")
    assert schema_resp.status_code == 200
    schema = schema_resp.json()
    assert schema["schema_version"] == 1
    assert "entity" in schema["node_kinds"]
    assert "asserted" in schema["epistemic_statuses"]

    upsert_resp = client.post(
        "/api/graph",
        json={
            "nodes": fixture["nodes"],
            "claims": fixture["claims"],
            "source_kind": "fixture",
            "source_ref": "basic_social_graph",
        },
    )
    assert upsert_resp.status_code == 200
    upsert_payload = upsert_resp.json()
    assert upsert_payload["status"] == "ok"
    assert upsert_payload["graph_update"]["node_count"] == 7
    assert upsert_payload["graph_update"]["claim_count"] == 7

    graph_resp = client.get("/api/graph")
    assert graph_resp.status_code == 200
    graph = graph_resp.json()["graph"]
    assert graph["metadata"]["available"] is True
    assert graph["metadata"]["node_count"] == 7
    assert graph["metadata"]["link_count"] == 7
    self_node = next(
        node for node in graph["nodes"] if node["node_id"] == "person:self"
    )
    assert self_node["attributes"]["city"] == "Vancouver"
    assert {
        (link["source"], link["target"], link["predicate"]) for link in graph["links"]
    } == {
        ("knowledge:person:self", "knowledge:person:friend-maya", "friend_of"),
        ("knowledge:person:self", "knowledge:person:friend-jules", "friend_of"),
        ("knowledge:person:self", "knowledge:person:coworker-ren", "works_with"),
        ("knowledge:person:self", "knowledge:person:family-lena", "family_of"),
        ("knowledge:person:friend-jules", "knowledge:org:computer-club", "member_of"),
        ("knowledge:person:self", "knowledge:org:job", "works_at"),
        ("knowledge:person:coworker-ren", "knowledge:org:job", "works_at"),
    }


def test_graph_route_records_revertible_revision_history(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch, with_action_history=True)
    fixture = json.loads((FIXTURES / "basic_social_graph.json").read_text())

    upsert_resp = client.post(
        "/api/graph",
        json={
            "nodes": fixture["nodes"],
            "claims": fixture["claims"],
            "source_kind": "fixture",
            "source_ref": "basic_social_graph",
        },
    )

    assert upsert_resp.status_code == 200
    payload = upsert_resp.json()
    revision = payload.get("revision")
    assert revision is not None
    assert revision["item_count"] == 14
    action_id = revision["action_id"]

    actions_resp = client.get("/api/actions")
    assert actions_resp.status_code == 200
    assert any(
        action.get("id") == action_id for action in actions_resp.json()["actions"]
    )

    app = client.app
    result = app.state.action_history_service.revert_actions(action_ids=[action_id])
    assert result["status"] == "reverted"

    graph = client.get("/api/graph").json()["graph"]
    assert graph["metadata"]["node_count"] == 0
    assert graph["metadata"]["claim_count"] == 0


def test_work_history_projects_deployment_events_without_diff_or_revert(
    tmp_path, monkeypatch
):
    client = _make_client(tmp_path, monkeypatch, with_action_history=True)
    from app.utils import deployment_event_store

    event = deployment_event_store.record_event(
        event_type="software.install",
        data_root=tmp_path / "data",
        software_after={
            "release_version": "0.1.0a1",
            "build_code": "b-test",
            "snapshot_digest": "a" * 64,
        },
        counts={"changed_file_count": 3},
    )

    actions = client.get("/api/actions").json()["actions"]
    projected = next(
        action for action in actions if action["id"].endswith(event["event_id"])
    )
    assert projected["summary"] == "Installed Float 0.1.0a1 // b-test"
    assert projected["metadata_only"] is True
    assert projected["revertible"] is False

    detail = client.get(f"/api/actions/{projected['id']}")
    assert detail.status_code == 200
    assert detail.json()["action"]["items"] == []
