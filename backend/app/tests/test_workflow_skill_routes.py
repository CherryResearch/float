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
