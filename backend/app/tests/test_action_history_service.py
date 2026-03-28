import sys
import time
from pathlib import Path


def _load_modules():
    backend_dir = Path(__file__).resolve().parents[2]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    from app.services.action_history_service import ActionHistoryService
    from app.services.instance_sync_service import InstanceSyncService
    from app.utils import calendar_store, conversation_store, memory_store, user_settings

    return {
        "ActionHistoryService": ActionHistoryService,
        "InstanceSyncService": InstanceSyncService,
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
    monkeypatch.setenv("FLOAT_MEMORY_FILE", str(tmp_path / "databases" / "memory.sqlite3"))
    return modules


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
