import sys
from pathlib import Path
import yaml

import pytest


@pytest.fixture(autouse=True)
def add_backend_to_sys_path():
    backend_dir = Path(__file__).resolve().parents[2]
    backend_dir = str(backend_dir)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

from config import load_model_catalog


def write_yaml(path: Path, data: dict) -> Path:
    path.write_text(yaml.safe_dump(data))
    return path


def test_select_endpoint_with_defaults_and_fallback(tmp_path: Path, monkeypatch):
    # Minimal catalog mirroring the example structure
    catalog_yaml = {
        "provider": "pipecat",
        "models": {
            "llm": {"server": "http://lmstudio.local:1234/v1", "api": "https://api.llm/v1"},
        },
        "defaults": {
            "chat": {"llm": "local", "fallback": "server"},
        },
        "workflows": {"chat_basic": {"requires": ["llm"]}},
    }

    path = write_yaml(tmp_path / "catalog.yaml", catalog_yaml)
    monkeypatch.setenv("MODEL_CATALOG_PATH", str(path))
    catalog = load_model_catalog()

    # Preferred is local, which doesn't exist; should fall back to server
    backend, endpoint = catalog.select_endpoint("llm", mode="chat")
    assert backend == "server"
    assert endpoint.startswith("http://lmstudio.local")


def test_readiness_aggregate(tmp_path: Path, monkeypatch):
    catalog_yaml = {
        "provider": "pipecat",
        "models": {
            "speech": {"api": "https://api.speech/v1"},
            "llm": {"server": "http://lmstudio.local:1234/v1"},
        },
        "defaults": {"voice": {"stt": "api", "tts": "api", "llm": "server"}},
        "workflows": {"voice_call": {"requires": ["stt", "tts", "llm"]}},
    }

    path = write_yaml(tmp_path / "catalog.yaml", catalog_yaml)
    monkeypatch.setenv("MODEL_CATALOG_PATH", str(path))
    catalog = load_model_catalog()

    status = catalog.readiness("voice_call")
    assert status["ready"] is True
    assert set(status["selected"].keys()) == {"stt", "tts", "llm"}
