from __future__ import annotations

from app.local_providers.lmstudio import LMStudioAdapter


class _FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._payload


def _base_cfg(**overrides):
    cfg = {
        "local_provider_mode": "local-managed",
        "local_provider_host": "127.0.0.1",
        "local_provider_port": 1234,
        "local_provider_base_url": "",
    }
    cfg.update(overrides)
    return cfg


def test_lmstudio_list_models_unreachable_sets_ok_false(monkeypatch):
    adapter = LMStudioAdapter()

    def fake_get(*_args, **_kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr("app.local_providers.lmstudio.requests.get", fake_get)

    result = adapter.list_models(_base_cfg())
    assert result["ok"] is False
    assert result["models"] == []

    status = adapter.poll_status(_base_cfg())
    assert status["server_running"] is False


def test_lmstudio_poll_status_treats_inventory_as_running(monkeypatch):
    adapter = LMStudioAdapter()

    def fake_get(url, timeout, headers=None):
        if url.endswith("/models"):
            return _FakeResponse({"models": [{"id": "gpt-oss-20b"}]})
        raise RuntimeError("offline")

    monkeypatch.setattr("app.local_providers.lmstudio.requests.get", fake_get)

    status = adapter.poll_status(_base_cfg())

    assert status["server_running"] is True
    assert status["status_reachable"] is False
    assert status["inventory_reachable"] is True
    assert status["inventory_model_count"] == 1
    assert status["model_loaded"] is False
    assert status["loaded_model"] is None


def test_lmstudio_poll_status_reads_loaded_model_from_inventory(monkeypatch):
    adapter = LMStudioAdapter()

    def fake_get(url, timeout, headers=None):
        if url.endswith("/api/v0/status"):
            return _FakeResponse({"error": "Unexpected endpoint or method."})
        if url.endswith("/api/v0/models"):
            return _FakeResponse(
                {
                    "data": [
                        {
                            "id": "openai/gpt-oss-20b",
                            "state": "loaded",
                            "loaded_context_length": 10379,
                        },
                        {"id": "gemma-4-e2b-it", "state": "not-loaded"},
                    ]
                }
            )
        raise RuntimeError("offline")

    monkeypatch.setattr("app.local_providers.lmstudio.requests.get", fake_get)

    status = adapter.poll_status(_base_cfg())

    assert status["server_running"] is True
    assert status["inventory_reachable"] is True
    assert status["model_loaded"] is True
    assert status["loaded_model"] == "openai/gpt-oss-20b"
    assert status["context_length"] == 10379


def test_lmstudio_remote_unmanaged_load_uses_http(monkeypatch):
    adapter = LMStudioAdapter()

    def fake_post(url, json, timeout, headers=None):
        assert url.endswith("/api/v0/model/load")
        assert json["model"] == "gpt-oss-20b"
        assert json["context_length"] == 4096
        return _FakeResponse({"ok": True})

    monkeypatch.setattr("app.local_providers.lmstudio.requests.post", fake_post)
    # Should not require local lms binary in remote-unmanaged mode.
    monkeypatch.setattr(
        LMStudioAdapter,
        "detect_installation",
        lambda self, cfg: {"ok": False, "installed": False, "binary": ""},
    )

    result = adapter.load_model(
        _base_cfg(local_provider_mode="remote-unmanaged"),
        model="gpt-oss-20b",
        context_length=4096,
    )
    assert result["ok"] is True


def test_lmstudio_start_server_reports_existing_server(monkeypatch):
    adapter = LMStudioAdapter()
    monkeypatch.setattr(
        LMStudioAdapter,
        "poll_status",
        lambda self, cfg, quick=False: {"server_running": True},
    )

    result = adapter.start_server(_base_cfg())

    assert result == {"ok": True, "note": "LM Studio server already running."}


def test_lmstudio_start_server_reports_unreachable_api_after_cli_wakeup(monkeypatch):
    adapter = LMStudioAdapter()
    monkeypatch.setattr(
        LMStudioAdapter,
        "poll_status",
        lambda self, cfg, quick=False: {"server_running": False},
    )
    monkeypatch.setattr(
        LMStudioAdapter,
        "detect_installation",
        lambda self, cfg: {"ok": True, "installed": True, "binary": "lms"},
    )
    monkeypatch.setattr(
        LMStudioAdapter,
        "_run_cmd",
        lambda self, args, timeout=45: {
            "ok": True,
            "stdout": "Waking up LM Studio service...",
        },
    )
    monkeypatch.setattr(
        LMStudioAdapter,
        "_wait_until_running",
        lambda self, cfg, timeout_seconds=30: False,
    )

    result = adapter.start_server(_base_cfg())

    assert result["ok"] is False
    assert "did not become reachable" in result["error"]
    assert "External HTTP only" in result["error"]


def test_lmstudio_remote_unmanaged_unload_uses_http(monkeypatch):
    adapter = LMStudioAdapter()

    def fake_post(url, json, timeout, headers=None):
        assert url.endswith("/api/v0/model/unload")
        assert json["model"] == "gpt-oss-20b"
        return _FakeResponse({"ok": True})

    monkeypatch.setattr("app.local_providers.lmstudio.requests.post", fake_post)

    result = adapter.unload_model(
        _base_cfg(local_provider_mode="remote-unmanaged"),
        model="gpt-oss-20b",
    )
    assert result["ok"] is True


def test_lmstudio_list_models_passes_api_token(monkeypatch):
    adapter = LMStudioAdapter()
    captured = {}

    def fake_get(url, timeout, headers=None):
        captured["url"] = url
        captured["timeout"] = timeout
        captured["headers"] = headers
        return _FakeResponse({"data": [{"id": "gemma-4-E2B-it"}]})

    monkeypatch.setattr("app.local_providers.lmstudio.requests.get", fake_get)

    result = adapter.list_models(
        _base_cfg(
            local_provider="custom-openai-compatible",
            local_provider_api_token="secret-token",
        )
    )
    assert result["ok"] is True
    assert result["models"] == ["gemma-4-E2B-it"]
    assert captured["headers"] == {"Authorization": "Bearer secret-token"}


def test_lmstudio_custom_provider_uses_remote_unmanaged_capabilities():
    adapter = LMStudioAdapter()
    capabilities = adapter.capabilities(
        _base_cfg(local_provider="custom-openai-compatible")
    )
    assert capabilities["start_stop"] is False
    assert capabilities["load_unload"] is False
    assert capabilities["context_length"] is True


def test_lmstudio_remote_unmanaged_keeps_load_controls():
    adapter = LMStudioAdapter()
    capabilities = adapter.capabilities(
        _base_cfg(local_provider="lmstudio", local_provider_mode="remote-unmanaged")
    )
    assert capabilities["start_stop"] is False
    assert capabilities["load_unload"] is True
    assert capabilities["context_length"] is True
    assert capabilities["logs_stream"] is False
