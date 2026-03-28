import sys
import time
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def add_backend_to_sys_path():
    backend_dir = Path(__file__).resolve().parents[2]
    backend_dir = str(backend_dir)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)


def test_watch_folder_ingests_and_stops(tmp_path):
    from app.services.rag_service import RAGService

    service = RAGService(persist_dir=str(tmp_path / "db"))
    observer = service.watch_folder(str(tmp_path))
    assert observer in service.watchers

    (tmp_path / "note.txt").write_text("hello")

    def has_docs():
        return bool(service.list_docs()["ids"])

    deadline = time.time() + 3
    while time.time() < deadline:
        if has_docs():
            break
        time.sleep(0.1)
    assert has_docs()

    service.stop_watchers()
    assert service.watchers == []
