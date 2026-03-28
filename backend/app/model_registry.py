from __future__ import annotations

from typing import Any, Dict, Iterable, List

# Central registry for model aliases used across the application.
# The mapping keeps UI-friendly shorthand names in sync with their
# canonical Hugging Face or provider identifiers so the backend can
# resolve them when calling external services.
MODEL_REPOS: Dict[str, str] = {
    # GPT-OSS (language) canonical names
    "gpt-oss-20b": "openai/gpt-oss-20b",
    "gpt-oss-120b": "openai/gpt-oss-120b",
    # Llama 3.1 (language)
    "Llama-3.1-8B": "meta-llama/Llama-3.1-8B",
    "Llama-3.1-70B": "meta-llama/Llama-3.1-70B",
    # Qwen 3 (language)
    "Qwen3-8B": "Qwen/Qwen3-8B",
    "Qwen3-235B-A22B-Instruct-2507": "Qwen/Qwen3-235B-A22B-Instruct-2507",
    # Mistral (language)
    "mistral-7b-instruct-v0.3": "mistralai/Mistral-7B-Instruct-v0.3",
    "mixtral-8x7b-instruct-v0.1": "mistralai/Mixtral-8x7B-Instruct-v0.1",
    # Gemma 3 (language / VLM)
    "gemma-3": "google/gemma-3-12b-it",
    "gemma-3-270m": "google/gemma-3-270m",
    "gemma-3-12b-it": "google/gemma-3-12b-it",
    "gemma-3-27b-it": "google/gemma-3-27b-it",
    # Whisper (speech recognition)
    "whisper-small": "openai/whisper-small",
    "whisper-large-v3-turbo": "openai/whisper-large-v3-turbo",
    # TTS (local options)
    "kokoro": "hexgrad/Kokoro-82M",
    "kitten": "KittenML/kitten-tts-nano-0.2",
    # Vision / VLM
    "clip-vit-base-patch32": "openai/clip-vit-base-patch32",
    "paligemma2-3b-pt-224": "google/paligemma2-3b-pt-224",
    "paligemma2-28b-pt-896": "google/paligemma2-28b-pt-896",
    # Pixtral (VLM)
    "pixtral-12b-2409": "mistralai/Pixtral-12B-2409",
    "pixtral-large-instruct-2411": "mistralai/Pixtral-Large-Instruct-2411",
    # Voxtral (voice / s2s family)
    "voxtral-mini-3b-2507": "mistralai/Voxtral-Mini-3B-2507",
    "voxtral-small-24b-2507": "mistralai/Voxtral-Small-24B-2507",
}


MODEL_CAPABILITIES: Dict[str, Dict[str, Any]] = {
    # Conservative minimum VRAM (GB) estimates for FP16 / MoE variants.
    "gpt-oss-20b": {"requires": "cuda", "min_vram_gb": 16},
    "gpt-oss-120b": {"requires": "cuda", "min_vram_gb": 80},
    "Llama-3.1-8B": {"requires": "cuda", "min_vram_gb": 16},
    "Llama-3.1-70B": {"requires": "cuda", "min_vram_gb": 80},
    "Qwen3-8B": {"requires": "cuda", "min_vram_gb": 16},
    "Qwen3-235B-A22B-Instruct-2507": {"requires": "cuda", "min_vram_gb": 160},
    "mixtral-8x7b-instruct-v0.1": {"requires": "cuda", "min_vram_gb": 48},
    "mistral-7b-instruct-v0.3": {"requires": "cuda", "min_vram_gb": 12},
    "gemma-3-12b-it": {"requires": "cuda", "min_vram_gb": 16},
    "gemma-3-27b-it": {"requires": "cuda", "min_vram_gb": 48},
    "pixtral-12b-2409": {"requires": "cuda", "min_vram_gb": 24},
    "pixtral-large-instruct-2411": {"requires": "cuda", "min_vram_gb": 48},
    "voxtral-small-24b-2507": {"requires": "cuda", "min_vram_gb": 32},
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


def get_download_allow_patterns(model_alias: str | None) -> List[str] | None:
    """Return allow_patterns to pass to snapshot_download for a model alias."""

    if not model_alias:
        return None
    return MODEL_DOWNLOAD_ALLOW_PATTERNS.get(model_alias)


def resolve_model_alias(name: str | None) -> str | None:
    """Return the canonical identifier for a supplied model alias."""

    if not name:
        return name
    return MODEL_REPOS.get(name, name)


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
    """Return True if the supplied model alias is feasible on the detected hardware."""

    summary = _summarise_devices(devices)
    meta = MODEL_CAPABILITIES.get(alias, {})
    requires = meta.get("requires")
    if requires == "cuda" and not summary["has_cuda"]:
        return False
    min_vram = meta.get("min_vram_gb")
    if isinstance(min_vram, (int, float)) and summary["max_cuda_vram"] and summary["max_cuda_vram"] < float(min_vram):
        return False
    return True


def filter_models_for_devices(devices: Iterable[Dict[str, Any]]) -> List[str]:
    """Return the list of model aliases that fit the detected hardware."""

    supported: List[str] = []
    for alias in MODEL_REPOS:
        if model_supported(alias, devices):
            supported.append(alias)
    return supported
