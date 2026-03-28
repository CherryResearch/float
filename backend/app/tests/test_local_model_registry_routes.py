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


def _make_client(tmp_path: Path, monkeypatch) -> TestClient:
    from app import config as app_config
    from app import routes
    from app.utils import user_settings

    user_settings_path = tmp_path / "user_settings.json"
    monkeypatch.setattr(user_settings, "USER_SETTINGS_PATH", user_settings_path)

    models_root = tmp_path / "models_root"
    models_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        app_config, "model_search_dirs", lambda custom_path=None: [models_root]
    )

    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    app.state.config = {"models_folder": str(models_root)}
    return TestClient(app)


def test_register_local_model_is_listed_and_resolvable(tmp_path: Path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    external_model_dir = tmp_path / "external_models" / "my_local_model"
    external_model_dir.mkdir(parents=True, exist_ok=True)
    (external_model_dir / "model.safetensors").write_text("stub", encoding="utf-8")

    register = client.post(
        "/api/models/registered",
        json={
            "alias": "local-alias",
            "path": str(external_model_dir),
            "model_type": "transformer",
        },
    )
    assert register.status_code == 200
    entry = register.json().get("model") or {}
    assert entry.get("alias") == "local-alias"
    assert entry.get("exists") is True
    assert entry.get("model_type") == "transformer"

    listed = client.get("/api/models/registered")
    assert listed.status_code == 200
    models = listed.json().get("models", [])
    assert any(item.get("alias") == "local-alias" for item in models)

    transformers = client.get("/api/transformers/models")
    assert transformers.status_code == 200
    assert "local-alias" in transformers.json().get("models", [])

    exists = client.get("/api/models/exists/local-alias")
    assert exists.status_code == 200
    assert exists.json().get("exists") is True

    removed = client.delete("/api/models/registered/local-alias")
    assert removed.status_code == 200
    assert removed.json().get("status") == "deleted"

    exists_after = client.get("/api/models/exists/local-alias")
    assert exists_after.status_code == 200
    assert exists_after.json().get("exists") is False


def test_delete_model_unregistration_does_not_delete_external_path(
    tmp_path: Path, monkeypatch
):
    client = _make_client(tmp_path, monkeypatch)
    external_model_dir = tmp_path / "outside" / "safe_model"
    external_model_dir.mkdir(parents=True, exist_ok=True)
    (external_model_dir / "weights.bin").write_text("stub", encoding="utf-8")

    register = client.post(
        "/api/models/registered",
        json={"alias": "safe-alias", "path": str(external_model_dir)},
    )
    assert register.status_code == 200

    deleted = client.delete("/api/models/safe-alias")
    assert deleted.status_code == 200
    assert deleted.json().get("status") == "unregistered"
    assert external_model_dir.exists()


def test_register_local_model_rejects_missing_path(tmp_path: Path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)

    register = client.post(
        "/api/models/registered",
        json={
            "alias": "missing-path",
            "path": str(tmp_path / "does-not-exist"),
            "model_type": "transformer",
        },
    )
    assert register.status_code == 400
    assert "path does not exist" in str(register.json().get("detail", ""))
