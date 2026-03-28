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
    from app.services import LLMService
    from app.base_services import ModelContext

    # Reset contexts to ensure rehydrate kicks in
    routes.llm_service.contexts = {"default": ModelContext(system_prompt="")}

    captured = {}

    def fake_generate(prompt, session_id=None, model=None, attachments=None, context=None, **kwargs):
        captured["context"] = context
        return {"text": "ok", "thought": "", "tools_used": [], "metadata": {}}

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    app = importlib.import_module("app.main").app
    client = TestClient(app)
    resp = client.post("/chat", json={"message": "new message", "session_id": "sess", "use_rag": False})
    assert resp.status_code == 200
    ctx = captured.get("context")
    assert ctx is not None
    # Expect historical messages to be present before the new turn
    assert any(msg["content"] == "hello from history" for msg in ctx.messages)
    assert any(msg["content"] == "previous reply" for msg in ctx.messages)
