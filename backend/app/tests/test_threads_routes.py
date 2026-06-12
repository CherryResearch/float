import asyncio
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


def test_threads_generate_emits_runtime_progress(monkeypatch):
    services_module = sys.modules.get("app.services")
    if services_module is not None and not hasattr(services_module, "CaptureService"):
        sys.modules.pop("app.services", None)
        sys.modules.pop("app.routes", None)
    from app import routes

    asyncio.__float_notifications__ = []  # type: ignore[attr-defined]

    def fake_generate_threads(**kwargs):
        assert kwargs["top_n"] == 3
        assert kwargs["preferred_k"] == 8
        assert kwargs["max_k"] == 16
        assert kwargs["embedding_model"] is None
        assert kwargs["sensitive_mode"] is True
        assert kwargs["topic_suggestion_provider"] is None
        assert kwargs["topic_suggestion_model"] is None
        assert str(kwargs["operation_id"]).startswith("rag-query:threads")
        assert kwargs["operation_owner"] == "/api/threads/generate"
        assert kwargs["operation_source"] == "/api/threads/generate"
        return {
            "total_threads": 3,
            "threads": [
                {"id": "food", "label": "food"},
                {"id": "memory", "label": "memory"},
                {"id": "tools", "label": "tools"},
            ],
        }

    monkeypatch.setattr(
        routes.threads_service, "generate_threads", fake_generate_threads
    )

    app = importlib.import_module("app.main").app
    client = TestClient(app)
    resp = client.post("/threads/generate", json={"top_n": 3})

    assert resp.status_code == 200
    notifications_resp = client.get("/notifications/recent")
    assert notifications_resp.status_code == 200
    notifications = notifications_resp.json().get("notifications") or []
    progress_entries = [
        entry
        for entry in notifications
        if entry.get("category") == "operation_progress"
        and entry.get("data", {}).get("kind") == "rag_query"
        and entry.get("title") == "Generating threads"
    ]
    assert progress_entries
    statuses = [entry.get("data", {}).get("status") for entry in progress_entries]
    assert "running" in statuses
    assert "complete" in statuses
    final_entry = progress_entries[-1]
    assert str(final_entry.get("data", {}).get("operation_id") or "").startswith(
        "rag-query:threads"
    )
    assert final_entry.get("data", {}).get("counts", {}).get("total_threads") == 3
