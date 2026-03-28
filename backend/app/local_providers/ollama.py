from __future__ import annotations

import json
import shutil
import subprocess
import time
from typing import Any, Dict, Iterator, List, Optional

import requests

from .base import LocalProviderAdapter, normalize_base_url


def _coerce_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except Exception:
        return fallback


class OllamaAdapter(LocalProviderAdapter):
    provider_name = "ollama"

    def __init__(self) -> None:
        self._managed_process: Optional[subprocess.Popen] = None

    def _mode(self, cfg: Dict[str, Any]) -> str:
        mode = str(cfg.get("local_provider_mode") or "").strip().lower()
        return mode if mode in {"local-managed", "remote-unmanaged"} else "local-managed"

    def _host(self, cfg: Dict[str, Any]) -> str:
        host = str(cfg.get("local_provider_host") or "").strip()
        return host or "127.0.0.1"

    def _port(self, cfg: Dict[str, Any]) -> int:
        port_value = cfg.get("local_provider_port")
        if port_value in (None, ""):
            return 11434
        return _coerce_int(port_value, 11434)

    def _base_url(self, cfg: Dict[str, Any], *, with_v1: bool) -> str:
        configured = str(cfg.get("local_provider_base_url") or "").strip()
        if configured:
            return normalize_base_url(configured, with_v1=with_v1)
        base = f"http://{self._host(cfg)}:{self._port(cfg)}"
        return normalize_base_url(base, with_v1=with_v1)

    def detect_installation(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        binary = shutil.which("ollama")
        return {"ok": bool(binary), "installed": bool(binary), "binary": binary or ""}

    def resolve_base_url(self, cfg: Dict[str, Any], *, with_v1: bool) -> str:
        return self._base_url(cfg, with_v1=with_v1)

    def _http_get_json(self, url: str, timeout: float = 2.5) -> Optional[Dict[str, Any]]:
        try:
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict):
                return payload
        except Exception:
            return None
        return None

    def list_models(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        base = self._base_url(cfg, with_v1=False)
        payload = self._http_get_json(f"{base}/api/tags")
        if not isinstance(payload, dict):
            return {"ok": False, "models": []}
        models: List[str] = []
        entries = payload.get("models")
        if isinstance(entries, list):
            for item in entries:
                if not isinstance(item, dict):
                    continue
                value = item.get("name") or item.get("model")
                if isinstance(value, str) and value.strip():
                    models.append(value.strip())
        return {"ok": True, "models": sorted(set(models))}

    def poll_status(self, cfg: Dict[str, Any], *, quick: bool = False) -> Dict[str, Any]:
        base = self._base_url(cfg, with_v1=False)
        timeout = 0.35 if quick else 2.5
        version = self._http_get_json(f"{base}/api/version", timeout=timeout)
        running = version is not None

        loaded_models: List[str] = []
        context_length = None
        ps = self._http_get_json(f"{base}/api/ps", timeout=timeout) if running else None
        if isinstance(ps, dict):
            items = ps.get("models")
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    value = item.get("name") or item.get("model")
                    if isinstance(value, str) and value.strip():
                        loaded_models.append(value.strip())
                    details = item.get("details")
                    if isinstance(details, dict) and context_length is None:
                        for key in ("num_ctx", "context_length", "max_context_length"):
                            raw = details.get(key)
                            if isinstance(raw, (int, float)):
                                context_length = int(raw)
                                break
                            if isinstance(raw, str) and raw.isdigit():
                                context_length = int(raw)
                                break
        loaded_model = loaded_models[0] if loaded_models else None
        return {
            "ok": True,
            "server_running": running,
            "model_loaded": bool(loaded_model),
            "loaded_model": loaded_model,
            "context_length": context_length,
            "base_url": self._base_url(cfg, with_v1=True),
            "details": {
                "version": version or {},
                "loaded_models": loaded_models,
            },
        }

    def _wait_until_running(self, cfg: Dict[str, Any], timeout_seconds: int = 30) -> bool:
        deadline = time.time() + max(1, timeout_seconds)
        while time.time() < deadline:
            status = self.poll_status(cfg)
            if bool(status.get("server_running")):
                return True
            time.sleep(0.4)
        return False

    def start_server(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        if self._mode(cfg) == "remote-unmanaged":
            return {"ok": False, "error": "Remote unmanaged mode does not support start."}
        install = self.detect_installation(cfg)
        if not install.get("installed"):
            return {"ok": False, "error": "Ollama CLI was not found."}
        current = self.poll_status(cfg)
        if current.get("server_running"):
            return {"ok": True, "note": "Ollama server already running."}
        if self._managed_process and self._managed_process.poll() is None:
            if self._wait_until_running(cfg):
                return {"ok": True}
        try:
            self._managed_process = subprocess.Popen(
                [str(install.get("binary") or ""), "serve"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if not self._wait_until_running(cfg):
            return {"ok": False, "error": "Ollama server did not become ready in time."}
        return {"ok": True}

    def stop_server(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        if self._mode(cfg) == "remote-unmanaged":
            return {"ok": False, "error": "Remote unmanaged mode does not support stop."}
        process = self._managed_process
        if process is None or process.poll() is not None:
            return {"ok": False, "error": "Ollama stop is only supported for Float-managed server processes."}
        try:
            process.terminate()
            process.wait(timeout=8)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
        finally:
            self._managed_process = None
        return {"ok": True}

    def load_model(
        self,
        cfg: Dict[str, Any],
        *,
        model: str,
        context_length: Optional[int] = None,
    ) -> Dict[str, Any]:
        chosen = str(model or "").strip()
        if not chosen:
            return {"ok": False, "error": "Model is required for Ollama load."}
        base = self._base_url(cfg, with_v1=False)
        payload: Dict[str, Any] = {
            "model": chosen,
            "prompt": "",
            "stream": False,
            "keep_alive": "30m",
        }
        if isinstance(context_length, int) and context_length > 0:
            payload["options"] = {"num_ctx": context_length}
        try:
            response = requests.post(f"{base}/api/generate", json=payload, timeout=120)
            response.raise_for_status()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    def unload_model(
        self,
        cfg: Dict[str, Any],
        *,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        base = self._base_url(cfg, with_v1=False)
        targets: List[str] = []
        chosen = str(model or "").strip()
        if chosen:
            targets = [chosen]
        else:
            status = self.poll_status(cfg)
            loaded = (
                status.get("details", {}).get("loaded_models")
                if isinstance(status.get("details"), dict)
                else []
            )
            if isinstance(loaded, list):
                for item in loaded:
                    if isinstance(item, str) and item.strip():
                        targets.append(item.strip())
        if not targets:
            return {"ok": True, "note": "No loaded Ollama models to unload."}

        errors = []
        for entry in targets:
            payload = {
                "model": entry,
                "prompt": "",
                "stream": False,
                "keep_alive": 0,
            }
            try:
                response = requests.post(f"{base}/api/generate", json=payload, timeout=60)
                response.raise_for_status()
            except Exception as exc:
                errors.append(str(exc))
        if errors:
            return {"ok": False, "error": "; ".join(errors)}
        return {"ok": True}

    def stream_logs(self, cfg: Dict[str, Any], stop_event) -> Iterator[Dict[str, Any]]:
        process = self._managed_process
        if process is None or process.poll() is not None:
            return
        try:
            assert process.stdout is not None
            while not stop_event.is_set():
                line = process.stdout.readline()
                if not line:
                    if process.poll() is not None:
                        break
                    time.sleep(0.1)
                    continue
                raw = line.rstrip("\r\n")
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                    if isinstance(payload, dict):
                        yield {"level": "info", "payload": payload, "message": raw}
                        continue
                except Exception:
                    pass
                yield {"level": "info", "message": raw}
        except Exception as exc:
            yield {"level": "error", "message": str(exc)}

    def capabilities(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        mode = self._mode(cfg)
        return {
            "start_stop": mode == "local-managed",
            "load_unload": True,
            "context_length": False,
            "logs_stream": mode == "local-managed",
        }
