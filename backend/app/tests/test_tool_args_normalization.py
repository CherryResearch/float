import pytest


@pytest.fixture
def client():
    import sys
    from pathlib import Path

    from fastapi.testclient import TestClient

    backend_dir = Path(__file__).resolve().parents[2]
    backend_dir = str(backend_dir)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    from app.main import app

    return TestClient(app)


def test_search_web_normalizes_topn_alias_and_defaults():
    from app.utils.tool_args import normalize_tool_args

    args = normalize_tool_args(
        "search_web",
        {"query": "recent AI advances", "topn": 3, "source": "news"},
    )
    assert args["query"] == "recent AI advances"
    assert args["max_results"] == 3
    assert args["region"] == "us-en"
    assert "topn" not in args
    assert "source" not in args


def test_search_web_fills_signature_defaults():
    from app.utils.tool_args import normalize_tool_args

    args = normalize_tool_args("search_web", {"query": "croissant"})
    assert args == {"query": "croissant", "max_results": 5, "region": "us-en"}


def test_crawl_fills_timeout_default():
    from app.utils.tool_args import normalize_tool_args

    args = normalize_tool_args("crawl", {"url": "https://example.com"})
    assert args["url"] == "https://example.com"
    assert args["timeout"] == 5


def test_search_web_missing_required_raises():
    from app.utils.tool_args import normalize_tool_args

    with pytest.raises(ValueError):
        normalize_tool_args("search_web", {"max_results": 3})


def test_tool_decision_returns_structured_error_on_invalid_args(client):
    payload = {
        "request_id": "bad-search",
        "decision": "accept",
        "name": "search_web",
        "args": {"max_results": 3},
        "session_id": "sess-test",
        "message_id": "msg-test",
    }
    res = client.post("/api/tools/decision", json=payload)
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "error"
    payload_result = body.get("result") or body.get("error") or ""
    if isinstance(payload_result, dict):
        payload_result = payload_result.get("message") or ""
    assert "Missing required argument" in payload_result


def test_tool_help_defaults_are_applied():
    from app.utils.tool_args import normalize_tool_args

    args = normalize_tool_args("tool_help", {})
    assert args["tool_name"] == ""
    assert args["detail"] == "brief"
    assert args["include_schema"] is False
    assert args["max_tools"] == 8


def test_help_defaults_are_applied():
    from app.utils.tool_args import normalize_tool_args

    args = normalize_tool_args("help", {})
    assert args["tool_name"] == ""
    assert args["detail"] == "brief"
    assert args["include_schema"] is False
    assert args["max_tools"] == 8


def test_list_dir_defaults_are_applied():
    from app.utils.tool_args import normalize_tool_args

    args = normalize_tool_args("list_dir", {})
    assert args["path"] == "."
    assert args["workspace_only"] is False
    assert args["recursive"] is False
    assert args["include_hidden"] is False
    assert args["max_entries"] == 100


def test_read_file_defaults_are_applied():
    from app.utils.tool_args import normalize_tool_args

    args = normalize_tool_args("read_file", {"path": "workspace/report.csv"})
    assert args["path"] == "workspace/report.csv"
    assert args["start_line"] == 1
    assert args["line_count"] == 200
    assert args["max_chars"] == 12000


def test_read_file_args_are_clamped_to_schema_limits():
    from app.utils.tool_args import normalize_tool_args

    args = normalize_tool_args(
        "read_file",
        {
            "path": "workspace/report.csv",
            "start_line": 0,
            "line_count": 5000,
            "max_chars": 50000,
        },
    )
    assert args["start_line"] == 1
    assert args["line_count"] == 1000
    assert args["max_chars"] == 20000


def test_list_dir_args_are_clamped_to_schema_limits():
    from app.utils.tool_args import normalize_tool_args

    args = normalize_tool_args("list_dir", {"path": ".", "max_entries": 999})
    assert args["path"] == "."
    assert args["max_entries"] == 200


def test_tool_info_defaults_are_applied():
    from app.utils.tool_args import normalize_tool_args

    args = normalize_tool_args("tool_info", {"tool_name": "search_web"})
    assert args["tool_name"] == "search_web"
    assert args["include_schema"] is True


def test_tool_info_accepts_single_tools_alias():
    from app.utils.tool_args import normalize_tool_args

    args = normalize_tool_args("tool_info", {"tools": ["write_file"]})
    assert args["tool_name"] == "write_file"
    assert args["include_schema"] is True


def test_routes_normalize_camera_alias():
    from app.routes import _normalize_tool_name

    assert _normalize_tool_name("camera") == "camera.capture"
    assert _normalize_tool_name(" camera.capture ") == "camera.capture"
