import sys
from types import SimpleNamespace


def test_builtin_presets_include_tinker_and_warn_for_manual_grok_targets(monkeypatch):
    from app.server_presets import (
        GROK_TRUST_WARNING,
        public_server_presets,
        resolve_server_auth_token,
        server_trust_warning,
    )

    monkeypatch.setenv("TINKER_API_KEY", "tinker-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
    presets = public_server_presets({"server_presets": []})
    by_id = {preset["id"]: preset for preset in presets}

    assert by_id["tinker"]["api_key_env"] == "TINKER_API_KEY"
    assert by_id["tinker"]["api_key_set"] is True
    assert by_id["tinker"]["native_tools"] is True
    assert by_id["anthropic-claude"]["api_key_set"] is True
    assert "xai-grok" not in by_id
    assert not any(
        preset.get("provider") == "xai" or "grok" in preset.get("name", "").lower()
        for preset in presets
    )
    assert (
        resolve_server_auth_token(
            {"server_presets": []},
            "https://api.anthropic.com/v1/",
            preset_id="anthropic-claude",
        )
        == "anthropic-secret"
    )
    assert (
        server_trust_warning(
            {"server_presets": []},
            base_url="https://api.x.ai/v1",
        )
        == GROK_TRUST_WARNING
    )
    assert (
        server_trust_warning(
            {"server_presets": []},
            base_url="https://router.example.test/v1",
            model="grok-via-router",
        )
        == GROK_TRUST_WARNING
    )


def test_custom_preset_resolves_only_its_named_environment_key(monkeypatch):
    from app.server_presets import (
        normalize_custom_server_presets,
        resolve_server_auth_token,
    )

    raw = [
        {
            "id": "research",
            "name": "Research endpoint",
            "provider": "openai-compatible",
            "base_url": "https://models.example.test/v1",
            "api_key_env": "RESEARCH_MODEL_KEY",
            "api_key": "must-not-be-persisted",
        }
    ]
    presets = normalize_custom_server_presets(raw)
    assert presets[0]["id"] == "custom-research"
    assert "api_key" not in presets[0]

    cfg = {"server_presets": presets, "api_key": "openai-secret"}
    monkeypatch.setenv("RESEARCH_MODEL_KEY", "research-secret")
    assert (
        resolve_server_auth_token(
            cfg,
            "https://models.example.test/v1",
            preset_id="custom-research",
        )
        == "research-secret"
    )
    assert (
        resolve_server_auth_token(
            cfg,
            "https://different.example.test/v1",
            preset_id="custom-research",
        )
        == ""
    )


def test_tinker_inventory_merges_sampler_checkpoints_before_base_models(monkeypatch):
    from app.services.tinker_inventory_service import list_tinker_account_models

    class Future:
        def __init__(self, value):
            self.value = value

        def result(self):
            return self.value

    class RestClient:
        def list_user_checkpoints(self, limit=100, offset=0):
            assert limit == 200
            assert offset == 0
            return Future(
                SimpleNamespace(
                    checkpoints=[
                        SimpleNamespace(
                            checkpoint_type="training",
                            tinker_path="tinker://run:train:0/weights/state",
                        ),
                        SimpleNamespace(
                            checkpoint_type="sampler",
                            tinker_path=(
                                "tinker://run:train:0/sampler_weights/inkling-fine-tune"
                            ),
                        ),
                    ]
                )
            )

    closed = []

    class ServiceClient:
        def __init__(self, api_key, _skip_session=False):
            assert api_key == "tinker-secret"
            assert _skip_session is True
            self.holder = SimpleNamespace(close=lambda: closed.append(True))

        def get_server_capabilities(self):
            return SimpleNamespace(
                supported_models=[
                    SimpleNamespace(
                        model_name="tml/Inkling",
                        max_context_length=262144,
                    )
                ]
            )

        def create_rest_client(self):
            return RestClient()

    monkeypatch.setitem(
        sys.modules, "tinker", SimpleNamespace(ServiceClient=ServiceClient)
    )
    result = list_tinker_account_models("tinker-secret")

    assert result["reachable"] is True
    assert result["models"] == [
        "tinker://run:train:0/sampler_weights/inkling-fine-tune",
        "tml/Inkling",
    ]
    assert result["sampler_checkpoints"] == [
        "tinker://run:train:0/sampler_weights/inkling-fine-tune"
    ]
    assert {
        "id": "tml/Inkling",
        "kind": "base",
        "source": "tinker-sdk",
        "max_context_length": 262144,
    } in result["model_details"]
    assert closed == [True]
