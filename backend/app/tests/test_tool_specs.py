import sys
from pathlib import Path

from fastapi.testclient import TestClient


def test_tool_specs_endpoint_returns_schemas(tmp_path, monkeypatch):
    backend_dir = Path(__file__).resolve().parents[2]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    from app.main import app
    from app.utils import calendar_store, conversation_store

    monkeypatch.setattr(conversation_store, "CONV_DIR", tmp_path, raising=False)
    monkeypatch.setattr(
        calendar_store, "EVENTS_DIR", tmp_path / "calendar", raising=False
    )
    calendar_store.EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    expected_tools = [
        "remember",
        "recall",
        "help",
        "tool_help",
        "tool_info",
        "list_actions",
        "read_action_diff",
        "revert_actions",
        "create_event",
        "create_task",
        "list_tasks",
        "list_dir",
        "read_file",
        "computer.observe",
        "computer.act",
        "shell.exec",
        "patch.apply",
    ]
    monkeypatch.setattr(
        app.state.memory_manager,
        "list_tools",
        lambda: list(expected_tools),
        raising=False,
    )

    client = TestClient(app)
    resp = client.get("/api/tools/specs")
    assert resp.status_code == 200
    tools = resp.json().get("tools")
    assert isinstance(tools, list)
    remember = next((t for t in tools if t.get("name") == "remember"), None)
    assert remember is not None
    assert "parameters" in remember
    assert remember["parameters"].get("type") == "object"
    props = remember["parameters"].get("properties") or {}
    assert "key" in props
    assert "value" in props
    assert props["value"].get("items") == {}
    assert "lifecycle" in props
    assert "grounded_at" in props
    assert "occurs_at" in props
    assert "review_at" in props
    assert "decay_at" in props
    recall = next((t for t in tools if t.get("name") == "recall"), None)
    assert recall is not None
    recall_props = recall["parameters"].get("properties") or {}
    assert "mode" in recall_props
    assert "top_k" in recall_props
    assert "include_images" in recall_props
    assert "image_top_k" in recall_props
    help_tool = next((t for t in tools if t.get("name") == "help"), None)
    assert help_tool is not None
    help_props = help_tool["parameters"].get("properties") or {}
    assert "tool_name" in help_props
    assert "detail" in help_props
    tool_help = next((t for t in tools if t.get("name") == "tool_help"), None)
    assert tool_help is not None
    tool_help_props = tool_help["parameters"].get("properties") or {}
    assert "tool_name" in tool_help_props
    assert "detail" in tool_help_props
    tool_info = next((t for t in tools if t.get("name") == "tool_info"), None)
    assert tool_info is not None
    tool_info_props = tool_info["parameters"].get("properties") or {}
    assert "tool_name" in tool_info_props
    assert "include_schema" in tool_info_props
    list_actions = next((t for t in tools if t.get("name") == "list_actions"), None)
    assert list_actions is not None
    list_actions_props = list_actions["parameters"].get("properties") or {}
    assert "conversation_id" in list_actions_props
    assert "response_id" in list_actions_props
    assert "include_reverted" in list_actions_props
    read_action_diff = next(
        (t for t in tools if t.get("name") == "read_action_diff"), None
    )
    assert read_action_diff is not None
    assert read_action_diff["parameters"].get("required") == ["action_id"]
    revert_actions = next((t for t in tools if t.get("name") == "revert_actions"), None)
    assert revert_actions is not None
    revert_actions_props = revert_actions["parameters"].get("properties") or {}
    assert "action_ids" in revert_actions_props
    assert "response_id" in revert_actions_props
    assert "conversation_id" in revert_actions_props
    assert "force" in revert_actions_props
    create_event = next((t for t in tools if t.get("name") == "create_event"), None)
    assert create_event is not None
    create_event_props = create_event["parameters"].get("properties") or {}
    assert "start" in create_event_props
    assert "duration" in create_event_props
    create_task = next((t for t in tools if t.get("name") == "create_task"), None)
    assert create_task is not None
    create_task_props = create_task["parameters"].get("properties") or {}
    assert "title" in create_task_props
    assert "start_time" in create_task_props
    assert "start" in create_task_props
    assert "grounded_at" in create_task_props
    assert "status" in create_task_props
    list_tasks = next((t for t in tools if t.get("name") == "list_tasks"), None)
    assert list_tasks is not None
    list_tasks_props = list_tasks["parameters"].get("properties") or {}
    assert "status" in list_tasks_props
    assert "include_past" in list_tasks_props
    assert "limit" in list_tasks_props
    list_dir = next((t for t in tools if t.get("name") == "list_dir"), None)
    assert list_dir is not None
    list_dir_props = list_dir["parameters"].get("properties") or {}
    assert "path" in list_dir_props
    assert "workspace_only" in list_dir_props
    assert "recursive" in list_dir_props
    assert list_dir_props["max_entries"]["default"] == 100
    assert list_dir_props["max_entries"]["maximum"] == 200
    read_file = next((t for t in tools if t.get("name") == "read_file"), None)
    assert read_file is not None
    read_file_props = read_file["parameters"].get("properties") or {}
    assert "start_line" in read_file_props
    assert "line_count" in read_file_props
    assert "max_chars" in read_file_props
    assert read_file_props["start_line"]["default"] == 1
    assert read_file_props["line_count"]["default"] == 200
    assert read_file_props["line_count"]["maximum"] == 1000
    assert read_file_props["max_chars"]["default"] == 12000
    assert read_file_props["max_chars"]["maximum"] == 20000
    computer_observe = next(
        (t for t in tools if t.get("name") == "computer.observe"),
        None,
    )
    assert computer_observe is not None
    computer_observe_props = computer_observe["parameters"].get("properties") or {}
    assert "session_id" in computer_observe_props
    computer_act = next((t for t in tools if t.get("name") == "computer.act"), None)
    assert computer_act is not None
    computer_act_props = computer_act["parameters"].get("properties") or {}
    assert "actions" in computer_act_props
    assert computer_act["parameters"].get("required") == ["session_id", "actions"]
    shell_exec = next((t for t in tools if t.get("name") == "shell.exec"), None)
    assert shell_exec is not None
    assert shell_exec["parameters"].get("required") == ["command"]
    patch_apply = next((t for t in tools if t.get("name") == "patch.apply"), None)
    assert patch_apply is not None
    assert patch_apply["parameters"].get("required") == ["path", "content"]
    assert all(tool.get("name") != "decay_memories" for tool in tools)
