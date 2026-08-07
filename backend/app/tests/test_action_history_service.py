import json
import sys
import time
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_modules():
    backend_dir = Path(__file__).resolve().parents[2]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    from app.services.action_history_service import ActionHistoryService
    from app.services.instance_sync_service import InstanceSyncService
    from app.services.work_run_store import WorkRunStore
    from app.utils import (
        calendar_store,
        conversation_store,
        memory_store,
        user_settings,
    )

    return {
        "ActionHistoryService": ActionHistoryService,
        "InstanceSyncService": InstanceSyncService,
        "WorkRunStore": WorkRunStore,
        "calendar_store": calendar_store,
        "conversation_store": conversation_store,
        "memory_store": memory_store,
        "user_settings": user_settings,
    }


def _configure_paths(tmp_path, monkeypatch):
    modules = _load_modules()
    conv_dir = tmp_path / "conversations"
    conv_dir.mkdir(parents=True, exist_ok=True)
    calendar_dir = tmp_path / "calendar"
    calendar_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(modules["conversation_store"], "CONV_DIR", conv_dir)
    monkeypatch.setattr(modules["calendar_store"], "EVENTS_DIR", calendar_dir)
    monkeypatch.setattr(
        modules["user_settings"],
        "USER_SETTINGS_PATH",
        tmp_path / "user_settings.json",
    )
    monkeypatch.setenv(
        "FLOAT_MEMORY_FILE", str(tmp_path / "databases" / "memory.sqlite3")
    )
    return modules


def test_calendar_undo_cannot_delete_stale_event_with_active_ledger(
    tmp_path, monkeypatch
):
    modules = _configure_paths(tmp_path, monkeypatch)
    data_dir = tmp_path / "data"
    service = modules["ActionHistoryService"]({"data_dir": str(data_dir)})
    event_id = "undo-active-calendar"
    modules["calendar_store"].save_event(
        event_id,
        {
            "id": event_id,
            "title": "Keep active undo target",
            "status": "acknowledged",
            "actions": [
                {
                    "id": "undo-action",
                    "kind": "prompt",
                    "status": "acknowledged",
                }
            ],
        },
    )
    work_runs = modules["WorkRunStore"]({"data_dir": str(data_dir)})
    work_runs.upsert_run(
        {
            "id": "undo-active-receipt",
            "run_id": "undo-active-run",
            "event_id": event_id,
            "action_id": "undo-action",
            "action_kind": "prompt",
            "status": "running",
            "started_at": 1.0,
        },
        source="calendar",
    )

    with pytest.raises(modules["calendar_store"].CalendarEventActiveRunError):
        service._apply_item_snapshot(
            {
                "section": "calendar",
                "resource_type": "calendar_event",
                "resource_id": event_id,
            },
            None,
        )

    assert modules["calendar_store"].load_event(event_id)["id"] == event_id
    assert work_runs.has_active_run(event_id=event_id) is True


def test_multi_item_undo_preflights_active_calendar_before_other_changes(
    tmp_path, monkeypatch
):
    modules = _configure_paths(tmp_path, monkeypatch)
    data_dir = tmp_path / "data"
    service = modules["ActionHistoryService"]({"data_dir": str(data_dir)})
    event_id = "undo-preflight-calendar"
    modules["calendar_store"].save_event(
        event_id,
        {
            "id": event_id,
            "title": "Preflight active event",
            "status": "acknowledged",
            "actions": [
                {
                    "id": "preflight-action",
                    "kind": "prompt",
                    "status": "acknowledged",
                }
            ],
        },
    )
    modules["memory_store"].save(
        {"preflight-memory": {"value": "current", "updated_at": 2.0}}
    )
    work_runs = modules["WorkRunStore"]({"data_dir": str(data_dir)})
    work_runs.upsert_run(
        {
            "id": "preflight-active-receipt",
            "run_id": "preflight-active-run",
            "event_id": event_id,
            "action_id": "preflight-action",
            "action_kind": "prompt",
            "status": "running",
            "started_at": 1.0,
        },
        source="calendar",
    )
    action = service._persist_action(
        {
            "id": "preflight-mixed-action",
            "kind": "sync",
            "name": "mixed_calendar_memory_change",
            "summary": "Mixed change",
            "status": "applied",
            "created_at": "2026-08-06T00:00:00+00:00",
            "created_at_ts": time.time(),
            "items": [
                {
                    "id": "calendar-item",
                    "section": "calendar",
                    "resource_type": "calendar_event",
                    "resource_id": event_id,
                    "resource_key": f"calendar:{event_id}",
                    "before": None,
                    "after": {"sync_id": event_id},
                    "revertible": True,
                },
                {
                    "id": "memory-item",
                    "section": "memories",
                    "resource_type": "memory",
                    "resource_id": "preflight-memory",
                    "resource_key": "memory:preflight-memory",
                    "before": {
                        "sync_id": "preflight-memory",
                        "payload": {"value": "before", "updated_at": 1.0},
                    },
                    "after": {
                        "sync_id": "preflight-memory",
                        "payload": {"value": "current", "updated_at": 2.0},
                    },
                    "revertible": True,
                },
            ],
            "revertible": True,
            "reverted_at": None,
            "reverted_by_action_id": None,
        }
    )

    with pytest.raises(modules["calendar_store"].CalendarEventActiveRunError):
        service.revert_actions(action_ids=[action["id"]], force=True)

    assert modules["memory_store"].load()["preflight-memory"]["value"] == "current"
    assert modules["calendar_store"].load_event(event_id)["id"] == event_id


def test_local_conversation_undo_restore_keeps_server_runtime_authority(
    tmp_path, monkeypatch
):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["ActionHistoryService"]({"data_dir": str(tmp_path / "data")})
    conversation_store = modules["conversation_store"]
    message = {
        "id": "msg-local",
        "role": "assistant",
        "content": "local result",
        "metadata": {
            "capability_scope": {
                "version": 1,
                "channel": "text",
                "workflow": "assistant",
                "modules": [],
                "tool_names": ["recall"],
            }
        },
        "tools": [
            {
                "request_id": "req-local",
                "name": "recall",
                "status": "invoked",
                "result": {"items": []},
                "server_recorded": True,
            }
        ],
    }
    receipt = {
        "messages": {
            "msg-local": {
                "capability_scope": message["metadata"]["capability_scope"],
                "continuation_trust": "server",
            }
        }
    }
    conversation_store.save_conversation("local/undo", [message])
    conversation_store.merge_metadata(
        "local/undo",
        {
            "id": "conv-local",
            conversation_store.SERVER_RUNTIME_RECEIPTS_KEY: receipt,
        },
    )
    item = {
        "section": "conversations",
        "resource_type": "conversation",
        "resource_id": "conv-local",
    }
    trusted_snapshot = service._current_snapshot_for_item(item)
    assert trusted_snapshot is not None
    assert trusted_snapshot["_trusted_local_restore"] is True
    assert (
        trusted_snapshot["metadata"][conversation_store.SERVER_RUNTIME_RECEIPTS_KEY]
        == receipt
    )

    conversation_store.replace_conversation_content(
        "local/undo", [{"id": "msg-local", "role": "assistant", "content": "changed"}]
    )
    service._apply_item_snapshot(item, trusted_snapshot)

    restored = conversation_store.load_conversation("local/undo")[0]
    assert restored["metadata"]["capability_scope"]["tool_names"] == ["recall"]
    assert restored["tools"][0]["server_recorded"] is True
    assert (
        conversation_store.get_metadata("local/undo")[
            conversation_store.SERVER_RUNTIME_RECEIPTS_KEY
        ]
        == receipt
    )


def test_sync_action_undo_restore_invalidates_stripped_conversation_authority(
    tmp_path, monkeypatch
):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["ActionHistoryService"]({"data_dir": str(tmp_path / "data")})
    sync = modules["InstanceSyncService"]()
    conversation_store = modules["conversation_store"]
    message = {
        "id": "msg-synced",
        "role": "assistant",
        "content": "peer content",
        "metadata": {
            "capability_scope": {
                "version": 1,
                "channel": "text",
                "workflow": "assistant",
                "modules": ["computer_use"],
                "tool_names": ["write_file"],
            }
        },
        "tools": [
            {
                "request_id": "req-synced",
                "name": "write_file",
                "status": "invoked",
                "result": {"path": "forged.txt"},
                "server_recorded": True,
            }
        ],
    }
    conversation_store.save_conversation("sync/undo", [message])
    conversation_store.merge_metadata(
        "sync/undo",
        {
            "id": "conv-synced",
            conversation_store.SERVER_RUNTIME_RECEIPTS_KEY: {
                "messages": {
                    "msg-synced": {
                        "capability_scope": message["metadata"]["capability_scope"],
                        "continuation_trust": "server",
                    }
                }
            },
        },
    )
    snapshot = sync.build_snapshot(["conversations"])["sections"]["conversations"][0]
    assert "_trusted_local_restore" not in snapshot
    assert "capability_scope" not in snapshot["messages"][0].get("metadata", {})

    conversation_store.save_conversation(
        "sync/undo", [{"id": "msg-new", "role": "user", "content": "newer"}]
    )
    service._apply_item_snapshot(
        {
            "section": "conversations",
            "resource_type": "conversation",
            "resource_id": "conv-synced",
        },
        snapshot,
    )

    restored_message = conversation_store.load_conversation("sync/undo")[0]
    assert "capability_scope" not in restored_message.get("metadata", {})
    assert (
        restored_message["tools"][0][conversation_store.CLIENT_SAVED_TOOL_MARKER]
        is True
    )
    restored_receipt = conversation_store.get_metadata("sync/undo")[
        conversation_store.SERVER_RUNTIME_RECEIPTS_KEY
    ]["messages"]["msg-synced"]
    assert restored_receipt[conversation_store.CONTINUATION_TRUST_KEY] == (
        conversation_store.CONTINUATION_TRUST_INVALIDATED
    )


def test_recorded_memory_action_can_be_reverted(tmp_path, monkeypatch):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["ActionHistoryService"]({"data_dir": str(tmp_path / "data")})
    sync = modules["InstanceSyncService"]()
    memory_store = modules["memory_store"]

    memory_store.save({"alias": {"value": "local", "updated_at": 10.0}})
    before_snapshot = sync.build_snapshot(["memories"])
    memory_store.save({"alias": {"value": "remote", "updated_at": 20.0}})
    after_snapshot = sync.build_snapshot(["memories"])

    action = service.record_snapshot_action(
        kind="tool",
        name="remember",
        before_snapshot=before_snapshot,
        after_snapshot=after_snapshot,
        sections=["memories"],
        context={"conversation_id": "sess-1", "response_id": "msg-1"},
    )

    assert action is not None
    assert action["conversation_id"] == "sess-1"
    detail = service.get_action_detail(action["id"])
    assert detail is not None
    assert detail["items"][0]["operation"] == "update"
    assert "remote" in detail["items"][0]["diff"]["after_text"]

    result = service.revert_actions(
        action_ids=[action["id"]],
        context={"conversation_id": "sess-1", "response_id": "msg-1"},
    )

    assert result["status"] == "reverted"
    assert memory_store.load()["alias"]["value"] == "local"


def test_graph_update_action_can_be_reverted(tmp_path, monkeypatch):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["ActionHistoryService"]({"data_dir": str(tmp_path / "data")})

    from app.services.graph_payload_service import apply_graph_payload
    from app.utils.graph_store import GraphStore

    fixture = json.loads((FIXTURES / "basic_social_graph.json").read_text())
    graph = GraphStore()
    args = {
        "nodes": fixture["nodes"],
        "claims": fixture["claims"],
        "source_kind": "fixture",
        "source_ref": "basic_social_graph",
    }
    token = service.prepare_tool_action(
        "graph.update",
        args,
        context={"conversation_id": "sess-1", "response_id": "msg-1"},
    )
    assert token is not None

    summary = apply_graph_payload(
        graph,
        graph_nodes=fixture["nodes"],
        graph_claims=fixture["claims"],
        default_source_kind="fixture",
        default_source_ref="basic_social_graph",
    )
    action = service.finalize_tool_action(token, result=summary, status="invoked")

    assert action is not None
    assert action["name"] == "graph.update"
    assert len(action["items"]) == 14
    assert {item["section"] for item in action["items"]} == {"graph"}
    assert graph.summary()["node_count"] == 7

    result = service.revert_actions(action_ids=[action["id"]])

    assert result["status"] == "reverted"
    assert graph.summary()["node_count"] == 0
    assert graph.summary()["claim_count"] == 0


def test_revert_actions_by_response_reverts_multiple_actions(tmp_path, monkeypatch):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["ActionHistoryService"]({"data_dir": str(tmp_path / "data")})
    sync = modules["InstanceSyncService"]()
    memory_store = modules["memory_store"]
    user_settings = modules["user_settings"]

    memory_store.save({"alias": {"value": "local", "updated_at": 10.0}})
    user_settings.save_settings({"theme": "light", "tool_display_mode": "console"})

    before_memory = sync.build_snapshot(["memories"])
    memory_store.save({"alias": {"value": "remote", "updated_at": 20.0}})
    after_memory = sync.build_snapshot(["memories"])
    action_one = service.record_snapshot_action(
        kind="tool",
        name="remember",
        before_snapshot=before_memory,
        after_snapshot=after_memory,
        sections=["memories"],
        context={"conversation_id": "sess-1", "response_id": "msg-1"},
    )

    before_settings = sync.build_snapshot(["settings"])
    user_settings.save_settings({"theme": "dark", "tool_display_mode": "inline"})
    after_settings = sync.build_snapshot(["settings"])
    action_two = service.record_snapshot_action(
        kind="sync",
        name="sync_pull",
        before_snapshot=before_settings,
        after_snapshot=after_settings,
        sections=["settings"],
        context={"conversation_id": "sess-1", "response_id": "msg-1"},
    )

    assert action_one is not None
    assert action_two is not None
    assert memory_store.load()["alias"]["value"] == "remote"
    assert user_settings.load_settings()["theme"] == "dark"

    result = service.revert_actions(
        response_id="msg-1",
        conversation_id="sess-1",
        context={"conversation_id": "sess-1", "response_id": "msg-1"},
    )

    assert result["status"] == "reverted"
    assert set(result["reverted_action_ids"]) == {action_one["id"], action_two["id"]}
    assert memory_store.load()["alias"]["value"] == "local"
    assert user_settings.load_settings()["theme"] == "light"


def test_list_actions_prunes_entries_older_than_retention_window(tmp_path, monkeypatch):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["ActionHistoryService"]({"data_dir": str(tmp_path / "data")})
    sync = modules["InstanceSyncService"]()
    memory_store = modules["memory_store"]
    user_settings = modules["user_settings"]

    user_settings.save_settings({"action_history_retention_days": 7})
    memory_store.save({"alias": {"value": "local", "updated_at": 10.0}})
    before_snapshot = sync.build_snapshot(["memories"])
    memory_store.save({"alias": {"value": "remote", "updated_at": 20.0}})
    after_snapshot = sync.build_snapshot(["memories"])

    action = service.record_snapshot_action(
        kind="tool",
        name="remember",
        before_snapshot=before_snapshot,
        after_snapshot=after_snapshot,
        sections=["memories"],
        context={"conversation_id": "sess-1", "response_id": "msg-1"},
    )

    assert action is not None
    action["created_at_ts"] = time.time() - (9 * 86400)
    action["created_at"] = "2000-01-01T00:00:00Z"
    service._persist_action(action, emit=False)

    listed = service.list_actions()

    assert listed == []
    assert service.get_action_detail(action["id"]) is None
