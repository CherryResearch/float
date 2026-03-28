import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def add_backend_to_sys_path():
    backend_dir = Path(__file__).resolve().parents[2]
    backend_dir = str(backend_dir)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)


def _make_dummy_model(dir_path: Path, name: str):
    target = dir_path / name
    target.mkdir(parents=True, exist_ok=True)
    # Touch a fake weights file so the scanner finds it
    weight_path = target / "snapshots" / "0000" / "model.safetensors"
    weight_path.parent.mkdir(parents=True, exist_ok=True)
    weight_path.write_text("stub")
    return target


def _make_app(monkeypatch, search_dirs):
    from app import config as app_config
    from app import routes

    # Force model_search_dirs to use our temporary paths
    monkeypatch.setattr(app_config, "model_search_dirs", lambda custom_path=None: search_dirs)

    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    app.state.config = {
        "models_folder": str(search_dirs[0]),
    }
    return TestClient(app)


def test_transformer_models_filters_hf_cache_noise(tmp_path: Path, monkeypatch):
    repo_dir = tmp_path / "models_repo"
    hf_cache = tmp_path / ".cache" / "huggingface" / "hub"
    repo_dir.mkdir()
    hf_cache.mkdir(parents=True, exist_ok=True)

    # Repo/local model should always appear
    _make_dummy_model(repo_dir, "gpt-oss-20b")
    # HF cache entries: one allowed, one noise
    _make_dummy_model(hf_cache, "models--meta-llama--Llama-3.1-8B")
    _make_dummy_model(hf_cache, "models--sentence-transformers--all-MiniLM-L6-v2")

    client = _make_app(monkeypatch, [repo_dir, hf_cache])

    resp = client.get("/api/transformers/models")
    assert resp.status_code == 200
    models = resp.json().get("models", [])

    assert "gpt-oss-20b" in models
    assert "Llama-3.1-8B" in models  # allowed HF cache entry
    assert "all-MiniLM-L6-v2" not in models  # filtered HF noise by default


def test_transformer_models_can_include_cache_noise_when_requested(tmp_path: Path, monkeypatch):
    hf_cache = tmp_path / ".cache" / "huggingface" / "hub"
    hf_cache.mkdir(parents=True, exist_ok=True)
    _make_dummy_model(hf_cache, "models--sentence-transformers--all-MiniLM-L6-v2")

    client = _make_app(monkeypatch, [hf_cache])

    resp = client.get("/api/transformers/models", params={"include_cache_unfiltered": True})
    assert resp.status_code == 200
    models = resp.json().get("models", [])
    assert "all-MiniLM-L6-v2" in models
