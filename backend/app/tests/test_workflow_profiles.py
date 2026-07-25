import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def add_backend_to_sys_path():
    backend_dir = Path(__file__).resolve().parents[2]
    backend_dir = str(backend_dir)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)


def test_list_addons_reads_repo_and_local_roots(tmp_path, monkeypatch):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    repo_root = tmp_path / "modules" / "addons"
    local_root = tmp_path / "data" / "modules" / "addons"
    repo_root.mkdir(parents=True, exist_ok=True)
    local_root.mkdir(parents=True, exist_ok=True)

    (repo_root / "repo-addon.json").write_text(
        '{"id": "repo-addon", "label": "Repo addon", "status": "live"}',
        encoding="utf-8",
    )
    (local_root / "local-addon.json").write_text(
        '{"id": "local-addon", "label": "Local addon", "status": "experimental"}',
        encoding="utf-8",
    )

    addons = workflow_profiles.list_addons()

    assert [item["id"] for item in addons] == ["local-addon", "repo-addon"]
    assert (
        next(item for item in addons if item["id"] == "repo-addon")["source"] == "repo"
    )
    assert (
        next(item for item in addons if item["id"] == "local-addon")["source"]
        == "local"
    )


def test_list_addons_prefers_local_override_for_duplicate_ids(tmp_path, monkeypatch):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    repo_root = tmp_path / "modules" / "addons"
    local_root = tmp_path / "data" / "modules" / "addons"
    repo_root.mkdir(parents=True, exist_ok=True)
    local_root.mkdir(parents=True, exist_ok=True)

    (repo_root / "shared.json").write_text(
        '{"id": "shared", "label": "Repo label", "status": "live"}',
        encoding="utf-8",
    )
    (local_root / "shared.json").write_text(
        '{"id": "shared", "label": "Local label", "status": "experimental"}',
        encoding="utf-8",
    )

    addons = workflow_profiles.list_addons()

    assert addons == [
        {
            "id": "shared",
            "label": "Local label",
            "description": "",
            "status": "experimental",
            "path": str(local_root / "shared.json"),
            "source": "local",
        }
    ]


def test_builtin_workflows_expose_role_metadata():
    from app import workflow_profiles

    workflow = workflow_profiles.resolve_workflow_profile("architect_planner")

    assert workflow["role"] == "architect"
    assert workflow["latency_tier"] == "deliberate"
    assert workflow["delegation_mode"] == "delegate"


def test_list_skills_prefers_local_override_for_duplicate_ids(tmp_path, monkeypatch):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    repo_root = tmp_path / "modules" / "skills"
    local_root = tmp_path / "data" / "modules" / "skills"
    repo_root.mkdir(parents=True, exist_ok=True)
    local_root.mkdir(parents=True, exist_ok=True)

    (repo_root / "computer_use.md").write_text(
        "Repo summary\n\n# Computer Use\n- Repo body\n",
        encoding="utf-8",
    )
    (local_root / "computer_use.md").write_text(
        "Local summary\n\n# Computer Use\n- Local body\n",
        encoding="utf-8",
    )

    skills = workflow_profiles.list_skills()

    assert skills == [
        {
            "id": "computer_use",
            "label": "computer use",
            "summary": "Local summary",
            "path": str(local_root / "computer_use.md"),
            "source": "local",
        }
    ]


def test_workflow_prompt_lists_enabled_modules_without_inlining_skill_bodies(
    tmp_path, monkeypatch
):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    repo_root = tmp_path / "modules" / "skills"
    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "computer_use.md").write_text(
        "Use this skill for browser tasks.\n\n# Computer Use\n- Keep steps small.\n",
        encoding="utf-8",
    )

    prompt = workflow_profiles.workflow_prompt(
        "default",
        modules=["computer_use"],
        include_default_modules=False,
    )

    assert "Enabled modules this turn: Computer Use." in prompt
    assert "Module guidance from packaged markdown skills:" not in prompt
    assert "Use this skill for browser tasks." not in prompt


def test_resolve_modules_normalizes_legacy_module_aliases():
    from app import workflow_profiles

    modules = workflow_profiles.resolve_modules(
        "default",
        ["camera_capture", "memory_promotion", "host_shell"],
    )

    assert modules == ["computer_use"]


def test_resolve_modules_can_ignore_workflow_defaults():
    from app import workflow_profiles

    modules = workflow_profiles.resolve_modules(
        "default",
        [],
        include_workflow_defaults=False,
    )

    assert modules == []


def test_workflow_catalog_payload_includes_module_skill_metadata(tmp_path, monkeypatch):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    repo_root = tmp_path / "modules" / "skills"
    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "computer_use.md").write_text(
        "Browser summary\n\n# Computer Use\n- Observe before acting.\n",
        encoding="utf-8",
    )

    payload = workflow_profiles.workflow_catalog_payload(
        enabled_modules=["computer_use"]
    )

    module = next(item for item in payload["modules"] if item["id"] == "computer_use")
    assert module["skill_id"] == "computer_use"
    assert module["source"] == "base"
    assert module["enabled"] is True
    assert module["doc_id"] == "skills:computer_use"
    assert module["skill_available"] is True
    assert module["skill_summary"] == "Browser summary"
    assert module["skill_path"] == str(repo_root / "computer_use.md")
    assert "camera.capture" in module["tool_names"]
    assert payload["skills_root"] == str(repo_root)


def test_workflow_catalog_payload_includes_custom_addon_modules(tmp_path, monkeypatch):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    local_root = tmp_path / "data" / "modules" / "addons"
    local_root.mkdir(parents=True, exist_ok=True)
    (local_root / "local-pack.json").write_text(
        """
        {
          "id": "local-pack",
          "label": "Local Pack",
          "modules": [
            {
              "id": "container_orchestration",
              "label": "Container Orchestration",
              "description": "Manage local containers.",
              "status": "experimental",
              "tool_names": ["containers.list", "containers.start"]
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    payload = workflow_profiles.workflow_catalog_payload(
        enabled_modules=["container_orchestration"]
    )

    module = next(
        item for item in payload["modules"] if item["id"] == "container_orchestration"
    )
    assert module["source"] == "custom"
    assert module["enabled"] is True
    assert module["addon_id"] == "local-pack"
    assert module["tool_names"] == ["containers.list", "containers.start"]
    assert workflow_profiles.normalize_module_id("container_orchestration") == (
        "container_orchestration"
    )
    assert workflow_profiles.tool_module_ids("containers.start") == [
        "container_orchestration"
    ]


def test_local_skill_doc_write_and_delete_preserves_repo_base(tmp_path, monkeypatch):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    repo_root = tmp_path / "modules" / "skills"
    repo_root.mkdir(parents=True, exist_ok=True)
    repo_skill = repo_root / "computer_use.md"
    repo_skill.write_text("Repo summary\n\n# Base\n", encoding="utf-8")

    saved = workflow_profiles.write_local_skill_doc(
        "computer_use",
        "Local summary\n\n# Override\n",
    )

    assert saved["local_exists"] is True
    assert saved["repo_exists"] is True
    assert saved["active"]["source"] == "local"
    assert saved["active"]["body"] == "Local summary\n\n# Override"
    assert repo_skill.read_text(encoding="utf-8") == "Repo summary\n\n# Base\n"

    deleted = workflow_profiles.delete_local_skill_doc("computer_use")

    assert deleted["local_exists"] is False
    assert deleted["repo_exists"] is True
    assert deleted["active"]["source"] == "repo"
    assert deleted["active"]["body"] == "Repo summary\n\n# Base"


def test_skill_doc_rejects_path_traversal_ids(tmp_path, monkeypatch):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)

    assert workflow_profiles.normalize_skill_id("../bad") == ""
    with pytest.raises(ValueError):
        workflow_profiles.write_local_skill_doc("../bad", "Nope")
