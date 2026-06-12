import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def add_backend_to_sys_path():
    backend_dir = Path(__file__).resolve().parents[2]
    backend_dir = str(backend_dir)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)


def test_lookup_message_runtime_hints_falls_back_to_live_stream_metadata(monkeypatch):
    from app import routes

    monkeypatch.setattr(
        routes.conversation_store,
        "load_conversation",
        lambda session_id: [
            {
                "id": "live-msg-1",
                "metadata": {
                    "live_stream": {
                        "mode": "api",
                        "model": "gpt-realtime-2",
                        "provider": "openai-realtime",
                    }
                },
            }
        ],
    )

    assert routes._lookup_message_runtime_hints("sess-live", "live-msg-1") == (
        "gpt-realtime-2",
        "api",
    )
