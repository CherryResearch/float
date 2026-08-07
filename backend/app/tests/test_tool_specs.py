import sys
from pathlib import Path

from fastapi.testclient import TestClient


def test_tool_specs_endpoint_returns_schemas(tmp_path, monkeypatch):
    backend_dir = Path(__file__).resolve().parents[2]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    from app.main import app
    from app.utils import calendar_store, conversation_store, user_settings

    monkeypatch.setattr(conversation_store, "CONV_DIR", tmp_path, raising=False)
    monkeypatch.setattr(
        user_settings,
        "USER_SETTINGS_PATH",
        tmp_path / "user_settings.json",
        raising=False,
    )
    monkeypatch.setattr(
        calendar_store, "EVENTS_DIR", tmp_path / "calendar", raising=False
    )
    calendar_store.EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    expected_tools = [
        "remember",
        "recall",
        "graph.update",
        "help",
        "tool_help",
        "tool_info",
        "read_capability_docs",
        "list_actions",
        "read_action_diff",
        "revert_actions",
        "subchat",
        "reflect",
        "list_reflections",
        "create_event",
        "create_task",
        "list_tasks",
        "list_dir",
        "read_file",
        "write_file",
        "compact_conversation_plan",
        "compact_conversation_preview",
        "compact_conversation_write",
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
    assert remember["policy"]["workflow"] == "both"
    assert remember["policy"]["approval"] == "low"
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
    assert "graph_nodes" in props
    assert "graph_claims" in props
    assert props["graph_nodes"]["items"]["properties"]["node_type"]["type"] == "string"
    assert props["graph_claims"]["items"]["required"] == ["predicate", "roles"]
    graph_update = next((t for t in tools if t.get("name") == "graph.update"), None)
    assert graph_update is not None
    graph_props = graph_update["parameters"].get("properties") or {}
    assert graph_props["nodes"]["items"]["required"] == ["node_type"]
    assert graph_props["claims"]["items"]["required"] == ["predicate", "roles"]
    assert graph_update["policy"]["approval"] == "high"
    assert "reflect_after_save" in props
    assert "reflection_prompt" in props
    assert "reflection_run_now" in props
    recall = next((t for t in tools if t.get("name") == "recall"), None)
    assert recall is not None
    recall_props = recall["parameters"].get("properties") or {}
    assert "mode" in recall_props
    assert "top_k" in recall_props
    assert "include_images" in recall_props
    assert "image_top_k" in recall_props
    help_tool = next((t for t in tools if t.get("name") == "help"), None)
    assert help_tool is not None
    assert "{}" in help_tool.get("description", "")
    help_props = help_tool["parameters"].get("properties") or {}
    assert "tool_name" in help_props
    assert "detail" in help_props
    assert "failed_tool_name" in help_props
    assert "failed_args" in help_props
    assert "failed_error" in help_props
    tool_help = next((t for t in tools if t.get("name") == "tool_help"), None)
    assert tool_help is not None
    assert "{}" in tool_help.get("description", "")
    tool_help_props = tool_help["parameters"].get("properties") or {}
    assert "tool_name" in tool_help_props
    assert "detail" in tool_help_props
    assert "failed_tool_name" in tool_help_props
    assert "failed_args" in tool_help_props
    assert "failed_error" in tool_help_props
    tool_info = next((t for t in tools if t.get("name") == "tool_info"), None)
    assert tool_info is not None
    tool_info_props = tool_info["parameters"].get("properties") or {}
    assert "tool_name" in tool_info_props
    assert "include_schema" in tool_info_props
    assert "failed_tool_name" in tool_info_props
    assert "failed_args" in tool_info_props
    assert "failed_error" in tool_info_props
    capability_docs = next(
        (t for t in tools if t.get("name") == "read_capability_docs"), None
    )
    assert capability_docs is not None
    capability_docs_props = capability_docs["parameters"].get("properties") or {}
    assert "action" in capability_docs_props
    assert "scope" in capability_docs_props
    assert "doc_id" in capability_docs_props
    assert "query" in capability_docs_props
    assert "start_line" in capability_docs_props
    assert "line_count" in capability_docs_props
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
    subchat = next((t for t in tools if t.get("name") == "subchat"), None)
    assert subchat is not None
    subchat_props = subchat["parameters"].get("properties") or {}
    assert subchat_props["action"]["default"] == "return"
    assert "continue" in subchat_props["action"]["enum"]
    reflect = next((t for t in tools if t.get("name") == "reflect"), None)
    assert reflect is not None
    reflect_props = reflect["parameters"].get("properties") or {}
    assert "question" in reflect_props
    assert "patience" in reflect_props
    assert "patience_budget" in reflect_props
    assert "oneOf" in reflect_props["patience"]
    assert "run_now" in reflect_props
    list_reflections = next(
        (t for t in tools if t.get("name") == "list_reflections"), None
    )
    assert list_reflections is not None
    assert list_reflections["policy"]["approval"] == "low"
    list_reflection_props = list_reflections["parameters"].get("properties") or {}
    assert "include_runs" in list_reflection_props
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
    assert "start_at" in create_task_props
    assert "end_at" in create_task_props
    assert "tz" in create_task_props
    assert "time_zone" in create_task_props
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
    assert read_file["policy"]["live_auto"] is True
    read_file_props = read_file["parameters"].get("properties") or {}
    assert "start_line" in read_file_props
    assert "line_count" in read_file_props
    assert "max_chars" in read_file_props
    assert read_file_props["start_line"]["default"] == 1
    assert read_file_props["line_count"]["default"] == 200
    assert read_file_props["line_count"]["maximum"] == 1000
    assert read_file_props["max_chars"]["default"] == 12000
    assert read_file_props["max_chars"]["maximum"] == 20000
    compact_plan = next(
        (t for t in tools if t.get("name") == "compact_conversation_plan"),
        None,
    )
    assert compact_plan is not None
    assert compact_plan["policy"]["approval"] == "low"
    compact_plan_props = compact_plan["parameters"].get("properties") or {}
    assert "context_window_tokens" in compact_plan_props
    assert compact_plan_props["context_window_tokens"]["default"] == 24000
    assert "soft_trigger_ratio" in compact_plan_props
    compact_preview = next(
        (t for t in tools if t.get("name") == "compact_conversation_preview"),
        None,
    )
    assert compact_preview is not None
    compact_preview_props = compact_preview["parameters"].get("properties") or {}
    assert "summary_workflow" in compact_preview_props
    compact_write = next(
        (t for t in tools if t.get("name") == "compact_conversation_write"),
        None,
    )
    assert compact_write is not None
    compact_write_props = compact_write["parameters"].get("properties") or {}
    assert "target_conversation_id" in compact_write_props
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

    live_resp = client.get("/api/tools/specs?workflow=live")
    assert live_resp.status_code == 200
    live_names = {tool.get("name") for tool in live_resp.json().get("tools", [])}
    assert {"help", "tool_info", "read_file", "list_dir"} <= live_names
    assert "tool_help" not in live_names
    assert "remember" in live_names
    assert all("." not in str(name or "") for name in live_names)
    assert "capture.list" not in live_names
    assert "write_file" not in live_names
    assert "computer.act" not in live_names

    user_settings.save_settings(
        {
            "tool_policies": {
                "write_file": {"workflow": "both", "approval": "low"},
                "read_file": {"workflow": "disabled", "approval": "low"},
                "mcp.call": {"workflow": "both", "approval": "low"},
            }
        }
    )
    overridden_resp = client.get("/api/tools/specs?workflow=live")
    assert overridden_resp.status_code == 200
    overridden_names = {
        tool.get("name") for tool in overridden_resp.json().get("tools", [])
    }
    assert "write_file" in overridden_names
    assert "read_file" not in overridden_names
    assert "mcp.call" not in overridden_names
