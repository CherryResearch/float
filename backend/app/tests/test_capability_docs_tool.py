import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def add_backend_to_sys_path():
    backend_dir = Path(__file__).resolve().parents[2]
    backend_dir = str(backend_dir)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)


def _sign(tool_name: str, payload: dict) -> str:
    from app.tools.capability_docs import _normalize_scope
    from app.utils import generate_signature

    normalized = {
        "action": str(payload.get("action") or "").strip().lower()
        or (
            "search"
            if str(payload.get("query") or "").strip()
            else "read"
            if str(payload.get("doc_id") or payload.get("path") or "").strip()
            else "list"
        ),
        "scope": _normalize_scope(payload.get("scope")),
        "doc_id": str(payload.get("doc_id") or ""),
        "path": str(payload.get("path") or ""),
        "query": str(payload.get("query") or ""),
        "start_line": int(payload.get("start_line") or 1),
        "line_count": int(payload.get("line_count") or 200),
        "max_chars": int(payload.get("max_chars") or 12000),
        "limit": int(payload.get("limit") or 20),
    }
    return generate_signature("tester", tool_name, normalized)


def test_read_capability_docs_lists_curated_docs(tmp_path, monkeypatch):
    from app import workflow_profiles
    from app.tools.capability_docs import read_capability_docs
    from app.utils import user_settings

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        user_settings,
        "load_settings",
        lambda: {"enabled_workflow_modules": []},
    )
    skill_root = tmp_path / "modules" / "skills"
    docs_root = tmp_path / "docs" / "function descriptions"
    skill_root.mkdir(parents=True, exist_ok=True)
    docs_root.mkdir(parents=True, exist_ok=True)
    (skill_root / "computer_use.md").write_text(
        "Skill summary\n\n# Computer Use\n- Observe first.\n",
        encoding="utf-8",
    )
    (docs_root / "memory.md").write_text(
        "Memory summary\n\n# Memory\n- Durable notes.\n",
        encoding="utf-8",
    )

    args = {"action": "list", "scope": "all", "limit": 10}
    result = read_capability_docs(
        user="tester",
        signature=_sign("read_capability_docs", args),
        **args,
    )

    assert result["action"] == "list"
    doc_ids = [item["id"] for item in result["docs"]]
    assert "skills:computer_use" in doc_ids
    assert "function_descriptions:memory" in doc_ids
    computer_doc = next(
        item for item in result["docs"] if item["id"] == "skills:computer_use"
    )
    assert computer_doc["module_id"] == "computer_use"
    assert computer_doc["module_source"] == "base"
    assert computer_doc["module_enabled"] is False
    assert "computer.observe" in computer_doc["module_tool_names"]


def test_read_capability_docs_marks_enabled_module(tmp_path, monkeypatch):
    from app import workflow_profiles
    from app.tools.capability_docs import read_capability_docs
    from app.utils import user_settings

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        user_settings,
        "load_settings",
        lambda: {"enabled_workflow_modules": ["computer_use"]},
    )
    skill_root = tmp_path / "modules" / "skills"
    skill_root.mkdir(parents=True, exist_ok=True)
    (skill_root / "computer_use.md").write_text(
        "Skill summary\n\n# Computer Use\n- Observe first.\n",
        encoding="utf-8",
    )

    args = {"action": "read", "scope": "skills", "doc_id": "skills:computer_use"}
    result = read_capability_docs(
        user="tester",
        signature=_sign("read_capability_docs", args),
        **args,
    )

    assert result["doc"]["module_id"] == "computer_use"
    assert result["doc"]["module_enabled"] is True


def test_read_capability_docs_searches_function_descriptions(tmp_path, monkeypatch):
    from app import workflow_profiles
    from app.tools.capability_docs import read_capability_docs

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    docs_root = tmp_path / "docs" / "function descriptions"
    docs_root.mkdir(parents=True, exist_ok=True)
    (docs_root / "memory.md").write_text(
        "# Memory\n\nUsers can pin durable notes and search them later.\n",
        encoding="utf-8",
    )

    args = {
        "action": "search",
        "scope": "function_descriptions",
        "query": "durable notes",
        "limit": 10,
    }
    result = read_capability_docs(
        user="tester",
        signature=_sign("read_capability_docs", args),
        **args,
    )

    assert result["action"] == "search"
    assert result["count"] == 1
    assert result["docs"][0]["id"] == "function_descriptions:memory"


def test_read_capability_docs_reads_local_skill_override(tmp_path, monkeypatch):
    from app import workflow_profiles
    from app.tools.capability_docs import read_capability_docs

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    repo_root = tmp_path / "modules" / "skills"
    local_root = tmp_path / "data" / "modules" / "skills"
    repo_root.mkdir(parents=True, exist_ok=True)
    local_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "computer_use.md").write_text(
        "Repo summary\n\n# Computer Use\n- Repo body.\n",
        encoding="utf-8",
    )
    (local_root / "computer_use.md").write_text(
        "Local summary\n\n# Computer Use\n- Local override body.\n",
        encoding="utf-8",
    )

    args = {
        "action": "read",
        "scope": "skills",
        "doc_id": "skills:computer_use",
        "start_line": 1,
        "line_count": 20,
        "max_chars": 500,
    }
    result = read_capability_docs(
        user="tester",
        signature=_sign("read_capability_docs", args),
        **args,
    )

    assert result["action"] == "read"
    assert result["doc"]["source"] == "local"
    assert "Local override body." in result["content"]
