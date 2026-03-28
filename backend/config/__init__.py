from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Mapping, Optional, Tuple
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

import yaml
from pydantic import BaseModel, ConfigDict


class ModelCatalog(BaseModel):
    """Catalog of model endpoints and selection policy.

    Notes:
    - Keep the schema intentionally flexible to accommodate example configs
      (e.g., additional keys like "server" for LLMs, or model names).
    - Implements intended behavior from
      docs/examples/pipecat_config.yaml: per-capability defaults with
      optional fallback and an aggregate readiness check for workflows.
    """

    # Provider of the streaming pipeline (e.g., "pipecat" or "livekit").
    provider: str
    # Map of model group -> backend key -> endpoint string (or value marker).
    # Example: models["llm"]["local"] == "transformers"
    models: Dict[str, Dict[str, str]]
    # Optional defaults: mode -> capability -> preferred backend name.
    # May include a mode-level "fallback" key for the capability's backend.
    defaults: Optional[Dict[str, Dict[str, str]]] = None
    # Optional workflows: name -> { requires: [capabilities...] }
    workflows: Optional[Dict[str, Dict[str, list[str]]]] = None

    # Allow unexpected extra keys in nested dicts without failing validation.
    # Pydantic v2 style config replaces class-based Config with model_config.
    model_config = ConfigDict(extra="ignore")

    # --- Capability selection helpers ----------------------------------------

    @staticmethod
    def _capability_to_model_group(capability: str) -> str:
        """Map a capability name to a model group key in the catalog.

        stt/tts -> speech, vlm -> vision, llm -> llm, and fall back to the
        capability itself if no mapping is needed.
        """
        cap = capability.lower()
        if cap in {"stt", "tts", "speech"}:
            return "speech"
        if cap in {"vlm", "vision"}:
            return "vision"
        return cap

    def _preferred_backend_for(
        self, mode: Optional[str], capability: str
    ) -> Tuple[Optional[str], Optional[str]]:
        """Return (preferred, fallback) backends for a capability in a mode.

        If no mode or mapping exists, returns (None, None).
        """
        if not mode or not self.defaults:
            return None, None
        mode_cfg = self.defaults.get(mode)
        if not mode_cfg:
            return None, None
        preferred = mode_cfg.get(capability)
        # Mode-level generic fallback (applies to the capability if present)
        fallback = mode_cfg.get("fallback")
        return preferred, fallback

    def select_endpoint(
        self,
        capability: str,
        mode: Optional[str] = None,
        *,
        check_health: bool = False,
        timeout: float = 0.7,
    ) -> Tuple[str, str]:
        """Select an endpoint for a capability given optional mode defaults.

        Order of resolution:
        1) Use defaults[mode][capability] if present.
        2) If not available, and mode-level "fallback" is set, try that.
        3) Else try common backends in order: local -> server -> api.
        4) Else return the first available backend for the model group.

        Raises KeyError if the capability cannot be mapped to any endpoint.
        """
        group = self._capability_to_model_group(capability)
        if group not in self.models:
            raise KeyError(
                f"Unknown model group for capability '{capability}': '{group}'"
            )

        backends: Mapping[str, str] = self.models[group]

        preferred, fallback = self._preferred_backend_for(mode, capability)
        candidates: list[str] = []
        if preferred:
            candidates.append(preferred)
        if fallback and fallback not in candidates:
            candidates.append(fallback)
        # Common heuristics for fastest local-first usage
        for common in ("local", "server", "api"):
            if common not in candidates:
                candidates.append(common)
        # Finally, as a last resort, accept any available backend key
        candidates.extend([k for k in backends.keys() if k not in candidates])

        # If health-checking is requested, try healthy endpoints in order.
        if check_health:
            for key in candidates:
                endpoint = backends.get(key)
                if not endpoint:
                    continue
                if _endpoint_looks_http(endpoint):
                    if _endpoint_is_healthy(endpoint, timeout=timeout):
                        return key, endpoint
                else:
                    # Non-HTTP endpoints (e.g., transformers/local marker) are
                    # considered usable without remote health checks.
                    return key, endpoint

        # Fallback: pick the first available per policy without health checks
        for key in candidates:
            endpoint = backends.get(key)
            if endpoint:
                return key, endpoint
        raise KeyError(
            (
                "No endpoint available for capability "
                f"'{capability}' in group '{group}'"
            )
        )

    # --- Readiness aggregation -------------------------------------------

    def required_capabilities_for(self, workflow: str) -> list[str]:
        """Return the list of required capabilities for a workflow name.

        If not present, try to infer from defaults of a mode with the same
        name by taking its capability keys (excluding 'fallback'). If still
        not found, return an empty list.
        """
        if self.workflows and workflow in self.workflows:
            # mypy: the nested dict contains list[str] under "requires"
            req = self.workflows[workflow].get("requires", [])
            return list(req)
        if self.defaults and workflow in self.defaults:
            mode_defaults = self.defaults[workflow]
            return [k for k in mode_defaults.keys() if k != "fallback"]
        return []

    def readiness(self, workflow: str, *, check_health: bool = False, timeout: float = 0.7) -> Dict[str, object]:
        """Aggregate readiness for a workflow based on required capabilities.

        Returns a dict with:
        - ready: bool
        - selected: {capability: {backend, endpoint}}
        - missing: [capability...]
        """
        required = self.required_capabilities_for(workflow)
        selected: Dict[str, Dict[str, str]] = {}
        missing: list[str] = []
        for cap in required:
            try:
                backend, endpoint = self.select_endpoint(
                    cap, mode=workflow, check_health=check_health, timeout=timeout
                )
                selected[cap] = {"backend": backend, "endpoint": endpoint}
            except KeyError:
                missing.append(cap)
        return {"ready": len(missing) == 0, "selected": selected, "missing": missing}


def load_model_catalog(path: str | Path | None = None) -> ModelCatalog:
    """Load the model catalog YAML and return a ``ModelCatalog`` instance."""
    if path is None:
        path = os.getenv("MODEL_CATALOG_PATH")
        if path is None:
            path = Path(__file__).with_name("model_catalog.yaml")
    data = yaml.safe_load(Path(path).read_text())
    return ModelCatalog(**data)


# --- Endpoint Health Checking Helpers ---------------------------------------

def _endpoint_looks_http(endpoint: str) -> bool:
    try:
        scheme = urlparse(endpoint).scheme.lower()
        return scheme in {"http", "https"}
    except Exception:
        return False


def _endpoint_is_healthy(endpoint: str, *, timeout: float = 0.7) -> bool:
    """Return True if an HTTP(S) endpoint appears reachable.

    Policy:
    - Attempt a GET to the base endpoint. Any response (including HTTP errors)
      indicates the server is reachable.
    - Only consider DNS/connection-level failures (URLError) as unhealthy.
    - Short timeout keeps UI snappy; callers can override.
    """
    if not _endpoint_looks_http(endpoint):
        return True
    try:
        req = Request(endpoint, method="GET")
        # Avoid large reads; rely on headers/first bytes
        with urlopen(req, timeout=timeout) as resp:
            return bool(resp)  # any response means reachable
    except HTTPError:
        return True  # server reachable but returned HTTP error
    except URLError:
        return False
    except Exception:
        # conservative: unknown error means unhealthy
        return False
