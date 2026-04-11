from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional

from .base import normalize_base_url
from .lmstudio import LMStudioAdapter
from .ollama import OllamaAdapter

PROVIDER_MARKERS = {"lmstudio", "ollama", "custom-openai-compatible"}
STATUS_CACHE_TTL_SECONDS = 5.0
MODELS_CACHE_TTL_SECONDS = 45.0
EMBEDDING_MODEL_HINTS = (
    "sentence-transformer",
    "sentence_transformer",
    "minilm",
    "mpnet",
    "instructor",
    "nomic-embed",
    "text-embedding",
    "embedding",
    "bge",
    "e5",
    "gte",
    "sbert",
    "retriever",
    "reranker",
    "-embed",
    "_embed",
)


def _normalize_provider(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in PROVIDER_MARKERS:
        return raw
    if raw == "lm-studio":
        return "lmstudio"
    return "lmstudio"


def _normalize_provider_mode(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"local-managed", "remote-unmanaged"}:
        return raw
    return "local-managed"


def _coerce_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except Exception:
        return fallback


def _normalize_model_name(value: Any) -> str:
    return str(value or "").strip()


def _is_likely_embedding_model_name(value: Any) -> bool:
    model_name = _normalize_model_name(value).lower()
    if not model_name:
        return False
    return any(hint in model_name for hint in EMBEDDING_MODEL_HINTS)


def _chat_candidate_models(models: List[str] | None) -> List[str]:
    if not isinstance(models, list):
        return []
    return [name for name in models if not _is_likely_embedding_model_name(name)]


class LocalProviderManager:
    def __init__(self, config_getter: Callable[[], Dict[str, Any]]) -> None:
        self._config_getter = config_getter
        lmstudio_adapter = LMStudioAdapter()
        self._adapters = {
            "lmstudio": lmstudio_adapter,
            "ollama": OllamaAdapter(),
            "custom-openai-compatible": lmstudio_adapter,
        }
        self._lock = threading.Lock()
        self._last_error: Dict[str, str] = {}
        self._log_entries: Dict[str, Deque[Dict[str, Any]]] = {
            "lmstudio": deque(maxlen=4000),
            "ollama": deque(maxlen=4000),
            "custom-openai-compatible": deque(maxlen=4000),
        }
        self._log_seq: Dict[str, int] = {
            "lmstudio": 0,
            "ollama": 0,
            "custom-openai-compatible": 0,
        }
        self._log_threads: Dict[str, Optional[threading.Thread]] = {
            "lmstudio": None,
            "ollama": None,
            "custom-openai-compatible": None,
        }
        self._log_stops: Dict[str, Optional[threading.Event]] = {
            "lmstudio": None,
            "ollama": None,
            "custom-openai-compatible": None,
        }
        self._runtime_cache: Dict[str, Dict[str, Any]] = {}
        self._models_cache: Dict[str, Dict[str, Any]] = {}
        self._owned_servers: set[str] = set()
        self._owned_loaded_models: Dict[str, Dict[str, str]] = {
            "lmstudio": {},
            "ollama": {},
            "custom-openai-compatible": {},
        }

    def _owns_server(self, provider: str) -> bool:
        with self._lock:
            return _normalize_provider(provider) in self._owned_servers

    @staticmethod
    def is_provider_marker(value: Any) -> bool:
        return str(value or "").strip().lower() in PROVIDER_MARKERS

    def _base_config(self) -> Dict[str, Any]:
        raw = self._config_getter() or {}
        cfg = dict(raw)
        provider = _normalize_provider(cfg.get("local_provider"))
        mode = _normalize_provider_mode(cfg.get("local_provider_mode"))
        port_default = 11434 if provider == "ollama" else 1234
        host_default = "127.0.0.1"
        settings = {
            **cfg,
            "local_provider": provider,
            "local_provider_mode": mode,
            "local_provider_host": str(
                cfg.get("local_provider_host") or host_default
            ).strip()
            or host_default,
            "local_provider_port": _coerce_int(
                cfg.get("local_provider_port"), port_default
            ),
            "local_provider_base_url": str(
                cfg.get("local_provider_base_url") or ""
            ).strip(),
            "lmstudio_path": str(cfg.get("lmstudio_path") or "").strip(),
            "local_provider_api_token": str(
                cfg.get("local_provider_api_token") or ""
            ).strip(),
            "local_provider_auto_start": bool(
                cfg.get("local_provider_auto_start", True)
            ),
            "local_provider_preferred_model": str(
                cfg.get("local_provider_preferred_model") or ""
            ).strip(),
            "local_provider_default_context_length": cfg.get(
                "local_provider_default_context_length"
            ),
            "local_provider_show_server_logs": bool(
                cfg.get("local_provider_show_server_logs", True)
            ),
            "local_provider_enable_cors": bool(
                cfg.get("local_provider_enable_cors", False)
            ),
            "local_provider_allow_lan": bool(
                cfg.get("local_provider_allow_lan", False)
            ),
        }
        return settings

    def _settings_for_provider(self, provider: Optional[str]) -> Dict[str, Any]:
        cfg = self._base_config()
        selected = _normalize_provider(provider or cfg.get("local_provider"))
        cfg["local_provider"] = selected
        if not cfg.get("local_provider_port"):
            cfg["local_provider_port"] = 11434 if selected == "ollama" else 1234
        return cfg

    def _adapter(self, provider: str):
        return self._adapters[_normalize_provider(provider)]

    def _invalidate_cache(self, provider: str) -> None:
        provider_key = _normalize_provider(provider)
        with self._lock:
            self._runtime_cache.pop(provider_key, None)
            self._models_cache.pop(provider_key, None)

    def _clean_models(self, result: Any) -> List[str]:
        models = result.get("models") if isinstance(result, dict) else []
        if not isinstance(models, list):
            return []
        return sorted(
            {
                str(item).strip()
                for item in models
                if isinstance(item, str) and str(item).strip()
            }
        )

    def _cached_runtime(self, provider: str) -> Optional[Dict[str, Any]]:
        provider_key = _normalize_provider(provider)
        now = time.monotonic()
        with self._lock:
            entry = self._runtime_cache.get(provider_key)
            if not entry:
                return None
            fetched_at = float(entry.get("fetched_at_monotonic", 0.0))
            if now - fetched_at >= STATUS_CACHE_TTL_SECONDS:
                return None
            runtime = entry.get("runtime")
            return dict(runtime) if isinstance(runtime, dict) else None

    def _cached_models(self, provider: str) -> Optional[List[str]]:
        provider_key = _normalize_provider(provider)
        now = time.monotonic()
        with self._lock:
            entry = self._models_cache.get(provider_key)
            if not entry:
                return None
            fetched_at = float(entry.get("fetched_at_monotonic", 0.0))
            if now - fetched_at >= MODELS_CACHE_TTL_SECONDS:
                return None
            models = entry.get("models")
            return list(models) if isinstance(models, list) else None

    def _store_runtime(self, provider: str, runtime: Dict[str, Any]) -> None:
        provider_key = _normalize_provider(provider)
        with self._lock:
            self._runtime_cache[provider_key] = {
                "runtime": dict(runtime or {}),
                "fetched_at_monotonic": time.monotonic(),
                "checked_at": time.time(),
            }

    def _store_models(self, provider: str, models: List[str]) -> None:
        provider_key = _normalize_provider(provider)
        with self._lock:
            self._models_cache[provider_key] = {
                "models": list(models),
                "fetched_at_monotonic": time.monotonic(),
                "checked_at": time.time(),
            }

    def _merge_cached_runtime(
        self,
        provider: str,
        current: Dict[str, Any],
        *,
        cached: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        merged = dict(current or {})
        cached_runtime = cached or self._cached_runtime(provider) or {}
        if merged.get("context_length") in (None, 0) and cached_runtime.get(
            "context_length"
        ):
            merged["context_length"] = cached_runtime.get("context_length")
        if merged.get("details") in (None, {}) and cached_runtime.get("details"):
            merged["details"] = cached_runtime.get("details")
        return merged

    def _runtime_with_inventory(
        self,
        provider: str,
        cfg: Dict[str, Any],
        adapter,
        runtime: Dict[str, Any],
        models: Optional[List[str]],
        *,
        models_source: str = "unknown",
        inventory_reachable: Optional[bool] = None,
    ) -> Dict[str, Any]:
        provider_key = _normalize_provider(provider)
        install = adapter.detect_installation(cfg)
        last_error = ""
        with self._lock:
            last_error = self._last_error.get(provider_key, "")
            runtime_entry = self._runtime_cache.get(provider_key, {})
            models_entry = self._models_cache.get(provider_key, {})
        loaded_model = _normalize_model_name(runtime.get("loaded_model")) or None
        loaded_model_chat_capable = bool(
            loaded_model and not _is_likely_embedding_model_name(loaded_model)
        )
        preferred_model = (
            _normalize_model_name(cfg.get("local_provider_preferred_model")) or None
        )
        preferred_model_chat_capable = bool(
            preferred_model and not _is_likely_embedding_model_name(preferred_model)
        )
        inventory_models = list(models or [])
        chat_inventory_models = _chat_candidate_models(inventory_models)
        if not loaded_model and len(chat_inventory_models) == 1:
            loaded_model = chat_inventory_models[0]
            loaded_model_chat_capable = True
        effective_model = (
            loaded_model
            if loaded_model_chat_capable
            else preferred_model
            if preferred_model_chat_capable
            else chat_inventory_models[0]
            if len(chat_inventory_models) == 1
            else None
        )
        if loaded_model_chat_capable:
            selection_source = "loaded"
        elif preferred_model_chat_capable:
            selection_source = "preferred"
        elif effective_model:
            selection_source = "inventory"
        else:
            selection_source = "unknown"
        runtime_entry_checked_at = runtime_entry.get("checked_at")
        models_entry_fetched_at = models_entry.get("checked_at")
        server_running = bool(runtime.get("server_running"))
        loaded_model_is_chat = bool(
            loaded_model and loaded_model_chat_capable and server_running
        )
        chat_ready = bool(server_running and loaded_model_is_chat)
        return {
            "provider": provider_key,
            "mode": cfg.get("local_provider_mode"),
            "installed": bool(install.get("installed")),
            "server_running": server_running,
            "status_reachable": server_running,
            "inventory_reachable": bool(inventory_reachable),
            "inventory_source": models_source,
            "inventory_stale": models_source == "cache",
            "chat_ready": chat_ready,
            "model_loaded": loaded_model_is_chat,
            "loaded_model": loaded_model,
            "loaded_model_chat_capable": loaded_model_chat_capable,
            "effective_model": effective_model,
            "preferred_model": preferred_model,
            "preferred_model_chat_capable": preferred_model_chat_capable,
            "selected_model_source": selection_source,
            "model_mismatch": bool(
                loaded_model_chat_capable
                and preferred_model_chat_capable
                and loaded_model
                and preferred_model
                and loaded_model != preferred_model
            ),
            "context_length": runtime.get("context_length")
            if isinstance(runtime.get("context_length"), int)
            else None,
            "base_url": normalize_base_url(
                adapter.resolve_base_url(cfg, with_v1=True), with_v1=True
            ),
            "host": cfg.get("local_provider_host"),
            "port": cfg.get("local_provider_port"),
            "last_error": last_error or None,
            "capabilities": adapter.capabilities(cfg),
            "details": runtime.get("details")
            if isinstance(runtime.get("details"), dict)
            else {},
            "inventory_model_count": len(inventory_models),
            "chat_model_count": len(chat_inventory_models),
            "embedding_only_inventory": bool(inventory_models)
            and not bool(chat_inventory_models),
            "inventory_cached_at": models_entry_fetched_at,
            "checked_at": runtime_entry_checked_at,
        }

    def provider_snapshot(
        self,
        provider: Optional[str] = None,
        *,
        quick: bool = False,
        refresh_models: bool = False,
        force: bool = False,
    ) -> Dict[str, Any]:
        cfg = self._settings_for_provider(provider)
        provider_key = _normalize_provider(cfg.get("local_provider"))
        adapter = self._adapter(provider_key)
        runtime = None
        cached_models = None if force else self._cached_models(provider_key)
        should_refresh_models = refresh_models or cached_models is None

        current = adapter.poll_status(cfg, quick=quick)
        runtime = self._merge_cached_runtime(provider_key, current)
        inventory_reachable = None
        models_source = "cache" if cached_models is not None else "unknown"
        if should_refresh_models:
            listed_result = adapter.list_models(cfg)
            inventory_reachable = bool(
                listed_result.get("ok") if isinstance(listed_result, dict) else False
            )
            listed = self._clean_models(listed_result)
            if inventory_reachable:
                cached_models = listed
                self._store_models(provider_key, listed)
                models_source = "live"
            elif cached_models is None:
                cached_models = listed
                self._store_models(provider_key, listed)
                models_source = "unknown"
            else:
                models_source = "cache"
        models = list(cached_models or [])
        enriched_runtime = self._runtime_with_inventory(
            provider_key,
            cfg,
            adapter,
            runtime,
            models,
            models_source=models_source,
            inventory_reachable=inventory_reachable,
        )
        enriched_runtime["checked_at"] = time.time()
        self._store_runtime(provider_key, enriched_runtime)
        return {
            "provider": provider_key,
            "runtime": enriched_runtime,
            "models": models,
        }

    def _record_error(self, provider: str, message: str) -> None:
        with self._lock:
            self._last_error[_normalize_provider(provider)] = str(message or "").strip()

    def _clear_error(self, provider: str) -> None:
        with self._lock:
            self._last_error.pop(_normalize_provider(provider), None)

    def _append_log(
        self, provider: str, level: str, message: str, payload: Any = None
    ) -> None:
        provider_key = _normalize_provider(provider)
        with self._lock:
            self._log_seq[provider_key] += 1
            entry = {
                "seq": self._log_seq[provider_key],
                "time": time.time(),
                "provider": provider_key,
                "level": level,
                "message": message,
            }
            if payload is not None:
                entry["payload"] = payload
            self._log_entries[provider_key].append(entry)

    def _start_log_stream(self, provider: str, cfg: Dict[str, Any]) -> None:
        provider_key = _normalize_provider(provider)
        if not bool(cfg.get("local_provider_show_server_logs", True)):
            return
        with self._lock:
            existing = self._log_threads.get(provider_key)
            if existing and existing.is_alive():
                return
            stop_event = threading.Event()
            self._log_stops[provider_key] = stop_event

        adapter = self._adapter(provider_key)

        def _worker() -> None:
            try:
                for item in adapter.stream_logs(cfg, stop_event):
                    if stop_event.is_set():
                        break
                    if not isinstance(item, dict):
                        self._append_log(provider_key, "info", str(item))
                        continue
                    level = str(item.get("level") or "info")
                    message = str(item.get("message") or "").strip()
                    payload = item.get("payload")
                    if message:
                        self._append_log(provider_key, level, message, payload)
                    elif payload is not None:
                        self._append_log(provider_key, level, str(payload), payload)
            except Exception as exc:
                self._append_log(provider_key, "error", str(exc))

        thread = threading.Thread(
            target=_worker,
            name=f"{provider_key}-provider-log-stream",
            daemon=True,
        )
        with self._lock:
            self._log_threads[provider_key] = thread
        thread.start()

    def _stop_log_stream(self, provider: str) -> None:
        provider_key = _normalize_provider(provider)
        thread = None
        stop_event = None
        with self._lock:
            thread = self._log_threads.get(provider_key)
            stop_event = self._log_stops.get(provider_key)
            self._log_threads[provider_key] = None
            self._log_stops[provider_key] = None
        if stop_event is not None:
            stop_event.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)

    def _status(
        self, provider: Optional[str], *, quick: bool = False
    ) -> Dict[str, Any]:
        snapshot = self.provider_snapshot(provider, quick=quick, refresh_models=True)
        runtime = snapshot.get("runtime")
        return dict(runtime) if isinstance(runtime, dict) else {}

    def provider_status(
        self, provider: Optional[str] = None, quick: bool = False
    ) -> Dict[str, Any]:
        return self._status(provider, quick=quick)

    def provider_models(
        self, provider: Optional[str] = None, *, refresh: bool = False
    ) -> Dict[str, Any]:
        snapshot = self.provider_snapshot(provider, refresh_models=refresh)
        models = _chat_candidate_models(list(snapshot.get("models") or []))
        return {
            "provider": snapshot.get("provider"),
            "models": models,
            "runtime": snapshot.get("runtime") or {},
        }

    def provider_start(self, provider: Optional[str] = None) -> Dict[str, Any]:
        cfg = self._settings_for_provider(provider)
        provider_key = _normalize_provider(cfg.get("local_provider"))
        adapter = self._adapter(provider_key)
        result = adapter.start_server(cfg)
        if result.get("ok"):
            if (
                cfg.get("local_provider_mode") == "local-managed"
                and not str(result.get("note") or "").strip()
            ):
                with self._lock:
                    self._owned_servers.add(provider_key)
            self._clear_error(provider_key)
            self._append_log(provider_key, "info", "Server start requested.")
            self._start_log_stream(provider_key, cfg)
        else:
            error = str(result.get("error") or "Failed to start provider server.")
            self._record_error(provider_key, error)
            self._append_log(provider_key, "error", error)
        self._invalidate_cache(provider_key)
        snapshot = self.provider_snapshot(
            provider_key,
            refresh_models=True,
            force=True,
        )
        return {
            "ok": bool(result.get("ok")),
            "result": result,
            "runtime": snapshot.get("runtime") or {},
        }

    def provider_stop(self, provider: Optional[str] = None) -> Dict[str, Any]:
        cfg = self._settings_for_provider(provider)
        provider_key = _normalize_provider(cfg.get("local_provider"))
        current = self._status(provider_key)
        if (
            provider_key == "lmstudio"
            and cfg.get("local_provider_mode") == "local-managed"
            and current.get("server_running")
            and not self._owns_server(provider_key)
        ):
            error = (
                "LM Studio server is already running outside Float. "
                "Switch to External HTTP only to control the existing app/server, "
                "or stop it first and let Float start a managed headless server."
            )
            self._record_error(provider_key, error)
            snapshot = self.provider_snapshot(
                provider_key,
                refresh_models=True,
                force=True,
            )
            return {
                "ok": False,
                "result": {"ok": False, "error": error},
                "runtime": snapshot.get("runtime") or {},
            }
        adapter = self._adapter(provider_key)
        result = adapter.stop_server(cfg)
        self._stop_log_stream(provider_key)
        if result.get("ok"):
            with self._lock:
                self._owned_servers.discard(provider_key)
                self._owned_loaded_models[provider_key].clear()
            self._clear_error(provider_key)
            self._append_log(provider_key, "info", "Server stop requested.")
        else:
            error = str(result.get("error") or "Failed to stop provider server.")
            self._record_error(provider_key, error)
            self._append_log(provider_key, "error", error)
        self._invalidate_cache(provider_key)
        snapshot = self.provider_snapshot(
            provider_key,
            refresh_models=True,
            force=True,
        )
        return {
            "ok": bool(result.get("ok")),
            "result": result,
            "runtime": snapshot.get("runtime") or {},
        }

    def provider_load(
        self,
        *,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        context_length: Optional[int] = None,
    ) -> Dict[str, Any]:
        cfg = self._settings_for_provider(provider)
        provider_key = _normalize_provider(cfg.get("local_provider"))
        adapter = self._adapter(provider_key)
        requested_model = str(
            model or cfg.get("local_provider_preferred_model") or ""
        ).strip()
        if not requested_model:
            error = "Model is required to load a provider runtime."
            self._record_error(provider_key, error)
            snapshot = self.provider_snapshot(provider_key, refresh_models=True)
            return {
                "ok": False,
                "result": {"ok": False, "error": error},
                "runtime": snapshot.get("runtime") or {},
            }

        current = self._status(provider_key)
        if (
            cfg.get("local_provider_mode") == "local-managed"
            and cfg.get("local_provider_auto_start", True)
            and not current.get("server_running")
        ):
            started = self.provider_start(provider_key)
            if not started.get("ok"):
                return started
            current = (
                started.get("runtime")
                if isinstance(started.get("runtime"), dict)
                else {}
            )
        if (
            provider_key == "lmstudio"
            and cfg.get("local_provider_mode") == "local-managed"
            and current.get("server_running")
            and not self._owns_server(provider_key)
        ):
            error = (
                "LM Studio server is already running outside Float. "
                "Switch to External HTTP only to control the existing app/server, "
                "or stop it first and let Float start a managed headless server."
            )
            self._record_error(provider_key, error)
            snapshot = self.provider_snapshot(
                provider_key,
                refresh_models=True,
                force=True,
            )
            return {
                "ok": False,
                "result": {"ok": False, "error": error},
                "runtime": snapshot.get("runtime") or {},
            }

        result = adapter.load_model(
            cfg,
            model=requested_model,
            context_length=context_length,
        )
        if result.get("ok"):
            with self._lock:
                self._owned_loaded_models[provider_key][requested_model] = str(
                    cfg.get("local_provider_mode") or "local-managed"
                )
            self._clear_error(provider_key)
            self._append_log(
                provider_key, "info", f"Model load requested: {requested_model}"
            )
            self._start_log_stream(provider_key, cfg)
        else:
            error = str(result.get("error") or "Failed to load provider model.")
            self._record_error(provider_key, error)
            self._append_log(provider_key, "error", error)
        self._invalidate_cache(provider_key)
        snapshot = self.provider_snapshot(
            provider_key,
            refresh_models=True,
            force=True,
        )
        return {
            "ok": bool(result.get("ok")),
            "result": result,
            "runtime": snapshot.get("runtime") or {},
        }

    def provider_unload(
        self,
        *,
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        cfg = self._settings_for_provider(provider)
        provider_key = _normalize_provider(cfg.get("local_provider"))
        current = self._status(provider_key)
        if (
            provider_key == "lmstudio"
            and cfg.get("local_provider_mode") == "local-managed"
            and current.get("server_running")
            and not self._owns_server(provider_key)
        ):
            error = (
                "LM Studio server is already running outside Float. "
                "Switch to External HTTP only to control the existing app/server, "
                "or stop it first and let Float start a managed headless server."
            )
            self._record_error(provider_key, error)
            snapshot = self.provider_snapshot(
                provider_key,
                refresh_models=True,
                force=True,
            )
            return {
                "ok": False,
                "result": {"ok": False, "error": error},
                "runtime": snapshot.get("runtime") or {},
            }
        adapter = self._adapter(provider_key)
        result = adapter.unload_model(cfg, model=model)
        if result.get("ok"):
            with self._lock:
                if model:
                    self._owned_loaded_models[provider_key].pop(str(model), None)
                else:
                    self._owned_loaded_models[provider_key].clear()
            self._clear_error(provider_key)
            self._append_log(provider_key, "info", "Model unload requested.")
        else:
            error = str(result.get("error") or "Failed to unload provider model.")
            self._record_error(provider_key, error)
            self._append_log(provider_key, "error", error)
        self._invalidate_cache(provider_key)
        snapshot = self.provider_snapshot(
            provider_key,
            refresh_models=True,
            force=True,
        )
        return {
            "ok": bool(result.get("ok")),
            "result": result,
            "runtime": snapshot.get("runtime") or {},
        }

    def provider_logs(
        self,
        *,
        provider: Optional[str] = None,
        cursor: int = 0,
        limit: int = 200,
    ) -> Dict[str, Any]:
        provider_key = _normalize_provider(
            provider or self._base_config().get("local_provider")
        )
        max_items = max(1, min(int(limit or 200), 2000))
        cursor_value = max(0, int(cursor or 0))
        with self._lock:
            all_items = list(self._log_entries[provider_key])
            next_cursor = self._log_seq[provider_key]
        items = [
            entry for entry in all_items if int(entry.get("seq", 0)) > cursor_value
        ]
        if len(items) > max_items:
            items = items[-max_items:]
        return {
            "provider": provider_key,
            "cursor": cursor_value,
            "next_cursor": next_cursor,
            "entries": items,
        }

    def shutdown(self) -> Dict[str, Any]:
        results: Dict[str, Any] = {
            "status": "stopped",
            "providers": {},
            "errors": [],
        }

        for provider in PROVIDER_MARKERS:
            try:
                self._stop_log_stream(provider)
            except Exception as exc:
                results["errors"].append(
                    {"provider": provider, "phase": "logs", "error": str(exc)}
                )

        with self._lock:
            owned_servers = set(self._owned_servers)
            owned_models = {
                provider: dict(models)
                for provider, models in self._owned_loaded_models.items()
                if models
            }

        for provider, models in owned_models.items():
            adapter = self._adapter(provider)
            provider_summary = results["providers"].setdefault(
                provider, {"unloaded_models": [], "server_stopped": False}
            )
            for model_name, mode in models.items():
                cfg = self._settings_for_provider(provider)
                cfg["local_provider_mode"] = mode
                try:
                    unload_result = adapter.unload_model(cfg, model=model_name)
                except Exception as exc:
                    unload_result = {"ok": False, "error": str(exc)}
                if unload_result.get("ok"):
                    provider_summary["unloaded_models"].append(model_name)
                    with self._lock:
                        self._owned_loaded_models[provider].pop(model_name, None)
                else:
                    results["errors"].append(
                        {
                            "provider": provider,
                            "phase": "unload",
                            "model": model_name,
                            "error": str(
                                unload_result.get("error")
                                or "Failed to unload provider model."
                            ),
                        }
                    )

        for provider in sorted(owned_servers):
            adapter = self._adapter(provider)
            cfg = self._settings_for_provider(provider)
            cfg["local_provider_mode"] = "local-managed"
            provider_summary = results["providers"].setdefault(
                provider, {"unloaded_models": [], "server_stopped": False}
            )
            try:
                stop_result = adapter.stop_server(cfg)
            except Exception as exc:
                stop_result = {"ok": False, "error": str(exc)}
            if stop_result.get("ok"):
                provider_summary["server_stopped"] = True
                with self._lock:
                    self._owned_servers.discard(provider)
                    self._owned_loaded_models[provider].clear()
            else:
                results["errors"].append(
                    {
                        "provider": provider,
                        "phase": "stop",
                        "error": str(
                            stop_result.get("error")
                            or "Failed to stop provider server."
                        ),
                    }
                )
            self._invalidate_cache(provider)

        return results

    def resolve_inference_target(
        self,
        *,
        provider: str,
        requested_model: Optional[str],
        allow_auto_start: bool = True,
    ) -> Dict[str, Any]:
        provider_key = _normalize_provider(provider)
        cfg = self._settings_for_provider(provider_key)
        mode = _normalize_provider_mode(cfg.get("local_provider_mode"))
        snapshot = self.provider_snapshot(provider_key, refresh_models=True)
        runtime = snapshot.get("runtime") if isinstance(snapshot, dict) else {}

        if not runtime.get("server_running"):
            should_start = (
                mode == "local-managed"
                and allow_auto_start
                and bool(cfg.get("local_provider_auto_start", True))
            )
            if should_start:
                started = self.provider_start(provider=provider_key)
                runtime = (
                    started.get("runtime") if isinstance(started, dict) else runtime
                )
            if not runtime.get("server_running"):
                raise RuntimeError(
                    f"{provider_key} server is not running. Start it in the runtime panel."
                )

        raw_requested = str(requested_model or "").strip()
        if self.is_provider_marker(raw_requested):
            raw_requested = ""
        if raw_requested and _is_likely_embedding_model_name(raw_requested):
            raise RuntimeError(
                f"Model '{raw_requested}' looks like an embedding model and cannot answer chat requests."
            )
        loaded_model = str(runtime.get("loaded_model") or "").strip()
        loaded_model_for_chat = (
            loaded_model
            if loaded_model and not _is_likely_embedding_model_name(loaded_model)
            else ""
        )
        configured_runtime_marker = _normalize_model_name(cfg.get("transformer_model"))
        preferred_model = str(cfg.get("local_provider_preferred_model") or "").strip()
        preferred_model_for_chat = (
            preferred_model
            if preferred_model and not _is_likely_embedding_model_name(preferred_model)
            else ""
        )
        if (
            self.is_provider_marker(configured_runtime_marker)
            and raw_requested
            and preferred_model_for_chat
            and raw_requested == preferred_model_for_chat
            and loaded_model_for_chat
            and loaded_model_for_chat != raw_requested
        ):
            raw_requested = ""
        model = (
            raw_requested
            or str(runtime.get("effective_model") or "").strip()
            or loaded_model_for_chat
            or preferred_model_for_chat
        )

        if not model:
            if loaded_model and _is_likely_embedding_model_name(loaded_model):
                raise RuntimeError(
                    f"{provider_key} has '{loaded_model}' loaded, but it looks like an embedding model. "
                    "Load a chat model in the runtime panel."
                )
            if preferred_model and _is_likely_embedding_model_name(preferred_model):
                raise RuntimeError(
                    f"Preferred provider model '{preferred_model}' looks like an embedding model. "
                    "Choose a chat model in Settings."
                )
            raise RuntimeError(
                f"No model is loaded for {provider_key}. Load one in the runtime panel."
            )

        if not runtime.get("model_loaded") and mode == "local-managed":
            loaded = self.provider_load(
                provider=provider_key,
                model=model,
                context_length=cfg.get("local_provider_default_context_length"),
            )
            runtime = loaded.get("runtime") if isinstance(loaded, dict) else runtime
            if not runtime.get("model_loaded"):
                raise RuntimeError(f"Failed to load model '{model}' on {provider_key}.")

        return {
            "provider": provider_key,
            "model": model,
            "base_url": normalize_base_url(
                self._adapter(provider_key).resolve_base_url(cfg, with_v1=True),
                with_v1=True,
            ),
            "api_token": str(cfg.get("local_provider_api_token") or "").strip(),
            "runtime": runtime,
        }
