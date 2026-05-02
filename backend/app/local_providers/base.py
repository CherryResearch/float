from __future__ import annotations

import abc
import os
from typing import Any, Dict, Iterator, Optional
from urllib.parse import urlparse


def normalize_base_url(url: str, *, with_v1: bool) -> str:
    value = str(url or "").strip().rstrip("/")
    if not value:
        return value
    lower = value.lower()
    if with_v1:
        if lower.endswith("/v1"):
            return value
        return f"{value}/v1"
    if lower.endswith("/v1"):
        return value[:-3]
    return value


def infer_openai_compatible_auth_token(cfg: Dict[str, Any], base_url: str = "") -> str:
    """Choose the bearer token for a configured OpenAI-compatible endpoint."""
    explicit = str(cfg.get("local_provider_api_token") or "").strip()
    if explicit:
        return explicit

    value = str(
        base_url or cfg.get("local_provider_base_url") or cfg.get("server_url") or ""
    ).strip()
    if not value:
        return ""
    candidate = value if "://" in value else f"https://{value}"
    try:
        parsed = urlparse(candidate)
    except Exception:
        return ""
    host = (parsed.netloc or parsed.path or "").split("@")[-1].split(":")[0].lower()
    if not host:
        return ""

    if host.endswith("huggingface.co") or host == "hf.co" or host.endswith(".hf.co"):
        return str(
            cfg.get("hf_token")
            or os.getenv("HUGGINGFACE_HUB_TOKEN")
            or os.getenv("HF_TOKEN")
            or ""
        ).strip()
    if host == "api.openai.com" or host.endswith(".api.openai.com"):
        return str(
            cfg.get("api_key")
            or os.getenv("OPENAI_API_KEY")
            or os.getenv("API_KEY")
            or ""
        ).strip()
    return ""


class LocalProviderAdapter(abc.ABC):
    provider_name: str

    @abc.abstractmethod
    def detect_installation(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    @abc.abstractmethod
    def resolve_base_url(self, cfg: Dict[str, Any], *, with_v1: bool) -> str:
        raise NotImplementedError

    @abc.abstractmethod
    def poll_status(
        self, cfg: Dict[str, Any], *, quick: bool = False
    ) -> Dict[str, Any]:
        raise NotImplementedError

    @abc.abstractmethod
    def list_models(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    @abc.abstractmethod
    def start_server(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    @abc.abstractmethod
    def stop_server(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    @abc.abstractmethod
    def load_model(
        self,
        cfg: Dict[str, Any],
        *,
        model: str,
        context_length: Optional[int] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    @abc.abstractmethod
    def unload_model(
        self,
        cfg: Dict[str, Any],
        *,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    @abc.abstractmethod
    def stream_logs(self, cfg: Dict[str, Any], stop_event) -> Iterator[Dict[str, Any]]:
        raise NotImplementedError

    @abc.abstractmethod
    def capabilities(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError
