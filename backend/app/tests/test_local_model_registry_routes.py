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


def test_register_huggingface_model_is_persisted_downloadable_and_resolvable(
    tmp_path: Path, monkeypatch
):
    from app.model_registry import (
        get_model_metadata,
        model_supports_download_job,
        resolve_model_alias,
    )

    client = _make_client(tmp_path, monkeypatch)

    register = client.post(
        "/api/models/registered/huggingface",
        json={
            "url": "https://huggingface.co/acme/example-model/tree/main",
            "alias": "my-hf-model",
            "model_type": "transformer",
            "runtime": "direct",
        },
    )

    assert register.status_code == 200
    entry = register.json()["model"]
    assert entry["alias"] == "my-hf-model"
    assert entry["repo_id"] == "acme/example-model"
    assert entry["source_type"] == "huggingface"

    listed = client.get("/api/models/registered")
    assert listed.status_code == 200
    assert any(item.get("alias") == "my-hf-model" for item in listed.json()["models"])

    downloadable = client.get("/api/models/downloadable")
    assert "my-hf-model" in downloadable.json()["models"]
    assert resolve_model_alias("my-hf-model") == "acme/example-model"
    assert model_supports_download_job("my-hf-model") is True
    assert get_model_metadata("my-hf-model")["lane"] == "direct"
    settings = client.get("/api/user-settings")
    assert settings.status_code == 200
    assert settings.json()["huggingface_model_registrations"][0]["repo_id"] == (
        "acme/example-model"
    )

    removed = client.delete("/api/models/registered/my-hf-model")
    assert removed.status_code == 200
    assert removed.json()["source"] == "huggingface"
    assert resolve_model_alias("my-hf-model") == "my-hf-model"


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com/acme/model",
        "https://huggingface.co/spaces/acme/demo",
        "acme/model/extra",
    ],
)
def test_register_huggingface_model_rejects_non_model_links(
    tmp_path: Path, monkeypatch, value: str
):
    client = _make_client(tmp_path, monkeypatch)

    response = client.post(
        "/api/models/registered/huggingface",
        json={"url": value},
    )

    assert response.status_code == 400


def test_register_huggingface_model_rejects_builtin_alias(tmp_path: Path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)

    response = client.post(
        "/api/models/registered/huggingface",
        json={
            "url": "acme/example-model",
            "alias": "gpt-oss-20b",
        },
    )

    assert response.status_code == 400
    assert "conflicts with a built-in model" in response.json()["detail"]


def test_delete_model_reports_runtime_lock_details(tmp_path: Path, monkeypatch):
    from app import routes
    from app.routers import model_filesystem

    client = _make_client(tmp_path, monkeypatch)
    model_dir = tmp_path / "models_root" / "gpt-oss-20b"
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "config.json").write_text("{}", encoding="utf-8")

    def _raise_permission_error(path):
        raise PermissionError("access denied")

    monkeypatch.setattr(model_filesystem.shutil, "rmtree", _raise_permission_error)
    monkeypatch.setattr(
        routes.llm_service,
        "local_runtime_status",
        lambda: {
            "model": "gpt-oss-20b",
            "effective_model_id": "gpt-oss-20b",
            "loaded": True,
            "load_state": "ready",
        },
    )
    monkeypatch.setattr(
        routes.provider_manager,
        "describe_model_locks",
        lambda model_name, providers=None: [
            {
                "provider": "lmstudio",
                "base_url": "http://127.0.0.1:1234/v1",
                "server_running": True,
                "server_owned_by_float": False,
                "loaded_model": "gpt-oss-20b",
                "loaded_model_owned_by_float": False,
                "owned_model_ids": ["gpt-oss-20b"],
                "mode": "local-managed",
            }
        ],
    )

    deleted = client.delete("/api/models/gpt-oss-20b")

    assert deleted.status_code == 409
    detail = deleted.json().get("detail", {})
    assert "Direct local runtime still has 'gpt-oss-20b' loaded." in detail["message"]
    assert "outside Float" in detail["message"]
    assert "External HTTP only" in detail["message"]
    explanation = detail["state_explanation"]
    assert explanation["title"] == "Why this model cannot be deleted"
    rows = {row["label"]: row["value"] for row in explanation["rows"]}
    assert rows["Source"] == "model delete guard"
    assert rows["Model"] == "gpt-oss-20b"
    assert "Direct local runtime" in rows["Evidence 1"]
    assert "External HTTP only" in rows["Evidence 2"]
