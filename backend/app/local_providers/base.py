from __future__ import annotations

import abc
from typing import Any, Dict, Iterator, Optional


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


class LocalProviderAdapter(abc.ABC):
    provider_name: str

    @abc.abstractmethod
    def detect_installation(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    @abc.abstractmethod
    def resolve_base_url(self, cfg: Dict[str, Any], *, with_v1: bool) -> str:
        raise NotImplementedError

    @abc.abstractmethod
    def poll_status(self, cfg: Dict[str, Any], *, quick: bool = False) -> Dict[str, Any]:
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
