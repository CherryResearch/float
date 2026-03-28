from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import requests

from .base import LocalProviderAdapter, normalize_base_url


def _coerce_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except Exception:
        return fallback


class LMStudioAdapter(LocalProviderAdapter):
    provider_name = "lmstudio"

    def _port(self, cfg: Dict[str, Any]) -> int:
        return _coerce_int(cfg.get("local_provider_port"), 1234)

    def _host(self, cfg: Dict[str, Any]) -> str:
        host = str(cfg.get("local_provider_host") or "").strip()
        return host or "127.0.0.1"

    def _mode(self, cfg: Dict[str, Any]) -> str:
        mode = str(cfg.get("local_provider_mode") or "").strip().lower()
        return mode if mode in {"local-managed", "remote-unmanaged"} else "local-managed"

    def _lms_binary(self, cfg: Dict[str, Any]) -> Optional[str]:
        explicit = str(cfg.get("lmstudio_path") or "").strip()
        if explicit:
            p = Path(explicit)
            if p.is_dir():
                exe_name = "lms.exe" if os.name == "nt" else "lms"
                candidate = p / exe_name
                if candidate.exists():
                    return str(candidate)
            elif p.exists():
                return str(p)
        found = shutil.which("lms")
        return found

    def detect_installation(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        binary = self._lms_binary(cfg)
        return {
            "ok": bool(binary),
            "installed": bool(binary),
            "binary": binary or "",
        }

    def resolve_base_url(self, cfg: Dict[str, Any], *, with_v1: bool) -> str:
        configured = str(cfg.get("local_provider_base_url") or "").strip()
        if configured:
            return normalize_base_url(configured, with_v1=with_v1)
        base = f"http://{self._host(cfg)}:{self._port(cfg)}"
        return normalize_base_url(base, with_v1=with_v1)

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

    def _http_post_json(
        self,
        url: str,
        payload: Dict[str, Any],
        timeout: float = 30.0,
    ) -> Optional[Dict[str, Any]]:
        try:
            response = requests.post(url, json=payload, timeout=timeout)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict):
                return data
            return {"ok": True}
        except Exception:
            return None

    def list_models(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        base_v1 = self.resolve_base_url(cfg, with_v1=True)
        candidates = [f"{base_v1}/models"]
        api_base = self.resolve_base_url(cfg, with_v1=False)
        candidates.extend(
            [
                f"{api_base}/api/v0/models",
                f"{api_base}/api/v1/models",
            ]
        )
        reachable = False
        for endpoint in candidates:
            payload = self._http_get_json(endpoint)
            if not isinstance(payload, dict):
                continue
            reachable = True
            models: List[str] = []
            data = payload.get("data")
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        value = item.get("id") or item.get("model")
                        if isinstance(value, str) and value.strip():
                            models.append(value.strip())
            raw_models = payload.get("models")
            if isinstance(raw_models, list):
                for item in raw_models:
                    if isinstance(item, str) and item.strip():
                        models.append(item.strip())
                    elif isinstance(item, dict):
                        value = item.get("id") or item.get("model")
                        if isinstance(value, str) and value.strip():
                            models.append(value.strip())
            if models:
                return {
                    "ok": True,
                    "models": sorted(set(models)),
                    "endpoint": endpoint,
                }
        return {"ok": reachable, "models": []}

    def poll_status(self, cfg: Dict[str, Any], *, quick: bool = False) -> Dict[str, Any]:
        base = self.resolve_base_url(cfg, with_v1=False)
        status_payload = None
        if quick:
            status_endpoints = (
                f"{base}/health",
                f"{base}/api/v1/status",
                f"{base}/api/v0/status",
            )
            status_timeout = 0.35
        else:
            status_endpoints = (
                f"{base}/api/v0/status",
                f"{base}/api/v0/system/status",
                f"{base}/api/v1/status",
                f"{base}/health",
            )
            status_timeout = 2.5
        for endpoint in status_endpoints:
            payload = self._http_get_json(endpoint, timeout=status_timeout)
            if payload is not None:
                status_payload = payload
                break

        if quick:
            models_result: Dict[str, Any] = {"ok": False, "models": []}
        else:
            models_result = self.list_models(cfg)
        models = models_result.get("models") if isinstance(models_result, dict) else []
        if not isinstance(models, list):
            models = []

        loaded_model = None
        context_length = None
        if isinstance(status_payload, dict):
            for key in (
                "loaded_model",
                "active_model",
                "current_model",
                "model",
            ):
                value = status_payload.get(key)
                if isinstance(value, str) and value.strip():
                    loaded_model = value.strip()
                    break
            for key in (
                "context_length",
                "max_context_length",
                "n_ctx",
            ):
                value = status_payload.get(key)
                if isinstance(value, (int, float)):
                    context_length = int(value)
                    break
                if isinstance(value, str) and value.isdigit():
                    context_length = int(value)
                    break
        if loaded_model is None and models:
            if len(models) == 1:
                loaded_model = str(models[0]).strip()

        base_v1 = self.resolve_base_url(cfg, with_v1=True)
        server_running = bool(status_payload is not None or models_result.get("ok"))
        return {
            "ok": True,
            "server_running": server_running,
            "model_loaded": bool(loaded_model),
            "loaded_model": loaded_model,
            "context_length": context_length,
            "base_url": base_v1,
            "details": status_payload or {},
        }

    def _run_cmd(self, args: List[str], timeout: int = 45) -> Dict[str, Any]:
        try:
            result = subprocess.run(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc), "cmd": args}
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            return {
                "ok": False,
                "error": detail or f"Command failed with exit code {result.returncode}",
                "cmd": args,
            }
        return {"ok": True, "stdout": result.stdout or "", "cmd": args}

    def _wait_until_running(self, cfg: Dict[str, Any], timeout_seconds: int = 30) -> bool:
        deadline = time.time() + max(1, timeout_seconds)
        while time.time() < deadline:
            status = self.poll_status(cfg)
            if bool(status.get("server_running")):
                return True
            time.sleep(0.5)
        return False

    def start_server(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        if self._mode(cfg) == "remote-unmanaged":
            return {"ok": False, "error": "Remote unmanaged mode does not support start."}
        install = self.detect_installation(cfg)
        if not install.get("installed"):
            return {"ok": False, "error": "LM Studio CLI (lms) was not found."}
        binary = str(install.get("binary") or "")
        args = [binary, "server", "start", "--port", str(self._port(cfg))]
        if bool(cfg.get("local_provider_allow_lan")):
            args.extend(["--host", "0.0.0.0"])
        if bool(cfg.get("local_provider_enable_cors")):
            args.append("--cors")
        result = self._run_cmd(args)
        if not result.get("ok"):
            return result
        if not self._wait_until_running(cfg):
            return {"ok": False, "error": "LM Studio server did not become ready in time."}
        return {"ok": True}

    def stop_server(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        if self._mode(cfg) == "remote-unmanaged":
            return {"ok": False, "error": "Remote unmanaged mode does not support stop."}
        install = self.detect_installation(cfg)
        if not install.get("installed"):
            return {"ok": False, "error": "LM Studio CLI (lms) was not found."}
        binary = str(install.get("binary") or "")
        return self._run_cmd([binary, "server", "stop"])

    def load_model(
        self,
        cfg: Dict[str, Any],
        *,
        model: str,
        context_length: Optional[int] = None,
    ) -> Dict[str, Any]:
        chosen = str(model or "").strip()
        if not chosen:
            return {"ok": False, "error": "Model is required for LM Studio load."}
        if self._mode(cfg) == "remote-unmanaged":
            base = self.resolve_base_url(cfg, with_v1=False)
            payload: Dict[str, Any] = {"model": chosen}
            if isinstance(context_length, int) and context_length > 0:
                payload["context_length"] = context_length
            for endpoint in (
                f"{base}/api/v0/model/load",
                f"{base}/api/v0/models/load",
                f"{base}/api/v1/model/load",
            ):
                response = self._http_post_json(endpoint, payload, timeout=8.0)
                if isinstance(response, dict):
                    return {"ok": True, "endpoint": endpoint, "response": response}
            return {
                "ok": False,
                "error": "LM Studio remote load endpoint is unavailable.",
            }
        install = self.detect_installation(cfg)
        if not install.get("installed"):
            return {"ok": False, "error": "LM Studio CLI (lms) was not found."}
        args = [str(install.get("binary") or ""), "load", chosen]
        if isinstance(context_length, int) and context_length > 0:
            args.extend(["--context-length", str(context_length)])
        return self._run_cmd(args, timeout=120)

    def unload_model(
        self,
        cfg: Dict[str, Any],
        *,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        chosen = str(model or "").strip()
        if self._mode(cfg) == "remote-unmanaged":
            base = self.resolve_base_url(cfg, with_v1=False)
            payload: Dict[str, Any] = {"model": chosen} if chosen else {}
            for endpoint in (
                f"{base}/api/v0/model/unload",
                f"{base}/api/v0/models/unload",
                f"{base}/api/v1/model/unload",
            ):
                response = self._http_post_json(endpoint, payload, timeout=8.0)
                if isinstance(response, dict):
                    return {"ok": True, "endpoint": endpoint, "response": response}
            return {
                "ok": False,
                "error": "LM Studio remote unload endpoint is unavailable.",
            }
        install = self.detect_installation(cfg)
        if not install.get("installed"):
            return {"ok": False, "error": "LM Studio CLI (lms) was not found."}
        binary = str(install.get("binary") or "")
        if chosen:
            primary = self._run_cmd([binary, "unload", chosen], timeout=60)
            if primary.get("ok"):
                return primary
        fallback = self._run_cmd([binary, "unload", "--all"], timeout=60)
        if fallback.get("ok"):
            return fallback
        return self._run_cmd([binary, "unload"], timeout=60)

    def stream_logs(self, cfg: Dict[str, Any], stop_event) -> Iterator[Dict[str, Any]]:
        install = self.detect_installation(cfg)
        if not install.get("installed"):
            return
        args = [
            str(install.get("binary") or ""),
            "log",
            "stream",
            "--source",
            "server",
            "--json",
        ]
        try:
            process = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except Exception as exc:
            yield {"level": "error", "message": str(exc)}
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
        finally:
            try:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=3)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass

    def capabilities(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        mode = self._mode(cfg)
        managed = mode == "local-managed"
        return {
            "start_stop": managed,
            "load_unload": True,
            "context_length": True,
            "logs_stream": managed,
        }
