from ..base_services import LLMService, MemoryManager, ModelContext, RAGHandler
from .action_history_service import ActionHistoryService
from .background_autonomy_service import (
    BackgroundAutonomyService,
    build_background_autonomy_service,
)
from .calendar_import import parse_google_calendar, parse_ics
from .capture_service import CaptureService, get_capture_service, set_capture_service
from .computer_service import (
    ComputerService,
    get_computer_service,
    set_computer_service,
)
from .langextract_service import LangExtractService
from .livekit_service import LiveKitService
from .reflection_service import ReflectionService, build_reflection_service
from .stt_service import STTService
from .sync_service import SyncService
from .tts_service import TTSService
from .work_run_store import WorkRunStore

try:  # pragma: no cover - optional dependency
    from .rag_service import RAGService

    RAG_IMPORT_ERROR = None
except ModuleNotFoundError as exc:  # pragma: no cover
    RAGService = None  # type: ignore
    RAG_IMPORT_ERROR = exc

__all__ = [
    "LLMService",
    "LiveKitService",
    "LangExtractService",
    "ActionHistoryService",
    "BackgroundAutonomyService",
    "CaptureService",
    "ComputerService",
    "MemoryManager",
    "ModelContext",
    "RAGHandler",
    "RAG_IMPORT_ERROR",
    "ReflectionService",
    "SyncService",
    "STTService",
    "TTSService",
    "WorkRunStore",
    "parse_google_calendar",
    "parse_ics",
    "get_capture_service",
    "set_capture_service",
    "get_computer_service",
    "set_computer_service",
    "build_reflection_service",
    "build_background_autonomy_service",
]
