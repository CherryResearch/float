from config import load_model_catalog
from fastapi import APIRouter, File, UploadFile, WebSocket
from streaming.base import StreamingService
from streaming.livekit_service import LiveKitService
from streaming.pipecat_service import PipecatService

router = APIRouter(prefix="/live", tags=["live"])


def _get_service() -> "StreamingService":
    catalog = load_model_catalog()
    provider = catalog.provider.lower()
    if provider == "pipecat":
        return PipecatService(catalog)
    return LiveKitService(catalog)


@router.post("/session")
async def negotiate(check_health: bool = False):
    service = _get_service()
    return await service.negotiate(check_health=check_health)


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    service = _get_service()
    await service.stream(websocket)


@router.post("/image")
async def upload_image(file: UploadFile = File(...)) -> dict[str, int | str]:
    data = await file.read()
    service = _get_service()
    return await service.handle_image(data)


@router.post("/worker-event")
async def worker_event(payload: dict[str, str]):
    event = payload.get("event", "unknown")
    service = _get_service()
    return await service.handle_event(event, payload)
