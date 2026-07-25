from __future__ import annotations

import hashlib
import re
import threading
import time
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

import requests
from app import config as app_config

OPENAI_MODELS_CACHE_TTL_SECONDS = 15 * 60
openai_models_cache: Dict[str, Dict[str, Any]] = {}
openai_models_cache_lock = threading.Lock()

_OPENAI_NON_CHAT_MODEL_MARKERS = (
    "embedding",
    "embed",
    "tts",
    "whisper",
    "transcribe",
    "speech",
    "audio",
    "realtime",
    "image",
    "dall-e",
    "moderation",
    "computer-use",
    "sora",
)
_OPENAI_LEGACY_COMPLETION_PREFIXES = (
    "ada-",
    "babbage-",
    "curie-",
    "davinci-",
    "text-davinci-",
)
_OPENAI_LEGACY_COMPLETION_SUFFIXES = ("-instruct",)
_OPENAI_MODEL_SIZE_RANK = {
    "base": 5,
    "chat": 4,
    "codex": 3,
    "pro": 2,
    "max": 1,
    "mini": -1,
    "nano": -2,
}
_OPENAI_ROLLING_CHAT_ALIASES = {"chat-latest": "GPT latest"}

SERVER_MODEL_PROBE_TIMEOUT_SECONDS = 0.8
SERVER_MODEL_PROBE_REFRESH_TIMEOUT_SECONDS = 1.5
SERVER_MODEL_PROBE_DEADLINE_SECONDS = 2.0
SERVER_MODEL_PROBE_REFRESH_DEADLINE_SECONDS = 5.0


def responses_api_base(api_url: Optional[str]) -> str:
    """Return an OpenAI-compatible API base without endpoint suffixes."""

    default_base = app_config.OPENAI_RESPONSES_URL.rsplit("/responses", 1)[0]
    trimmed = str(api_url or "").strip()
    if not trimmed:
        return default_base
    normalized = trimmed.rstrip("/")
    for suffix in ("/responses", "/chat/completions"):
        if normalized.endswith(suffix):
            base = normalized[: -len(suffix)] or default_base
            return base.rstrip("/") or default_base
    return normalized


def _openai_models_cache_key(base_url: str, api_key: str) -> str:
    digest = hashlib.sha256()
    digest.update(base_url.strip().encode("utf-8"))
    digest.update(b"\n")
    digest.update(api_key.strip().encode("utf-8"))
    return digest.hexdigest()


def get_cached_openai_models(base_url: str, api_key: str) -> Optional[List[str]]:
    cache_key = _openai_models_cache_key(base_url, api_key)
    now = time.monotonic()
    with openai_models_cache_lock:
        stale_keys = [
            key
            for key, entry in openai_models_cache.items()
            if now - float(entry.get("fetched_at", 0.0))
            >= OPENAI_MODELS_CACHE_TTL_SECONDS
        ]
        for key in stale_keys:
            openai_models_cache.pop(key, None)
        entry = openai_models_cache.get(cache_key)
        if not entry:
            return None
        models = entry.get("models", [])
        return list(models) if isinstance(models, list) else None


def store_cached_openai_models(base_url: str, api_key: str, models: List[str]) -> None:
    cache_key = _openai_models_cache_key(base_url, api_key)
    with openai_models_cache_lock:
        openai_models_cache[cache_key] = {
            "models": list(models),
            "fetched_at": time.monotonic(),
        }


def _openai_chat_model_allowed(model_id: str) -> bool:
    lowered = str(model_id or "").strip().lower()
    if not lowered:
        return False
    if lowered.startswith(_OPENAI_LEGACY_COMPLETION_PREFIXES):
        return False
    if lowered.endswith(_OPENAI_LEGACY_COMPLETION_SUFFIXES) or any(
        f"{suffix}-" in lowered for suffix in _OPENAI_LEGACY_COMPLETION_SUFFIXES
    ):
        return False
    return not any(marker in lowered for marker in _OPENAI_NON_CHAT_MODEL_MARKERS)


def _parse_openai_model_date(model_id: str) -> int:
    match = re.search(r"(?:^|-)(20\d{2})-(\d{2})-(\d{2})(?:$|-)", model_id)
    if not match:
        return 0
    return int(f"{match.group(1)}{match.group(2)}{match.group(3)}")


def _openai_model_sort_key(model_id: str) -> tuple[Any, ...]:
    lowered = str(model_id or "").strip().lower()
    if lowered in _OPENAI_ROLLING_CHAT_ALIASES:
        return (-1, lowered)
    match = re.match(r"^gpt-(\d+)(?:\.(\d+))?", lowered)
    if not match:
        return (1, lowered)
    suffix = lowered[len(match.group(0)) :].lstrip("-")
    size = next(
        (part for part in suffix.split("-") if part in _OPENAI_MODEL_SIZE_RANK),
        "base",
    )
    return (
        0,
        -(int(match.group(1)) if match.group(1) else 0),
        -(int(match.group(2)) if match.group(2) else 0),
        1 if _parse_openai_model_date(lowered) else 0,
        -_OPENAI_MODEL_SIZE_RANK.get(size, _OPENAI_MODEL_SIZE_RANK["base"]),
        -_parse_openai_model_date(lowered),
        lowered,
    )


def _sort_openai_model_ids(model_ids: Iterable[str]) -> List[str]:
    return sorted(model_ids, key=_openai_model_sort_key)


def filter_openai_model_ids(
    model_ids: List[str],
    *,
    include_non_chat: bool = False,
) -> List[str]:
    if include_non_chat:
        return _sort_openai_model_ids(model_ids)
    return _sort_openai_model_ids(
        model_id for model_id in model_ids if _openai_chat_model_allowed(model_id)
    )


def _openai_best_concrete_chat_model(
    model_ids: Iterable[str],
) -> Optional[str]:
    candidates = [
        model_id
        for model_id in model_ids
        if _openai_chat_model_allowed(model_id)
        and str(model_id or "").strip().lower() not in _OPENAI_ROLLING_CHAT_ALIASES
        and str(model_id or "").strip().lower().startswith("gpt-")
    ]
    if not candidates:
        return None
    stable_aliases = [
        model_id for model_id in candidates if _parse_openai_model_date(model_id) == 0
    ]
    ranked = _sort_openai_model_ids(stable_aliases or candidates)
    return ranked[0] if ranked else None


def openai_model_alias_metadata(
    model_ids: Iterable[str],
) -> Dict[str, Dict[str, str]]:
    by_normalized = {
        str(model_id or "").strip().lower(): str(model_id or "").strip()
        for model_id in model_ids
        if str(model_id or "").strip()
    }
    target = _openai_best_concrete_chat_model(by_normalized.values())
    aliases: Dict[str, Dict[str, str]] = {}
    for alias, label in _OPENAI_ROLLING_CHAT_ALIASES.items():
        raw_alias = by_normalized.get(alias)
        if not raw_alias:
            continue
        display_label = f"{label} ({target})" if target else f"{label} ({raw_alias})"
        payload = {
            "label": label,
            "display_label": display_label,
        }
        if target:
            payload["target_model"] = target
        aliases[raw_alias] = payload
    return aliases


def server_model_probe_targets(server_url: str) -> List[str]:
    value = str(server_url or "").strip()
    if not value:
        return []
    if "://" not in value and not value.startswith("/"):
        value = f"http://{value}"
    try:
        parsed = urlparse(value)
    except Exception:
        return []
    if not parsed.scheme or not parsed.netloc:
        return []
    origin = f"{parsed.scheme}://{parsed.netloc}"
    path = (parsed.path or "").rstrip("/")
    lowered = path.lower()
    targets: List[str] = []
    seen: set[str] = set()

    def add(pathname: str) -> None:
        normalized_path = pathname if pathname.startswith("/") else f"/{pathname}"
        target = f"{origin}{normalized_path}"
        if target not in seen:
            seen.add(target)
            targets.append(target)

    endpoint_suffixes = (
        "/chat/completions",
        "/completions",
        "/responses",
    )
    for suffix in endpoint_suffixes:
        if lowered.endswith(suffix):
            add(path[: -len(suffix)] + "/models")
            break
    if lowered.endswith("/models"):
        add(path or "/models")
    elif re.search(r"/v\d+$", lowered):
        add(f"{path}/models")
    elif path:
        add(f"{path}/v1/models")
        add(f"{path}/models")

    add("/api/v0/models")
    add("/api/v1/models")
    add("/v1/models")
    add("/models")
    return targets


def server_model_probe_request(
    endpoint: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = SERVER_MODEL_PROBE_TIMEOUT_SECONDS,
) -> requests.Response:
    # Provider inventory probes are usually local/LAN health checks. Avoid the
    # shared retrying session so a down provider cannot stall the FastAPI loop.
    return requests.get(endpoint, headers=headers or None, timeout=timeout)


def extract_model_inventory(payload: Any) -> Dict[str, Any]:
    models: List[str] = []
    model_details: Dict[str, Dict[str, Any]] = {}
    loaded_model = ""

    def positive_int(value: Any) -> Optional[int]:
        if isinstance(value, bool):
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    def capability_value(raw_entry: Dict[str, Any], *names: str) -> Optional[int]:
        containers = [raw_entry]
        for key in ("capabilities", "limits", "limit", "metadata"):
            nested = raw_entry.get(key)
            if isinstance(nested, dict):
                containers.append(nested)
        for container in containers:
            for name in names:
                value = positive_int(container.get(name))
                if value is not None:
                    return value
        return None

    def append_entry(raw_entry: Any) -> None:
        nonlocal loaded_model
        if isinstance(raw_entry, str) and raw_entry.strip():
            models.append(raw_entry.strip())
            return
        if not isinstance(raw_entry, dict):
            return
        value = raw_entry.get("id") or raw_entry.get("model") or raw_entry.get("name")
        if not isinstance(value, str) or not value.strip():
            return
        model_id = value.strip()
        models.append(model_id)
        detail: Dict[str, Any] = {"id": model_id}
        max_context_length = capability_value(
            raw_entry,
            "max_context_length",
            "context_length",
            "context_window",
            "max_model_len",
            "n_ctx",
            "context",
        )
        if max_context_length is not None:
            detail["max_context_length"] = max_context_length
        max_output_tokens = capability_value(
            raw_entry,
            "max_output_tokens",
            "max_completion_tokens",
            "output_token_limit",
            "max_generation_tokens",
            "output",
        )
        if max_output_tokens is not None:
            detail["max_output_tokens"] = max_output_tokens
        model_details[model_id] = {
            **model_details.get(model_id, {}),
            **detail,
        }
        state = (
            str(raw_entry.get("state") or raw_entry.get("status") or "").strip().lower()
        )
        if not loaded_model and state in {"loaded", "active", "running"}:
            loaded_model = model_id

    if isinstance(payload, dict):
        for key in ("data", "models"):
            raw_models = payload.get(key)
            if isinstance(raw_models, list):
                for item in raw_models:
                    append_entry(item)
        for key in ("loaded_model", "active_model", "current_model", "model"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                loaded_model = loaded_model or value.strip()
                models.append(value.strip())
                break
    elif isinstance(payload, list):
        for item in payload:
            append_entry(item)

    deduped = sorted({model for model in models if model})
    return {
        "models": deduped,
        "loaded_model": loaded_model,
        "model_details": [
            model_details[model_id] for model_id in deduped if model_id in model_details
        ],
    }


def probe_server_model_inventory(
    target_url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    refresh: bool = False,
) -> Dict[str, Any]:
    reachable = False
    last_error = ""
    checked_endpoints: List[str] = []
    timeout_seconds = (
        SERVER_MODEL_PROBE_REFRESH_TIMEOUT_SECONDS
        if refresh
        else SERVER_MODEL_PROBE_TIMEOUT_SECONDS
    )
    deadline_seconds = (
        SERVER_MODEL_PROBE_REFRESH_DEADLINE_SECONDS
        if refresh
        else SERVER_MODEL_PROBE_DEADLINE_SECONDS
    )
    deadline = time.monotonic() + deadline_seconds
    for endpoint in server_model_probe_targets(target_url):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            last_error = "probe timed out"
            break
        checked_endpoints.append(endpoint)
        try:
            response = server_model_probe_request(
                endpoint,
                headers=headers,
                timeout=max(0.1, min(timeout_seconds, remaining)),
            )
        except requests.RequestException as exc:
            last_error = str(exc)
            continue
        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code in {401, 403}:
            return {
                "status": "success",
                "models": [],
                "reachable": True,
                "endpoint": endpoint,
                "checked_endpoints": checked_endpoints,
                "auth_required": True,
            }
        if status_code >= 400:
            last_error = str(getattr(response, "text", "") or f"HTTP {status_code}")
            continue
        reachable = True
        try:
            payload = response.json()
        except Exception as exc:
            last_error = str(exc)
            continue
        inventory = extract_model_inventory(payload)
        models = inventory.get("models", [])
        if models:
            return {
                "status": "success",
                "models": models,
                "model_details": inventory.get("model_details", []),
                "loaded_model": inventory.get("loaded_model") or "",
                "reachable": True,
                "endpoint": endpoint,
                "checked_endpoints": checked_endpoints,
            }
    return {
        "status": "success",
        "models": [],
        "loaded_model": "",
        "reachable": reachable,
        "checked_endpoints": checked_endpoints,
        "error": last_error,
    }


__all__ = [
    "OPENAI_MODELS_CACHE_TTL_SECONDS",
    "extract_model_inventory",
    "filter_openai_model_ids",
    "get_cached_openai_models",
    "openai_model_alias_metadata",
    "openai_models_cache",
    "probe_server_model_inventory",
    "responses_api_base",
    "server_model_probe_request",
    "server_model_probe_targets",
    "store_cached_openai_models",
]
