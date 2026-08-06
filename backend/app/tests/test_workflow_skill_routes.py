import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def add_backend_to_sys_path():
    backend_dir = Path(__file__).resolve().parents[2]
    backend_dir = str(backend_dir)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)


def test_workflow_skill_doc_routes_manage_local_override(tmp_path, monkeypatch):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    repo_root = tmp_path / "modules" / "skills"
    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "computer_use.md").write_text(
        "Repo summary\n\n# Computer Use\n",
        encoding="utf-8",
    )

    app = importlib.import_module("app.main").app
    client = TestClient(app)

    initial = client.get("/api/workflows/skills/computer_use")
    assert initial.status_code == 200
    assert initial.json()["active"]["source"] == "repo"

    saved = client.put(
        "/api/workflows/skills/computer_use",
        json={"body": "Local summary\n\n# Local Computer Use\n"},
    )
    assert saved.status_code == 200
    saved_payload = saved.json()
    assert saved_payload["local_exists"] is True
    assert saved_payload["active"]["source"] == "local"
    assert saved_payload["active"]["body"] == "Local summary\n\n# Local Computer Use"

    deleted = client.delete("/api/workflows/skills/computer_use")
    assert deleted.status_code == 200
    deleted_payload = deleted.json()
    assert deleted_payload["local_exists"] is False
    assert deleted_payload["active"]["source"] == "repo"


def test_workflow_skill_doc_route_returns_blank_payload_for_missing_valid_skill(
    tmp_path, monkeypatch
):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)

    app = importlib.import_module("app.main").app
    client = TestClient(app)

    response = client.get("/api/workflows/skills/incident_triage")

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "incident_triage"
    assert payload["repo_exists"] is False
    assert payload["local_exists"] is False
    assert payload["active"] is None
    assert (
        payload["local_path"]
        .replace("\\", "/")
        .endswith("data/modules/skills/incident_triage.md")
    )


def test_workflow_skill_doc_routes_reject_invalid_ids(tmp_path, monkeypatch):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)

    app = importlib.import_module("app.main").app
    client = TestClient(app)

    assert client.get("/api/workflows/skills/bad$id").status_code == 400
    assert (
        client.put(
            "/api/workflows/skills/bad$id",
            json={"body": "Nope"},
        ).status_code
        == 400
    )
    assert client.post("/api/workflows/skills/bad$id/draft", json={}).status_code == 400


def test_skill_duplicate_and_import_previews_are_no_write_drafts(tmp_path, monkeypatch):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    repo_root = tmp_path / "modules" / "skills"
    repo_root.mkdir(parents=True)
    (repo_root / "incident_triage.md").write_text(
        "# Incident Triage\n\nInspect impact first.\n",
        encoding="utf-8",
    )

    app = importlib.import_module("app.main").app
    client = TestClient(app)

    duplicate = client.post(
        "/api/workflows/skills/incident_triage/duplicate-preview",
        json={"target_id": "incident_response"},
    )
    imported = client.post(
        "/api/workflows/skills/import-preview",
        json={
            "filename": "review_checklist.md",
            "body": "# Review Checklist\n\n- Verify the result.\n",
            "target_id": "review_checklist",
        },
    )

    assert duplicate.status_code == 200
    assert duplicate.json() == {
        "status": "drafted",
        "source_id": "incident_triage",
        "target_id": "incident_response",
        "document": {
            "id": "incident_response",
            "doc_id": "skills:incident_response",
            "repo_path": str(repo_root / "incident_response.md"),
            "local_path": str(
                tmp_path / "data" / "modules" / "skills" / "incident_response.md"
            ),
            "repo_exists": False,
            "local_exists": False,
            "active": None,
            "linked_modules": [],
            "rename_allowed": False,
            "rename_reason": "Only saved local skill documents can be renamed.",
            "rename_block_reason": "Only saved local skill documents can be renamed.",
        },
        "proposal": {
            "body": "# Incident Triage\n\nInspect impact first.",
            "source": "duplicate",
            "requires_user_save": True,
            "save_mode": "create_only",
        },
        "audit": {"wrote_skill_file": False},
    }
    assert imported.status_code == 200
    assert imported.json()["status"] == "drafted"
    assert imported.json()["target_id"] == "review_checklist"
    assert imported.json()["proposal"] == {
        "body": "# Review Checklist\n\n- Verify the result.\n",
        "source": "import",
        "requires_user_save": True,
        "save_mode": "create_only",
    }
    assert imported.json()["audit"] == {"wrote_skill_file": False}
    assert imported.json()["document"]["active"] is None
    assert not (
        tmp_path / "data" / "modules" / "skills" / "incident_response.md"
    ).exists()
    assert not (
        tmp_path / "data" / "modules" / "skills" / "review_checklist.md"
    ).exists()

    text_import = client.post(
        "/api/workflows/skills/import-preview",
        json={"filename": "notes.txt", "body": "Plain notes"},
    )
    markdown_import = client.post(
        "/api/workflows/skills/import-preview",
        json={"filename": "guide.markdown", "body": "# Guide"},
    )
    assert text_import.status_code == 200
    assert text_import.json()["target_id"] == "notes"
    assert markdown_import.status_code == 200
    assert markdown_import.json()["target_id"] == "guide"


def test_create_only_save_rejects_a_target_created_after_preview(tmp_path, monkeypatch):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    repo_root = tmp_path / "modules" / "skills"
    repo_root.mkdir(parents=True)
    (repo_root / "incident_triage.md").write_text("Source body", encoding="utf-8")

    app = importlib.import_module("app.main").app
    client = TestClient(app)
    preview = client.post(
        "/api/workflows/skills/incident_triage/duplicate-preview",
        json={"target_id": "incident_response"},
    )
    competing_save = client.put(
        "/api/workflows/skills/incident_response",
        json={"body": "Other user's body"},
    )
    create_only_save = client.put(
        "/api/workflows/skills/incident_response",
        json={"body": preview.json()["proposal"]["body"], "create_only": True},
    )

    assert preview.status_code == 200
    assert preview.json()["proposal"]["save_mode"] == "create_only"
    assert competing_save.status_code == 200
    assert create_only_save.status_code == 409
    assert "already exists" in create_only_save.json()["detail"]
    saved = client.get("/api/workflows/skills/incident_response")
    assert saved.status_code == 200
    assert saved.json()["active"]["body"] == "Other user's body"


def test_skill_save_maps_storage_failures_to_server_errors(tmp_path, monkeypatch):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)

    def _storage_failure(_target, _body):
        raise workflow_profiles.SkillStorageError("Storage unavailable")

    monkeypatch.setattr(workflow_profiles, "_atomic_create_text", _storage_failure)
    app = importlib.import_module("app.main").app
    client = TestClient(app)

    response = client.put(
        "/api/workflows/skills/storage_test",
        json={"body": "Draft", "create_only": True},
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "Storage unavailable"


def test_skill_delete_and_rename_map_storage_failures_to_server_errors(
    tmp_path, monkeypatch
):
    from app import routes, workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)

    def _storage_failure(*_args, **_kwargs):
        raise workflow_profiles.SkillStorageError("Lifecycle storage unavailable")

    monkeypatch.setattr(routes, "delete_local_skill_doc", _storage_failure)
    monkeypatch.setattr(routes, "rename_local_skill_doc", _storage_failure)
    app = importlib.import_module("app.main").app
    client = TestClient(app)

    deleted = client.delete("/api/workflows/skills/storage_test")
    renamed = client.post(
        "/api/workflows/skills/storage_test/rename",
        json={"target_id": "renamed_storage_test"},
    )

    assert deleted.status_code == 500
    assert deleted.json()["detail"] == "Lifecycle storage unavailable"
    assert renamed.status_code == 500
    assert renamed.json()["detail"] == "Lifecycle storage unavailable"


def test_skill_preview_targets_reject_portability_and_casefold_collisions(
    tmp_path, monkeypatch
):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    repo_root = tmp_path / "modules" / "skills"
    repo_root.mkdir(parents=True)
    (repo_root / "Incident_Triage.md").write_text("Body", encoding="utf-8")

    app = importlib.import_module("app.main").app
    client = TestClient(app)

    collision = client.post(
        "/api/workflows/skills/Incident_Triage/duplicate-preview",
        json={"target_id": "incident_triage"},
    )
    reserved = client.post(
        "/api/workflows/skills/import-preview",
        json={"filename": "README.md", "body": "Body", "target_id": "README"},
    )

    assert collision.status_code == 409
    assert "already exists" in collision.json()["detail"]
    assert reserved.status_code == 400
    assert "reserved" in reserved.json()["detail"]


def test_skill_rename_and_export_complete_local_only_lifecycle(tmp_path, monkeypatch):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    local_root = tmp_path / "data" / "modules" / "skills"

    app = importlib.import_module("app.main").app
    client = TestClient(app)
    saved = client.put(
        "/api/workflows/skills/incident_triage",
        json={"body": "# Incident Triage\n\nExact body.\n"},
    )
    assert saved.status_code == 200

    renamed = client.post(
        "/api/workflows/skills/incident_triage/rename",
        json={"target_id": "incident_response"},
    )
    exported = client.get("/api/workflows/skills/incident_response/export")

    assert renamed.status_code == 200
    assert renamed.json()["status"] == "renamed"
    assert renamed.json()["old_id"] == "incident_triage"
    assert renamed.json()["new_id"] == "incident_response"
    assert renamed.json()["document"]["active"]["body"] == (
        "# Incident Triage\n\nExact body."
    )
    assert not (local_root / "incident_triage.md").exists()
    assert (local_root / "incident_response.md").is_file()
    assert exported.status_code == 200
    assert exported.text == "# Incident Triage\n\nExact body."
    assert exported.headers["content-type"].startswith("text/markdown")
    assert exported.headers["content-disposition"] == (
        'attachment; filename="incident_response.md"'
    )


def test_skill_rename_rejects_linked_or_nonlocal_sources(tmp_path, monkeypatch):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    repo_root = tmp_path / "modules" / "skills"
    repo_root.mkdir(parents=True)
    (repo_root / "computer_use.md").write_text("Packaged", encoding="utf-8")
    (repo_root / "packaged_only.md").write_text("Packaged only", encoding="utf-8")

    app = importlib.import_module("app.main").app
    client = TestClient(app)
    assert (
        client.put(
            "/api/workflows/skills/computer_use", json={"body": "Local override"}
        ).status_code
        == 200
    )

    linked = client.post(
        "/api/workflows/skills/computer_use/rename",
        json={"target_id": "computer_control"},
    )
    packaged = client.post(
        "/api/workflows/skills/packaged_only/rename",
        json={"target_id": "packaged_copy"},
    )

    assert linked.status_code == 409
    assert "Linked skill" in linked.json()["detail"]
    assert packaged.status_code == 409
    assert "saved local" in packaged.json()["detail"]


def test_workflow_skill_reflection_draft_is_audited_but_not_written(
    tmp_path, monkeypatch
):
    from app import workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    repo_root = tmp_path / "modules" / "skills"
    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "incident_triage.md").write_text(
        "# Incident Triage\n\nInspect the highest-impact failure first.\n",
        encoding="utf-8",
    )

    class StubReflectionService:
        def __init__(self):
            self.created = None
            self.task = None

        def create_task(self, **kwargs):
            self.created = kwargs
            self.task = {
                "id": "thought-skill-draft",
                "title": kwargs["title"],
                "question": kwargs["question"],
                "status": "open",
                "source": kwargs["source"],
                "metadata": kwargs["metadata"],
                "patience": kwargs["patience"],
            }
            return self.task

        def get_task(self, task_id):
            return self.task if task_id == "thought-skill-draft" else None

        def run_task(self, task_id, *, force=False):
            assert task_id == "thought-skill-draft"
            assert force is True
            return {
                "status": "resolved",
                "task": {**self.task, "status": "resolved"},
                "run": {
                    "id": "run-skill-draft",
                    "created_at": 123.0,
                    "output": "# Incident Triage\n\n## Core loop\n\n- Inspect impact first.",
                    "compact_note": "Proposed incident triage guidance.",
                    "should_surface_to_user": False,
                    "thought": "Plan the operational sections.",
                    "thought_trace": [
                        {"index": 0, "text": "Plan the operational sections."}
                    ],
                    "thought_trace_count": 1,
                    "generation": {
                        "provider": "tinker",
                        "requested_model": "thinkingmachines/Inkling",
                    },
                },
            }

    app = importlib.import_module("app.main").app
    service = StubReflectionService()
    monkeypatch.setattr(app.state, "reflection_service", service, raising=False)
    client = TestClient(app)

    local_path = tmp_path / "data" / "modules" / "skills" / "incident_triage.md"
    response = client.post(
        "/api/workflows/skills/incident_triage/draft",
        json={
            "focus": "Make the loop operational.",
            "model": "thinkingmachines/Inkling",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "drafted"
    assert payload["proposal"] == {
        "body": "# Incident Triage\n\n## Core loop\n\n- Inspect impact first.",
        "source": "background_reflection",
        "requires_user_save": True,
    }
    assert payload["audit"]["task_id"] == "thought-skill-draft"
    assert payload["audit"]["run_id"] == "run-skill-draft"
    assert payload["audit"]["wrote_skill_file"] is False
    assert payload["audit"]["reasoning_trace"] == {
        "preserved": True,
        "entries": 1,
        "characters": 30,
    }
    assert payload["audit"]["generation"]["provider"] == "tinker"
    assert service.created["metadata"]["proposal_kind"] == "skill_markdown"
    assert service.created["metadata"]["requires_user_save"] is True
    assert service.created["metadata"]["requested_model"] == "thinkingmachines/Inkling"
    assert "Make the loop operational." in service.created["question"]
    assert not local_path.exists()


def test_turn_tool_filter_enforces_custom_module_membership_and_guides_recovery(
    tmp_path, monkeypatch
):
    from app import routes, workflow_profiles

    monkeypatch.setattr(workflow_profiles.app_config, "REPO_ROOT", tmp_path)
    addon_root = tmp_path / "data" / "modules" / "addons" / "task-pack"
    addon_root.mkdir(parents=True)
    (addon_root / "config.json").write_text(
        """
        {
          "id": "task-pack",
          "label": "Task Pack",
          "modules": [{
            "id": "task_tools",
            "label": "Task Tools",
            "tool_names": ["list_tasks"]
          }]
        }
        """,
        encoding="utf-8",
    )
    settings = {"enabled_workflow_modules": []}
    monkeypatch.setattr(routes.user_settings, "load_settings", lambda: settings)
    definitions = [{"name": "list_tasks"}, {"name": "remember"}]

    filtered = routes._filter_turn_tool_definitions(
        definitions, allow_computer_capture=False
    )
    assert [item["name"] for item in filtered] == ["remember"]

    settings["enabled_workflow_modules"] = ["task_tools"]
    filtered = routes._filter_turn_tool_definitions(
        definitions, allow_computer_capture=False
    )
    assert [item["name"] for item in filtered] == ["list_tasks", "remember"]

    settings["enabled_workflow_modules"] = []
    snapshot = routes._capture_turn_tool_filter_snapshot(
        verified_enabled_modules=["task_tools"]
    )
    filtered = routes._filter_turn_tool_definitions(
        definitions,
        allow_computer_capture=False,
        tool_filter_snapshot=snapshot,
    )
    assert [item["name"] for item in filtered] == ["list_tasks", "remember"]
    settings["enabled_workflow_modules"] = ["task_tools"]
    snapshot = routes._capture_turn_tool_filter_snapshot(verified_enabled_modules=[])
    filtered = routes._filter_turn_tool_definitions(
        definitions,
        allow_computer_capture=False,
        tool_filter_snapshot=snapshot,
    )
    assert [item["name"] for item in filtered] == ["remember"]
    assert "Knowledge > Skills" in routes._disabled_workflow_modules_note(
        ["task_tools"]
    )

    def _catalog_failure():
        raise OSError("catalog unavailable")

    monkeypatch.setattr(routes, "module_catalog_snapshot", _catalog_failure)
    assert (
        routes._filter_turn_tool_definitions(definitions, allow_computer_capture=False)
        == []
    )
    assert (
        routes._filter_turn_tool_definitions(definitions, allow_computer_capture=True)
        == []
    )


def test_turn_tool_snapshot_is_single_scan_and_reused_for_requested_module_scope(
    monkeypatch,
):
    from types import SimpleNamespace

    from app import routes, tool_specs

    counters = {"settings": 0, "catalog": 0}
    settings_state = {
        "default_workflow": "default",
        "enabled_workflow_modules": [],
        "tool_policies": {},
    }
    catalog_state = [
        {
            "id": "task_tools",
            "label": "Task Tools",
            "source": "custom",
            "tool_names": ["list_tasks"],
        },
        {
            "id": "core_tools",
            "label": "Core Tools",
            "source": "base",
            "tool_names": ["remember"],
        },
    ]

    def _load_settings():
        counters["settings"] += 1
        return settings_state

    def _load_catalog():
        counters["catalog"] += 1
        return catalog_state, set()

    monkeypatch.setattr(routes.user_settings, "load_settings", _load_settings)
    monkeypatch.setattr(routes, "module_catalog_snapshot", _load_catalog)
    definitions = [{"name": "list_tasks"}, {"name": "remember"}]
    monkeypatch.setattr(tool_specs, "get_tool_specs", lambda _names: definitions)
    manager = SimpleNamespace(list_tools=lambda: ["list_tasks", "remember"])
    app = SimpleNamespace(state=SimpleNamespace(memory_manager=manager))

    captured = routes._capture_turn_tool_filter_snapshot()
    workflow = routes._workflow_request_config(
        "default",
        ["task_tools", "core_tools"],
        settings_payload=captured.settings_payload,
        module_catalog=captured.module_catalog,
    )
    snapshot = routes._turn_tool_filter_snapshot_with_modules(
        captured,
        workflow["modules"],
    )

    # A mid-turn disk/settings mutation cannot change either filtering lane.
    settings_state["enabled_workflow_modules"] = []
    catalog_state.clear()
    context_tools = routes._filter_turn_tool_definitions(
        definitions,
        allow_computer_capture=False,
        tool_filter_snapshot=snapshot,
    )
    registered_tools = routes._registered_prompt_tool_definitions(
        app,
        allow_computer_capture=False,
        tool_filter_snapshot=snapshot,
    )

    assert workflow["modules"] == ["core_tools", "task_tools"]
    assert [item["name"] for item in context_tools] == ["list_tasks", "remember"]
    assert [item["name"] for item in registered_tools] == ["remember", "list_tasks"]
    assert counters == {"settings": 1, "catalog": 1}


def test_verified_continuation_module_scope_overrides_current_settings(monkeypatch):
    from app import routes

    settings = {"enabled_workflow_modules": ["task_tools"]}
    catalog = [
        {
            "id": "task_tools",
            "label": "Task Tools",
            "source": "custom",
            "tool_names": ["list_tasks"],
        }
    ]
    monkeypatch.setattr(routes.user_settings, "load_settings", lambda: settings)
    monkeypatch.setattr(routes, "module_catalog_snapshot", lambda: (catalog, set()))

    captured = routes._capture_turn_tool_filter_snapshot(verified_enabled_modules=[])
    workflow = routes._workflow_request_config(
        "default",
        [],
        include_global_modules=False,
        settings_payload=captured.settings_payload,
        module_catalog=captured.module_catalog,
    )
    snapshot = routes._turn_tool_filter_snapshot_with_modules(
        captured,
        workflow["modules"],
    )
    filtered = routes._filter_turn_tool_definitions(
        [{"name": "list_tasks"}],
        allow_computer_capture=False,
        tool_filter_snapshot=snapshot,
    )

    assert workflow["modules"] == []
    assert filtered == []
