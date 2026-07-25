import sys
from pathlib import Path

from fastapi.testclient import TestClient


def test_tool_catalog_endpoint_returns_builtin_metadata(tmp_path, monkeypatch):
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

    client = TestClient(app)
    resp = client.get("/api/tools/catalog")
    assert resp.status_code == 200
    tools = resp.json().get("tools")
    assert isinstance(tools, list)

    open_url = next((tool for tool in tools if tool.get("id") == "open_url"), None)
    assert open_url is not None
    assert open_url["status"] == "legacy"
    assert open_url["category"] == "web"
    assert open_url["origin"] == "builtin"
    assert any(
        "browser" in str(item).lower() for item in open_url.get("can_access", [])
    )

    computer_observe = next(
        (tool for tool in tools if tool.get("id") == "computer.observe"),
        None,
    )
    assert computer_observe is not None
    assert computer_observe["status"] == "live"
    assert computer_observe["category"] == "computer"
    assert computer_observe["safety"]["default_approval"] == "confirm"

    shell_exec = next((tool for tool in tools if tool.get("id") == "shell.exec"), None)
    assert shell_exec is not None
    assert shell_exec["category"] == "system"
    assert shell_exec["persistence"]["writes_state"] is True

    list_dir = next((tool for tool in tools if tool.get("id") == "list_dir"), None)
    assert list_dir is not None
    assert list_dir["policy"]["workflow"] == "both"
    assert list_dir["policy"]["live_auto"] is True
    assert list_dir["sandbox"]["read_roots"] == ["data/"]
    assert list_dir["limits"]["default_max_entries"] == 100
    assert list_dir["limits"]["max_entries"] == 200
    assert any(
        "workspace" in str(item).lower() for item in list_dir.get("can_access", [])
    )
    capability_docs = next(
        (tool for tool in tools if tool.get("id") == "read_capability_docs"), None
    )
    assert capability_docs is not None
    assert capability_docs["category"] == "docs"
    assert "modules/skills/" in capability_docs["sandbox"]["read_roots"]
    assert capability_docs["policy"]["approval"] == "low"
    read_file = next((tool for tool in tools if tool.get("id") == "read_file"), None)
    assert read_file is not None
    assert read_file["limits"]["default_start_line"] == 1
    assert read_file["limits"]["default_line_count"] == 200
    assert read_file["limits"]["max_line_count"] == 1000
    assert read_file["limits"]["default_max_chars"] == 12000
    assert read_file["limits"]["max_chars"] == 20000
    route_to_local = next(
        (tool for tool in tools if tool.get("id") == "route_to_local_model"),
        None,
    )
    assert route_to_local is not None
    assert route_to_local["category"] == "routing"
    assert route_to_local["runtime"]["executor"] == "client_resolution"
    assert route_to_local["policy"]["live_auto"] is False
    assert route_to_local["policy"]["live_unavailable_reason"] == (
        "client_resolution_required"
    )
    list_actions = next(
        (tool for tool in tools if tool.get("id") == "list_actions"), None
    )
    assert list_actions is not None
    assert list_actions["category"] == "history"
    assert list_actions["persistence"]["writes_state"] is False
    revert_actions = next(
        (tool for tool in tools if tool.get("id") == "revert_actions"), None
    )
    assert revert_actions is not None
    assert revert_actions["policy"]["approval"] == "high"
    assert revert_actions["category"] == "history"
    assert revert_actions["persistence"]["writes_state"] is True
    assert revert_actions["safety"]["default_approval"] == "confirm"
    graph_update = next(
        (tool for tool in tools if tool.get("id") == "graph.update"), None
    )
    assert graph_update is not None
    assert graph_update["category"] == "memory"
    assert graph_update["persistence"]["writes_state"] is True
    assert graph_update["safety"]["default_approval"] == "confirm"
    list_tasks = next((tool for tool in tools if tool.get("id") == "list_tasks"), None)
    assert list_tasks is not None
    assert list_tasks["category"] == "calendar"
    assert list_tasks["persistence"]["writes_state"] is False
    compact_preview = next(
        (tool for tool in tools if tool.get("id") == "compact_conversation_preview"),
        None,
    )
    compact_plan = next(
        (tool for tool in tools if tool.get("id") == "compact_conversation_plan"),
        None,
    )
    assert compact_plan is not None
    assert compact_plan["category"] == "conversation"
    assert compact_plan["limits"]["default_context_window_tokens"] == 24000
    assert compact_plan["limits"]["context_profiles"] == ["short", "medium", "long"]
    assert compact_plan["persistence"]["writes_state"] is False
    assert compact_plan["policy"]["approval"] == "low"
    assert compact_preview is not None
    assert compact_preview["category"] == "conversation"
    assert compact_preview["limits"]["max_keep_last"] == 200
    assert compact_preview["limits"]["summary_workflows"] == [
        "conversation_handoff",
        "decision_focus",
        "task_state",
    ]
    assert compact_preview["persistence"]["writes_state"] is False
    assert compact_preview["policy"]["approval"] == "low"
    compact_write = next(
        (tool for tool in tools if tool.get("id") == "compact_conversation_write"),
        None,
    )
    assert compact_write is not None
    assert compact_write["category"] == "conversation"
    assert compact_write["limits"]["summary_modes"] == ["deterministic", "llm"]
    assert compact_write["limits"]["summary_workflows"][0] == ("conversation_handoff")
    assert compact_write["persistence"]["writes_state"] is True
    assert compact_write["policy"]["approval"] == "high"
    subchat = next((tool for tool in tools if tool.get("id") == "subchat"), None)
    assert subchat is not None
    assert subchat["category"] == "workflow"
    assert subchat["safety"]["default_approval"] == "auto"
    reflect = next((tool for tool in tools if tool.get("id") == "reflect"), None)
    assert reflect is not None
    assert reflect["category"] == "reflection"
    assert reflect["persistence"]["writes_state"] is True
    assert reflect["limits"]["manual_only_v0"] is True
    list_reflections = next(
        (tool for tool in tools if tool.get("id") == "list_reflections"), None
    )
    assert list_reflections is not None
    assert list_reflections["policy"]["approval"] == "low"
    assert list_reflections["persistence"]["writes_state"] is False
    assert all(tool.get("id") != "decay_memories" for tool in tools)


def test_tool_catalog_single_entry_endpoint(tmp_path, monkeypatch):
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

    client = TestClient(app)
    resp = client.get("/api/tools/catalog/search_web")
    assert resp.status_code == 200
    tool = resp.json().get("tool")
    assert isinstance(tool, dict)
    assert tool["id"] == "search_web"
    assert tool["policy"]["workflow"] == "both"
    assert tool["runtime"]["network"] is True
    assert tool["limits"]["max_results"] == 10


def test_tool_limits_endpoint_returns_roots_and_caps(tmp_path, monkeypatch):
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

    client = TestClient(app)
    resp = client.get("/api/tools/limits")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["roots"]["data"]
    assert payload["roots"]["workspace"].endswith("/workspace")
    assert payload["limits"]["list_dir_max_entries"] == 200
    assert payload["limits"]["tool_help_max_tools"] == 50
    assert payload["limits"]["computer_default_width"] == 1280
    assert payload["limits"]["computer_default_height"] == 720
    assert payload["limits"]["shell_exec_timeout_seconds"] == 20
