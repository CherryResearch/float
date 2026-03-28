import sys
from pathlib import Path

import pytest
from app.base_services import MemoryManager
from app.tools import local_files
from app.utils import generate_signature, sanitize_args
from fastapi.testclient import TestClient


@pytest.fixture
def mem_mgr():
    return MemoryManager({})


@pytest.fixture
def add_backend_to_sys_path():
    backend_dir = Path(__file__).resolve().parents[2]
    backend_dir = str(backend_dir)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)


@pytest.fixture
def client(add_backend_to_sys_path):
    from app.main import app

    return TestClient(app)


def test_injection_attempt_rejected(client):
    client.post("/tools/register", json={"name": "write_file"})
    payload = {
        "name": "write_file",
        "args": {"path": "note.txt; rm -rf /", "content": "hi"},
    }
    res = client.post("/tools/invoke", json=payload)
    assert res.status_code == 400


def test_sanitize_allows_rich_text():
    raw = {
        "value": "Profit & Loss <Q1> via `pip install`",
        "urls": ["https://example.com?foo=1&bar=2"],
        "nested": {"note": "C:\\tmp\\files\\report.txt"},
    }
    sanitized = sanitize_args(raw)
    assert sanitized == raw


def test_sanitize_rejects_shell_like_sequences():
    with pytest.raises(ValueError):
        sanitize_args({"value": "note.txt; rm -rf /"})
    with pytest.raises(ValueError):
        sanitize_args({"value": "report | grep password"})
    with pytest.raises(ValueError):
        sanitize_args({"value": "tmp $(rm -rf /)"})


def test_missing_and_invalid_signatures(mem_mgr, tmp_path, monkeypatch):
    monkeypatch.setenv("FLOAT_DATA_DIR", str(tmp_path))
    mem_mgr.register_tool("read_file", local_files.read_file)
    target = tmp_path / "a.txt"
    target.write_text("hi")
    args = {
        "path": str(target),
        "start_line": 1,
        "line_count": 200,
        "max_chars": 12000,
    }
    sig = generate_signature("bob", "read_file", args)
    result = mem_mgr.invoke_tool(
        "read_file",
        user="bob",
        signature=sig,
        **args,
    )
    assert result["text"] == "hi"
    assert result["truncated"] is False
    with pytest.raises(PermissionError):
        mem_mgr.invoke_tool("read_file", user="bob", signature=None, **args)
    with pytest.raises(PermissionError):
        mem_mgr.invoke_tool("read_file", user="bob", signature="bad", **args)


def test_read_file_rejects_outside_data_dir(mem_mgr, tmp_path, monkeypatch):
    monkeypatch.setenv("FLOAT_DATA_DIR", str(tmp_path))
    mem_mgr.register_tool("read_file", local_files.read_file)
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("nope")
    args = {
        "path": str(outside),
        "start_line": 1,
        "line_count": 200,
        "max_chars": 12000,
    }
    sig = generate_signature("bob", "read_file", args)
    with pytest.raises(PermissionError):
        mem_mgr.invoke_tool("read_file", user="bob", signature=sig, **args)


def test_list_dir_restricted_to_data_dir(mem_mgr, tmp_path, monkeypatch):
    monkeypatch.setenv("FLOAT_DATA_DIR", str(tmp_path))
    mem_mgr.register_tool("list_dir", local_files.list_dir)
    (tmp_path / "workspace").mkdir(parents=True, exist_ok=True)
    (tmp_path / "workspace" / "note.txt").write_text("hi")
    outside = tmp_path.parent / "outside-dir"
    outside.mkdir(exist_ok=True)
    args = {
        "path": "workspace",
        "workspace_only": False,
        "recursive": False,
        "include_hidden": False,
        "max_entries": 100,
    }
    sig = generate_signature("bob", "list_dir", args)
    result = mem_mgr.invoke_tool("list_dir", user="bob", signature=sig, **args)
    assert result["scope"] == "data"
    assert any(item["path"] == "workspace/note.txt" for item in result["entries"])

    bad_args = {
        "path": str(outside),
        "workspace_only": False,
        "recursive": False,
        "include_hidden": False,
        "max_entries": 100,
    }
    bad_sig = generate_signature("bob", "list_dir", bad_args)
    with pytest.raises(PermissionError):
        mem_mgr.invoke_tool("list_dir", user="bob", signature=bad_sig, **bad_args)


def test_list_dir_workspace_only_normalizes_prefix(mem_mgr, tmp_path, monkeypatch):
    monkeypatch.setenv("FLOAT_DATA_DIR", str(tmp_path))
    mem_mgr.register_tool("list_dir", local_files.list_dir)
    target = tmp_path / "workspace" / "nested" / "hello.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("hello")
    args = {
        "path": "data/workspace/nested",
        "workspace_only": True,
        "recursive": False,
        "include_hidden": False,
        "max_entries": 100,
    }
    sig = generate_signature("bob", "list_dir", args)
    result = mem_mgr.invoke_tool("list_dir", user="bob", signature=sig, **args)
    assert result["scope"] == "workspace"
    assert result["path"] == "nested"
    assert any(item["path"] == "nested/hello.txt" for item in result["entries"])


def test_write_file_restricted_to_workspace(mem_mgr, tmp_path, monkeypatch):
    monkeypatch.setenv("FLOAT_DATA_DIR", str(tmp_path))
    mem_mgr.register_tool("write_file", local_files.write_file)
    args = {"path": "note.txt", "content": "hi"}
    sig = generate_signature("bob", "write_file", args)
    result = mem_mgr.invoke_tool("write_file", user="bob", signature=sig, **args)
    assert result == "written"
    assert (tmp_path / "workspace" / "note.txt").exists()

    outside = tmp_path / "outside.txt"
    bad_args = {"path": str(outside), "content": "nope"}
    bad_sig = generate_signature("bob", "write_file", bad_args)
    with pytest.raises(PermissionError):
        mem_mgr.invoke_tool("write_file", user="bob", signature=bad_sig, **bad_args)


def test_write_file_normalizes_data_workspace_prefix(mem_mgr, tmp_path, monkeypatch):
    monkeypatch.setenv("FLOAT_DATA_DIR", str(tmp_path))
    mem_mgr.register_tool("write_file", local_files.write_file)
    args = {"path": "data/workspace/hello.txt", "content": "good morning"}
    sig = generate_signature("bob", "write_file", args)
    result = mem_mgr.invoke_tool("write_file", user="bob", signature=sig, **args)
    assert result == "written"
    assert (tmp_path / "workspace" / "hello.txt").read_text() == "good morning"
    assert not (tmp_path / "workspace" / "data" / "workspace" / "hello.txt").exists()


def test_read_file_normalizes_data_prefix(mem_mgr, tmp_path, monkeypatch):
    monkeypatch.setenv("FLOAT_DATA_DIR", str(tmp_path))
    mem_mgr.register_tool("read_file", local_files.read_file)
    target = tmp_path / "workspace" / "hello.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("hi")
    args = {
        "path": "data/workspace/hello.txt",
        "start_line": 1,
        "line_count": 200,
        "max_chars": 12000,
    }
    sig = generate_signature("bob", "read_file", args)
    result = mem_mgr.invoke_tool("read_file", user="bob", signature=sig, **args)
    assert result["path"] == "workspace/hello.txt"
    assert result["text"] == "hi"


def test_read_file_returns_windowed_excerpt(mem_mgr, tmp_path, monkeypatch):
    monkeypatch.setenv("FLOAT_DATA_DIR", str(tmp_path))
    mem_mgr.register_tool("read_file", local_files.read_file)
    target = tmp_path / "workspace" / "rows.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("a\nb\nc\nd\ne\n", encoding="utf-8")
    args = {
        "path": "data/workspace/rows.csv",
        "start_line": 2,
        "line_count": 2,
        "max_chars": 12000,
    }
    sig = generate_signature("bob", "read_file", args)
    result = mem_mgr.invoke_tool("read_file", user="bob", signature=sig, **args)
    assert result["start_line"] == 2
    assert result["end_line"] == 3
    assert result["text"] == "b\nc"
    assert result["truncated"] is True
    assert result["next_start_line"] == 4


def test_tool_decision_recovers_from_payload(client):
    from app.main import app

    if hasattr(app.state, "pending_tools"):
        delattr(app.state, "pending_tools")
    payload = {
        "request_id": "fallback-req",
        "decision": "accept",
        "name": "remember",
        "args": {
            "key": "api_status_note",
            "value": "Tool approval fallback executed",
        },
        "session_id": "sess-fallback",
        "message_id": "msg-fallback",
    }
    res = client.post("/api/tools/decision", json=payload)
    assert res.status_code == 200
    assert res.json()["status"] == "invoked"
