from __future__ import annotations

import asyncio
from typing import Dict, Optional

from app.local_providers import LocalProviderManager
from app.local_providers.selection import (
    effective_provider_for_runtime,
    provider_model_for_action,
    provider_runtime_response,
)
from app.server_presets import (
    find_server_preset,
    resolve_server_auth_token,
    server_trust_warning,
)
from app.services import model_inventory_service
from app.services.tinker_inventory_service import list_tinker_account_models
from fastapi import APIRouter, Body, HTTPException, Query, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool


class ProviderControlRequest(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None
    context_length: Optional[int] = None


def create_provider_router(provider_manager: LocalProviderManager) -> APIRouter:
    router = APIRouter()

    def resolve_provider_for_request(
        request: Request,
        *,
        requested_model: Optional[str] = None,
        explicit_provider: Optional[str] = None,
    ) -> Optional[str]:
        cfg = (
            request.app.state.config
            if isinstance(request.app.state.config, dict)
            else {}
        )
        return effective_provider_for_runtime(
            cfg,
            requested_model=requested_model,
            explicit_provider=explicit_provider,
        )

    @router.get("/llm/provider/status")
    async def provider_status(
        request: Request,
        provider: Optional[str] = Query(default=None),
        model: Optional[str] = Query(default=None),
        quick: bool = Query(default=False),
    ):
        chosen_provider = resolve_provider_for_request(
            request,
            requested_model=model,
            explicit_provider=provider,
        )
        if not chosen_provider:
            raise HTTPException(
                status_code=400,
                detail="Provider must be 'lmstudio' or 'ollama'.",
            )
        runtime = await run_in_threadpool(
            provider_manager.provider_status, chosen_provider, quick
        )
        return {"status": "success", "runtime": runtime}

    @router.get("/llm/provider/models")
    async def provider_models(
        request: Request,
        provider: Optional[str] = Query(default=None),
        model: Optional[str] = Query(default=None),
        refresh: bool = Query(default=False),
    ):
        chosen_provider = resolve_provider_for_request(
            request,
            requested_model=model,
            explicit_provider=provider,
        )
        if not chosen_provider:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Provider must be 'lmstudio', 'ollama', or "
                    "'custom-openai-compatible'."
                ),
            )
        snapshot = provider_manager.provider_models(chosen_provider, refresh=refresh)
        return {
            "status": "success",
            "models": snapshot.get("models", []),
            "runtime": provider_runtime_response(
                snapshot.get("runtime")
                if isinstance(snapshot.get("runtime"), dict)
                else {}
            ),
        }

    @router.get("/llm/server/models")
    async def server_models(
        request: Request,
        server_url: Optional[str] = Query(default=None),
        preset_id: Optional[str] = Query(default=None),
        refresh: bool = Query(default=False),
    ):
        """Probe an OpenAI-compatible server URL for its model inventory."""

        cfg = request.app.state.config
        target_url = str(server_url or cfg.get("server_url") or "").strip()
        if not target_url:
            raise HTTPException(status_code=400, detail="server_url is required")
        headers: Dict[str, str] = {}
        token = resolve_server_auth_token(cfg, target_url, preset_id=preset_id)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        preset = find_server_preset(cfg, preset_id=preset_id, base_url=target_url)
        if str((preset or {}).get("provider") or "").strip().lower() == "tinker":
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(list_tinker_account_models, token), timeout=30.0
                )
            except TimeoutError:
                result = {
                    "reachable": False,
                    "models": [],
                    "loaded_model": "",
                    "provider": "tinker",
                    "inventory_source": "tinker-sdk",
                    "error": "Tinker account inventory timed out after 30 seconds.",
                }
        else:
            result = await asyncio.to_thread(
                model_inventory_service.probe_server_model_inventory,
                target_url,
                headers=headers,
                refresh=refresh,
            )
        warning = server_trust_warning(cfg, preset_id=preset_id, base_url=target_url)
        if warning:
            result["trust_warning"] = warning
        if preset:
            result["preset_id"] = preset.get("id")
            result["provider"] = preset.get("provider")
        return result

    @router.post("/llm/provider/start")
    async def provider_start(
        request: Request,
        payload: Optional[ProviderControlRequest] = Body(default=None),
    ):
        payload = payload or ProviderControlRequest()
        chosen_provider = resolve_provider_for_request(
            request,
            requested_model=payload.model,
            explicit_provider=payload.provider,
        )
        if not chosen_provider:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Provider must be 'lmstudio', 'ollama', or "
                    "'custom-openai-compatible'."
                ),
            )
        result = provider_manager.provider_start(chosen_provider)
        if not result.get("ok"):
            detail = (result.get("result") or {}).get(
                "error"
            ) or "Failed to start provider."
            raise HTTPException(status_code=409, detail=str(detail))
        return {"status": "success", **result}

    @router.post("/llm/provider/stop")
    async def provider_stop(
        request: Request,
        payload: Optional[ProviderControlRequest] = Body(default=None),
    ):
        payload = payload or ProviderControlRequest()
        chosen_provider = resolve_provider_for_request(
            request,
            requested_model=payload.model,
            explicit_provider=payload.provider,
        )
        if not chosen_provider:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Provider must be 'lmstudio', 'ollama', or "
                    "'custom-openai-compatible'."
                ),
            )
        result = provider_manager.provider_stop(chosen_provider)
        if not result.get("ok"):
            detail = (result.get("result") or {}).get(
                "error"
            ) or "Failed to stop provider."
            raise HTTPException(status_code=409, detail=str(detail))
        return {"status": "success", **result}

    @router.post("/llm/provider/load")
    async def provider_load(
        request: Request,
        payload: Optional[ProviderControlRequest] = Body(default=None),
    ):
        payload = payload or ProviderControlRequest()
        chosen_provider = resolve_provider_for_request(
            request,
            requested_model=payload.model,
            explicit_provider=payload.provider,
        )
        if not chosen_provider:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Provider must be 'lmstudio', 'ollama', or "
                    "'custom-openai-compatible'."
                ),
            )
        result = provider_manager.provider_load(
            provider=chosen_provider,
            model=provider_model_for_action(payload.model),
            context_length=payload.context_length,
        )
        if not result.get("ok"):
            detail = (result.get("result") or {}).get(
                "error"
            ) or "Failed to load provider model."
            raise HTTPException(status_code=409, detail=str(detail))
        return {"status": "success", **result}

    @router.post("/llm/provider/unload")
    async def provider_unload(
        request: Request,
        payload: Optional[ProviderControlRequest] = Body(default=None),
    ):
        payload = payload or ProviderControlRequest()
        chosen_provider = resolve_provider_for_request(
            request,
            requested_model=payload.model,
            explicit_provider=payload.provider,
        )
        if not chosen_provider:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Provider must be 'lmstudio', 'ollama', or "
                    "'custom-openai-compatible'."
                ),
            )
        result = provider_manager.provider_unload(
            provider=chosen_provider,
            model=provider_model_for_action(payload.model),
        )
        if not result.get("ok"):
            detail = (result.get("result") or {}).get(
                "error"
            ) or "Failed to unload provider model."
            raise HTTPException(status_code=409, detail=str(detail))
        return {"status": "success", **result}

    @router.get("/llm/provider/logs")
    async def provider_logs(
        request: Request,
        provider: Optional[str] = Query(default=None),
        model: Optional[str] = Query(default=None),
        cursor: int = Query(default=0, ge=0),
        limit: int = Query(default=200, ge=1, le=2000),
    ):
        chosen_provider = resolve_provider_for_request(
            request,
            requested_model=model,
            explicit_provider=provider,
        )
        if not chosen_provider:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Provider must be 'lmstudio', 'ollama', or "
                    "'custom-openai-compatible'."
                ),
            )
        logs = provider_manager.provider_logs(
            provider=chosen_provider,
            cursor=cursor,
            limit=limit,
        )
        return {"status": "success", "logs": logs}

    return router


__all__ = ["ProviderControlRequest", "create_provider_router"]
