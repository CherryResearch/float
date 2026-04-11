from __future__ import annotations

from typing import Any, Dict, Iterable, List

# Central registry for model aliases used across the application.
# The richer metadata lets the UI and runtime distinguish direct local
# checkpoints from provider/server-first models without hardcoded family checks.
MODEL_REGISTRY: Dict[str, Dict[str, Any]] = {
    "gpt-oss-20b": {
        "repo_id": "openai/gpt-oss-20b",
        "family": "gpt-oss",
        "lane": "local",
        "local_download_supported": True,
        "provider_supported": True,
        "mobile_catalog_allowed": False,
        "local_loader": "causal_lm",
        "supports_images": False,
        "requires": "cuda",
        "min_vram_gb": 16,
    },
    "gpt-oss-120b": {
        "repo_id": "openai/gpt-oss-120b",
        "family": "gpt-oss",
        "lane": "local",
        "local_download_supported": True,
        "provider_supported": True,
        "mobile_catalog_allowed": False,
        "local_loader": "causal_lm",
        "supports_images": False,
        "requires": "cuda",
        "min_vram_gb": 80,
    },
    "Llama-3.1-8B": {
        "repo_id": "meta-llama/Llama-3.1-8B",
        "family": "llama",
        "lane": "local",
        "local_download_supported": True,
        "provider_supported": True,
        "mobile_catalog_allowed": False,
        "local_loader": "causal_lm",
        "supports_images": False,
        "requires": "cuda",
        "min_vram_gb": 16,
    },
    "Llama-3.1-70B": {
        "repo_id": "meta-llama/Llama-3.1-70B",
        "family": "llama",
        "lane": "local",
        "local_download_supported": True,
        "provider_supported": True,
        "mobile_catalog_allowed": False,
        "local_loader": "causal_lm",
        "supports_images": False,
        "requires": "cuda",
        "min_vram_gb": 80,
    },
    "Qwen3-8B": {
        "repo_id": "Qwen/Qwen3-8B",
        "family": "qwen",
        "lane": "local",
        "local_download_supported": True,
        "provider_supported": True,
        "mobile_catalog_allowed": False,
        "local_loader": "causal_lm",
        "supports_images": False,
        "requires": "cuda",
        "min_vram_gb": 16,
    },
    "Qwen3-235B-A22B-Instruct-2507": {
        "repo_id": "Qwen/Qwen3-235B-A22B-Instruct-2507",
        "family": "qwen",
        "lane": "local",
        "local_download_supported": True,
        "provider_supported": True,
        "mobile_catalog_allowed": False,
        "local_loader": "causal_lm",
        "supports_images": False,
        "requires": "cuda",
        "min_vram_gb": 160,
    },
    "mistral-7b-instruct-v0.3": {
        "repo_id": "mistralai/Mistral-7B-Instruct-v0.3",
        "family": "mistral",
        "lane": "local",
        "local_download_supported": True,
        "provider_supported": True,
        "mobile_catalog_allowed": False,
        "local_loader": "causal_lm",
        "supports_images": False,
        "requires": "cuda",
        "min_vram_gb": 12,
    },
    "mixtral-8x7b-instruct-v0.1": {
        "repo_id": "mistralai/Mixtral-8x7B-Instruct-v0.1",
        "family": "mixtral",
        "lane": "local",
        "local_download_supported": True,
        "provider_supported": True,
        "mobile_catalog_allowed": False,
        "local_loader": "causal_lm",
        "supports_images": False,
        "requires": "cuda",
        "min_vram_gb": 48,
    },
    "gemma-3": {
        "repo_id": "google/gemma-3-12b-it",
        "family": "gemma",
        "lane": "local",
        "local_download_supported": True,
        "provider_supported": True,
        "mobile_catalog_allowed": False,
        "local_loader": "causal_lm",
        "supports_images": False,
    },
    "gemma-3-270m": {
        "repo_id": "google/gemma-3-270m",
        "family": "gemma",
        "lane": "local",
        "local_download_supported": True,
        "provider_supported": True,
        "mobile_catalog_allowed": False,
        "local_loader": "causal_lm",
        "supports_images": False,
    },
    "gemma-3-12b-it": {
        "repo_id": "google/gemma-3-12b-it",
        "family": "gemma",
        "lane": "local",
        "local_download_supported": True,
        "provider_supported": True,
        "mobile_catalog_allowed": False,
        "local_loader": "causal_lm",
        "supports_images": False,
        "requires": "cuda",
        "min_vram_gb": 16,
    },
    "gemma-3-27b-it": {
        "repo_id": "google/gemma-3-27b-it",
        "family": "gemma",
        "lane": "local",
        "local_download_supported": True,
        "provider_supported": True,
        "mobile_catalog_allowed": False,
        "local_loader": "causal_lm",
        "supports_images": False,
        "requires": "cuda",
        "min_vram_gb": 48,
    },
    "gemma-4-E2B-it": {
        "repo_id": "google/gemma-4-E2B-it",
        "family": "gemma",
        "lane": "local",
        "local_download_supported": True,
        "provider_supported": True,
        "mobile_catalog_allowed": True,
        "local_loader": "image_text_to_text",
        "supports_images": True,
        "min_vram_gb": 12,
    },
    "gemma-4-E4B-it": {
        "repo_id": "google/gemma-4-E4B-it",
        "family": "gemma",
        "lane": "server_lan",
        "local_download_supported": False,
        "download_job_supported": True,
        "provider_supported": True,
        "mobile_catalog_allowed": True,
        "local_loader": "none",
        "supports_images": True,
        "min_vram_gb": 18,
    },
    "gemma-4-26B-A4B-it": {
        "repo_id": "google/gemma-4-26B-A4B-it",
        "family": "gemma",
        "lane": "server_lan",
        "local_download_supported": False,
        "provider_supported": True,
        "mobile_catalog_allowed": False,
        "local_loader": "none",
        "supports_images": True,
        "requires": "cuda",
        "min_vram_gb": 28,
    },
    "gemma-4-31B-it": {
        "repo_id": "google/gemma-4-31B-it",
        "family": "gemma",
        "lane": "server_lan",
        "local_download_supported": False,
        "provider_supported": True,
        "mobile_catalog_allowed": False,
        "local_loader": "none",
        "supports_images": True,
        "requires": "cuda",
        "min_vram_gb": 32,
    },
    "whisper-small": {
        "repo_id": "openai/whisper-small",
        "family": "whisper",
        "lane": "local",
        "local_download_supported": True,
        "provider_supported": False,
        "mobile_catalog_allowed": False,
        "local_loader": "causal_lm",
        "supports_images": False,
    },
    "whisper-large-v3-turbo": {
        "repo_id": "openai/whisper-large-v3-turbo",
        "family": "whisper",
        "lane": "local",
        "local_download_supported": True,
        "provider_supported": False,
        "mobile_catalog_allowed": False,
        "local_loader": "causal_lm",
        "supports_images": False,
    },
    "kokoro": {
        "repo_id": "hexgrad/Kokoro-82M",
        "family": "kokoro",
        "lane": "local",
        "local_download_supported": True,
        "provider_supported": False,
        "mobile_catalog_allowed": False,
        "local_loader": "causal_lm",
        "supports_images": False,
    },
    "kitten": {
        "repo_id": "KittenML/kitten-tts-nano-0.2",
        "family": "kitten",
        "lane": "local",
        "local_download_supported": True,
        "provider_supported": False,
        "mobile_catalog_allowed": False,
        "local_loader": "causal_lm",
        "supports_images": False,
    },
    "clip-vit-base-patch32": {
        "repo_id": "openai/clip-vit-base-patch32",
        "family": "clip",
        "lane": "local",
        "local_download_supported": True,
        "provider_supported": False,
        "mobile_catalog_allowed": False,
        "local_loader": "causal_lm",
        "supports_images": False,
    },
    "paligemma2-3b-pt-224": {
        "repo_id": "google/paligemma2-3b-pt-224",
        "family": "paligemma",
        "lane": "local",
        "local_download_supported": True,
        "provider_supported": False,
        "mobile_catalog_allowed": False,
        "local_loader": "causal_lm",
        "supports_images": True,
    },
    "paligemma2-28b-pt-896": {
        "repo_id": "google/paligemma2-28b-pt-896",
        "family": "paligemma",
        "lane": "local",
        "local_download_supported": True,
        "provider_supported": False,
        "mobile_catalog_allowed": False,
        "local_loader": "causal_lm",
        "supports_images": True,
    },
    "pixtral-12b-2409": {
        "repo_id": "mistralai/Pixtral-12B-2409",
        "family": "pixtral",
        "lane": "local",
        "local_download_supported": True,
        "provider_supported": True,
        "mobile_catalog_allowed": False,
        "local_loader": "causal_lm",
        "supports_images": True,
        "requires": "cuda",
        "min_vram_gb": 24,
    },
    "pixtral-large-instruct-2411": {
        "repo_id": "mistralai/Pixtral-Large-Instruct-2411",
        "family": "pixtral",
        "lane": "local",
        "local_download_supported": True,
        "provider_supported": True,
        "mobile_catalog_allowed": False,
        "local_loader": "causal_lm",
        "supports_images": True,
        "requires": "cuda",
        "min_vram_gb": 48,
    },
    "voxtral-mini-3b-2507": {
        "repo_id": "mistralai/Voxtral-Mini-3B-2507",
        "family": "voxtral",
        "lane": "local",
        "local_download_supported": True,
        "provider_supported": False,
        "mobile_catalog_allowed": False,
        "local_loader": "causal_lm",
        "supports_images": False,
    },
    "voxtral-small-24b-2507": {
        "repo_id": "mistralai/Voxtral-Small-24B-2507",
        "family": "voxtral",
        "lane": "local",
        "local_download_supported": True,
        "provider_supported": False,
        "mobile_catalog_allowed": False,
        "local_loader": "causal_lm",
        "supports_images": False,
        "requires": "cuda",
        "min_vram_gb": 32,
    },
}


MODEL_REPOS: Dict[str, str] = {
    alias: str(meta.get("repo_id") or "")
    for alias, meta in MODEL_REGISTRY.items()
    if str(meta.get("repo_id") or "").strip()
}

MODEL_ALIASES_BY_REPO: Dict[str, str] = {
    repo_id.strip().lower(): alias
    for alias, repo_id in MODEL_REPOS.items()
    if str(repo_id).strip()
}

MODEL_ALIASES_BY_NORMALIZED_NAME: Dict[str, str] = {
    alias.strip().lower(): alias for alias in MODEL_REGISTRY if str(alias).strip()
}


MODEL_CAPABILITIES: Dict[str, Dict[str, Any]] = {
    alias: {
        key: value
        for key, value in {
            "requires": meta.get("requires"),
            "min_vram_gb": meta.get("min_vram_gb"),
        }.items()
        if value is not None
    }
    for alias, meta in MODEL_REGISTRY.items()
    if meta.get("requires") is not None or meta.get("min_vram_gb") is not None
}


MODEL_DOWNLOAD_ALLOW_PATTERNS: Dict[str, List[str]] = {
    # GPT-OSS repos include large optional variants (metal/original). By default we
    # download the MXFP4 safetensors shards + tokenizer/config needed for local
    # inference, and skip platform-specific or CPU variants unless explicitly requested.
    "gpt-oss-20b": [
        ".gitattributes",
        "LICENSE",
        "README.md",
        "USAGE_POLICY",
        "chat_template.jinja",
        "config.json",
        "generation_config.json",
        "model-*.safetensors",
        "model.safetensors.index.json",
        "special_tokens_map.json",
        "tokenizer.json",
        "tokenizer_config.json",
    ],
    "gpt-oss-120b": [
        ".gitattributes",
        "LICENSE",
        "README.md",
        "USAGE_POLICY",
        "chat_template.jinja",
        "config.json",
        "generation_config.json",
        "model-*.safetensors",
        "model.safetensors.index.json",
        "special_tokens_map.json",
        "tokenizer.json",
        "tokenizer_config.json",
    ],
}


def get_model_metadata(name: str | None) -> Dict[str, Any]:
    if not name:
        return {}
    return dict(MODEL_REGISTRY.get(canonical_model_alias(name), {}))


def canonical_model_alias(name: str | None) -> str | None:
    """Return the registry alias for a supplied alias, repo id, or repo tail."""

    raw = str(name or "").strip()
    if not raw:
        return None
    if raw in MODEL_REGISTRY:
        return raw
    lowered = raw.lower()
    alias = MODEL_ALIASES_BY_NORMALIZED_NAME.get(lowered)
    if alias:
        return alias
    alias = MODEL_ALIASES_BY_REPO.get(lowered)
    if alias:
        return alias
    if "/" in raw:
        tail = raw.rsplit("/", 1)[-1].strip()
        if tail:
            if tail in MODEL_REGISTRY:
                return tail
            alias = MODEL_ALIASES_BY_NORMALIZED_NAME.get(tail.lower())
            if alias:
                return alias
    return raw


def get_model_lane(name: str | None) -> str | None:
    meta = get_model_metadata(name)
    lane = meta.get("lane")
    return str(lane).strip() or None if lane is not None else None


def get_local_loader(name: str | None) -> str:
    meta = get_model_metadata(name)
    loader = str(meta.get("local_loader") or "causal_lm").strip().lower()
    return loader or "causal_lm"


def model_supports_images(name: str | None) -> bool:
    meta = get_model_metadata(name)
    return bool(meta.get("supports_images"))


def model_supports_local_download(name: str | None) -> bool:
    meta = get_model_metadata(name)
    return bool(meta.get("local_download_supported"))


def model_supports_download_job(name: str | None) -> bool:
    meta = get_model_metadata(name)
    if "download_job_supported" in meta:
        return bool(meta.get("download_job_supported"))
    return bool(meta.get("local_download_supported"))


def model_supports_provider_lane(name: str | None) -> bool:
    meta = get_model_metadata(name)
    return bool(meta.get("provider_supported"))


def model_allowed_in_mobile_catalog(name: str | None) -> bool:
    meta = get_model_metadata(name)
    return bool(meta.get("mobile_catalog_allowed"))


def is_gemma_family_model(name: str | None) -> bool:
    normalized = str(name or "").strip().lower()
    if not normalized:
        return False
    family = str(get_model_metadata(name).get("family") or "").strip().lower()
    return family == "gemma" or normalized.startswith("gemma-")


def get_download_allow_patterns(model_alias: str | None) -> List[str] | None:
    """Return allow_patterns to pass to snapshot_download for a model alias."""

    if not model_alias:
        return None
    return MODEL_DOWNLOAD_ALLOW_PATTERNS.get(canonical_model_alias(model_alias))


def resolve_model_alias(name: str | None) -> str | None:
    """Return the upstream repo id for a supplied alias or repo-style identifier."""

    if not name:
        return name
    alias = canonical_model_alias(name)
    return MODEL_REPOS.get(alias, name)


def _summarise_devices(devices: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    cuda_vram = 0.0
    has_cuda = False
    for device in devices or []:
        if not isinstance(device, dict):
            continue
        if device.get("type") == "cuda":
            has_cuda = True
            try:
                vram = float(device.get("total_memory_gb") or 0)
                cuda_vram = max(cuda_vram, vram)
            except Exception:
                continue
    return {"has_cuda": has_cuda, "max_cuda_vram": cuda_vram}


def model_supported(alias: str, devices: Iterable[Dict[str, Any]]) -> bool:
    """Return True if the supplied direct-local alias is feasible on the detected hardware."""

    meta = get_model_metadata(alias)
    if not meta or not model_supports_local_download(alias):
        return False
    summary = _summarise_devices(devices)
    requires = meta.get("requires")
    if requires == "cuda" and not summary["has_cuda"]:
        return False
    min_vram = meta.get("min_vram_gb")
    if (
        isinstance(min_vram, (int, float))
        and summary["max_cuda_vram"]
        and summary["max_cuda_vram"] < float(min_vram)
    ):
        return False
    return True


def filter_models_for_devices(devices: Iterable[Dict[str, Any]]) -> List[str]:
    """Return the list of direct-local model aliases that fit the detected hardware."""

    supported: List[str] = []
    for alias in MODEL_REGISTRY:
        if model_supported(alias, devices):
            supported.append(alias)
    return supported


def list_downloadable_models() -> List[str]:
    """Return model aliases that support background download jobs."""

    return sorted(
        alias for alias in MODEL_REGISTRY if model_supports_download_job(alias)
    )
