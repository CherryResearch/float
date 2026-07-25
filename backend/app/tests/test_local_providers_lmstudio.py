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


def test_lmstudio_quick_poll_status_does_not_probe_model_inventory(monkeypatch):
    adapter = LMStudioAdapter()
    called_urls = []

    def fake_get(url, timeout, headers=None):
        called_urls.append(url)
        if url.endswith("/api/v1/status"):
            return _FakeResponse({"status": "ok"})
        raise RuntimeError("offline")

    monkeypatch.setattr("app.local_providers.lmstudio.requests.get", fake_get)

    status = adapter.poll_status(_base_cfg(), quick=True)

    assert status["server_running"] is True
    assert status["status_reachable"] is True
    assert status["inventory_reachable"] is False
    assert all("/models" not in url for url in called_urls)


def test_lmstudio_quick_poll_status_falls_back_to_openai_inventory(monkeypatch):
    adapter = LMStudioAdapter()
    called_urls = []

    def fake_get(url, timeout, headers=None):
        called_urls.append(url)
        if url.endswith("/api/v1/status"):
            return _FakeResponse({"error": "Unexpected endpoint or method."})
        if url.endswith("/v1/models"):
            return _FakeResponse(
                {
                    "data": [
                        {
                            "id": "google/gemma-4-12b",
                            "state": "loaded",
                            "loaded_context_length": 32768,
                        }
                    ]
                }
            )
        raise RuntimeError("offline")

    monkeypatch.setattr("app.local_providers.lmstudio.requests.get", fake_get)

    status = adapter.poll_status(_base_cfg(), quick=True)

    assert status["server_running"] is True
    assert status["status_reachable"] is True
    assert status["inventory_reachable"] is True
    assert status["inventory_model_count"] == 1
    assert status["model_loaded"] is True
    assert status["loaded_model"] == "google/gemma-4-12b"
    assert status["context_length"] == 32768
    assert any(url.endswith("/v1/models") for url in called_urls)


def test_lmstudio_quick_poll_prefers_native_inventory_with_load_state(monkeypatch):
    adapter = LMStudioAdapter()
    called_urls = []

    def fake_get(url, timeout, headers=None):
        called_urls.append(url)
        if url.endswith("/api/v1/status"):
            raise RuntimeError("http 404")
        if url.endswith("/api/v0/status"):
            return _FakeResponse({"error": "Unexpected endpoint or method."})
        if url.endswith("/api/v0/models"):
            return _FakeResponse(
                {
                    "data": [
                        {
                            "id": "openai/gpt-oss-20b",
                            "state": "loaded",
                            "loaded_context_length": 8192,
                        },
                        {"id": "google/gemma-4-12b", "state": "not-loaded"},
                    ],
                    "object": "list",
                }
            )
        if url.endswith("/v1/models"):
            return _FakeResponse(
                {
                    "data": [
                        {"id": "openai/gpt-oss-20b", "object": "model"},
                        {"id": "google/gemma-4-12b", "object": "model"},
                    ],
                    "object": "list",
                }
            )
        raise RuntimeError("offline")

    monkeypatch.setattr("app.local_providers.lmstudio.requests.get", fake_get)

    status = adapter.poll_status(_base_cfg(), quick=True)
    model_urls = [url for url in called_urls if url.endswith("/models")]

    assert model_urls == ["http://127.0.0.1:1234/api/v0/models"]
    assert status["server_running"] is True
    assert status["model_loaded"] is True
    assert status["loaded_model"] == "openai/gpt-oss-20b"
    assert status["model_state_known"] is True
    assert status["model_state_source"] == "inventory"
    assert status["model_state_stale"] is False
    assert status["context_length"] == 8192


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

    assert result["ok"] is True
    assert result["note"] == "LM Studio server already running."
    assert result["base_url"] == "http://127.0.0.1:1234"


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


def test_lmstudio_start_server_uses_bind_for_lan(monkeypatch):
    adapter = LMStudioAdapter()
    captured = {}
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

    def fake_run(self, args, timeout=45):
        captured["args"] = args
        return {"ok": True, "stdout": "started"}

    monkeypatch.setattr(LMStudioAdapter, "_run_cmd", fake_run)
    monkeypatch.setattr(
        LMStudioAdapter,
        "_wait_until_running",
        lambda self, cfg, timeout_seconds=30: True,
    )

    result = adapter.start_server(_base_cfg(local_provider_allow_lan=True))

    assert result["ok"] is True
    assert "--host" not in captured["args"]
    assert captured["args"][-2:] == ["--bind", "0.0.0.0"]


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


def test_custom_provider_huggingface_url_uses_stored_hf_token(monkeypatch):
    adapter = LMStudioAdapter()
    captured = {}

    def fake_get(url, timeout, headers=None):
        captured["url"] = url
        captured["headers"] = headers
        return _FakeResponse({"data": [{"id": "openai/gpt-oss-120b"}]})

    monkeypatch.setattr("app.local_providers.lmstudio.requests.get", fake_get)

    result = adapter.list_models(
        _base_cfg(
            local_provider="custom-openai-compatible",
            local_provider_base_url="https://router.huggingface.co/v1",
            hf_token="hf-secret-token",
        )
    )

    assert result["ok"] is True
    assert result["models"] == ["openai/gpt-oss-120b"]
    assert captured["url"] == "https://router.huggingface.co/v1/models"
    assert captured["headers"] == {"Authorization": "Bearer hf-secret-token"}


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
