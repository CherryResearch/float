import sys
import types
from pathlib import Path

backend_dir = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(backend_dir))

sys.modules.setdefault(
    "openai_harmony",
    types.SimpleNamespace(Message=None, Role=None),
)

dotenv_stub = types.ModuleType("dotenv")
dotenv_stub.load_dotenv = lambda *args, **kwargs: None
sys.modules.setdefault("dotenv", dotenv_stub)


langextract_data = types.ModuleType("langextract.data")


class _Extraction:
    def __init__(
        self,
        extraction_class="",
        extraction_text="",
        attributes=None,
    ):
        self.extraction_class = extraction_class
        self.extraction_text = extraction_text
        self.attributes = attributes


class _AnnotatedDocument:
    def __init__(self, extractions=None):
        self.extractions = extractions or []


class _ExampleData:
    pass


langextract_data.Extraction = _Extraction
langextract_data.AnnotatedDocument = _AnnotatedDocument
langextract_data.ExampleData = _ExampleData

langextract_stub = types.ModuleType("langextract")


def _extract(text, prompt_description=None, examples=None):
    return _AnnotatedDocument([])


langextract_stub.extract = _extract
langextract_stub.data = langextract_data
sys.modules.setdefault("langextract", langextract_stub)
sys.modules.setdefault("langextract.data", langextract_data)

jwt_stub = types.ModuleType("jwt")
jwt_stub.encode = lambda *args, **kwargs: ""
# Provide InvalidTokenError so other tests can assert on it
class _InvalidTokenError(Exception):
    pass

jwt_stub.InvalidTokenError = _InvalidTokenError
sys.modules.setdefault("jwt", jwt_stub)


watchdog_events_stub = types.ModuleType("watchdog.events")
watchdog_events_stub.FileSystemEventHandler = object
sys.modules.setdefault("watchdog.events", watchdog_events_stub)

watchdog_observers_stub = types.ModuleType("watchdog.observers")
watchdog_observers_stub.Observer = type(
    "Observer",
    (),
    {"schedule": lambda *a, **k: None, "start": lambda *a, **k: None},
)
sys.modules.setdefault("watchdog.observers", watchdog_observers_stub)
