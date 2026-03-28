from __future__ import annotations

import os
from typing import Dict

from app.services.sync_service import SyncService
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/sync", tags=["sync"])

_secret = os.getenv("SYNC_SECRET", "change-me")
service = SyncService(secret_key=_secret)


class Destination(BaseModel):
    name: str
    url: str


@router.get("/destinations")
async def list_destinations() -> Dict[str, Dict[str, str]]:
    return {"destinations": service.list_destinations()}


@router.post("/destinations")
async def add_destination(dest: Destination) -> Dict[str, str]:
    service.add_destination(dest.name, dest.url)
    return {"status": "added"}


@router.delete("/destinations/{name}")
async def remove_destination(name: str) -> Dict[str, str]:
    if name not in service.list_destinations():
        raise HTTPException(status_code=404, detail="Destination not found")
    service.remove_destination(name)
    return {"status": "removed"}
