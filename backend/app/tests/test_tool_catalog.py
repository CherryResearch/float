import sys
from pathlib import Path

from fastapi.testclient import TestClient


def test_tool_catalog_endpoint_returns_builtin_metadata(tmp_path, monkeypatch):
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
    assert list_dir["sandbox"]["read_roots"] == ["data/"]
    assert list_dir["limits"]["default_max_entries"] == 100
    assert list_dir["limits"]["max_entries"] == 200
    assert any(
        "workspace" in str(item).lower() for item in list_dir.get("can_access", [])
    )
    read_file = next((tool for tool in tools if tool.get("id") == "read_file"), None)
    assert read_file is not None
    assert read_file["limits"]["default_start_line"] == 1
    assert read_file["limits"]["default_line_count"] == 200
    assert read_file["limits"]["max_line_count"] == 1000
    assert read_file["limits"]["default_max_chars"] == 12000
    assert read_file["limits"]["max_chars"] == 20000
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
    assert revert_actions["category"] == "history"
    assert revert_actions["persistence"]["writes_state"] is True
    assert revert_actions["safety"]["default_approval"] == "confirm"
    assert all(tool.get("id") != "decay_memories" for tool in tools)


def test_tool_catalog_single_entry_endpoint(tmp_path, monkeypatch):
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
    resp = client.get("/api/tools/catalog/search_web")
    assert resp.status_code == 200
    tool = resp.json().get("tool")
    assert isinstance(tool, dict)
    assert tool["id"] == "search_web"
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
