from __future__ import annotations

from typing import Any, Dict, Optional

from app.model_catalog import build_model_catalog
from app.model_registry import (
    MODEL_REGISTRY,
    filter_models_for_devices,
    list_downloadable_models,
)
from app.services.model_inventory_service import (
    filter_openai_model_ids,
    get_cached_openai_models,
    openai_model_alias_metadata,
    responses_api_base,
    store_cached_openai_models,
)
from app.utils.http_client import http_session
from app.utils.local_model_registry import (
    list_local_model_entries,
    remove_local_model_entry,
    upsert_local_model_entry,
)
from app.utils.user_model_catalog import (
    list_user_hf_models,
    normalize_hf_repo_id,
    normalize_user_model_alias,
    remove_user_hf_model,
    upsert_user_hf_model,
)
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

router = APIRouter()


class LocalModelRegistrationPayload(BaseModel):
    alias: Optional[str] = None
    path: str
    model_type: Optional[str] = None


class HuggingFaceModelRegistrationPayload(BaseModel):
    url: str
    alias: Optional[str] = None
    model_type: Optional[str] = None
    runtime: Optional[str] = None


@router.get("/models/supported")
async def list_supported_models(request: Request):
    devices = request.app.state.config.get("available_devices", [])
    return {"models": sorted(filter_models_for_devices(devices))}


@router.get("/models/downloadable")
async def list_downloadable_model_aliases():
    return {"models": list_downloadable_models()}


@router.get("/models/registered")
async def list_registered_models():
    entries = [
        *list_local_model_entries(include_missing=True),
        *list_user_hf_models(),
    ]
    entries.sort(key=lambda entry: str(entry.get("alias") or "").lower())
    return {"models": entries}


@router.post("/models/registered")
async def register_local_model(payload: LocalModelRegistrationPayload):
    raw_path = str(payload.path or "").strip()
    if not raw_path:
        raise HTTPException(status_code=400, detail="path is required")
    try:
        entry = upsert_local_model_entry(
            path=raw_path,
            alias=payload.alias,
            model_type=payload.model_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"model": entry}


@router.post("/models/registered/huggingface")
async def register_huggingface_model(payload: HuggingFaceModelRegistrationPayload):
    try:
        repo_id = normalize_hf_repo_id(payload.url)
        candidate_alias = normalize_user_model_alias(
            payload.alias or repo_id.rsplit("/", 1)[-1]
        )
        static_aliases = {alias.lower() for alias in MODEL_REGISTRY}
        local_aliases = {
            str(entry.get("alias") or "").lower()
            for entry in list_local_model_entries(include_missing=True)
        }
        if candidate_alias.lower() in static_aliases:
            raise ValueError("alias conflicts with a built-in model")
        if candidate_alias.lower() in local_aliases:
            raise ValueError("alias conflicts with a registered local model")
        entry = upsert_user_hf_model(
            url=repo_id,
            alias=candidate_alias,
            model_type=payload.model_type,
            runtime=payload.runtime,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"model": entry}


@router.delete("/models/registered/{alias}")
async def unregister_model(alias: str):
    removed_local = remove_local_model_entry(alias)
    removed_huggingface = False
    if not removed_local:
        removed_huggingface = remove_user_hf_model(alias)
    if not (removed_local or removed_huggingface):
        raise HTTPException(status_code=404, detail="Registered model not found")
    return {
        "status": "deleted",
        "source": "local" if removed_local else "huggingface",
    }


def _catalog_payload(
    model_ids: list[str],
    *,
    include_non_chat: bool,
    selected_model: Optional[str],
) -> Dict[str, Any]:
    models = filter_openai_model_ids(
        model_ids,
        include_non_chat=include_non_chat,
    )
    payload: Dict[str, Any] = {"models": models}
    aliases = openai_model_alias_metadata(models)
    if aliases:
        payload["model_aliases"] = aliases
    payload.update(build_model_catalog(models, selected_model=selected_model))
    return payload


@router.get("/openai/models")
async def openai_models(
    request: Request,
    include_non_chat: bool = False,
    selected_model: Optional[str] = Query(default=None),
):
    """Return provider inventory plus lifecycle-aware selection metadata."""

    cfg = request.app.state.config
    api_key = cfg.get("api_key")
    if not api_key:
        raise HTTPException(status_code=400, detail="API key not configured")
    base = responses_api_base(cfg.get("api_url"))
    cached_models = get_cached_openai_models(base, api_key)
    if cached_models is not None:
        return _catalog_payload(
            cached_models,
            include_non_chat=include_non_chat,
            selected_model=selected_model,
        )

    headers = {"Authorization": f"Bearer {api_key}"}
    url = f"{base.rstrip('/')}/models"
    response = http_session.get(url, headers=headers, timeout=10)
    try:
        response.raise_for_status()
    except Exception as exc:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.text,
        ) from exc
    data = response.json()
    raw = data.get("data", []) if isinstance(data, dict) else []
    if not isinstance(raw, list):
        raw = []
    model_ids = sorted(
        {
            entry.get("id")
            for entry in raw
            if isinstance(entry, dict)
            and isinstance(entry.get("id"), str)
            and entry.get("id")
        }
    )
    store_cached_openai_models(base, api_key, model_ids)
    return _catalog_payload(
        model_ids,
        include_non_chat=include_non_chat,
        selected_model=selected_model,
    )


__all__ = ["router"]
