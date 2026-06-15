import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parents[2]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.utils import generate_signature  # noqa: E402


def _sign(tool_name: str, args: dict):
    from app.tool_specs import BUILTIN_TOOL_SPECS
    from app.utils.tool_args import normalize_tool_args

    try:
        normalized = normalize_tool_args(tool_name, args)
    except ValueError:
        normalized = dict(args)
        spec = BUILTIN_TOOL_SPECS.get(tool_name) or {}
        params = spec.get("parameters") if isinstance(spec, dict) else {}
        properties = params.get("properties") if isinstance(params, dict) else {}
        if isinstance(properties, dict):
            for key, prop in properties.items():
                if key in normalized:
                    continue
                if isinstance(prop, dict) and "default" in prop:
                    normalized[key] = prop.get("default")
    args.clear()
    args.update(normalized)
    return generate_signature("tester", tool_name, normalized)


def _settings_with_modules(modules):
    return {"enabled_workflow_modules": list(modules)}


def test_tool_help_returns_rich_single_tool_entry():
    from app.tools.tool_help import tool_help

    args = {
        "tool_name": "remember",
        "detail": "rich",
        "include_schema": True,
        "max_tools": 20,
    }
    signature = _sign("tool_help", args)
    result = tool_help(user="tester", signature=signature, **args)
    assert result["count"] == 1
    entry = result["tools"][0]
    assert entry["name"] == "remember"
    assert "arguments" in entry
    assert "schema" in entry
    assert "examples" in entry
    assert any("canonical" in str(note).lower() for note in entry.get("notes", []))


def test_tool_help_accepts_full_detail_alias_without_signature_error():
    from app.tools.tool_help import tool_help

    args = {
        "tool_name": "write_file",
        "detail": "full",
        "include_schema": True,
        "max_tools": 1,
    }
    signature = _sign("tool_help", args)
    result = tool_help(user="tester", signature=signature, **args)
    assert result["query"]["detail"] == "rich"
    assert result["count"] == 1
    assert result["tools"][0]["name"] == "write_file"
    assert "schema" in result["tools"][0]


def test_tool_help_recall_mentions_hybrid_search():
    from app.tools.tool_help import tool_help

    args = {
        "tool_name": "recall",
        "detail": "rich",
        "include_schema": True,
        "max_tools": 20,
    }
    signature = _sign("tool_help", args)
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
    signature = _sign("tool_help", args)
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
    signature = _sign("tool_help", args)
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
    signature = _sign("tool_help", args)
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
    signature = _sign("help", args)
    result = help_tool(user="tester", signature=signature, **args)
    assert result["count"] == 1
    entry = result["tools"][0]
    assert entry["name"] == "modules"
    assert isinstance(entry.get("workflows"), list)
    assert isinstance(entry.get("modules"), list)
    assert "tool_names" in entry["modules"][0]
    notes = " ".join(str(note) for note in entry.get("notes", []))
    assert "workflow" in notes.lower()
    assert "add-ons" in notes.lower()
    assert "read_capability_docs" in notes


def test_help_special_skills_returns_skill_catalog():
    from app.tools.tool_help import help_tool

    args = {
        "tool_name": "skills",
        "detail": "rich",
        "include_schema": False,
        "max_tools": 8,
    }
    signature = _sign("help", args)
    result = help_tool(user="tester", signature=signature, **args)
    assert result["count"] == 1
    entry = result["tools"][0]
    assert entry["name"] == "skills"
    assert "skills_root" in entry
    assert "skills_roots" in entry
    skill_ids = [item.get("id") for item in entry.get("skills", [])]
    assert "float_self_knowledge" in skill_ids
    notes = " ".join(str(note) for note in entry.get("notes", []))
    lowered = notes.lower()
    assert "markdown" in lowered
    assert "read_capability_docs" in lowered


def test_tool_info_special_modules_returns_catalog_entry():
    from app.tools.tool_help import tool_info

    args = {
        "tool_name": "modules",
        "include_schema": False,
    }
    signature = _sign("tool_info", args)
    result = tool_info(user="tester", signature=signature, **args)
    assert result["name"] == "modules"
    assert result["category"] == "runtime"


def test_tool_info_special_skills_returns_catalog_entry():
    from app.tools.tool_help import tool_info

    args = {
        "tool_name": "skills",
        "include_schema": False,
    }
    signature = _sign("tool_info", args)
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
    signature = _sign("tool_help", args)
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
    signature = _sign("tool_help", args)
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
    signature = _sign("tool_help", args)
    result = tool_help(user="tester", signature=signature, **args)
    entry = result["tools"][0]
    notes = " ".join(str(note) for note in entry.get("notes", []))
    lowered = notes.lower()
    assert "reminder" in lowered
    assert "actions" in lowered
    assert "start_at" in lowered
    assert "time_zone" in lowered


def test_tool_help_read_file_mentions_chunked_usage():
    from app.tools.tool_help import tool_help

    args = {
        "tool_name": "read_file",
        "detail": "rich",
        "include_schema": True,
        "max_tools": 20,
    }
    signature = _sign("tool_help", args)
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
    signature = _sign("tool_help", args)
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


def test_base_system_prompt_is_structured_and_general(monkeypatch):
    from app import config

    monkeypatch.delenv("SYSTEM_PROMPT", raising=False)
    prompt = config.load_config()["system_prompt"]
    assert "\n\n**personality:" in prompt
    assert "help" in prompt
    assert "tool_help" in prompt
    assert "tool_info" in prompt
    assert "for obvious actionable requests" in prompt
    assert "claim durable changes only after a successful durable tool result" in prompt
    assert "float's personality is light, clever, curious and helpful." in prompt
    assert "memory.*" not in prompt
    assert "open_url" not in prompt
    assert "shell.exec" not in prompt
    assert "patch.apply" not in prompt
    assert "mcp.call" not in prompt
    assert "stub only" not in prompt


def test_tool_help_patch_apply_matches_text_write_schema(monkeypatch):
    from app.tools.tool_help import tool_help
    from app.utils import user_settings

    monkeypatch.setattr(
        user_settings, "load_settings", lambda: _settings_with_modules(["computer_use"])
    )

    args = {
        "tool_name": "patch.apply",
        "detail": "rich",
        "include_schema": True,
        "max_tools": 20,
    }
    signature = _sign("tool_help", args)
    result = tool_help(user="tester", signature=signature, **args)
    entry = result["tools"][0]
    notes = " ".join(str(note) for note in entry.get("notes", []))
    examples = entry.get("examples") or []
    schema = entry.get("schema") or {}

    assert "text file helper" in notes
    assert "not a git-style patch engine" in notes
    assert schema.get("required") == ["path", "content"]
    assert examples and all("patch" not in example for example in examples)
    assert all("path" in example and "content" in example for example in examples)


def test_tool_help_computer_observe_mentions_session_state(monkeypatch):
    from app.tools.tool_help import tool_help
    from app.utils import user_settings

    monkeypatch.setattr(
        user_settings, "load_settings", lambda: _settings_with_modules(["computer_use"])
    )

    args = {
        "tool_name": "computer.observe",
        "detail": "rich",
        "include_schema": True,
        "max_tools": 20,
    }
    signature = _sign("tool_help", args)
    result = tool_help(user="tester", signature=signature, **args)
    entry = result["tools"][0]
    notes = " ".join(str(note) for note in entry.get("notes", []))
    lowered = notes.lower()
    assert "screenshot" in lowered
    assert "session" in lowered
    assert "window" in lowered


def test_tool_help_names_list_mode_honors_limit():
    from app.tools.tool_help import tool_help

    args = {
        "tool_name": "",
        "detail": "names",
        "include_schema": False,
        "max_tools": 3,
    }
    signature = _sign("tool_help", args)
    result = tool_help(user="tester", signature=signature, **args)
    assert result["count"] == 3
    assert len(result["tools"]) == 3
    assert result["total_count"] >= result["count"]
    assert result["tools"] == ["help", "tool_help", "tool_info"]
    assert result["remaining_count"] == result["total_count"] - result["count"]
    assert "list_actions" in result["more_tools"]


def test_tool_help_names_list_mode_surfaces_tail_tools_when_truncated(monkeypatch):
    from app.tools.tool_help import tool_help
    from app.utils import user_settings

    monkeypatch.setattr(
        user_settings, "load_settings", lambda: _settings_with_modules(["computer_use"])
    )

    args = {
        "tool_name": "",
        "detail": "names",
        "include_schema": False,
        "max_tools": 20,
    }
    signature = _sign("tool_help", args)
    result = tool_help(user="tester", signature=signature, **args)
    assert result["count"] == 20
    assert "write_file" in result["tools"]
    assert "create_task" in result["tools"]
    assert result["remaining_count"] > 0


def test_tool_help_brief_list_mode_returns_summaries():
    from app.tools.tool_help import tool_help

    args = {
        "tool_name": "",
        "detail": "brief",
        "include_schema": False,
        "max_tools": 2,
    }
    signature = _sign("tool_help", args)
    result = tool_help(user="tester", signature=signature, **args)
    assert result["count"] == 2
    assert result["tools"][0]["name"] == "help"
    assert "summary" in result["tools"][0]
    assert "schema" not in result["tools"][0]


def test_tool_help_fuzzy_filter_lists_computer_tools(monkeypatch):
    from app.tools.tool_help import tool_help
    from app.utils import user_settings

    monkeypatch.setattr(
        user_settings, "load_settings", lambda: _settings_with_modules(["computer_use"])
    )

    args = {
        "tool_name": "computer",
        "detail": "names",
        "include_schema": False,
        "max_tools": 6,
    }
    signature = _sign("tool_help", args)
    result = tool_help(user="tester", signature=signature, **args)
    assert result["filtered_by"] == "computer"
    assert "error" not in result
    assert result["count"] <= 6
    assert "computer.session.start" in result["tools"]
    assert all("computer" in name for name in result["tools"])


def test_tool_help_hides_disabled_module_tools(monkeypatch):
    from app.tools.tool_help import tool_help
    from app.utils import user_settings

    monkeypatch.setattr(
        user_settings, "load_settings", lambda: _settings_with_modules([])
    )

    args = {
        "tool_name": "computer",
        "detail": "names",
        "include_schema": False,
        "max_tools": 6,
    }
    signature = _sign("tool_help", args)
    result = tool_help(user="tester", signature=signature, **args)

    assert "computer.session.start" not in result.get("tools", [])
    suite_names = {suite["name"] for suite in result.get("suites", [])}
    assert "computer.*" not in suite_names
    assert all("computer" not in str(name) for name in result.get("available", []))


def test_tool_help_family_filter_lists_file_tools():
    from app.tools.tool_help import tool_help

    args = {
        "tool_name": "file",
        "detail": "names",
        "include_schema": False,
        "max_tools": 10,
    }
    signature = _sign("tool_help", args)
    result = tool_help(user="tester", signature=signature, **args)
    assert result["filtered_by"] == "file"
    assert "error" not in result
    assert {"read_file", "list_dir", "write_file"}.issubset(set(result["tools"]))


def test_tool_help_family_alias_lists_calendar_tools():
    from app.tools.tool_help import tool_help

    args = {
        "tool_name": "tasks",
        "detail": "names",
        "include_schema": False,
        "max_tools": 10,
    }
    signature = _sign("tool_help", args)
    result = tool_help(user="tester", signature=signature, **args)
    assert result["filtered_by"] == "tasks"
    assert "error" not in result
    assert {"create_task", "list_tasks"}.issubset(set(result["tools"]))
    assert "create_event" not in result["tools"]


def test_tool_help_module_alias_returns_runtime_catalog():
    from app.tools.tool_help import tool_help

    args = {
        "tool_name": "module",
        "detail": "names",
        "include_schema": False,
        "max_tools": 10,
    }
    signature = _sign("tool_help", args)
    result = tool_help(user="tester", signature=signature, **args)
    assert result["count"] == 1
    assert result["tools"][0]["name"] == "modules"
    assert "modules" in result["tools"][0]


def test_tool_info_family_query_returns_family_menu():
    from app.tools.tool_help import tool_info

    args = {"tool_name": "files", "include_schema": False}
    signature = _sign("tool_info", args)
    result = tool_info(user="tester", signature=signature, **args)
    assert result["error"] == "tool_family"
    assert {"read_file", "list_dir", "write_file"}.issubset(
        set(result["menu"]["tools"])
    )


def test_help_alias_uses_compact_defaults(monkeypatch):
    from app.tools.tool_help import help_tool
    from app.utils import user_settings

    monkeypatch.setattr(
        user_settings, "load_settings", lambda: _settings_with_modules(["computer_use"])
    )

    args = {}
    signature = _sign("help", args)
    result = help_tool(user="tester", signature=signature, **args)
    assert result["default_menu"] is True
    assert result["count"] < result["total_count"]
    assert result["hidden_count"] > 0
    assert "memory.save" not in result["tools"]
    assert "open_url" not in result["tools"]
    assert "subchat" not in result["tools"]
    suite_names = {suite["name"] for suite in result.get("suites", [])}
    assert {"compact_conversation.*", "computer.*", "capture.*"} <= suite_names
    compact_suite = next(
        suite for suite in result["suites"] if suite["name"] == "compact_conversation.*"
    )
    assert "compact_conversation_plan" in compact_suite["exact_tools"]
    assert "detail='brief'" in compact_suite["hint"]


def test_tool_help_memory_family_hides_legacy_alias():
    from app.tools.tool_help import tool_help

    args = {
        "tool_name": "memory",
        "detail": "names",
        "include_schema": False,
        "max_tools": 10,
    }
    signature = _sign("tool_help", args)
    result = tool_help(user="tester", signature=signature, **args)
    assert result["filtered_by"] == "memory"
    assert "remember" in result["tools"]
    assert "recall" in result["tools"]
    assert "memory.save" not in result["tools"]


def test_tool_info_memory_read_returns_compat_suggestions():
    from app.tools.tool_help import tool_info

    args = {
        "tool_name": "memory.read",
        "include_schema": False,
    }
    signature = _sign("tool_info", args)
    result = tool_info(user="tester", signature=signature, **args)
    assert result["error"] == "unknown_tool"
    assert result["did_you_mean"][:3] == ["recall", "remember", "memory.save"]
    assert result["menu"]["tools"] == ["recall", "remember", "memory.save"]


def test_help_rich_entry_prefers_empty_args_for_menu():
    from app.tools.tool_help import help_tool

    args = {
        "tool_name": "help",
        "detail": "rich",
        "include_schema": True,
        "max_tools": 8,
    }
    signature = _sign("help", args)
    result = help_tool(user="tester", signature=signature, **args)
    entry = result["tools"][0]
    notes = " ".join(str(note) for note in entry.get("notes", []))
    assert "{}" in notes
    assert "ordinary discovery" in notes.lower()
    assert {} in entry.get("examples", [])


def test_tool_help_rich_entry_prefers_empty_args_for_menu():
    from app.tools.tool_help import tool_help

    args = {
        "tool_name": "tool_help",
        "detail": "rich",
        "include_schema": True,
        "max_tools": 8,
    }
    signature = _sign("tool_help", args)
    result = tool_help(user="tester", signature=signature, **args)
    entry = result["tools"][0]
    notes = " ".join(str(note) for note in entry.get("notes", []))
    assert "{}" in notes
    assert "ordinary browsing" in notes.lower()
    assert {} in entry.get("examples", [])


def test_tool_help_unknown_tool_returns_menu_and_failed_call():
    from app.tools.tool_help import tool_help

    args = {
        "tool_name": "zzz_not_a_tool",
        "detail": "names",
        "include_schema": False,
        "max_tools": 4,
        "failed_tool_name": "tool_info",
        "failed_args": {},
        "failed_error": "missing_tool",
    }
    signature = _sign("tool_help", args)
    result = tool_help(user="tester", signature=signature, **args)
    assert result["error"] == "unknown_tool"
    assert "compact menu" in result["message"].lower()
    assert result["failed_call"] == "tool_info() -> missing_tool"
    assert result["tools"] == ["help", "tool_help", "tool_info", "remember"]


def test_help_unknown_tool_still_returns_menu():
    from app.tools.tool_help import help_tool

    args = {
        "tool_name": "totally-not-real",
        "detail": "names",
        "include_schema": False,
        "max_tools": 3,
    }
    signature = _sign("help", args)
    result = help_tool(user="tester", signature=signature, **args)
    assert result["error"] == "unknown_tool"
    assert result["tools"] == ["help", "tool_help", "tool_info"]
    assert result["failed_call"] == "help(tool_name=totally-not-real) -> unknown_tool"


def test_tool_info_missing_tool_guides_discovery():
    from app.tools.tool_help import tool_info

    args = {
        "tool_name": "",
        "include_schema": False,
    }
    signature = _sign("tool_info", args)
    result = tool_info(user="tester", signature=signature, **args)
    assert result["error"] == "missing_tool"
    assert "help" in result["hint"]
    assert result["failed_call"] == "tool_info() -> missing_tool"
    assert result["menu"]["tools"][:3] == ["help", "tool_help", "tool_info"]


def test_tool_info_returns_single_catalog_entry():
    from app.tools.tool_help import tool_info

    args = {
        "tool_name": "list_dir",
        "include_schema": True,
    }
    signature = _sign("tool_info", args)
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
    signature = _sign("tool_info", args)
    result = tool_info(user="tester", signature=signature, **args)
    assert result["error"] == "unknown_tool"
    assert "write_file" in result.get("did_you_mean", [])
