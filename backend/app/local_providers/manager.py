from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional

from .base import normalize_base_url
from .lmstudio import LMStudioAdapter
from .ollama import OllamaAdapter

PROVIDER_MARKERS = {"lmstudio", "ollama"}


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


class LocalProviderManager:
    def __init__(self, config_getter: Callable[[], Dict[str, Any]]) -> None:
        self._config_getter = config_getter
        self._adapters = {
            "lmstudio": LMStudioAdapter(),
            "ollama": OllamaAdapter(),
        }
        self._lock = threading.Lock()
        self._last_error: Dict[str, str] = {}
        self._log_entries: Dict[str, Deque[Dict[str, Any]]] = {
            "lmstudio": deque(maxlen=4000),
            "ollama": deque(maxlen=4000),
        }
        self._log_seq: Dict[str, int] = {"lmstudio": 0, "ollama": 0}
        self._log_threads: Dict[str, Optional[threading.Thread]] = {
            "lmstudio": None,
            "ollama": None,
        }
        self._log_stops: Dict[str, Optional[threading.Event]] = {
            "lmstudio": None,
            "ollama": None,
        }

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
            "local_provider_host": str(cfg.get("local_provider_host") or host_default).strip()
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
            "local_provider_allow_lan": bool(cfg.get("local_provider_allow_lan", False)),
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

    def _record_error(self, provider: str, message: str) -> None:
        with self._lock:
            self._last_error[_normalize_provider(provider)] = str(message or "").strip()

    def _clear_error(self, provider: str) -> None:
        with self._lock:
            self._last_error.pop(_normalize_provider(provider), None)

    def _append_log(self, provider: str, level: str, message: str, payload: Any = None) -> None:
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

    def _status(self, provider: Optional[str], *, quick: bool = False) -> Dict[str, Any]:
        cfg = self._settings_for_provider(provider)
        provider_key = _normalize_provider(cfg.get("local_provider"))
        adapter = self._adapter(provider_key)
        install = adapter.detect_installation(cfg)
        status = adapter.poll_status(cfg, quick=quick)
        last_error = ""
        with self._lock:
            last_error = self._last_error.get(provider_key, "")
        model = status.get("loaded_model") if isinstance(status, dict) else None
        context_length = status.get("context_length") if isinstance(status, dict) else None
        base_v1 = adapter.resolve_base_url(cfg, with_v1=True)
        return {
            "provider": provider_key,
            "mode": cfg.get("local_provider_mode"),
            "installed": bool(install.get("installed")),
            "server_running": bool(status.get("server_running")),
            "model_loaded": bool(status.get("model_loaded")),
            "loaded_model": model if isinstance(model, str) and model.strip() else None,
            "context_length": context_length if isinstance(context_length, int) else None,
            "base_url": normalize_base_url(base_v1, with_v1=True),
            "host": cfg.get("local_provider_host"),
            "port": cfg.get("local_provider_port"),
            "last_error": last_error or None,
            "capabilities": adapter.capabilities(cfg),
            "details": status.get("details") if isinstance(status.get("details"), dict) else {},
        }

    def provider_status(
        self, provider: Optional[str] = None, quick: bool = False
    ) -> Dict[str, Any]:
        return self._status(provider, quick=quick)

    def provider_models(self, provider: Optional[str] = None) -> Dict[str, Any]:
        cfg = self._settings_for_provider(provider)
        provider_key = _normalize_provider(cfg.get("local_provider"))
        adapter = self._adapter(provider_key)
        result = adapter.list_models(cfg)
        models = result.get("models") if isinstance(result, dict) else []
        if not isinstance(models, list):
            models = []
        cleaned = sorted(
            {
                str(item).strip()
                for item in models
                if isinstance(item, str) and str(item).strip()
            }
        )
        return {
            "provider": provider_key,
            "models": cleaned,
        }

    def provider_start(self, provider: Optional[str] = None) -> Dict[str, Any]:
        cfg = self._settings_for_provider(provider)
        provider_key = _normalize_provider(cfg.get("local_provider"))
        adapter = self._adapter(provider_key)
        result = adapter.start_server(cfg)
        if result.get("ok"):
            self._clear_error(provider_key)
            self._append_log(provider_key, "info", "Server start requested.")
            self._start_log_stream(provider_key, cfg)
        else:
            error = str(result.get("error") or "Failed to start provider server.")
            self._record_error(provider_key, error)
            self._append_log(provider_key, "error", error)
        status = self._status(provider_key)
        return {"ok": bool(result.get("ok")), "result": result, "runtime": status}

    def provider_stop(self, provider: Optional[str] = None) -> Dict[str, Any]:
        cfg = self._settings_for_provider(provider)
        provider_key = _normalize_provider(cfg.get("local_provider"))
        adapter = self._adapter(provider_key)
        result = adapter.stop_server(cfg)
        self._stop_log_stream(provider_key)
        if result.get("ok"):
            self._clear_error(provider_key)
            self._append_log(provider_key, "info", "Server stop requested.")
        else:
            error = str(result.get("error") or "Failed to stop provider server.")
            self._record_error(provider_key, error)
            self._append_log(provider_key, "error", error)
        status = self._status(provider_key)
        return {"ok": bool(result.get("ok")), "result": result, "runtime": status}

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
        requested_model = str(model or cfg.get("local_provider_preferred_model") or "").strip()
        if not requested_model:
            error = "Model is required to load a provider runtime."
            self._record_error(provider_key, error)
            return {"ok": False, "result": {"ok": False, "error": error}, "runtime": self._status(provider_key)}

        current = self._status(provider_key)
        if (
            cfg.get("local_provider_mode") == "local-managed"
            and cfg.get("local_provider_auto_start", True)
            and not current.get("server_running")
        ):
            self.provider_start(provider_key)

        result = adapter.load_model(
            cfg,
            model=requested_model,
            context_length=context_length,
        )
        if result.get("ok"):
            self._clear_error(provider_key)
            self._append_log(provider_key, "info", f"Model load requested: {requested_model}")
            self._start_log_stream(provider_key, cfg)
        else:
            error = str(result.get("error") or "Failed to load provider model.")
            self._record_error(provider_key, error)
            self._append_log(provider_key, "error", error)
        status = self._status(provider_key)
        return {"ok": bool(result.get("ok")), "result": result, "runtime": status}

    def provider_unload(
        self,
        *,
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        cfg = self._settings_for_provider(provider)
        provider_key = _normalize_provider(cfg.get("local_provider"))
        adapter = self._adapter(provider_key)
        result = adapter.unload_model(cfg, model=model)
        if result.get("ok"):
            self._clear_error(provider_key)
            self._append_log(provider_key, "info", "Model unload requested.")
        else:
            error = str(result.get("error") or "Failed to unload provider model.")
            self._record_error(provider_key, error)
            self._append_log(provider_key, "error", error)
        status = self._status(provider_key)
        return {"ok": bool(result.get("ok")), "result": result, "runtime": status}

    def provider_logs(
        self,
        *,
        provider: Optional[str] = None,
        cursor: int = 0,
        limit: int = 200,
    ) -> Dict[str, Any]:
        provider_key = _normalize_provider(provider or self._base_config().get("local_provider"))
        max_items = max(1, min(int(limit or 200), 2000))
        cursor_value = max(0, int(cursor or 0))
        with self._lock:
            all_items = list(self._log_entries[provider_key])
            next_cursor = self._log_seq[provider_key]
        items = [entry for entry in all_items if int(entry.get("seq", 0)) > cursor_value]
        if len(items) > max_items:
            items = items[-max_items:]
        return {
            "provider": provider_key,
            "cursor": cursor_value,
            "next_cursor": next_cursor,
            "entries": items,
        }

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
        runtime = self._status(provider_key)

        if not runtime.get("server_running"):
            should_start = (
                mode == "local-managed"
                and allow_auto_start
                and bool(cfg.get("local_provider_auto_start", True))
            )
            if should_start:
                started = self.provider_start(provider=provider_key)
                runtime = started.get("runtime") if isinstance(started, dict) else runtime
            if not runtime.get("server_running"):
                raise RuntimeError(
                    f"{provider_key} server is not running. Start it in the runtime panel."
                )

        raw_requested = str(requested_model or "").strip()
        if self.is_provider_marker(raw_requested):
            raw_requested = ""
        model = (
            raw_requested
            or str(runtime.get("loaded_model") or "").strip()
            or str(cfg.get("local_provider_preferred_model") or "").strip()
        )

        if not model:
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
                raise RuntimeError(
                    f"Failed to load model '{model}' on {provider_key}."
                )

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
