import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def add_backend_to_sys_path():
    backend_dir = Path(__file__).resolve().parents[2]
    backend_dir = str(backend_dir)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)


def test_provider_models_filters_embedding_inventory():
    from app.local_providers.manager import LocalProviderManager

    manager = LocalProviderManager(
        lambda: {
            "local_provider": "lmstudio",
            "local_provider_mode": "local-managed",
            "local_provider_host": "127.0.0.1",
            "local_provider_port": 1234,
            "local_provider_api_token": "",
            "local_provider_preferred_model": "",
        }
    )
    manager.provider_snapshot = lambda provider, **kwargs: {  # type: ignore[method-assign]
        "provider": provider,
        "models": ["text-embedding-nomic-embed-text-v1.5", "gpt-oss-20b"],
        "runtime": {
            "provider": provider,
            "server_running": True,
            "model_loaded": True,
            "loaded_model": "text-embedding-nomic-embed-text-v1.5",
        },
    }

    snapshot = manager.provider_models("lmstudio")

    assert snapshot["models"] == ["gpt-oss-20b"]


def test_resolve_inference_target_rejects_embedding_only_loaded_model():
    from app.local_providers.manager import LocalProviderManager

    manager = LocalProviderManager(
        lambda: {
            "local_provider": "lmstudio",
            "local_provider_mode": "local-managed",
            "local_provider_host": "127.0.0.1",
            "local_provider_port": 1234,
            "local_provider_api_token": "",
            "local_provider_preferred_model": "",
        }
    )
    manager.provider_snapshot = lambda provider, **kwargs: {  # type: ignore[method-assign]
        "provider": provider,
        "models": ["text-embedding-nomic-embed-text-v1.5"],
        "runtime": {
            "provider": provider,
            "server_running": True,
            "model_loaded": False,
            "loaded_model": "text-embedding-nomic-embed-text-v1.5",
            "effective_model": None,
        },
    }

    with pytest.raises(RuntimeError, match="embedding model"):
        manager.resolve_inference_target(
            provider="lmstudio", requested_model="lmstudio"
        )


def test_resolve_inference_target_uses_hf_token_for_huggingface_endpoint():
    from app.local_providers.manager import LocalProviderManager

    manager = LocalProviderManager(
        lambda: {
            "local_provider": "custom-openai-compatible",
            "local_provider_mode": "remote-unmanaged",
            "local_provider_base_url": "https://router.huggingface.co/v1",
            "local_provider_api_token": "",
            "local_provider_preferred_model": "openai/gpt-oss-120b",
            "hf_token": "hf-secret-token",
        }
    )
    manager.provider_snapshot = lambda provider, **kwargs: {  # type: ignore[method-assign]
        "provider": provider,
        "models": ["openai/gpt-oss-120b"],
        "runtime": {
            "provider": provider,
            "server_running": True,
            "model_loaded": True,
            "loaded_model": "openai/gpt-oss-120b",
            "effective_model": "openai/gpt-oss-120b",
        },
    }

    target = manager.resolve_inference_target(
        provider="custom-openai-compatible",
        requested_model="custom-openai-compatible",
    )

    assert target["base_url"] == "https://router.huggingface.co/v1"
    assert target["api_token"] == "hf-secret-token"
    assert target["model"] == "openai/gpt-oss-120b"


def test_provider_snapshot_does_not_resurrect_ready_state_from_cache():
    from app.local_providers.manager import LocalProviderManager

    class _FakeAdapter:
        def detect_installation(self, cfg):
            return {"ok": True, "installed": True, "binary": "lms"}

        def resolve_base_url(self, cfg, *, with_v1):
            return "http://127.0.0.1:1234/v1" if with_v1 else "http://127.0.0.1:1234"

        def poll_status(self, cfg, *, quick=False):
            return {
                "ok": True,
                "server_running": False,
                "model_loaded": False,
                "loaded_model": None,
                "context_length": None,
                "details": {},
            }

        def list_models(self, cfg):
            return {"ok": False, "models": []}

        def capabilities(self, cfg):
            return {
                "start_stop": True,
                "load_unload": True,
                "context_length": True,
                "logs_stream": True,
            }

    manager = LocalProviderManager(
        lambda: {
            "local_provider": "lmstudio",
            "local_provider_mode": "local-managed",
            "local_provider_host": "127.0.0.1",
            "local_provider_port": 1234,
            "local_provider_api_token": "",
            "local_provider_preferred_model": "",
        }
    )
    manager._adapters["lmstudio"] = _FakeAdapter()
    manager._store_runtime(
        "lmstudio",
        {
            "provider": "lmstudio",
            "server_running": True,
            "model_loaded": True,
            "loaded_model": "gpt-oss-20b",
            "context_length": 8192,
            "details": {"source": "cached"},
        },
    )
    manager._store_models("lmstudio", ["gpt-oss-20b"])

    snapshot = manager.provider_snapshot("lmstudio", refresh_models=True)
    runtime = snapshot["runtime"]

    assert runtime["server_running"] is False
    assert runtime["model_loaded"] is False
    assert runtime["loaded_model"] == "gpt-oss-20b"
    assert runtime["chat_ready"] is False
    assert runtime["inventory_source"] == "cache"
    assert runtime["inventory_stale"] is True


def test_quick_provider_status_skips_model_inventory_refresh():
    from app.local_providers.manager import LocalProviderManager

    class _FakeAdapter:
        def __init__(self):
            self.list_calls = 0

        def detect_installation(self, cfg):
            return {"ok": True, "installed": True, "binary": "fake"}

        def resolve_base_url(self, cfg, *, with_v1):
            return "http://127.0.0.1:1234/v1" if with_v1 else "http://127.0.0.1:1234"

        def poll_status(self, cfg, *, quick=False):
            assert quick is True
            return {
                "ok": True,
                "server_running": False,
                "model_loaded": False,
                "loaded_model": None,
                "context_length": None,
                "details": {},
            }

        def list_models(self, cfg):
            self.list_calls += 1
            raise AssertionError("quick status should not refresh model inventory")

        def capabilities(self, cfg):
            return {}

    manager = LocalProviderManager(
        lambda: {
            "local_provider": "lmstudio",
            "local_provider_mode": "local-managed",
            "local_provider_host": "127.0.0.1",
            "local_provider_port": 1234,
            "local_provider_api_token": "",
            "local_provider_preferred_model": "",
        }
    )
    fake_adapter = _FakeAdapter()
    manager._adapters["lmstudio"] = fake_adapter

    runtime = manager.provider_status("lmstudio", quick=True)

    assert runtime["provider"] == "lmstudio"
    assert runtime["server_running"] is False
    assert runtime["inventory_source"] == "unknown"
    assert fake_adapter.list_calls == 0


def test_quick_provider_status_uses_adapter_openai_inventory_snapshot():
    from app.local_providers.manager import LocalProviderManager

    class _FakeAdapter:
        def __init__(self):
            self.list_calls = 0

        def detect_installation(self, cfg):
            return {"ok": True, "installed": True, "binary": "fake"}

        def resolve_base_url(self, cfg, *, with_v1):
            return "http://127.0.0.1:1234/v1" if with_v1 else "http://127.0.0.1:1234"

        def poll_status(self, cfg, *, quick=False):
            assert quick is True
            return {
                "ok": True,
                "server_running": True,
                "inventory_reachable": True,
                "models": ["google/gemma-4-12B-it-qat-q4_0-gguf"],
                "model_loaded": False,
                "loaded_model": None,
                "context_length": None,
                "details": {},
            }

        def list_models(self, cfg):
            self.list_calls += 1
            raise AssertionError("quick status should not run full model refresh")

        def capabilities(self, cfg):
            return {}

    manager = LocalProviderManager(
        lambda: {
            "local_provider": "lmstudio",
            "local_provider_mode": "local-managed",
            "local_provider_host": "127.0.0.1",
            "local_provider_port": 1234,
            "local_provider_api_token": "",
            "local_provider_preferred_model": "",
        }
    )
    fake_adapter = _FakeAdapter()
    manager._adapters["lmstudio"] = fake_adapter

    runtime = manager.provider_status("lmstudio", quick=True)

    assert runtime["server_running"] is True
    assert runtime["inventory_reachable"] is True
    assert runtime["inventory_source"] == "quick"
    assert runtime["inventory_model_count"] == 1
    assert runtime["effective_model"] == "google/gemma-4-12B-it-qat-q4_0-gguf"
    assert fake_adapter.list_calls == 0


def test_quick_provider_status_prefers_openai_inventory_over_stale_cache():
    from app.local_providers.manager import LocalProviderManager

    class _FakeAdapter:
        def __init__(self):
            self.list_calls = 0

        def detect_installation(self, cfg):
            return {"ok": True, "installed": True, "binary": "fake"}

        def resolve_base_url(self, cfg, *, with_v1):
            return "http://127.0.0.1:1234/v1" if with_v1 else "http://127.0.0.1:1234"

        def poll_status(self, cfg, *, quick=False):
            assert quick is True
            return {
                "ok": True,
                "server_running": True,
                "inventory_reachable": True,
                "models": [
                    "google/gemma-4-12b-live-test",
                    "google/gemma-4-31b-qat",
                ],
                "model_loaded": False,
                "loaded_model": None,
                "context_length": None,
                "details": {},
            }

        def list_models(self, cfg):
            self.list_calls += 1
            raise AssertionError("quick status should not run full model refresh")

        def capabilities(self, cfg):
            return {}

    manager = LocalProviderManager(
        lambda: {
            "local_provider": "lmstudio",
            "local_provider_mode": "local-managed",
            "local_provider_host": "127.0.0.1",
            "local_provider_port": 1234,
            "local_provider_api_token": "",
            "local_provider_preferred_model": "",
        }
    )
    fake_adapter = _FakeAdapter()
    manager._adapters["lmstudio"] = fake_adapter
    manager._store_runtime(
        "lmstudio",
        {
            "server_running": True,
            "model_loaded": True,
            "loaded_model": "google/gemma-4-12b-live-test",
            "context_length": 4096,
        },
    )
    manager._store_models("lmstudio", ["stale-gemma"])

    runtime = manager.provider_status("lmstudio", quick=True)

    assert runtime["server_running"] is True
    assert runtime["inventory_reachable"] is True
    assert runtime["inventory_source"] == "quick"
    assert runtime["inventory_stale"] is False
    assert runtime["inventory_model_count"] == 2
    assert runtime["loaded_model"] == "google/gemma-4-12b-live-test"
    assert runtime["model_loaded"] is True
    assert runtime["chat_ready"] is True
    assert runtime["effective_model"] == "google/gemma-4-12b-live-test"
    assert fake_adapter.list_calls == 0


def test_provider_manager_shutdown_stops_owned_models_and_servers():
    from app.local_providers.manager import LocalProviderManager

    class _FakeAdapter:
        def __init__(self):
            self.unload_calls = []
            self.stop_calls = []

        def detect_installation(self, cfg):
            return {"ok": True, "installed": True, "binary": "fake"}

        def resolve_base_url(self, cfg, *, with_v1):
            return "http://127.0.0.1:1234/v1" if with_v1 else "http://127.0.0.1:1234"

        def poll_status(self, cfg, *, quick=False):
            return {"server_running": False, "details": {}}

        def list_models(self, cfg):
            return {"ok": True, "models": []}

        def start_server(self, cfg):
            return {"ok": True}

        def stop_server(self, cfg):
            self.stop_calls.append(dict(cfg))
            return {"ok": True}

        def load_model(self, cfg, *, model, context_length=None):
            return {"ok": True}

        def unload_model(self, cfg, *, model=None):
            self.unload_calls.append({"cfg": dict(cfg), "model": model})
            return {"ok": True}

        def stream_logs(self, cfg, stop_event):
            if False:
                yield {}

        def capabilities(self, cfg):
            return {}

    manager = LocalProviderManager(
        lambda: {
            "local_provider": "lmstudio",
            "local_provider_mode": "remote-unmanaged",
            "local_provider_host": "127.0.0.1",
            "local_provider_port": 1234,
            "local_provider_api_token": "",
            "local_provider_preferred_model": "",
        }
    )
    fake_adapter = _FakeAdapter()
    manager._adapters["lmstudio"] = fake_adapter
    manager._owned_servers.add("lmstudio")
    manager._owned_loaded_models["lmstudio"]["gpt-oss-20b"] = "remote-unmanaged"

    result = manager.shutdown()

    assert result["status"] == "stopped"
    assert fake_adapter.unload_calls == [
        {
            "cfg": {
                "local_provider": "lmstudio",
                "local_provider_mode": "remote-unmanaged",
                "local_provider_host": "127.0.0.1",
                "local_provider_port": 1234,
                "local_provider_base_url": "",
                "lmstudio_path": "",
                "local_provider_api_token": "",
                "local_provider_auto_start": True,
                "local_provider_preferred_model": "",
                "local_provider_default_context_length": None,
                "local_provider_show_server_logs": True,
                "local_provider_enable_cors": False,
                "local_provider_allow_lan": False,
            },
            "model": "gpt-oss-20b",
        }
    ]
    assert fake_adapter.stop_calls[0]["local_provider_mode"] == "local-managed"
    assert manager._owned_servers == set()
    assert manager._owned_loaded_models["lmstudio"] == {}


def test_provider_load_rejects_external_lmstudio_server_in_managed_mode():
    from app.local_providers.manager import LocalProviderManager

    class _FakeAdapter:
        def detect_installation(self, cfg):
            return {"ok": True, "installed": True, "binary": "fake"}

        def resolve_base_url(self, cfg, *, with_v1):
            return "http://127.0.0.1:1234/v1" if with_v1 else "http://127.0.0.1:1234"

        def poll_status(self, cfg, *, quick=False):
            return {
                "server_running": True,
                "model_loaded": False,
                "loaded_model": None,
                "context_length": None,
                "details": {},
            }

        def list_models(self, cfg):
            return {"ok": True, "models": []}

        def start_server(self, cfg):
            return {"ok": True}

        def stop_server(self, cfg):
            return {"ok": True}

        def load_model(self, cfg, *, model, context_length=None):
            raise AssertionError("load_model should not run against external LM Studio")

        def unload_model(self, cfg, *, model=None):
            raise AssertionError(
                "unload_model should not run against external LM Studio"
            )

        def stream_logs(self, cfg, stop_event):
            if False:
                yield {}

        def capabilities(self, cfg):
            return {}

    manager = LocalProviderManager(
        lambda: {
            "local_provider": "lmstudio",
            "local_provider_mode": "local-managed",
            "local_provider_host": "127.0.0.1",
            "local_provider_port": 1234,
            "local_provider_api_token": "",
            "local_provider_preferred_model": "gpt-oss-20b",
        }
    )
    manager._adapters["lmstudio"] = _FakeAdapter()

    load_result = manager.provider_load(provider="lmstudio", model="gpt-oss-20b")
    unload_result = manager.provider_unload(provider="lmstudio", model="gpt-oss-20b")
    stop_result = manager.provider_stop(provider="lmstudio")

    assert load_result["ok"] is False
    assert "outside Float" in load_result["result"]["error"]
    assert unload_result["ok"] is False
    assert "outside Float" in unload_result["result"]["error"]
    assert stop_result["ok"] is False
    assert "outside Float" in stop_result["result"]["error"]


def test_provider_load_records_operation_telemetry_and_logs():
    from app.local_providers.manager import LocalProviderManager

    class _FakeAdapter:
        def __init__(self):
            self.loaded_model = None

        def detect_installation(self, cfg):
            return {"ok": True, "installed": True, "binary": "fake-ollama"}

        def resolve_base_url(self, cfg, *, with_v1):
            return "http://127.0.0.1:11434/v1" if with_v1 else "http://127.0.0.1:11434"

        def poll_status(self, cfg, *, quick=False):
            return {
                "ok": True,
                "server_running": True,
                "model_loaded": bool(self.loaded_model),
                "loaded_model": self.loaded_model,
                "context_length": 4096 if self.loaded_model else None,
                "details": {},
            }

        def list_models(self, cfg):
            return {
                "ok": True,
                "models": [self.loaded_model] if self.loaded_model else [],
            }

        def start_server(self, cfg):
            return {"ok": True}

        def stop_server(self, cfg):
            return {"ok": True}

        def load_model(self, cfg, *, model, context_length=None):
            self.loaded_model = model
            return {
                "ok": True,
                "endpoint": "http://127.0.0.1:11434/api/generate",
                "targets": [model],
            }

        def unload_model(self, cfg, *, model=None):
            self.loaded_model = None
            return {"ok": True}

        def stream_logs(self, cfg, stop_event):
            if False:
                yield {}

        def capabilities(self, cfg):
            return {
                "start_stop": True,
                "load_unload": True,
                "context_length": True,
                "logs_stream": True,
            }

    manager = LocalProviderManager(
        lambda: {
            "local_provider": "ollama",
            "local_provider_mode": "local-managed",
            "local_provider_host": "127.0.0.1",
            "local_provider_port": 11434,
            "local_provider_api_token": "",
            "local_provider_preferred_model": "",
        }
    )
    manager._adapters["ollama"] = _FakeAdapter()

    result = manager.provider_load(
        provider="ollama",
        model="gpt-oss-20b",
        context_length=4096,
    )

    assert result["ok"] is True
    operation = result["operation"]
    assert operation["id"] == "load#1"
    assert operation["status"] == "ok"
    assert operation["context_length"] == 4096
    assert operation["result"]["endpoint"] == "http://127.0.0.1:11434/api/generate"
    assert operation["result"]["targets"] == ["gpt-oss-20b"]

    runtime = result["runtime"]
    assert runtime["loaded_model"] == "gpt-oss-20b"
    assert runtime["last_operation"]["id"] == "load#1"
    assert runtime["last_operation"]["status"] == "ok"

    logs = manager.provider_logs(provider="ollama", cursor=0, limit=20)["entries"]
    messages = [str(entry.get("message") or "") for entry in logs]
    assert any(msg.startswith("[load#1] load begin") for msg in messages)
    assert any("[load#1] load ok" in msg for msg in messages)


def test_provider_stop_rejection_records_operation_telemetry():
    from app.local_providers.manager import LocalProviderManager

    class _FakeAdapter:
        def detect_installation(self, cfg):
            return {"ok": True, "installed": True, "binary": "fake"}

        def resolve_base_url(self, cfg, *, with_v1):
            return "http://127.0.0.1:1234/v1" if with_v1 else "http://127.0.0.1:1234"

        def poll_status(self, cfg, *, quick=False):
            return {
                "ok": True,
                "server_running": True,
                "model_loaded": True,
                "loaded_model": "gpt-oss-20b",
                "context_length": 8192,
                "details": {},
            }

        def list_models(self, cfg):
            return {"ok": True, "models": ["gpt-oss-20b"]}

        def start_server(self, cfg):
            return {"ok": True}

        def stop_server(self, cfg):
            raise AssertionError(
                "stop_server should not run against external LM Studio"
            )

        def load_model(self, cfg, *, model, context_length=None):
            return {"ok": True}

        def unload_model(self, cfg, *, model=None):
            return {"ok": True}

        def stream_logs(self, cfg, stop_event):
            if False:
                yield {}

        def capabilities(self, cfg):
            return {
                "start_stop": True,
                "load_unload": True,
                "context_length": True,
                "logs_stream": True,
            }

    manager = LocalProviderManager(
        lambda: {
            "local_provider": "lmstudio",
            "local_provider_mode": "local-managed",
            "local_provider_host": "127.0.0.1",
            "local_provider_port": 1234,
            "local_provider_api_token": "",
            "local_provider_preferred_model": "gpt-oss-20b",
        }
    )
    manager._adapters["lmstudio"] = _FakeAdapter()

    result = manager.provider_stop(provider="lmstudio")

    assert result["ok"] is False
    operation = result["operation"]
    assert operation["id"] == "stop#1"
    assert operation["status"] == "error"
    assert operation["result"]["rejected"] is True
    assert "outside Float" in operation["result"]["error"]
    assert result["runtime"]["last_operation"]["id"] == "stop#1"
    assert result["runtime"]["last_operation"]["status"] == "error"

    logs = manager.provider_logs(provider="lmstudio", cursor=0, limit=20)["entries"]
    messages = [str(entry.get("message") or "") for entry in logs]
    assert any(msg.startswith("[stop#1] stop begin") for msg in messages)
    assert any(
        "[stop#1] stop error" in msg and "outside Float" in msg for msg in messages
    )


def test_provider_snapshot_marks_external_server_and_loaded_model_as_unowned():
    from app.local_providers.manager import LocalProviderManager

    class _FakeAdapter:
        def detect_installation(self, cfg):
            return {"ok": True, "installed": False, "binary": None}

        def resolve_base_url(self, cfg, *, with_v1):
            return "http://127.0.0.1:1234/v1" if with_v1 else "http://127.0.0.1:1234"

        def poll_status(self, cfg, *, quick=False):
            return {
                "ok": True,
                "server_running": True,
                "model_loaded": True,
                "loaded_model": "gpt-oss-20b",
                "context_length": 8192,
                "details": {},
            }

        def list_models(self, cfg):
            return {"ok": True, "models": ["gpt-oss-20b"]}

        def capabilities(self, cfg):
            return {
                "start_stop": True,
                "load_unload": True,
                "context_length": True,
                "logs_stream": True,
            }

    manager = LocalProviderManager(
        lambda: {
            "local_provider": "lmstudio",
            "local_provider_mode": "local-managed",
            "local_provider_host": "127.0.0.1",
            "local_provider_port": 1234,
            "local_provider_api_token": "",
            "local_provider_preferred_model": "gpt-oss-20b",
        }
    )
    manager._adapters["lmstudio"] = _FakeAdapter()

    snapshot = manager.provider_snapshot("lmstudio", refresh_models=True)
    runtime = snapshot["runtime"]
    hints = manager.describe_model_locks("gpt-oss-20b", providers=["lmstudio"])

    assert runtime["server_running"] is True
    assert runtime["server_owned_by_float"] is False
    assert runtime["loaded_model"] == "gpt-oss-20b"
    assert runtime["loaded_model_owned_by_float"] is False
    assert runtime["owned_model_ids"] == []
    assert len(hints) == 1
    assert hints[0]["server_owned_by_float"] is False
    assert hints[0]["loaded_model_owned_by_float"] is False
    assert hints[0]["loaded_model"] == "gpt-oss-20b"


def test_provider_load_stops_when_managed_start_fails():
    from app.local_providers.manager import LocalProviderManager

    manager = LocalProviderManager(
        lambda: {
            "local_provider": "lmstudio",
            "local_provider_mode": "local-managed",
            "local_provider_host": "127.0.0.1",
            "local_provider_port": 1234,
            "local_provider_api_token": "",
            "local_provider_preferred_model": "gpt-oss-20b",
            "local_provider_auto_start": True,
        }
    )
    manager._status = lambda provider, quick=False: {  # type: ignore[method-assign]
        "server_running": False
    }
    manager.provider_start = lambda provider=None: {  # type: ignore[method-assign]
        "ok": False,
        "result": {
            "ok": False,
            "error": "LM Studio server did not become ready in time.",
        },
        "runtime": {"server_running": False},
    }

    result = manager.provider_load(provider="lmstudio", model="gpt-oss-20b")

    assert result["ok"] is False
    assert result["result"]["error"] == "LM Studio server did not become ready in time."


def test_resolve_inference_target_prefers_loaded_model_over_stale_preferred_request():
    from app.local_providers.manager import LocalProviderManager

    manager = LocalProviderManager(
        lambda: {
            "transformer_model": "lmstudio",
            "local_provider": "lmstudio",
            "local_provider_mode": "remote-unmanaged",
            "local_provider_host": "127.0.0.1",
            "local_provider_port": 1234,
            "local_provider_api_token": "",
            "local_provider_preferred_model": "gemma-4-e4b-it",
        }
    )
    manager.provider_snapshot = lambda provider, **kwargs: {  # type: ignore[method-assign]
        "provider": provider,
        "models": ["gemma-4-e4b-it", "openai/gpt-oss-20b"],
        "runtime": {
            "provider": provider,
            "server_running": True,
            "model_loaded": True,
            "loaded_model": "openai/gpt-oss-20b",
            "effective_model": "openai/gpt-oss-20b",
        },
    }

    target = manager.resolve_inference_target(
        provider="lmstudio",
        requested_model="gemma-4-e4b-it",
    )

    assert target["model"] == "openai/gpt-oss-20b"
