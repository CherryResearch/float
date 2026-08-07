import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


def _load_launcher_module():
    repo_root = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location(
        "float_launcher_network_test",
        repo_root / "main.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_launcher_backend_defaults_to_loopback():
    launcher = _load_launcher_module()

    command = launcher._build_backend_cmd(8123)

    host_index = command.index("--host") + 1
    assert command[host_index] == "127.0.0.1"
    assert "--reload" not in command


def test_launcher_backend_reload_is_explicit():
    launcher = _load_launcher_module()

    assert "--reload" in launcher._build_backend_cmd(8123, reload=True)


@pytest.mark.parametrize(
    ("bind_host", "proxy_host"),
    [
        ("127.0.0.1", "127.0.0.1"),
        ("0.0.0.0", "127.0.0.1"),
        ("::", "::1"),
        ("192.168.1.20", "192.168.1.20"),
    ],
)
def test_launcher_selects_connectable_frontend_proxy_host(bind_host, proxy_host):
    launcher = _load_launcher_module()

    assert launcher._backend_proxy_connect_host(bind_host) == proxy_host


def test_launcher_uses_saved_lan_visibility_without_cli_override(tmp_path):
    launcher = _load_launcher_module()
    (tmp_path / "user_settings.json").write_text(
        json.dumps({"sync_visible_on_lan": True}),
        encoding="utf-8",
    )

    assert launcher._resolve_backend_host(repo_root=str(tmp_path)) == "0.0.0.0"
    assert (
        launcher._resolve_backend_host(repo_root=str(tmp_path), lan_mode=False)
        == "127.0.0.1"
    )
    assert (
        launcher._resolve_backend_host(
            repo_root=str(tmp_path), explicit_host="127.0.0.9"
        )
        == "127.0.0.9"
    )


def test_default_cors_rejects_unlisted_browser_origin():
    from app.main import app

    response = TestClient(app).options(
        "/api/health",
        headers={
            "Origin": "https://example.invalid",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert "access-control-allow-origin" not in response.headers
    assert "access-control-allow-credentials" not in response.headers


def test_direct_lan_request_cannot_reach_local_memory_api(monkeypatch):
    from app.main import app

    class EmptyMemoryManager:
        def list_items(self, include_pruned=False):
            return []

    monkeypatch.setattr(app.state, "memory_manager", EmptyMemoryManager())
    response = TestClient(app).get(
        "/api/memory",
        headers={"x-forwarded-for": "192.168.1.25"},
    )

    assert response.status_code == 403
    assert "local frontend" in response.json()["detail"].lower()


def test_trusted_local_frontend_proxy_can_reach_memory_api(monkeypatch):
    from app.main import app

    class EmptyMemoryManager:
        def list_items(self, include_pruned=False):
            return []

    monkeypatch.setattr(app.state, "memory_manager", EmptyMemoryManager())
    response = TestClient(app).get(
        "/api/memory",
        headers={
            "x-forwarded-for": "192.168.1.25",
            "x-float-frontend-proxy": "1",
        },
    )

    assert response.status_code == 200


def test_private_interface_frontend_proxy_requires_launcher_token(monkeypatch):
    from app.main import app

    class EmptyMemoryManager:
        def list_items(self, include_pruned=False):
            return []

    monkeypatch.setattr(app.state, "memory_manager", EmptyMemoryManager())
    monkeypatch.setenv("FLOAT_FRONTEND_PROXY_TOKEN", "launcher-secret")
    remote_client = TestClient(app, client=("192.168.1.25", 50000))
    base_headers = {"x-float-frontend-proxy": "1"}

    missing = remote_client.get("/api/memory", headers=base_headers)
    rejected = remote_client.get(
        "/api/memory",
        headers={
            **base_headers,
            "x-float-frontend-proxy-token": "spoofed-secret",
        },
    )
    accepted = remote_client.get(
        "/api/memory",
        headers={
            **base_headers,
            "x-float-frontend-proxy-token": "launcher-secret",
        },
    )

    assert missing.status_code == 403
    assert rejected.status_code == 403
    assert accepted.status_code == 200
    assert accepted.json() == {"keys": []}


def test_direct_lan_client_cannot_spoof_loopback_forwarded_address():
    from app.utils.device_visibility import client_host

    request = SimpleNamespace(
        client=SimpleNamespace(host="192.168.1.25"),
        headers={"x-forwarded-for": "127.0.0.1"},
    )

    assert client_host(request) == "192.168.1.25"


def test_cors_origins_must_be_explicit():
    from app.utils.network_policy import configured_cors_origins

    assert configured_cors_origins("http://localhost:5173, https://float.example/") == [
        "http://localhost:5173",
        "https://float.example",
    ]
    with pytest.raises(ValueError, match="wildcard CORS is disabled"):
        configured_cors_origins("*")
