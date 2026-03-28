import importlib
import sys
from types import SimpleNamespace

import requests


def test_mcp_stub_fallback_serves_http(monkeypatch):
    mcp_server = importlib.import_module("app.mcp_server")
    monkeypatch.setattr(mcp_server, "FastMCP", None)

    server = mcp_server.FloatMCPServer()
    url, provider = server.start()
    try:
        assert provider == "stub"
        response = requests.get(f"{url}/health", timeout=2)
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "ok"
        assert payload["provider"] == "stub"
    finally:
        stub = getattr(server, "_server", None)
        if stub is not None and hasattr(stub, "shutdown"):
            stub.shutdown()
        if stub is not None and hasattr(stub, "server_close"):
            stub.server_close()


def test_remote_manifest_uses_explicit_token(monkeypatch):
    routes = importlib.import_module("app.routes")
    captured: dict[str, str | None] = {}

    class FakeHfApi:
        def __init__(self, token=None):
            captured["token"] = token

        def model_info(self, repo_id, files_metadata=False):  # noqa: ARG002
            assert files_metadata is True
            return SimpleNamespace(
                siblings=[
                    SimpleNamespace(
                        rfilename="weights/model.bin",
                        size=42,
                        lfs={"sha256": "a" * 64},
                    )
                ],
                sha="commit-1",
            )

    monkeypatch.setitem(
        sys.modules, "huggingface_hub", SimpleNamespace(HfApi=FakeHfApi)
    )

    manifest, total, commit = routes._remote_manifest(
        "openai/gpt-oss-20b",
        allow_patterns=["weights/*"],
        token="hf_test_token",
    )
    assert captured["token"] == "hf_test_token"
    assert total == 42
    assert commit == "commit-1"
    assert manifest == [
        {
            "path": "weights/model.bin",
            "size": 42,
            "sha256": "a" * 64,
        }
    ]
