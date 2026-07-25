from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

TINKER_OPENAI_BASE_URL = (
    "https://tinker.thinkingmachines.dev/services/tinker-prod/oai/api/v1"
)
GROK_TRUST_WARNING = "This model may not be trustworthy."

_ENV_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_ID_PATTERN = re.compile(r"[^a-z0-9]+")


BUILTIN_SERVER_PRESETS: tuple[Dict[str, Any], ...] = (
    {
        "id": "lm-studio-local",
        "name": "LM Studio (localhost:1234)",
        "provider": "lmstudio",
        "base_url": "http://127.0.0.1:1234/v1",
        "api_key_env": "",
        "description": "OpenAI-compatible LM Studio server on this computer.",
        "builtin": True,
    },
    {
        "id": "ollama-local",
        "name": "Ollama (localhost:11434)",
        "provider": "ollama",
        "base_url": "http://127.0.0.1:11434/v1",
        "api_key_env": "",
        "description": "OpenAI-compatible Ollama server on this computer.",
        "builtin": True,
    },
    {
        "id": "tinker",
        "name": "Tinker / Inkling",
        "provider": "tinker",
        "base_url": TINKER_OPENAI_BASE_URL,
        "api_key_env": "TINKER_API_KEY",
        "native_tools": True,
        "description": (
            "Thinking Machines Tinker inference, including account sampler "
            "checkpoints and fine-tunes."
        ),
        "builtin": True,
    },
    {
        "id": "gemini",
        "name": "Gemini (OpenAI compatibility)",
        "provider": "gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "api_key_env": "GEMINI_API_KEY",
        "description": "Google Gemini through its OpenAI-compatible endpoint.",
        "builtin": True,
    },
    {
        "id": "anthropic-claude",
        "name": "Anthropic / Claude (compatibility)",
        "provider": "anthropic",
        "base_url": "https://api.anthropic.com/v1/",
        "api_key_env": "ANTHROPIC_API_KEY",
        "description": (
            "Claude through Anthropic's OpenAI SDK compatibility layer; native-only "
            "features are not available in this lane."
        ),
        "builtin": True,
    },
    {
        "id": "openrouter",
        "name": "OpenRouter",
        "provider": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "description": (
            "OpenAI-compatible routing for providers such as Anthropic and Google."
        ),
        "builtin": True,
    },
)

_BUILTIN_IDS = {str(item["id"]) for item in BUILTIN_SERVER_PRESETS}


def _slug(value: Any, fallback: str = "custom") -> str:
    normalized = _ID_PATTERN.sub("-", str(value or "").strip().lower()).strip("-")
    return normalized or fallback


def _normalize_url(value: Any) -> str:
    return str(value or "").strip().rstrip("/").lower()


def _normalize_env_name(value: Any) -> str:
    candidate = str(value or "").strip().upper()
    return candidate if _ENV_NAME_PATTERN.fullmatch(candidate) else ""


def _coerce_preset_list(raw: Any) -> List[Any]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    return list(raw) if isinstance(raw, list) else []


def normalize_custom_server_presets(raw: Any) -> List[Dict[str, Any]]:
    """Validate persisted user presets without accepting embedded secrets."""

    normalized: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(_coerce_preset_list(raw)):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        base_url = str(item.get("base_url") or "").strip()
        if not name or not base_url:
            continue
        requested_id = _slug(item.get("id") or name, f"custom-{index + 1}")
        preset_id = (
            requested_id
            if requested_id.startswith("custom-")
            else f"custom-{requested_id}"
        )
        if preset_id in seen or preset_id in _BUILTIN_IDS:
            suffix = 2
            candidate = f"{preset_id}-{suffix}"
            while candidate in seen or candidate in _BUILTIN_IDS:
                suffix += 1
                candidate = f"{preset_id}-{suffix}"
            preset_id = candidate
        seen.add(preset_id)
        provider = _slug(item.get("provider") or "openai-compatible")
        preset: Dict[str, Any] = {
            "id": preset_id,
            "name": name[:80],
            "provider": provider[:64],
            "base_url": base_url[:2048],
            "api_key_env": _normalize_env_name(item.get("api_key_env")),
            "description": str(item.get("description") or "").strip()[:240],
            "builtin": False,
        }
        if item.get("native_tools") is True:
            preset["native_tools"] = True
        warning = str(item.get("trust_warning") or "").strip()
        if provider == "xai" or "grok" in name.lower():
            preset["trust_warning"] = GROK_TRUST_WARNING
        elif warning:
            preset["trust_warning"] = warning[:240]
        normalized.append(preset)
    return normalized[:50]


def all_server_presets(cfg: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    custom = normalize_custom_server_presets((cfg or {}).get("server_presets"))
    return [dict(item) for item in BUILTIN_SERVER_PRESETS] + custom


def public_server_presets(cfg: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for preset in all_server_presets(cfg):
        item = dict(preset)
        env_name = str(item.get("api_key_env") or "")
        item["api_key_set"] = bool(env_name and os.getenv(env_name))
        result.append(item)
    return result


def serialize_custom_server_presets(raw: Any) -> str:
    return json.dumps(
        normalize_custom_server_presets(raw),
        ensure_ascii=True,
        separators=(",", ":"),
    )


def _host(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    candidate = raw if "://" in raw else f"https://{raw}"
    try:
        parsed = urlparse(candidate)
    except Exception:
        return ""
    return (parsed.hostname or "").lower()


def find_server_preset(
    cfg: Optional[Dict[str, Any]],
    *,
    preset_id: Any = None,
    base_url: Any = None,
) -> Optional[Dict[str, Any]]:
    presets = all_server_presets(cfg)
    target_url = _normalize_url(base_url)
    requested_id = str(preset_id or "").strip()
    if requested_id:
        for preset in presets:
            if str(preset.get("id") or "") != requested_id:
                continue
            if target_url and _normalize_url(preset.get("base_url")) != target_url:
                break
            return preset
    if target_url:
        for preset in presets:
            if _normalize_url(preset.get("base_url")) == target_url:
                return preset
    return None


def server_supports_native_tools(
    cfg: Optional[Dict[str, Any]],
    *,
    preset_id: Any = None,
    base_url: Any = None,
) -> bool:
    """Return whether a preset supports structured OpenAI-style tool calls."""

    preset = find_server_preset(cfg, preset_id=preset_id, base_url=base_url)
    return bool(preset and preset.get("native_tools") is True)


def _candidate_auth_env_names(
    cfg: Optional[Dict[str, Any]], base_url: Any, preset_id: Any = None
) -> Iterable[str]:
    preset = find_server_preset(cfg, preset_id=preset_id, base_url=base_url)
    if preset:
        configured = _normalize_env_name(preset.get("api_key_env"))
        if configured:
            yield configured

    host = _host(base_url)
    if host == "tinker.thinkingmachines.dev" or host.endswith(".thinkingmachines.dev"):
        yield "TINKER_API_KEY"
    elif host == "generativelanguage.googleapis.com":
        yield "GEMINI_API_KEY"
        yield "GOOGLE_API_KEY"
    elif host == "api.anthropic.com" or host.endswith(".api.anthropic.com"):
        yield "ANTHROPIC_API_KEY"
    elif host == "openrouter.ai" or host.endswith(".openrouter.ai"):
        yield "OPENROUTER_API_KEY"
    elif host == "api.groq.com" or host.endswith(".api.groq.com"):
        yield "GROQ_API_KEY"


def resolve_server_auth_token(
    cfg: Optional[Dict[str, Any]], base_url: Any, *, preset_id: Any = None
) -> str:
    """Resolve bearer auth for Server/LAN without exposing or persisting the key."""

    seen: set[str] = set()
    for env_name in _candidate_auth_env_names(cfg, base_url, preset_id):
        if env_name in seen:
            continue
        seen.add(env_name)
        value = str(os.getenv(env_name) or "").strip()
        if value:
            return value

    host = _host(base_url)
    if host == "api.openai.com" or host.endswith(".api.openai.com"):
        return str(
            (cfg or {}).get("api_key")
            or os.getenv("OPENAI_API_KEY")
            or os.getenv("API_KEY")
            or ""
        ).strip()
    return ""


def server_trust_warning(
    cfg: Optional[Dict[str, Any]],
    *,
    preset_id: Any = None,
    base_url: Any = None,
    model: Any = None,
) -> str:
    preset = find_server_preset(cfg, preset_id=preset_id, base_url=base_url)
    provider = str((preset or {}).get("provider") or "").strip().lower()
    warning = str((preset or {}).get("trust_warning") or "").strip()
    model_name = str(model or "").strip().lower()
    if provider == "xai" or _host(base_url) == "api.x.ai" or "grok" in model_name:
        return GROK_TRUST_WARNING
    return warning


__all__ = [
    "BUILTIN_SERVER_PRESETS",
    "GROK_TRUST_WARNING",
    "TINKER_OPENAI_BASE_URL",
    "all_server_presets",
    "find_server_preset",
    "normalize_custom_server_presets",
    "public_server_presets",
    "resolve_server_auth_token",
    "serialize_custom_server_presets",
    "server_trust_warning",
]
