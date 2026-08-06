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
            "config_path": str(local_root / "shared.json"),
            "package_path": str(local_root),
            "source": "local",
        }
    ]


def test_local_module_roots_follow_float_data_dir(tmp_path, monkeypatch):
    from app import workflow_profiles

    repo_root = tmp_path / "repo"
    data_root = tmp_path / "portable-data"
    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", repo_root)
    monkeypatch.setenv("FLOAT_DATA_DIR", str(data_root))

    assert workflow_profiles.active_data_root() == data_root.resolve()
    assert (
        workflow_profiles.local_skills_root()
        == (data_root / "modules" / "skills").resolve()
    )
    assert (
        workflow_profiles.addons_root() == (data_root / "modules" / "addons").resolve()
    )


def test_canonical_addon_packages_preserve_local_precedence_and_module_details(
    tmp_path, monkeypatch
):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    repo_package = tmp_path / "modules" / "addons" / "shared"
    local_package = tmp_path / "data" / "modules" / "addons" / "shared"
    repo_package.mkdir(parents=True)
    local_package.mkdir(parents=True)
    (repo_package / "config.json").write_text(
        '{"id":"shared","label":"Repo","modules":[{"id":"shared_tool"}]}',
        encoding="utf-8",
    )
    (local_package / "config.json").write_text(
        """
        {
          "id": "shared",
          "label": "Local",
          "modules": [{
            "id": "shared_tool",
            "skill_id": "shared_skill",
            "tool_names": ["list_tasks"],
            "config": {"mode": "careful"},
            "assets": [{"path": "assets/reference.txt", "label": "Reference"}]
          }]
        }
        """,
        encoding="utf-8",
    )
    (local_package.parent / "shared.json").write_text(
        '{"id":"shared","label":"Legacy Local"}',
        encoding="utf-8",
    )

    addons = workflow_profiles.list_addons()
    modules = workflow_profiles.list_custom_modules()

    assert addons == [
        {
            "id": "shared",
            "label": "Local",
            "description": "",
            "status": "available",
            "path": str(local_package / "config.json"),
            "config_path": str(local_package / "config.json"),
            "package_path": str(local_package),
            "source": "local",
        }
    ]
    assert len(modules) == 1
    assert modules[0]["source"] == "custom"
    assert modules[0]["skill_id"] == "shared_skill"
    assert modules[0]["config"] == {"mode": "careful"}
    assert modules[0]["assets"] == [
        {"path": "assets/reference.txt", "label": "Reference"}
    ]
    assert workflow_profiles.tool_enabled_by_modules("list_tasks", []) is False
    assert (
        workflow_profiles.tool_enabled_by_modules("list_tasks", ["shared_tool"]) is True
    )


def test_custom_module_ids_omit_protected_and_casefold_ambiguous_claims(
    tmp_path, monkeypatch
):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("FLOAT_DATA_DIR", raising=False)
    repo_root = tmp_path / "modules" / "addons"
    local_root = tmp_path / "data" / "modules" / "addons"
    repo_root.mkdir(parents=True)
    local_root.mkdir(parents=True)
    (repo_root / "claims.json").write_text(
        """
        {
          "modules": [
            {"id": "Computer_Use", "tool_names": ["spoof.builtin"]},
            {"id": "CAMERA_CAPTURE", "tool_names": ["spoof.alias"]},
            {"id": "ReviewTools", "tool_names": ["spoof.upper"]},
            {"id": "duplicate_tools", "tool_names": ["spoof.duplicate_one"]},
            {"id": "safe_tools", "tool_names": ["safe.repo"]}
          ]
        }
        """,
        encoding="utf-8",
    )
    (repo_root / "other-claims.json").write_text(
        """
        {
          "modules": [
            {"id": "duplicate_tools", "tool_names": ["spoof.duplicate_two"]}
          ]
        }
        """,
        encoding="utf-8",
    )
    (local_root / "claims.json").write_text(
        """
        {
          "modules": [
            {"id": "reviewtools", "tool_names": ["spoof.lower"]},
            {"id": "safe_tools", "tool_names": ["safe.local"]}
          ]
        }
        """,
        encoding="utf-8",
    )

    custom_modules = workflow_profiles.list_custom_modules()
    catalog, rejected_tools = workflow_profiles.module_catalog_snapshot()

    assert [module["id"] for module in custom_modules] == ["safe_tools"]
    assert custom_modules[0]["source"] == "custom"
    assert custom_modules[0]["tool_names"] == ["safe.local"]
    assert [
        module for module in catalog if module["id"].casefold() == "computer_use"
    ] == [next(module for module in catalog if module["source"] == "base")]
    assert {
        "spoof.builtin",
        "spoof.alias",
        "spoof.upper",
        "spoof.lower",
        "spoof.duplicate_one",
        "spoof.duplicate_two",
    }.issubset(rejected_tools)
    assert (
        workflow_profiles.tool_enabled_by_modules(
            "spoof.builtin",
            ["computer_use"],
            module_catalog=catalog,
            rejected_tool_names=rejected_tools,
        )
        is False
    )
    assert (
        workflow_profiles.tool_enabled_by_modules(
            "spoof.alias",
            ["camera_capture"],
            module_catalog=catalog,
            rejected_tool_names=rejected_tools,
        )
        is False
    )
    assert (
        workflow_profiles.tool_enabled_by_modules(
            "shell.exec",
            ["camera_capture"],
            module_catalog=catalog,
            rejected_tool_names=rejected_tools,
        )
        is True
    )


def test_builtin_workflows_expose_truthful_runtime_metadata():
    from app import workflow_profiles
    from app.agent_workflows import build_workflow_metadata

    workflow = workflow_profiles.resolve_workflow_profile("architect_planner")

    assert workflow["role"] == "architect"
    assert workflow["profile_kind"] == "foreground"
    assert workflow["guidance_style"] == "planning"
    assert workflow["latency_tier"] == "deliberate"
    assert workflow["selectable_in_chat"] is True
    assert workflow["selectable_as_default"] is True
    assert workflow["automatic_delegation"] is False
    assert workflow["tool_scope"] == "global"
    assert workflow["module_scope"] == "global"
    assert workflow["enabled_modules"] == []
    assert "delegation_mode" not in workflow
    assert "preferred_continue" not in workflow

    serialized = build_workflow_metadata(workflow)
    assert serialized["guidance_style"] == "planning"
    assert serialized["automatic_delegation"] is False
    assert "delegation_mode" not in serialized
    assert "preferred_continue" not in serialized


def test_background_workflow_is_system_only_for_foreground_selection():
    from app import workflow_profiles

    workflow = workflow_profiles.resolve_workflow_profile("background_reflection")

    assert workflow["profile_kind"] == "system"
    assert workflow["guidance_style"] == "reflection"
    assert workflow["selectable_in_chat"] is False
    assert workflow["selectable_as_default"] is False
    assert workflow["supports_background"] is True
    assert (
        workflow_profiles.resolve_foreground_workflow_name("background_reflection")
        == "default"
    )
    assert (
        workflow_profiles.resolve_foreground_workflow_name("architect_planner")
        == "architect_planner"
    )


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


def test_skill_summary_uses_yaml_front_matter_description(tmp_path, monkeypatch):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    repo_root = tmp_path / "modules" / "skills"
    repo_root.mkdir(parents=True)
    (repo_root / "incident_triage.md").write_text(
        "---\nname: incident-triage\ndescription: 'Triage the highest-impact failure first.'\n---\n# Incident Triage\n",
        encoding="utf-8",
    )

    skill = workflow_profiles.list_skills()[0]

    assert skill["summary"] == "Triage the highest-impact failure first."


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


@pytest.mark.parametrize(
    ("skill_id", "reason_fragment"),
    [
        ("README", "reserved"),
        (".hidden", "start or end"),
        ("trailing.", "start or end"),
        ("CON.notes", "Windows"),
        ("x" * 121, "120"),
        ("bad skill", "letters, numbers"),
    ],
)
def test_new_skill_ids_follow_portable_filename_rules(
    tmp_path, monkeypatch, skill_id, reason_fragment
):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)

    reason = workflow_profiles.portable_skill_id_reason(skill_id)

    assert reason_fragment.casefold() in reason.casefold()
    with pytest.raises(ValueError):
        workflow_profiles.validate_new_skill_id(skill_id)


def test_new_skill_ids_reject_casefold_collisions_but_legacy_safe_ids_remain_readable(
    tmp_path, monkeypatch
):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    repo_root = tmp_path / "modules" / "skills"
    repo_root.mkdir(parents=True)
    (repo_root / "Incident_Triage.md").write_text("Legacy body", encoding="utf-8")
    (repo_root / ".legacy.md").write_text("Legacy hidden body", encoding="utf-8")

    assert workflow_profiles.get_skill_entry(".legacy", include_body=True)["body"] == (
        "Legacy hidden body"
    )
    with pytest.raises(workflow_profiles.SkillConflictError):
        workflow_profiles.validate_new_skill_id("incident_triage")


def test_existing_legacy_safe_local_skill_id_remains_editable(tmp_path, monkeypatch):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("FLOAT_DATA_DIR", raising=False)
    local_root = workflow_profiles.local_skills_root()
    local_root.mkdir(parents=True)
    legacy = local_root / ".legacy.md"
    legacy.write_text("Legacy body", encoding="utf-8")

    saved = workflow_profiles.write_local_skill_doc(".legacy", "Updated body")

    assert saved["active"]["body"] == "Updated body"
    assert legacy.read_text(encoding="utf-8") == "Updated body"


def test_skill_doc_payload_reports_links_and_local_rename_rules(tmp_path, monkeypatch):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    repo_root = tmp_path / "modules" / "skills"
    repo_root.mkdir(parents=True)
    (repo_root / "computer_use.md").write_text("Packaged body", encoding="utf-8")

    packaged = workflow_profiles.skill_doc_payload("computer_use")
    local_only = workflow_profiles.write_local_skill_doc(
        "incident_triage", "# Incident Triage\n"
    )

    assert packaged["linked_modules"] == [
        {
            "id": "computer_use",
            "label": "Computer Use",
            "source": "base",
            "addon_id": "",
        }
    ]
    assert packaged["rename_allowed"] is False
    assert "saved local" in packaged["rename_reason"]
    assert packaged["rename_block_reason"] == packaged["rename_reason"]
    assert local_only["linked_modules"] == []
    assert local_only["rename_allowed"] is True
    assert local_only["rename_reason"] == ""
    assert local_only["rename_block_reason"] == ""


def test_local_skill_rename_moves_one_unlinked_document_without_overwriting(
    tmp_path, monkeypatch
):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    workflow_profiles.write_local_skill_doc("incident_triage", "Exact body\n")
    repo_root = tmp_path / "modules" / "skills"
    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "Taken.md").write_text("Packaged collision", encoding="utf-8")

    renamed = workflow_profiles.rename_local_skill_doc(
        "incident_triage", "incident_response"
    )

    assert renamed["id"] == "incident_response"
    assert renamed["active"]["body"] == "Exact body"
    assert renamed["local_exists"] is True
    assert not (workflow_profiles.local_skills_root() / "incident_triage.md").exists()
    with pytest.raises(workflow_profiles.SkillConflictError):
        workflow_profiles.rename_local_skill_doc("incident_response", "taken")
    with pytest.raises(workflow_profiles.SkillConflictError, match="must differ"):
        workflow_profiles.rename_local_skill_doc(
            "incident_response", "INCIDENT_RESPONSE"
        )


def test_atomic_skill_create_fails_closed_when_posix_hardlinks_are_unavailable(
    tmp_path, monkeypatch
):
    from app import workflow_profiles

    target = tmp_path / "unsupported.md"

    def _unsupported_link(_source, _target):
        raise OSError("hard links are unavailable")

    monkeypatch.setattr(workflow_profiles, "_USE_WINDOWS_NO_REPLACE_RENAME", False)
    monkeypatch.setattr(workflow_profiles.os, "link", _unsupported_link)

    with pytest.raises(workflow_profiles.SkillStorageError, match="publish"):
        workflow_profiles._atomic_create_text(target, "Complete staged body")

    assert not target.exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_posix_create_receipt_cleanup_retries_before_delete(tmp_path, monkeypatch):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("FLOAT_DATA_DIR", raising=False)
    monkeypatch.setattr(workflow_profiles, "_USE_WINDOWS_NO_REPLACE_RENAME", False)
    monkeypatch.setattr(workflow_profiles, "_fsync_directory", lambda _path: None)
    monkeypatch.setattr(
        workflow_profiles, "_acquire_skill_file_lock", lambda _handle: None
    )
    monkeypatch.setattr(
        workflow_profiles, "_release_skill_file_lock", lambda _handle: None
    )
    workflow_profiles._SKILL_TEMP_CLEANUP_QUEUE.clear()
    root = workflow_profiles.local_skills_root()
    root.mkdir(parents=True)
    target = root / "cleanup.md"
    original_unlink = Path.unlink
    failures = {"remaining": 1}

    def _fail_temp_cleanup(path, *args, **kwargs):
        if (
            workflow_profiles._SKILL_RECEIPT_MARKER in path.name
            and failures["remaining"]
        ):
            failures["remaining"] -= 1
            raise PermissionError("scanner retained the staged file")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _fail_temp_cleanup)
    with workflow_profiles._skill_lifecycle_guard(root):
        workflow_profiles._atomic_create_text(target, "Published body")

    assert target.read_text(encoding="utf-8") == "Published body"
    receipts = list(root.glob(f"*{workflow_profiles._SKILL_RECEIPT_MARKER}*.tmp"))
    assert len(receipts) == 1
    assert workflow_profiles._SKILL_TEMP_CLEANUP_QUEUE

    workflow_profiles.delete_local_skill_doc("cleanup")

    assert not target.exists()
    assert not receipts[0].exists()
    assert workflow_profiles._SKILL_TEMP_CLEANUP_QUEUE == set()


def test_delete_fails_closed_when_associated_create_receipt_cannot_be_cleaned(
    tmp_path, monkeypatch
):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("FLOAT_DATA_DIR", raising=False)
    monkeypatch.setattr(workflow_profiles, "_USE_WINDOWS_NO_REPLACE_RENAME", False)
    monkeypatch.setattr(workflow_profiles, "_fsync_directory", lambda _path: None)
    monkeypatch.setattr(
        workflow_profiles, "_acquire_skill_file_lock", lambda _handle: None
    )
    monkeypatch.setattr(
        workflow_profiles, "_release_skill_file_lock", lambda _handle: None
    )
    workflow_profiles._SKILL_TEMP_CLEANUP_QUEUE.clear()
    root = workflow_profiles.local_skills_root()
    root.mkdir(parents=True)
    target = root / "cleanup.md"
    original_unlink = Path.unlink

    def _retain_receipt(path, *args, **kwargs):
        if workflow_profiles._SKILL_RECEIPT_MARKER in path.name:
            raise PermissionError("scanner retained the staged file")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _retain_receipt)
    with workflow_profiles._skill_lifecycle_guard(root):
        workflow_profiles._atomic_create_text(target, "Published body")
    receipts = list(root.glob(f"*{workflow_profiles._SKILL_RECEIPT_MARKER}*.tmp"))

    with pytest.raises(workflow_profiles.SkillStorageError, match="prior save receipt"):
        workflow_profiles.delete_local_skill_doc("cleanup")

    assert target.read_text(encoding="utf-8") == "Published body"
    assert len(receipts) == 1
    assert receipts[0].exists()
    workflow_profiles._SKILL_TEMP_CLEANUP_QUEUE.clear()
    original_unlink(receipts[0])


def test_local_skill_rename_does_not_replace_target_created_after_validation(
    tmp_path, monkeypatch
):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("FLOAT_DATA_DIR", raising=False)
    workflow_profiles.write_local_skill_doc("source", "Source body")
    root = workflow_profiles.local_skills_root()
    source = root / "source.md"
    target = root / "target.md"

    if workflow_profiles._USE_WINDOWS_NO_REPLACE_RENAME:
        original_publish = workflow_profiles.os.rename

        def _racing_publish(published_source, published_target):
            if Path(published_source) == source and Path(published_target) == target:
                target.write_text("Competing body", encoding="utf-8")
            return original_publish(published_source, published_target)

        monkeypatch.setattr(workflow_profiles.os, "rename", _racing_publish)
    else:
        original_publish = workflow_profiles.os.link

        def _racing_publish(published_source, published_target):
            if Path(published_source) == source and Path(published_target) == target:
                target.write_text("Competing body", encoding="utf-8")
            return original_publish(published_source, published_target)

        monkeypatch.setattr(workflow_profiles.os, "link", _racing_publish)

    with pytest.raises(workflow_profiles.SkillConflictError, match="already exists"):
        workflow_profiles.rename_local_skill_doc("source", "target")

    assert source.read_text(encoding="utf-8") == "Source body"
    assert target.read_text(encoding="utf-8") == "Competing body"


def test_local_skill_rename_revalidates_casefold_collisions_inside_guard(
    tmp_path, monkeypatch
):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("FLOAT_DATA_DIR", raising=False)
    workflow_profiles.write_local_skill_doc("source", "Source body")
    root = workflow_profiles.local_skills_root()
    original_retry = workflow_profiles._retry_skill_temp_cleanup
    injected = {"done": False}

    def _inject_collision(guarded_root):
        original_retry(guarded_root)
        if not injected["done"]:
            injected["done"] = True
            (guarded_root / "TARGET.md").write_text("Competing body", encoding="utf-8")

    monkeypatch.setattr(
        workflow_profiles, "_retry_skill_temp_cleanup", _inject_collision
    )

    with pytest.raises(workflow_profiles.SkillConflictError, match="already exists"):
        workflow_profiles.rename_local_skill_doc("source", "target")

    assert (root / "source.md").read_text(encoding="utf-8") == "Source body"
    assert (root / "TARGET.md").read_text(encoding="utf-8") == "Competing body"


def test_posix_rename_intent_recovery_finishes_samefile_publication(
    tmp_path, monkeypatch
):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles, "_USE_WINDOWS_NO_REPLACE_RENAME", False)
    monkeypatch.setattr(workflow_profiles, "_fsync_directory", lambda _path: None)
    monkeypatch.setattr(
        workflow_profiles, "_acquire_skill_file_lock", lambda _handle: None
    )
    monkeypatch.setattr(
        workflow_profiles, "_release_skill_file_lock", lambda _handle: None
    )
    root = tmp_path / "skills"
    root.mkdir()
    source = root / "source.md"
    target = root / "target.md"
    source.write_text("Source body", encoding="utf-8")
    marker = workflow_profiles._write_rename_intent(root, source, target)
    workflow_profiles.os.link(source, target)

    with workflow_profiles._skill_lifecycle_guard(root):
        pass

    assert not source.exists()
    assert target.read_text(encoding="utf-8") == "Source body"
    assert not marker.exists()


@pytest.mark.parametrize(
    "receipt_identity",
    ["samefile", "replaced_target"],
)
def test_next_guard_immediately_cleans_crash_receipts_before_overwrite(
    tmp_path, monkeypatch, receipt_identity
):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("FLOAT_DATA_DIR", raising=False)
    workflow_profiles.write_local_skill_doc("overwrite", "Original body")
    root = workflow_profiles.local_skills_root()
    target = root / "overwrite.md"
    receipt = root / (
        f".{target.name}{workflow_profiles._SKILL_RECEIPT_MARKER}crash.tmp"
    )
    workflow_profiles.os.link(target, receipt)
    if receipt_identity == "replaced_target":
        replacement = root / ".external-replacement.tmp"
        replacement.write_text("Intervening body", encoding="utf-8")
        workflow_profiles.os.replace(replacement, target)
        assert receipt.read_text(encoding="utf-8") == "Original body"
        assert not workflow_profiles.os.path.samefile(receipt, target)

    workflow_profiles.write_local_skill_doc("overwrite", "Final body")

    assert target.read_text(encoding="utf-8") == "Final body"
    assert not receipt.exists()


@pytest.mark.parametrize(
    "operation",
    ["create", "update", "delete", "rename_source", "rename_target"],
)
def test_lifecycle_mutations_fail_closed_when_associated_receipt_cleanup_fails(
    tmp_path, monkeypatch, operation
):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("FLOAT_DATA_DIR", raising=False)
    workflow_profiles._SKILL_TEMP_CLEANUP_QUEUE.clear()
    root = workflow_profiles.local_skills_root()
    root.mkdir(parents=True, exist_ok=True)
    if operation in {"update", "delete"}:
        workflow_profiles.write_local_skill_doc("blocked", "Original body")
    elif operation.startswith("rename"):
        workflow_profiles.write_local_skill_doc("source", "Source body")

    receipt_target = {
        "create": root / "blocked.md",
        "update": root / "blocked.md",
        "delete": root / "blocked.md",
        "rename_source": root / "source.md",
        "rename_target": root / "target.md",
    }[operation]
    receipt = root / (
        f".{receipt_target.name}"
        f"{workflow_profiles._SKILL_RECEIPT_MARKER}blocked.tmp"
    )
    receipt.write_text("Retained staging body", encoding="utf-8")
    original_unlink = Path.unlink

    def _fail_receipt_unlink(path, *args, **kwargs):
        if Path(path) == receipt:
            raise PermissionError("receipt is still open")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _fail_receipt_unlink)

    with pytest.raises(workflow_profiles.SkillStorageError, match="prior save receipt"):
        if operation == "create":
            workflow_profiles.write_local_skill_doc(
                "blocked", "New body", create_only=True
            )
        elif operation == "update":
            workflow_profiles.write_local_skill_doc("blocked", "New body")
        elif operation == "delete":
            workflow_profiles.delete_local_skill_doc("blocked")
        else:
            workflow_profiles.rename_local_skill_doc("source", "target")

    assert receipt.exists()
    if operation == "create":
        assert not (root / "blocked.md").exists()
    elif operation in {"update", "delete"}:
        assert (root / "blocked.md").read_text(encoding="utf-8") == "Original body"
    else:
        assert (root / "source.md").read_text(encoding="utf-8") == "Source body"
        assert not (root / "target.md").exists()


@pytest.mark.parametrize(
    "operation",
    ["create", "update", "delete", "rename_source", "rename_target"],
)
def test_case_variant_receipt_cleanup_failure_cannot_bypass_skill_identity(
    tmp_path, monkeypatch, operation
):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("FLOAT_DATA_DIR", raising=False)
    workflow_profiles._SKILL_TEMP_CLEANUP_QUEUE.clear()
    root = workflow_profiles.local_skills_root()
    root.mkdir(parents=True, exist_ok=True)
    if operation in {"update", "delete"}:
        workflow_profiles.write_local_skill_doc("blocked", "Original body")
    elif operation.startswith("rename"):
        workflow_profiles.write_local_skill_doc("source", "Source body")

    receipt_stem = {
        "create": "Blocked",
        "update": "Blocked",
        "delete": "Blocked",
        "rename_source": "Source",
        "rename_target": "Target",
    }[operation]
    receipt = root / (
        f".{receipt_stem}.md"
        f"{workflow_profiles._SKILL_RECEIPT_MARKER}case-variant.tmp"
    )
    receipt.write_text("Retained case-variant staging body", encoding="utf-8")
    original_unlink = Path.unlink

    def _retain_case_variant_receipt(path, *args, **kwargs):
        if Path(path) == receipt:
            raise PermissionError("case-variant receipt is still open")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _retain_case_variant_receipt)

    with pytest.raises(workflow_profiles.SkillStorageError, match="prior save receipt"):
        if operation == "create":
            workflow_profiles.write_local_skill_doc(
                "blocked", "New body", create_only=True
            )
        elif operation == "update":
            workflow_profiles.write_local_skill_doc("blocked", "New body")
        elif operation == "delete":
            workflow_profiles.delete_local_skill_doc("blocked")
        else:
            workflow_profiles.rename_local_skill_doc("source", "target")

    assert receipt.read_text(encoding="utf-8") == ("Retained case-variant staging body")
    if operation == "create":
        assert not (root / "blocked.md").exists()
    elif operation in {"update", "delete"}:
        assert (root / "blocked.md").read_text(encoding="utf-8") == "Original body"
    else:
        assert (root / "source.md").read_text(encoding="utf-8") == "Source body"
        assert not (root / "target.md").exists()


def test_windows_failed_create_temp_is_reconciled_on_next_guard(tmp_path, monkeypatch):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("FLOAT_DATA_DIR", raising=False)
    monkeypatch.setattr(workflow_profiles, "_USE_WINDOWS_NO_REPLACE_RENAME", True)
    monkeypatch.setattr(
        workflow_profiles, "_acquire_skill_file_lock", lambda _handle: None
    )
    monkeypatch.setattr(
        workflow_profiles, "_release_skill_file_lock", lambda _handle: None
    )
    workflow_profiles._SKILL_TEMP_CLEANUP_QUEUE.clear()
    original_rename = workflow_profiles.os.rename
    original_unlink = Path.unlink
    failures = {"rename": 1, "unlink": 1}

    def _fail_publish_once(source, target):
        if (
            workflow_profiles._SKILL_RECEIPT_MARKER in Path(source).name
            and failures["rename"]
        ):
            failures["rename"] -= 1
            raise PermissionError("publication interrupted")
        return original_rename(source, target)

    def _fail_cleanup_once(path, *args, **kwargs):
        if workflow_profiles._SKILL_RECEIPT_MARKER in path.name and failures["unlink"]:
            failures["unlink"] -= 1
            raise PermissionError("temporary file is still open")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(workflow_profiles.os, "rename", _fail_publish_once)
    monkeypatch.setattr(Path, "unlink", _fail_cleanup_once)

    with pytest.raises(workflow_profiles.SkillStorageError, match="publish"):
        workflow_profiles.write_local_skill_doc(
            "windows_retry", "First body", create_only=True
        )
    root = workflow_profiles.local_skills_root()
    assert list(root.glob(f"*{workflow_profiles._SKILL_RECEIPT_MARKER}*.tmp"))

    saved = workflow_profiles.write_local_skill_doc(
        "windows_retry", "Second body", create_only=True
    )

    assert saved["active"]["body"] == "Second body"
    assert list(root.glob(f"*{workflow_profiles._SKILL_RECEIPT_MARKER}*.tmp")) == []
    assert workflow_profiles._SKILL_TEMP_CLEANUP_QUEUE == set()


def test_rename_intent_is_atomically_published_from_unscanned_stage(
    tmp_path, monkeypatch
):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles, "_USE_WINDOWS_NO_REPLACE_RENAME", False)
    monkeypatch.setattr(workflow_profiles, "_fsync_directory", lambda _path: None)
    root = tmp_path / "skills"
    root.mkdir()
    source = root / "source.md"
    target = root / "target.md"
    original_replace = workflow_profiles.os.replace
    observed = {"published": False}

    def _inspect_atomic_publish(staging, marker):
        staging = Path(staging)
        marker = Path(marker)
        assert staging.name.startswith(workflow_profiles._SKILL_RENAME_STAGE_PREFIX)
        assert marker.name.startswith(workflow_profiles._SKILL_RENAME_INTENT_PREFIX)
        assert (
            list(root.glob(f"{workflow_profiles._SKILL_RENAME_INTENT_PREFIX}*.json"))
            == []
        )
        observed["published"] = True
        return original_replace(staging, marker)

    monkeypatch.setattr(workflow_profiles.os, "replace", _inspect_atomic_publish)

    marker = workflow_profiles._write_rename_intent(root, source, target)

    assert observed["published"] is True
    assert marker.read_text(encoding="utf-8") == (
        '{"source": "source.md", "target": "target.md"}'
    )
    assert list(root.glob(f"{workflow_profiles._SKILL_RENAME_STAGE_PREFIX}*.tmp")) == []


@pytest.mark.parametrize("marker_state", ["malformed", "different", "missing"])
def test_non_actionable_rename_intents_are_quarantined_without_wedging_store(
    tmp_path, monkeypatch, marker_state
):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("FLOAT_DATA_DIR", raising=False)
    monkeypatch.setattr(workflow_profiles, "_USE_WINDOWS_NO_REPLACE_RENAME", False)
    monkeypatch.setattr(workflow_profiles, "_fsync_directory", lambda _path: None)
    monkeypatch.setattr(
        workflow_profiles, "_acquire_skill_file_lock", lambda _handle: None
    )
    monkeypatch.setattr(
        workflow_profiles, "_release_skill_file_lock", lambda _handle: None
    )
    root = workflow_profiles.local_skills_root()
    root.mkdir(parents=True)
    marker = root / f"{workflow_profiles._SKILL_RENAME_INTENT_PREFIX}crash.json"
    source = root / "source.md"
    target = root / "target.md"
    if marker_state == "malformed":
        marker.write_text('{"source":', encoding="utf-8")
    else:
        marker.write_text(
            '{"source": "source.md", "target": "target.md"}',
            encoding="utf-8",
        )
    if marker_state == "different":
        source.write_text("Source body", encoding="utf-8")
        target.write_text("Target body", encoding="utf-8")

    saved = workflow_profiles.write_local_skill_doc("unrelated", "Unrelated body")

    assert saved["active"]["body"] == "Unrelated body"
    assert not marker.exists()
    quarantined = list(
        root.glob(f"{workflow_profiles._SKILL_RENAME_QUARANTINE_PREFIX}*.json")
    )
    assert len(quarantined) == 1
    if marker_state == "different":
        assert source.read_text(encoding="utf-8") == "Source body"
        assert target.read_text(encoding="utf-8") == "Target body"
    else:
        assert not source.exists()
        assert not target.exists()


def test_rename_collision_marker_cleanup_failure_is_storage_error(
    tmp_path, monkeypatch
):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("FLOAT_DATA_DIR", raising=False)
    workflow_profiles.write_local_skill_doc("source", "Source body")
    root = workflow_profiles.local_skills_root()
    source = root / "source.md"
    target = root / "target.md"
    monkeypatch.setattr(workflow_profiles, "_USE_WINDOWS_NO_REPLACE_RENAME", False)
    monkeypatch.setattr(workflow_profiles, "_fsync_directory", lambda _path: None)
    monkeypatch.setattr(
        workflow_profiles, "_acquire_skill_file_lock", lambda _handle: None
    )
    monkeypatch.setattr(
        workflow_profiles, "_release_skill_file_lock", lambda _handle: None
    )
    original_link = workflow_profiles.os.link
    original_unlink = Path.unlink

    def _publish_into_race(published_source, published_target):
        if Path(published_source) == source and Path(published_target) == target:
            target.write_text("Competing body", encoding="utf-8")
        return original_link(published_source, published_target)

    def _retain_marker(path, *args, **kwargs):
        if path.name.startswith(workflow_profiles._SKILL_RENAME_INTENT_PREFIX):
            raise PermissionError("rename marker is still open")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(workflow_profiles.os, "link", _publish_into_race)
    monkeypatch.setattr(Path, "unlink", _retain_marker)

    with pytest.raises(workflow_profiles.SkillStorageError, match="conflicted"):
        workflow_profiles.rename_local_skill_doc("source", "target")

    assert source.read_text(encoding="utf-8") == "Source body"
    assert target.read_text(encoding="utf-8") == "Competing body"
    assert (
        len(list(root.glob(f"{workflow_profiles._SKILL_RENAME_INTENT_PREFIX}*.json")))
        == 1
    )


def test_get_skill_entry_rejects_symlink_reads(tmp_path, monkeypatch):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    local_root = workflow_profiles.local_skills_root()
    local_root.mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    link = local_root / "linked.md"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation is unavailable on this Windows host.")

    with pytest.raises(ValueError, match="Symlink"):
        workflow_profiles.get_skill_entry("linked", include_body=True)
