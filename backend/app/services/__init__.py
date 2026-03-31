from ..base_services import LLMService, MemoryManager, ModelContext, RAGHandler
from .action_history_service import ActionHistoryService
from .calendar_import import parse_google_calendar, parse_ics
from .capture_service import CaptureService, get_capture_service, set_capture_service
from .computer_service import ComputerService, get_computer_service, set_computer_service
from .langextract_service import LangExtractService
from .livekit_service import LiveKitService
from .sync_service import SyncService
from .tts_service import TTSService

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
    "CaptureService",
    "ComputerService",
    "MemoryManager",
    "ModelContext",
    "RAGHandler",
    "RAG_IMPORT_ERROR",
    "SyncService",
    "TTSService",
    "parse_google_calendar",
    "parse_ics",
    "get_capture_service",
    "set_capture_service",
    "get_computer_service",
    "set_computer_service",
]
