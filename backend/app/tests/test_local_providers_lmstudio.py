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


def test_lmstudio_remote_unmanaged_load_uses_http(monkeypatch):
    adapter = LMStudioAdapter()

    def fake_post(url, json, timeout):
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


def test_lmstudio_remote_unmanaged_unload_uses_http(monkeypatch):
    adapter = LMStudioAdapter()

    def fake_post(url, json, timeout):
        assert url.endswith("/api/v0/model/unload")
        assert json["model"] == "gpt-oss-20b"
        return _FakeResponse({"ok": True})

    monkeypatch.setattr("app.local_providers.lmstudio.requests.post", fake_post)

    result = adapter.unload_model(
        _base_cfg(local_provider_mode="remote-unmanaged"),
        model="gpt-oss-20b",
    )
    assert result["ok"] is True
