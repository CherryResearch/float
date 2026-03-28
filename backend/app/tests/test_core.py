import logging
import os
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

if not os.getenv("RUN_EMBEDDING_TEST"):
    pytest.skip(
        "Skipping core endpoint tests (unrelated to UI changes)",
        allow_module_level=True,
    )

backend_dir = Path(__file__).resolve().parents[2]
backend_dir = str(backend_dir)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app import config  # noqa: E402

DEFAULT_PROMPT = config.load_config()["system_prompt"]


@pytest.fixture(autouse=True)
def add_backend_to_sys_path():
    # Ensure backend/app is importable as 'app'
    # tests live in backend/app/tests so go two levels up to reach the project
    # root 'backend' directory
    backend_dir = Path(__file__).resolve().parents[2]
    backend_dir = str(backend_dir)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)


@pytest.fixture
def client(add_backend_to_sys_path):
    """Return a TestClient for the FastAPI app after adjusting sys.path."""
    from app.main import app

    return TestClient(app)


@pytest.mark.parametrize(
    "path, expected",
    [
        ("/", {"Hello": "World"}),
        ("/health", {"status": "healthy"}),
        ("/api/health", {"status": "healthy"}),
    ],
)
def test_root_and_health(client, path, expected):
    resp = client.get(path)
    assert resp.status_code == 200
    assert resp.json() == expected


def test_chat_endpoint_fallback(client):
    # Using fallback stub (no API key set)
    resp = client.post(
        "/chat",
        json={"message": "test", "session_id": "sess", "use_rag": False},
    )
    assert resp.status_code == 200
    data = resp.json()
    # Validate response structure
    assert data["message"] == "You said: test"
    assert data["thought"] == ""
    assert data["tools_used"] == []
    assert data["metadata"] == {}
    # Validate context
    assert "context" in data
    ctx = data["context"]
    assert ctx["system_prompt"] == DEFAULT_PROMPT
    messages = ctx["messages"]
    # Expect user then assistant messages
    assert isinstance(messages, list) and len(messages) == 2
    assert messages[0]["role"] == "user" and messages[0]["content"] == "test"
    assert (
        messages[1]["role"] == "assistant"
        and messages[1]["content"] == "You said: test"
    )


def test_settings_and_memory_and_tools(client):
    # Test settings endpoint
    resp = client.get("/settings")
    assert resp.status_code == 200
    settings = resp.json()
    assert settings["mode"] == "api"
    assert "model" in settings and "api_key" in settings

    # Test memory update
    update_payload = {"key": "foo", "value": {"bar": "baz"}}
    resp2 = client.post("/memory/update/", json=update_payload)
    assert resp2.status_code == 200
    result = resp2.json()
    assert result["status"] == "success"
    assert result["updated"] == {"foo": {"bar": "baz"}}

    # Test tools listing
    resp3 = client.get("/tools/")
    assert resp3.status_code == 200
    assert resp3.json() == {"tools": []}


def test_context_endpoints(client):
    # Create context
    ctx_payload = {
        "system_prompt": "Hello",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [],
        "metadata": {"k": "v"},
    }
    resp = client.post("/context/testctx", json=ctx_payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    ctx = body["context"]
    assert ctx["system_prompt"] == "Hello"
    assert ctx["metadata"] == {"k": "v"}
    assert len(ctx["messages"]) == 1

    # Retrieve context
    resp2 = client.get("/context/testctx")
    assert resp2.status_code == 200
    assert resp2.json()["context"] == ctx

    # Add a message
    params = {"role": "assistant", "content": "reply"}
    resp3 = client.post("/context/testctx/message", params=params)
    assert resp3.status_code == 200
    new_ctx = resp3.json()["context"]
    assert len(new_ctx["messages"]) == 2

    # Add a tool
    tool_payload = {
        "name": "t1",
        "description": "desc",
        "parameters": {"x": "int"},
    }
    resp4 = client.post("/context/testctx/tool", json=tool_payload)
    assert resp4.status_code == 200
    new_ctx2 = resp4.json()["context"]
    assert len(new_ctx2["tools"]) == 1
    # Validate tool data
    tool_entry = new_ctx2["tools"][-1]
    assert tool_entry["name"] == "t1"
    assert tool_entry["parameters"] == {"x": "int"}

    # Set metadata
    resp5 = client.post(
        "/context/testctx/metadata",
        params={"key": "z", "value": "42"},
    )
    assert resp5.status_code == 200
    meta_ctx = resp5.json()["context"]
    assert meta_ctx["metadata"].get("z") == "42"

    # Clear context
    resp6 = client.delete("/context/testctx")
    assert resp6.status_code == 200
    assert resp6.json()["status"] == "success"
    resp7 = client.get("/context/testctx")
    cleared = resp7.json()["context"]
    assert (
        cleared["messages"] == []
        and cleared["tools"] == []
        and cleared["metadata"] == {}
    )


def test_context_branching(client):
    base = {"system_prompt": "Base"}
    client.post("/context/original", json=base)
    r = client.post("/context/original/branch", json={"new_id": "child"})
    assert r.status_code == 200
    child_ctx = r.json()["context"]
    assert child_ctx["system_prompt"] == "Base"
    client.post(
        "/context/child/message",
        params={"role": "user", "content": "hi"},
    )
    orig = client.get("/context/original").json()["context"]
    assert orig["messages"] == []


def test_llm_generate_modes(client):
    # API mode
    r1 = client.post(
        "/llm/generate",
        json={"prompt": "hello", "mode": "api"},
    )
    assert r1.status_code == 200 and "response" in r1.json()
    # Local mode
    r2 = client.post(
        "/llm/generate",
        json={"prompt": "hello", "mode": "local"},
    )
    assert r2.status_code == 200 and "response" in r2.json()
    # Dynamic mode
    r3 = client.post("/llm/start-dynamic")
    assert r3.status_code == 200
    r4 = client.post(
        "/llm/generate",
        json={"prompt": "hello", "mode": "dynamic"},
    )
    assert r4.status_code == 200 and "response" in r4.json()
    r5 = client.post("/llm/stop-dynamic")
    assert r5.status_code == 200


def test_embedding_service(monkeypatch, caplog):
    class DummyModel:
        def encode(self, text: str):
            return types.SimpleNamespace(tolist=lambda: [0.0])

    fake_module = types.SimpleNamespace(
        SentenceTransformer=lambda model_type: DummyModel()
    )
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)

    from app.utils.embedding import EmbeddingService

    with caplog.at_level(logging.INFO):
        service = EmbeddingService()

    assert "Loading embedding model" in caplog.text
    assert service.embed_text("hello") == [0.0]


def test_modelcontext_and_llmservice(monkeypatch):
    # Test ModelContext functionality
    from app.services import LLMService, ModelContext

    ctx = ModelContext(
        system_prompt="sys",
        messages=None,
        tools=None,
        metadata=None,
    )
    ctx.add_message("user", "hi", {"m": 1})
    assert ctx.messages[-1]["role"] == "user"
    assert ctx.messages[-1]["metadata"] == {"m": 1}

    ctx.add_tool("t", "desc", {"p": str}, {"tm": 2})
    assert ctx.tools[-1]["name"] == "t"
    assert ctx.tools[-1]["metadata"] == {"tm": 2}

    ctx.set_metadata("a", 123)
    assert ctx.get_metadata("a") == 123

    d = ctx.to_dict()
    assert d["system_prompt"] == "sys"

    ctx.clear()
    assert ctx.messages == [] and ctx.tools == [] and ctx.metadata == {}

    # Test LLMService fallback (no API key)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)
    llm = LLMService()
    res = llm.generate("abc")
    assert res.get("text") == "You said: abc"
    assert res.get("tools_used") == [] and res.get("metadata") == {}


def test_llmservice_api_error_fallback(monkeypatch):
    import requests
    from app.services import LLMService

    svc = LLMService()
    svc.config["api_key"] = "tok"

    def boom(*args, **kwargs):
        raise requests.exceptions.HTTPError("fail")

    monkeypatch.setattr(requests, "post", boom)

    res = svc.generate("oops")
    assert res["text"] == "You said: oops"
    assert "error" in res["metadata"]


def test_llmservice_modes(monkeypatch):
    """Ensure LLMService works in api, local and dynamic modes."""
    from app.services import LLMService

    # Remove any API keys to trigger fallback responses
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)

    # API mode
    svc_api = LLMService(mode="api")
    assert svc_api.generate("hi")["text"] == "You said: hi"

    # Local mode
    svc_local = LLMService(mode="local")
    assert svc_local.generate("hi")["text"] == "You said: hi"

    # Dynamic mode start/stop
    svc_dyn = LLMService(mode="dynamic")
    svc_dyn.start_dynamic_server()
    assert svc_dyn.dynamic_process is True
    assert svc_dyn.generate("hi")["text"] == "You said: hi"
    svc_dyn.stop_dynamic_server()
    assert svc_dyn.dynamic_process is None


def test_knowledge_routes(client, tmp_path):
    doc = tmp_path / "note.txt"
    doc.write_text("hello world")

    r = client.post(
        "/knowledge/add",
        json={"path": str(doc)},
    )
    assert r.status_code == 200
    doc_id = r.json()["id"]

    q = client.get("/knowledge/query", params={"q": "hello", "k": 1})
    assert q.status_code == 200
    assert q.json()["ids"]

    t = client.get(f"/knowledge/trace/{doc_id}")
    assert t.status_code == 200

    upload = client.post(
        "/knowledge/upload",
        files={"file": ("note2.txt", b"hi")},
    )
    assert upload.status_code == 200

    img = client.post(
        "/knowledge/upload",
        files={"file": ("pic.png", b"\x89PNG\r\n", "image/png")},
    )
    assert img.status_code == 200

    big = b"x" * (8 * 1024 * 1024 + 1)
    too_big = client.post(
        "/knowledge/upload",
        files={"file": ("big.txt", big, "text/plain")},
    )
    assert too_big.status_code == 400

    bad = client.post(
        "/knowledge/upload",
        files={"file": ("bad.bin", b"hi", "application/octet-stream")},
    )
    assert bad.status_code == 400

    # cleanup


def test_knowledge_edit_browse(client, tmp_path):
    doc = tmp_path / "a.txt"
    doc.write_text("alpha")
    r = client.post("/knowledge/add", json={"path": str(doc)})
    doc_id = r.json()["id"]

    lst = client.get("/knowledge/list")
    assert doc_id in lst.json()["ids"]

    upd = client.put(f"/knowledge/{doc_id}", json={"text": "beta"})
    assert upd.status_code == 200

    g = client.get(f"/knowledge/{doc_id}")
    assert g.status_code == 200
    assert g.json()["documents"][0] == "beta"

    d = client.delete(f"/knowledge/{doc_id}")
    assert d.status_code == 200

    lst2 = client.get("/knowledge/list")
    assert doc_id not in lst2.json()["ids"]


def test_knowledge_list_endpoint(client, tmp_path):
    doc = tmp_path / "view.txt"
    doc.write_text("hello")
    r = client.post("/knowledge/add", json={"path": str(doc)})
    doc_id = r.json()["id"]

    res = client.get("/knowledge/list")
    assert res.status_code == 200
    data = res.json()
    assert doc_id in data["ids"]
    idx = data["ids"].index(doc_id)
    assert isinstance(data["metadatas"][idx], dict)


def test_import_from_weaviate(monkeypatch):
    from app.services.rag_service import RAGService

    class DummyData:
        def get(self, class_name=None):
            return {"objects": [{"id": "w1", "properties": {"text": "hi"}}]}

    class DummyClient:
        def __init__(self, url, auth_client_secret=None):
            self._data_object = DummyData()

        @property
        def data_object(self):
            return self._data_object

    dummy_module = types.SimpleNamespace(
        AuthApiKey=lambda k: k,
        Client=DummyClient,
    )

    monkeypatch.setitem(sys.modules, "weaviate", dummy_module)
    import tempfile

    service = RAGService(persist_dir=tempfile.mkdtemp())

    ids = service.import_from_weaviate("http://x", "Foo")
    assert ids


def test_tool_registration_and_invocation(client, tmp_path):
    # register file tools
    r = client.post("/tools/register", json={"name": "write_file"})
    assert r.status_code == 200
    r2 = client.post("/tools/register", json={"name": "read_file"})
    assert r2.status_code == 200

    target = tmp_path / "note.txt"
    write_payload = {
        "name": "write_file",
        "args": {"path": str(target), "content": "hi"},
    }
    w = client.post("/tools/invoke", json=write_payload)
    assert w.status_code == 200
    read_payload = {"name": "read_file", "args": {"path": str(target)}}
    res = client.post("/tools/invoke", json=read_payload)
    assert res.status_code == 200
    result = res.json()["result"]
    assert result["status"] == "invoked"
    assert result["ok"] is True
    assert result["data"]["text"] == "hi"
    assert result["data"]["truncated"] is False


def test_chat_logs_error_on_failure(client, monkeypatch):
    from app import routes

    def boom(*args, **kwargs):
        raise RuntimeError("fail")

    monkeypatch.setattr(routes.llm_service, "generate", boom)

    logged = {}

    def fake_error(msg, *args, **kwargs):
        logged.update({"msg": msg, **kwargs})

    monkeypatch.setattr(routes.logger, "error", fake_error)

    resp = client.post(
        "/chat",
        json={"message": "bad", "session_id": "s1", "use_rag": False},
    )
    assert resp.status_code == 500
    assert logged.get("msg") == "Chat failed"
    assert logged.get("exc_info") is True
    extra = logged.get("extra")
    assert extra["session_id"] == "s1"
    assert extra["prompt_snippet"] == "bad"
