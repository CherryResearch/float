from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, urlparse

from app.utils import user_settings
from app.utils.local_model_registry import normalize_model_type

_HF_HOSTS = {"huggingface.co", "www.huggingface.co", "hf.co", "www.hf.co"}
_HF_RESERVED_NAMESPACES = {
    "datasets",
    "docs",
    "models",
    "organizations",
    "settings",
    "spaces",
    "tasks",
}
_REPO_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_ALLOWED_RUNTIMES = {"direct", "provider"}


def _sanitize_alias(value: Optional[str]) -> str:
    raw = str(value or "").strip()
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-_.")
    return clean


def normalize_user_model_alias(value: Optional[str]) -> str:
    return _sanitize_alias(value)


def normalize_hf_repo_id(value: str) -> str:
    """Normalize a Hugging Face model URL or ``owner/repo`` identifier."""

    raw = str(value or "").strip()
    if not raw:
        raise ValueError("Hugging Face model URL or repo id is required")

    if "://" in raw:
        parsed = urlparse(raw)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("Hugging Face URL must use http or https")
        if parsed.username or parsed.password:
            raise ValueError("Hugging Face URL must not include credentials")
        if (parsed.hostname or "").lower() not in _HF_HOSTS:
            raise ValueError("URL must point to huggingface.co")
        parts = [unquote(part).strip() for part in parsed.path.split("/") if part]
        if len(parts) < 2:
            raise ValueError("Hugging Face model URL must include owner and repo")
        owner, repo = parts[0], parts[1]
    else:
        parts = [part.strip() for part in raw.split("/") if part.strip()]
        if len(parts) != 2:
            raise ValueError("Hugging Face repo id must use owner/repo")
        owner, repo = parts

    repo = repo.removesuffix(".git")
    if owner.lower() in _HF_RESERVED_NAMESPACES:
        raise ValueError("Link must point to a Hugging Face model repository")
    if not _REPO_PART.fullmatch(owner) or not _REPO_PART.fullmatch(repo):
        raise ValueError("Hugging Face owner/repo contains unsupported characters")
    return f"{owner}/{repo}"


def _normalize_entry(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    try:
        repo_id = normalize_hf_repo_id(str(raw.get("repo_id") or raw.get("url") or ""))
    except ValueError:
        return None
    alias = _sanitize_alias(raw.get("alias")) or _sanitize_alias(
        repo_id.rsplit("/", 1)[-1]
    )
    if not alias:
        return None
    model_type = normalize_model_type(raw.get("model_type"))
    runtime = str(raw.get("runtime") or "").strip().lower()
    if runtime not in _ALLOWED_RUNTIMES:
        runtime = "provider" if "gguf" in repo_id.lower() else "direct"
    return {
        "alias": alias,
        "repo_id": repo_id,
        "url": f"https://huggingface.co/{repo_id}",
        "model_type": model_type,
        "runtime": runtime,
        "lane": "provider" if runtime == "provider" else "direct",
        "source_type": "huggingface",
        "updated_at": float(raw.get("updated_at") or 0.0),
    }


def list_user_hf_models() -> List[Dict[str, Any]]:
    raw_entries = user_settings.load_settings().get(
        "huggingface_model_registrations", []
    )
    if not isinstance(raw_entries, list):
        return []
    deduped: Dict[str, Dict[str, Any]] = {}
    for raw in raw_entries:
        entry = _normalize_entry(raw)
        if entry is not None:
            deduped[entry["alias"].lower()] = entry
    return [deduped[key] for key in sorted(deduped)]


def upsert_user_hf_model(
    *,
    url: str,
    alias: Optional[str] = None,
    model_type: Optional[str] = None,
    runtime: Optional[str] = None,
) -> Dict[str, Any]:
    repo_id = normalize_hf_repo_id(url)
    alias_value = _sanitize_alias(alias) or _sanitize_alias(repo_id.rsplit("/", 1)[-1])
    if not alias_value:
        raise ValueError("alias is required")
    runtime_value = str(runtime or "").strip().lower()
    if runtime_value and runtime_value not in _ALLOWED_RUNTIMES:
        raise ValueError("runtime must be 'direct' or 'provider'")
    if not runtime_value:
        runtime_value = "provider" if "gguf" in repo_id.lower() else "direct"
    entry = {
        "alias": alias_value,
        "repo_id": repo_id,
        "model_type": normalize_model_type(model_type),
        "runtime": runtime_value,
        "updated_at": time.time(),
    }
    existing = list_user_hf_models()
    filtered = [
        item
        for item in existing
        if str(item.get("alias") or "").lower() != alias_value.lower()
        and str(item.get("repo_id") or "").lower() != repo_id.lower()
    ]
    filtered.append(entry)
    filtered.sort(key=lambda item: str(item.get("alias") or "").lower())
    user_settings.save_settings({"huggingface_model_registrations": filtered})
    normalized = _normalize_entry(entry)
    if normalized is None:
        raise ValueError("failed to normalize Hugging Face model entry")
    return normalized


def remove_user_hf_model(alias: str) -> bool:
    alias_value = _sanitize_alias(alias).lower()
    if not alias_value:
        return False
    existing = list_user_hf_models()
    filtered = [
        entry
        for entry in existing
        if str(entry.get("alias") or "").lower() != alias_value
    ]
    if len(filtered) == len(existing):
        return False
    user_settings.save_settings({"huggingface_model_registrations": filtered})
    return True


def resolve_user_hf_model_alias(value: str | None) -> Optional[str]:
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    for entry in list_user_hf_models():
        alias = str(entry.get("alias") or "").strip()
        repo_id = str(entry.get("repo_id") or "").strip()
        if raw in {alias.lower(), repo_id.lower()}:
            return alias
    return None


def get_user_hf_model_metadata(value: str | None) -> Dict[str, Any]:
    alias = resolve_user_hf_model_alias(value)
    if not alias:
        return {}
    entry = next(
        (
            item
            for item in list_user_hf_models()
            if str(item.get("alias") or "").lower() == alias.lower()
        ),
        None,
    )
    if entry is None:
        return {}
    runtime = entry.get("runtime")
    model_type = entry.get("model_type")
    return {
        **entry,
        "family": "user",
        "local_download_supported": runtime == "direct",
        "download_job_supported": True,
        "provider_supported": runtime == "provider",
        "mobile_catalog_allowed": False,
        "local_loader": "causal_lm",
        "supports_images": model_type == "vision",
        "user_registered": True,
    }


__all__ = [
    "get_user_hf_model_metadata",
    "list_user_hf_models",
    "normalize_hf_repo_id",
    "normalize_user_model_alias",
    "remove_user_hf_model",
    "resolve_user_hf_model_alias",
    "upsert_user_hf_model",
]
