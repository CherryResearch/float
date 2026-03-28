from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.local_providers import LocalProviderManager


@dataclass
class Step:
    name: str
    ok: bool
    detail: str = ""


def _parse_model_map(raw: str) -> Dict[str, str]:
    value = str(raw or "").strip()
    if not value:
        return {}
    out: Dict[str, str] = {}
    for item in value.split(","):
        part = item.strip()
        if not part or "=" not in part:
            continue
        key, model = part.split("=", 1)
        key = key.strip().lower()
        model = model.strip()
        if key and model:
            out[key] = model
    return out


def _provider_port(provider: str, override: Optional[int]) -> int:
    if isinstance(override, int) and override > 0:
        return override
    return 11434 if provider == "ollama" else 1234


def _provider_host(override: Optional[str]) -> str:
    host = str(override or "").strip()
    return host or "127.0.0.1"


def _print_step(provider: str, step: Step) -> None:
    mark = "PASS" if step.ok else "FAIL"
    detail = f" - {step.detail}" if step.detail else ""
    print(f"[{provider}] {mark} {step.name}{detail}")


def _run_inference(base_url: str, model: str, api_token: str, prompt: str) -> Step:
    endpoint = f"{base_url.rstrip('/')}/chat/completions"
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    token = str(api_token or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 64,
        "temperature": 0,
    }
    try:
        resp = requests.post(endpoint, headers=headers, json=payload, timeout=90)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            return Step("inference", False, "Non-JSON response payload.")
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            return Step("inference", False, "No choices returned.")
        return Step("inference", True, f"Received {len(choices)} choice(s).")
    except Exception as exc:
        return Step("inference", False, str(exc))


def _run_provider(
    *,
    provider: str,
    mode: str,
    host: str,
    port: int,
    base_url: str,
    lmstudio_path: str,
    api_token: str,
    auto_start: bool,
    model_hint: str,
    context_length: Optional[int],
    inference_prompt: str,
    manage_server: bool,
) -> List[Step]:
    cfg: Dict[str, Any] = {
        "local_provider": provider,
        "local_provider_mode": mode,
        "local_provider_host": host,
        "local_provider_port": port,
        "local_provider_base_url": base_url,
        "lmstudio_path": lmstudio_path,
        "local_provider_api_token": api_token,
        "local_provider_auto_start": auto_start,
        "local_provider_preferred_model": model_hint,
        "local_provider_default_context_length": context_length or 0,
        "local_provider_show_server_logs": False,
        "local_provider_enable_cors": False,
        "local_provider_allow_lan": False,
    }
    manager = LocalProviderManager(lambda: cfg)
    steps: List[Step] = []
    started_here = False

    try:
        status = manager.provider_status(provider)
        steps.append(
            Step(
                "install-detection",
                True,
                "installed" if status.get("installed") else "not detected",
            )
        )

        if manage_server and mode == "local-managed":
            started = manager.provider_start(provider)
            steps.append(
                Step(
                    "start-server",
                    bool(started.get("ok")),
                    str((started.get("result") or {}).get("error") or ""),
                )
            )
            started_here = bool(started.get("ok"))

        status = manager.provider_status(provider)
        steps.append(
            Step(
                "status-poll",
                bool(status.get("server_running")),
                json.dumps(
                    {
                        "running": bool(status.get("server_running")),
                        "loaded_model": status.get("loaded_model"),
                    }
                ),
            )
        )

        models_result = manager.provider_models(provider)
        models = models_result.get("models") if isinstance(models_result, dict) else []
        if not isinstance(models, list):
            models = []
        steps.append(
            Step(
                "list-models",
                True,
                f"{len(models)} model(s) discovered",
            )
        )

        chosen_model = str(model_hint or "").strip()
        if not chosen_model and models:
            chosen_model = str(models[0]).strip()
        if chosen_model:
            loaded = manager.provider_load(
                provider=provider,
                model=chosen_model,
                context_length=context_length,
            )
            steps.append(
                Step(
                    "load-model",
                    bool(loaded.get("ok")),
                    str((loaded.get("result") or {}).get("error") or chosen_model),
                )
            )
        else:
            steps.append(Step("load-model", False, "No model available to load."))

        if chosen_model:
            try:
                target = manager.resolve_inference_target(
                    provider=provider,
                    requested_model=chosen_model,
                    allow_auto_start=auto_start,
                )
                infer_step = _run_inference(
                    str(target.get("base_url") or ""),
                    str(target.get("model") or chosen_model),
                    str(target.get("api_token") or api_token),
                    inference_prompt,
                )
            except Exception as exc:
                infer_step = Step("inference", False, str(exc))
            steps.append(infer_step)

            unloaded = manager.provider_unload(provider=provider, model=chosen_model)
            steps.append(
                Step(
                    "unload-model",
                    bool(unloaded.get("ok")),
                    str((unloaded.get("result") or {}).get("error") or ""),
                )
            )
        else:
            steps.append(Step("inference", False, "Skipped because no model is selected."))
            steps.append(Step("unload-model", False, "Skipped because no model is selected."))
    finally:
        if started_here and manage_server and mode == "local-managed":
            stopped = manager.provider_stop(provider)
            steps.append(
                Step(
                    "stop-server",
                    bool(stopped.get("ok")),
                    str((stopped.get("result") or {}).get("error") or ""),
                )
            )
    return steps


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Smoke test managed local providers (LM Studio/Ollama) via Float's "
            "provider manager adapter contract."
        )
    )
    parser.add_argument(
        "--providers",
        default="lmstudio,ollama",
        help="Comma-separated providers to test (default: lmstudio,ollama).",
    )
    parser.add_argument(
        "--mode",
        default="local-managed",
        choices=["local-managed", "remote-unmanaged"],
        help="Provider mode to test.",
    )
    parser.add_argument(
        "--model-map",
        default="",
        help="Optional provider=model map, e.g. 'lmstudio=gpt-oss-20b,ollama=llama3.2'.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host override for both providers.")
    parser.add_argument("--lmstudio-port", type=int, default=1234, help="LM Studio port.")
    parser.add_argument("--ollama-port", type=int, default=11434, help="Ollama port.")
    parser.add_argument(
        "--base-url",
        default="",
        help="Optional explicit base URL override for both providers.",
    )
    parser.add_argument(
        "--lmstudio-path",
        default="",
        help="Optional lms CLI path override for LM Studio managed mode.",
    )
    parser.add_argument("--api-token", default="", help="Optional API token for inference.")
    parser.add_argument(
        "--context-length",
        type=int,
        default=0,
        help="Optional context length for load requests.",
    )
    parser.add_argument(
        "--no-manage-server",
        action="store_true",
        help="Skip explicit start/stop lifecycle checks.",
    )
    parser.add_argument(
        "--prompt",
        default="Reply with exactly: smoke-ok",
        help="Inference prompt to send.",
    )
    args = parser.parse_args()

    providers = [
        item.strip().lower()
        for item in str(args.providers or "").split(",")
        if item.strip()
    ]
    valid = {"lmstudio", "ollama"}
    providers = [item for item in providers if item in valid]
    if not providers:
        print("No valid providers specified. Use lmstudio and/or ollama.")
        return 2

    model_map = _parse_model_map(args.model_map)
    all_steps: List[Step] = []
    any_fail = False

    for provider in providers:
        port = _provider_port(
            provider,
            args.ollama_port if provider == "ollama" else args.lmstudio_port,
        )
        host = _provider_host(args.host)
        model_hint = model_map.get(provider, "")
        steps = _run_provider(
            provider=provider,
            mode=args.mode,
            host=host,
            port=port,
            base_url=str(args.base_url or "").strip(),
            lmstudio_path=str(args.lmstudio_path or "").strip(),
            api_token=str(args.api_token or "").strip(),
            auto_start=(args.mode == "local-managed"),
            model_hint=model_hint,
            context_length=args.context_length if args.context_length > 0 else None,
            inference_prompt=args.prompt,
            manage_server=not args.no_manage_server,
        )
        for step in steps:
            _print_step(provider, step)
            all_steps.append(step)
            if not step.ok:
                any_fail = True
        print("")

    passed = sum(1 for step in all_steps if step.ok)
    failed = sum(1 for step in all_steps if not step.ok)
    print(f"Summary: {passed} passed, {failed} failed")
    return 1 if any_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
