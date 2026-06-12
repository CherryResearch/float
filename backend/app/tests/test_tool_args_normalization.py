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
    data = body.get("result", {}).get("data") or {}
    assert data.get("recovery_tool") == "help"
    assert data.get("recovery_args", {}).get("failed_tool_name") == "search_web"
    payload_result = body.get("result") or body.get("error") or ""
    if isinstance(payload_result, dict):
        payload_result = payload_result.get("message") or ""
    assert "Missing required argument" in payload_result


def test_tool_help_defaults_are_applied():
    from app.utils.tool_args import normalize_tool_args

    args = normalize_tool_args("tool_help", {})
    assert args["tool_name"] == ""
    assert args["detail"] == "names"
    assert args["include_schema"] is False
    assert args["max_tools"] == 50
    assert args["failed_tool_name"] == ""
    assert args["failed_args"] == {}
    assert args["failed_error"] == ""


def test_help_defaults_are_applied():
    from app.utils.tool_args import normalize_tool_args

    args = normalize_tool_args("help", {})
    assert args["tool_name"] == ""
    assert args["detail"] == "names"
    assert args["include_schema"] is False
    assert args["max_tools"] == 50
    assert args["failed_tool_name"] == ""
    assert args["failed_args"] == {}
    assert args["failed_error"] == ""


def test_tool_help_full_detail_alias_is_canonicalized():
    from app.utils.tool_args import normalize_tool_args

    args = normalize_tool_args(
        "tool_help",
        {
            "tool_name": "write_file",
            "detail": "full",
            "include_schema": "true",
            "max_tools": "1",
        },
    )
    assert args["tool_name"] == "write_file"
    assert args["detail"] == "rich"
    assert args["include_schema"] is True
    assert args["max_tools"] == 1


def test_tool_help_full_detail_alias_invokes_through_route(client):
    client.post("/tools/register", json={"name": "tool_help"})
    res = client.post(
        "/tools/invoke",
        json={
            "name": "tool_help",
            "args": {
                "tool_name": "write_file",
                "detail": "full",
                "include_schema": True,
                "max_tools": 1,
            },
        },
    )
    assert res.status_code == 200
    result = res.json()["result"]
    assert result["status"] == "invoked"
    assert result["ok"] is True
    assert result["data"]["query"]["detail"] == "rich"
    assert result["data"]["tools"][0]["name"] == "write_file"


def test_memory_save_accepts_content_alias():
    from app.utils.tool_args import normalize_tool_args

    args = normalize_tool_args(
        "memory.save",
        {"content": "Food diary entry", "tags": ["food diary"]},
    )

    assert args["text"] == "Food diary entry"
    assert args["tags"] == ["food diary"]
    assert "content" not in args


def test_remember_and_write_file_accept_common_text_aliases():
    from app.utils.tool_args import normalize_tool_args

    remember_args = normalize_tool_args(
        "remember",
        {"key": "food_diary", "content": "Food diary entry"},
    )
    write_args = normalize_tool_args(
        "write_file",
        {"path": "notes/food.txt", "text": "Food diary entry"},
    )

    assert remember_args == {"key": "food_diary", "value": "Food diary entry"}
    assert write_args["path"] == "notes/food.txt"
    assert write_args["content"] == "Food diary entry"
    assert "text" not in write_args


def test_recall_accepts_search_query_alias():
    from app.utils.tool_args import normalize_tool_args

    args = normalize_tool_args("recall", {"query": "temporary eval marker"})

    assert args["key"] == "temporary eval marker"
    assert args["mode"] == "hybrid"
    assert "query" not in args


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
    assert args["failed_tool_name"] == ""
    assert args["failed_args"] == {}
    assert args["failed_error"] == ""


def test_tool_info_accepts_single_tools_alias():
    from app.utils.tool_args import normalize_tool_args

    args = normalize_tool_args("tool_info", {"tools": ["write_file"]})
    assert args["tool_name"] == "write_file"
    assert args["include_schema"] is True


def test_create_task_accepts_natural_language_when_union_type():
    from app.utils.tool_args import normalize_tool_args

    args = normalize_tool_args(
        "create_task",
        {
            "title": "Check the build",
            "when": "tomorrow at 9",
            "timezone": "America/Vancouver",
        },
    )

    assert args["when"] == "tomorrow at 9"
    assert args["timezone"] == "America/Vancouver"


def test_routes_normalize_camera_alias():
    from app.routes import _normalize_tool_name

    assert _normalize_tool_name("camera") == "camera.capture"
    assert _normalize_tool_name(" camera.capture ") == "camera.capture"


def test_routes_normalize_common_tool_name_aliases():
    from app.routes import _normalize_tool_name

    assert _normalize_tool_name("memory.search") == "recall"
    assert _normalize_tool_name("memory.store") == "remember"
    assert _normalize_tool_name("browser.open") == "computer.navigate"
    assert _normalize_tool_name("patch") == "patch.apply"
    assert _normalize_tool_name("writefile") == "write_file"


def test_routes_suggest_memory_read_compatibility_names():
    from app.routes import _suggest_tool_names

    suggestions = _suggest_tool_names("memory.read")
    assert suggestions[:3] == ["recall", "remember", "memory.save"]


def test_chat_context_schema_converts_to_service_context():
    from app.models import Message, ModelContext
    from app.routes import _context_schema_to_service_context

    context = ModelContext(
        system_prompt="eval prompt",
        messages=[Message(role="user", content="hello", metadata={"source": "test"})],
        metadata={"eval": True},
    )

    service_context = _context_schema_to_service_context(context)

    assert service_context.system_prompt == "eval prompt"
    assert service_context.messages == [
        {"role": "user", "content": "hello", "metadata": {"source": "test"}}
    ]
    assert service_context.metadata == {"eval": True}
    service_context.add_message("assistant", "ok")
    assert service_context.messages[-1]["content"] == "ok"
