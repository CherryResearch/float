from app.local_providers.selection import (
    effective_provider_for_runtime,
    provider_model_for_action,
    provider_runtime_response,
)


def test_provider_selection_precedence_is_explicit_marker_then_configured():
    cfg = {
        "local_provider": "ollama",
        "transformer_model": "lmstudio",
    }

    assert (
        effective_provider_for_runtime(
            cfg,
            requested_model="",
            explicit_provider="custom-openai-compatible",
        )
        == "custom-openai-compatible"
    )
    assert effective_provider_for_runtime(cfg, requested_model="lmstudio") == "lmstudio"
    assert effective_provider_for_runtime(cfg, requested_model="") == "ollama"
    assert effective_provider_for_runtime(cfg, requested_model="gemma-4") is None


def test_provider_model_action_and_runtime_mapping_keep_marker_semantics():
    assert provider_model_for_action("lmstudio") is None
    assert provider_model_for_action(" gemma-4 ") == "gemma-4"

    runtime = provider_runtime_response(
        {
            "provider": "lmstudio",
            "model_loaded": True,
            "loaded_model": "gemma-4",
            "effective_model": "gemma-4",
            "last_error": None,
        }
    )

    assert runtime["active_backend"] == "provider"
    assert runtime["model"] == "lmstudio"
    assert runtime["loaded"] is True
    assert runtime["effective_model_id"] == "gemma-4"
