from __future__ import annotations

from typing import Any, Dict, Optional

from .manager import LocalProviderManager


def normalize_local_provider(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"lm-studio", "lm_studio"}:
        raw = "lmstudio"
    if raw in {"lmstudio", "ollama", "custom-openai-compatible"}:
        return raw
    return "lmstudio"


def default_local_provider_port(provider: str) -> int:
    return 11434 if provider == "ollama" else 1234


def provider_marker_from_model(value: Any) -> Optional[str]:
    marker = str(value or "").strip().lower()
    if LocalProviderManager.is_provider_marker(marker):
        return marker
    return None


def provider_model_for_action(value: Any) -> Optional[str]:
    candidate = str(value or "").strip()
    if not candidate or LocalProviderManager.is_provider_marker(candidate):
        return None
    return candidate


def effective_provider_for_runtime(
    cfg: Dict[str, Any] | None,
    *,
    requested_model: Optional[str] = None,
    explicit_provider: Optional[str] = None,
) -> Optional[str]:
    cfg_dict = cfg if isinstance(cfg, dict) else {}
    if explicit_provider:
        normalized = normalize_local_provider(explicit_provider)
        if LocalProviderManager.is_provider_marker(normalized):
            return normalized
    marker = provider_marker_from_model(requested_model)
    if marker:
        return marker
    if isinstance(requested_model, str) and requested_model.strip():
        return None
    configured_provider = normalize_local_provider(cfg_dict.get("local_provider"))
    if LocalProviderManager.is_provider_marker(configured_provider):
        return configured_provider
    configured_marker = provider_marker_from_model(cfg_dict.get("transformer_model"))
    if configured_marker:
        return configured_marker
    return None


def provider_runtime_response(runtime: Dict[str, Any]) -> Dict[str, Any]:
    mapped = dict(runtime or {})
    mapped["mode"] = "local"
    mapped["active_backend"] = "provider"
    mapped["loaded"] = bool(mapped.get("model_loaded"))
    mapped["load_state"] = "ready" if mapped.get("model_loaded") else "idle"
    mapped["load_error"] = mapped.get("last_error")
    provider_name = str(mapped.get("provider") or "").strip()
    loaded_model = str(mapped.get("loaded_model") or "").strip()
    if provider_name:
        mapped["model"] = provider_name
    elif loaded_model:
        mapped["model"] = loaded_model
    effective_model = str(mapped.get("effective_model") or "").strip()
    if effective_model:
        mapped["effective_model_id"] = effective_model
    return mapped


__all__ = [
    "default_local_provider_port",
    "effective_provider_for_runtime",
    "normalize_local_provider",
    "provider_marker_from_model",
    "provider_model_for_action",
    "provider_runtime_response",
]
