import asyncio
import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def add_backend_to_sys_path():
    import sys

    backend_dir = Path(__file__).resolve().parents[2]
    backend_dir = str(backend_dir)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)


def test_chat_rehydrates_context(monkeypatch, tmp_path):
    # Point conversation store at a temp directory and reload it
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    # Build a persisted conversation with prior turns
    conv_store.save_conversation(
        "sess",
        [
            {"role": "user", "text": "hello from history"},
            {"role": "ai", "text": "previous reply"},
        ],
    )

    from app import routes
    from app.base_services import ModelContext

    # Reset contexts to ensure rehydrate kicks in
    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}

    captured = {}

    def fake_generate(
        prompt, session_id=None, model=None, attachments=None, context=None, **kwargs
    ):
        captured["context"] = context
        return {"text": "ok", "thought": "", "tools_used": [], "metadata": {}}

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    client = TestClient(app)
    resp = client.post(
        "/chat", json={"message": "new message", "session_id": "sess", "use_rag": False}
    )
    assert resp.status_code == 200
    ctx = captured.get("context")
    assert ctx is not None
    # Expect historical messages to be present before the new turn
    assert any(msg["content"] == "hello from history" for msg in ctx.messages)
    assert any(msg["content"] == "previous reply" for msg in ctx.messages)


def test_chat_rehydrate_strips_inline_tool_placeholders(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path))
    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    conv_store.save_conversation(
        "sess",
        [
            {
                "role": "ai",
                "text": "Checking tools first.[[tool_call:0]][[tool_call:1]] Then I will browse.",
            },
        ],
    )

    from app import routes
    from app.base_services import ModelContext

    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}
    captured = {}

    def fake_generate(
        prompt, session_id=None, model=None, attachments=None, context=None, **kwargs
    ):
        captured["context"] = context
        return {"text": "ok", "thought": "", "tools_used": [], "metadata": {}}

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    client = TestClient(app)
    resp = client.post(
        "/chat", json={"message": "continue", "session_id": "sess", "use_rag": False}
    )
    assert resp.status_code == 200
    ctx = captured.get("context")
    assert ctx is not None
    assert any(
        msg["content"] == "Checking tools first. Then I will browse."
        for msg in ctx.messages
    )
    assert all("[[tool_call:" not in msg["content"] for msg in ctx.messages)


def test_chat_emits_rag_operation_progress(monkeypatch, tmp_path):
    data_root = tmp_path / "data_root"
    monkeypatch.setenv("FLOAT_DATA_DIR", str(data_root))
    monkeypatch.setenv(
        "FLOAT_MEMORY_FILE",
        str(data_root / "databases" / "memory.sqlite3"),
    )
    monkeypatch.setenv("FLOAT_CONV_DIR", str(tmp_path / "conversations"))

    conv_store = importlib.import_module("app.utils.conversation_store")
    importlib.reload(conv_store)

    from app import routes
    from app.base_services import ModelContext

    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}
    asyncio.__float_notifications__ = []  # type: ignore[attr-defined]

    class FakeService:
        embedding_model = "fake-embed"

        def query(self, query, top_k=5):
            assert query == "find the tea notes"
            assert top_k >= 1
            return [
                {
                    "id": "tea-doc",
                    "text": "Tea notes retrieved for the current chat turn.",
                    "metadata": {"source": "workspace/tea.md"},
                    "score": 0.91,
                }
            ]

    def fake_generate(
        prompt, session_id=None, model=None, attachments=None, context=None, **kwargs
    ):
        return {"text": "ok", "thought": "", "tools_used": [], "metadata": {}}

    monkeypatch.setattr(routes, "_get_rag_service", lambda: FakeService())
    monkeypatch.setattr(routes, "_get_clip_rag_service", lambda **_kwargs: None)
    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    client = TestClient(app)
    resp = client.post(
        "/chat",
        json={"message": "find the tea notes", "session_id": "sess"},
    )
    assert resp.status_code == 200

    notifications_resp = client.get("/notifications/recent")
    assert notifications_resp.status_code == 200
    notifications = notifications_resp.json().get("notifications") or []
    progress_entries = [
        entry
        for entry in notifications
        if entry.get("category") == "operation_progress"
        and entry.get("data", {}).get("kind") == "rag_query"
        and entry.get("title") == "Retrieving chat context"
    ]
    assert progress_entries
    statuses = [entry.get("data", {}).get("status") for entry in progress_entries]
    assert "running" in statuses
    assert "complete" in statuses
    final_entry = progress_entries[-1]
    assert final_entry.get("data", {}).get("phase_label") == "Chat retrieval finished"
    assert final_entry.get("data", {}).get("counts", {}).get("returned_matches") == 1
    assert str(final_entry.get("data", {}).get("operation_id") or "").startswith(
        "rag-query:chat:"
    )
