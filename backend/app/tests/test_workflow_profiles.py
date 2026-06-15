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

    (repo_root / "repo-addon").mkdir()
    (repo_root / "repo-addon" / "config.json").write_text(
        '{"id": "repo-addon", "label": "Repo addon", "status": "live"}',
        encoding="utf-8",
    )
    (local_root / "local-addon").mkdir()
    (local_root / "local-addon" / "config.json").write_text(
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

    (repo_root / "shared").mkdir()
    (repo_root / "shared" / "config.json").write_text(
        '{"id": "shared", "label": "Repo label", "status": "live"}',
        encoding="utf-8",
    )
    (local_root / "shared").mkdir()
    (local_root / "shared" / "config.json").write_text(
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
            "path": str(local_root / "shared" / "config.json"),
            "config_path": str(local_root / "shared" / "config.json"),
            "package_path": str(local_root / "shared"),
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
    (local_root / "local-pack").mkdir()
    (local_root / "local-pack" / "config.json").write_text(
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
    assert module["config_path"] == str(local_root / "local-pack" / "config.json")
    assert module["package_path"] == str(local_root / "local-pack")
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


def test_import_addon_pack_previews_and_copies_skills(tmp_path, monkeypatch):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    source = tmp_path / "workspace" / "Hermes Pack"
    source.mkdir(parents=True)
    (source / "skills").mkdir()
    (source / "config.json").write_text(
        """
        {
          "id": "Hermes Pack",
          "label": "Hermes Pack",
          "modules": [
            {
              "id": "Hermes Agent",
              "skill_id": "Hermes Agent",
              "tool_names": ["hermes.plan"]
            }
          ]
        }
        """,
        encoding="utf-8",
    )
    (source / "skills" / "Hermes Agent.md").write_text(
        "Hermes skill summary\n\n# Hermes Agent\n",
        encoding="utf-8",
    )

    preview = workflow_profiles.import_addon_pack(str(source), dry_run=True)

    assert preview["status"] == "preview"
    assert preview["addon"]["id"] == "hermes_pack"
    assert preview["addon"]["module_ids"] == ["hermes_agent"]
    assert preview["addon"]["skill_ids"] == ["hermes_agent"]
    assert preview["skill_doc_count"] == 1
    assert preview["can_write"] is True

    imported = workflow_profiles.import_addon_pack(str(source), dry_run=False)

    local_config = (
        tmp_path / "data" / "modules" / "addons" / "hermes_pack" / "config.json"
    )
    local_skill = tmp_path / "data" / "modules" / "skills" / "hermes_agent.md"
    assert imported["status"] == "imported"
    assert local_config.exists()
    assert local_skill.read_text(encoding="utf-8").startswith("Hermes skill summary")
    payload = workflow_profiles.workflow_catalog_payload(
        enabled_modules=["hermes_agent"]
    )
    module = next(item for item in payload["modules"] if item["id"] == "hermes_agent")
    assert module["source"] == "custom"
    assert module["skill_available"] is True
    assert module["skill_source"] == "local"


def test_import_addon_pack_rejects_traversal_and_missing_config(tmp_path, monkeypatch):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    source = tmp_path / "workspace" / "bad-pack"
    source.mkdir(parents=True)

    with pytest.raises(ValueError, match="config.json"):
        workflow_profiles.import_addon_pack(str(source), dry_run=True)

    with pytest.raises(ValueError, match="not allowed"):
        workflow_profiles.import_addon_pack("../outside", dry_run=True)


def test_export_addon_pack_writes_local_pack_and_skill_docs(tmp_path, monkeypatch):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    local_addon = tmp_path / "data" / "modules" / "addons" / "custom_pack"
    local_skill = tmp_path / "data" / "modules" / "skills"
    local_addon.mkdir(parents=True)
    local_skill.mkdir(parents=True)
    (local_addon / "config.json").write_text(
        """
        {
          "id": "custom_pack",
          "modules": [{"id": "custom_mod", "skill_id": "custom_skill"}]
        }
        """,
        encoding="utf-8",
    )
    (local_skill / "custom_skill.md").write_text(
        "Custom skill summary\n\n# Custom Skill\n",
        encoding="utf-8",
    )
    destination = tmp_path / "workspace" / "exports"

    preview = workflow_profiles.export_addon_pack(
        "custom_pack",
        str(destination),
        dry_run=True,
    )

    assert preview["status"] == "preview"
    assert preview["skill_doc_count"] == 1
    assert preview["destination_path"] == str(destination / "custom_pack")

    exported = workflow_profiles.export_addon_pack(
        "custom_pack",
        str(destination),
        dry_run=False,
    )

    assert exported["status"] == "exported"
    assert (destination / "custom_pack" / "config.json").exists()
    assert (destination / "custom_pack" / "skills" / "custom_skill.md").exists()


def test_import_and_export_skill_markdown(tmp_path, monkeypatch):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    source = tmp_path / "workspace" / "Skill File.md"
    source.parent.mkdir(parents=True)
    source.write_text(
        "---\n"
        "name: skill-file\n"
        "description: Skill summary\n"
        "---\n\n"
        "# Skill\n",
        encoding="utf-8",
    )

    preview = workflow_profiles.import_skill_markdown(str(source), dry_run=True)

    assert preview["skill_id"] == "skill_file"
    assert preview["can_write"] is True

    imported = workflow_profiles.import_skill_markdown(str(source), dry_run=False)
    assert imported["status"] == "imported"
    assert imported["summary"] == "Skill summary"
    assert (tmp_path / "data" / "modules" / "skills" / "skill_file.md").exists()

    destination = tmp_path / "workspace" / "skill-export"
    exported = workflow_profiles.export_skill_markdown(
        "skill_file",
        str(destination),
        dry_run=False,
    )
    assert exported["status"] == "exported"
    assert (destination / "skill_file.md").read_text(encoding="utf-8").startswith("---")
