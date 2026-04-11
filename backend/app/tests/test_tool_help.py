import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parents[2]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.utils import generate_signature  # noqa: E402


def test_tool_help_returns_rich_single_tool_entry():
    from app.tools.tool_help import tool_help

    args = {
        "tool_name": "remember",
        "detail": "rich",
        "include_schema": True,
        "max_tools": 20,
    }
    signature = generate_signature("tester", "tool_help", args)
    result = tool_help(user="tester", signature=signature, **args)
    assert result["count"] == 1
    entry = result["tools"][0]
    assert entry["name"] == "remember"
    assert "arguments" in entry
    assert "schema" in entry
    assert "examples" in entry
    assert any("canonical" in str(note).lower() for note in entry.get("notes", []))


def test_tool_help_recall_mentions_hybrid_search():
    from app.tools.tool_help import tool_help

    args = {
        "tool_name": "recall",
        "detail": "rich",
        "include_schema": True,
        "max_tools": 20,
    }
    signature = generate_signature("tester", "tool_help", args)
    result = tool_help(user="tester", signature=signature, **args)
    assert result["count"] == 1
    entry = result["tools"][0]
    notes = " ".join(str(note) for note in entry.get("notes", []))
    assert "hybrid" in notes.lower()


def test_tool_help_tool_help_mentions_runtime_and_sandbox_checks():
    from app.tools.tool_help import tool_help

    args = {
        "tool_name": "tool_help",
        "detail": "rich",
        "include_schema": True,
        "max_tools": 20,
    }
    signature = generate_signature("tester", "tool_help", args)
    result = tool_help(user="tester", signature=signature, **args)
    assert result["count"] == 1
    entry = result["tools"][0]
    notes = " ".join(str(note) for note in entry.get("notes", []))
    lowered = notes.lower()
    assert "runtime" in lowered
    assert "sandbox" in lowered


def test_tool_help_tool_help_mentions_repo_readme_for_float_docs():
    from app.tools.tool_help import tool_help

    args = {
        "tool_name": "tool_help",
        "detail": "rich",
        "include_schema": True,
        "max_tools": 20,
    }
    signature = generate_signature("tester", "tool_help", args)
    result = tool_help(user="tester", signature=signature, **args)
    entry = result["tools"][0]
    notes = " ".join(str(note) for note in entry.get("notes", []))
    lowered = notes.lower()
    assert "readme" in lowered
    assert "shell.exec" in notes


def test_tool_help_tool_help_mentions_create_task_discovery():
    from app.tools.tool_help import tool_help

    args = {
        "tool_name": "tool_help",
        "detail": "rich",
        "include_schema": True,
        "max_tools": 20,
    }
    signature = generate_signature("tester", "tool_help", args)
    result = tool_help(user="tester", signature=signature, **args)
    entry = result["tools"][0]
    notes = " ".join(str(note) for note in entry.get("notes", []))
    lowered = notes.lower()
    assert "create_task" in lowered
    assert "list_tasks" in lowered
    assert "scheduler" in lowered


def test_help_special_modules_returns_workflow_catalog():
    from app.tools.tool_help import help_tool

    args = {
        "tool_name": "modules",
        "detail": "rich",
        "include_schema": False,
        "max_tools": 8,
    }
    signature = generate_signature("tester", "help", args)
    result = help_tool(user="tester", signature=signature, **args)
    assert result["count"] == 1
    entry = result["tools"][0]
    assert entry["name"] == "modules"
    assert isinstance(entry.get("workflows"), list)
    assert isinstance(entry.get("modules"), list)
    notes = " ".join(str(note) for note in entry.get("notes", []))
    assert "workflow" in notes.lower()
    assert "add-ons" in notes.lower()


def test_help_special_skills_returns_skill_catalog():
    from app.tools.tool_help import help_tool

    args = {
        "tool_name": "skills",
        "detail": "rich",
        "include_schema": False,
        "max_tools": 8,
    }
    signature = generate_signature("tester", "help", args)
    result = help_tool(user="tester", signature=signature, **args)
    assert result["count"] == 1
    entry = result["tools"][0]
    assert entry["name"] == "skills"
    assert "skills_root" in entry
    notes = " ".join(str(note) for note in entry.get("notes", []))
    lowered = notes.lower()
    assert "markdown" in lowered
    assert "not yet dynamically injected" in lowered


def test_tool_info_special_modules_returns_catalog_entry():
    from app.tools.tool_help import tool_info

    args = {
        "tool_name": "modules",
        "include_schema": False,
    }
    signature = generate_signature("tester", "tool_info", args)
    result = tool_info(user="tester", signature=signature, **args)
    assert result["name"] == "modules"
    assert result["category"] == "runtime"


def test_tool_info_special_skills_returns_catalog_entry():
    from app.tools.tool_help import tool_info

    args = {
        "tool_name": "skills",
        "include_schema": False,
    }
    signature = generate_signature("tester", "tool_info", args)
    result = tool_info(user="tester", signature=signature, **args)
    assert result["name"] == "skills"
    assert result["category"] == "runtime"


def test_tool_help_list_actions_mentions_revert_batches():
    from app.tools.tool_help import tool_help

    args = {
        "tool_name": "list_actions",
        "detail": "rich",
        "include_schema": True,
        "max_tools": 20,
    }
    signature = generate_signature("tester", "tool_help", args)
    result = tool_help(user="tester", signature=signature, **args)
    entry = result["tools"][0]
    notes = " ".join(str(note) for note in entry.get("notes", []))
    lowered = notes.lower()
    assert "response" in lowered
    assert "conversation" in lowered
    assert "revert" in lowered


def test_tool_help_revert_actions_mentions_conflicts():
    from app.tools.tool_help import tool_help

    args = {
        "tool_name": "revert_actions",
        "detail": "rich",
        "include_schema": True,
        "max_tools": 20,
    }
    signature = generate_signature("tester", "tool_help", args)
    result = tool_help(user="tester", signature=signature, **args)
    entry = result["tools"][0]
    notes = " ".join(str(note) for note in entry.get("notes", []))
    lowered = notes.lower()
    assert "conflict" in lowered
    assert "before-snapshot" in lowered or "before" in lowered


def test_tool_help_create_task_mentions_reminders_and_actions():
    from app.tools.tool_help import tool_help

    args = {
        "tool_name": "create_task",
        "detail": "rich",
        "include_schema": True,
        "max_tools": 20,
    }
    signature = generate_signature("tester", "tool_help", args)
    result = tool_help(user="tester", signature=signature, **args)
    entry = result["tools"][0]
    notes = " ".join(str(note) for note in entry.get("notes", []))
    lowered = notes.lower()
    assert "reminder" in lowered
    assert "actions" in lowered


def test_tool_help_read_file_mentions_chunked_usage():
    from app.tools.tool_help import tool_help

    args = {
        "tool_name": "read_file",
        "detail": "rich",
        "include_schema": True,
        "max_tools": 20,
    }
    signature = generate_signature("tester", "tool_help", args)
    result = tool_help(user="tester", signature=signature, **args)
    entry = result["tools"][0]
    notes = " ".join(str(note) for note in entry.get("notes", []))
    assert "list_dir" in notes
    assert "start_line" in notes
    assert "data/" in notes
    schema = entry["schema"]
    props = schema["properties"]
    assert props["start_line"]["default"] == 1
    assert props["line_count"]["default"] == 200
    assert props["line_count"]["maximum"] == 1000
    assert props["max_chars"]["default"] == 12000
    assert props["max_chars"]["maximum"] == 20000


def test_tool_help_includes_catalog_metadata():
    from app.tools.tool_help import tool_help

    args = {
        "tool_name": "open_url",
        "detail": "rich",
        "include_schema": False,
        "max_tools": 20,
    }
    signature = generate_signature("tester", "tool_help", args)
    result = tool_help(user="tester", signature=signature, **args)
    entry = result["tools"][0]
    assert entry["status"] == "legacy"
    assert entry["category"] == "web"
    access_notes = " ".join(str(item) for item in entry.get("can_access", []))
    assert "browser" in access_notes.lower()
    assert any("approval" in str(item).lower() for item in entry.get("safety", []))
    notes = " ".join(str(item) for item in entry.get("notes", []))
    assert "legacy" in notes.lower()
    assert "computer.navigate" in notes


def test_tool_help_system_prompt_mentions_file_workflow(monkeypatch):
    from app import config

    monkeypatch.delenv("SYSTEM_PROMPT", raising=False)
    prompt = config.load_config()["system_prompt"]
    assert "help" in prompt
    assert "tool_help" in prompt
    assert "tool_info" in prompt
    assert "list_dir" in prompt
    assert "read_file" in prompt
    assert "start_line" in prompt
    assert "line_count" in prompt
    assert "max_chars" in prompt
    assert "write_file" in prompt
    assert "list_actions" in prompt
    assert "read_action_diff" in prompt
    assert "revert_actions" in prompt
    assert "computer.observe" in prompt
    assert "computer.act" in prompt
    assert "shell.exec" in prompt
    assert "patch.apply" in prompt
    assert "mcp.call" in prompt
    assert "stub only" not in prompt


def test_tool_help_computer_observe_mentions_session_state():
    from app.tools.tool_help import tool_help

    args = {
        "tool_name": "computer.observe",
        "detail": "rich",
        "include_schema": True,
        "max_tools": 20,
    }
    signature = generate_signature("tester", "tool_help", args)
    result = tool_help(user="tester", signature=signature, **args)
    entry = result["tools"][0]
    notes = " ".join(str(note) for note in entry.get("notes", []))
    lowered = notes.lower()
    assert "screenshot" in lowered
    assert "session" in lowered
    assert "window" in lowered


def test_tool_help_brief_list_mode_honors_limit():
    from app.tools.tool_help import tool_help

    args = {
        "tool_name": "",
        "detail": "brief",
        "include_schema": False,
        "max_tools": 3,
    }
    signature = generate_signature("tester", "tool_help", args)
    result = tool_help(user="tester", signature=signature, **args)
    assert result["count"] == 3
    assert len(result["tools"]) == 3
    assert result["total_count"] >= result["count"]
    assert result["tools"] == ["help", "tool_help", "recall"]
    assert result["remaining_count"] == result["total_count"] - result["count"]
    assert "tool_info" in result["more_tools"]


def test_tool_help_brief_list_mode_surfaces_tail_tools_when_truncated():
    from app.tools.tool_help import tool_help

    args = {
        "tool_name": "",
        "detail": "brief",
        "include_schema": False,
        "max_tools": 20,
    }
    signature = generate_signature("tester", "tool_help", args)
    result = tool_help(user="tester", signature=signature, **args)
    assert result["count"] == 20
    assert "write_file" in result["tools"]
    assert "create_task" in result["tools"]
    assert result["remaining_count"] > 0


def test_help_alias_uses_compact_defaults():
    from app.tools.tool_help import help_tool

    args = {
        "tool_name": "",
        "detail": "brief",
        "include_schema": False,
        "max_tools": 8,
    }
    signature = generate_signature("tester", "help", args)
    result = help_tool(user="tester", signature=signature, **args)
    assert result["count"] == 8
    assert result["tools"] == [
        "help",
        "tool_help",
        "tool_info",
        "list_actions",
        "create_task",
        "memory.save",
        "remember",
        "recall",
    ]


def test_tool_info_returns_single_catalog_entry():
    from app.tools.tool_help import tool_info

    args = {
        "tool_name": "list_dir",
        "include_schema": True,
    }
    signature = generate_signature("tester", "tool_info", args)
    result = tool_info(user="tester", signature=signature, **args)
    assert result["id"] == "list_dir"
    assert result["category"] == "files"
    assert result["sandbox"]["read_roots"] == ["data/"]
    assert "input_schema" in result


def test_tool_info_unknown_tool_returns_did_you_mean():
    from app.tools.tool_help import tool_info

    args = {
        "tool_name": "writefile",
        "include_schema": False,
    }
    signature = generate_signature("tester", "tool_info", args)
    result = tool_info(user="tester", signature=signature, **args)
    assert result["error"] == "unknown_tool"
    assert "write_file" in result.get("did_you_mean", [])
