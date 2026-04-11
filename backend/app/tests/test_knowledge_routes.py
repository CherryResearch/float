import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _install_celery_stub() -> None:
    if "celery" in sys.modules and "celery.result" in sys.modules:
        return

    celery_module = types.ModuleType("celery")
    celery_result_module = types.ModuleType("celery.result")

    class _Signal:
        def connect(self, func=None, **_kwargs):
            if func is None:

                def decorator(callback):
                    return callback

                return decorator
            return func

    class _Signals:
        task_prerun = _Signal()
        task_postrun = _Signal()
        task_failure = _Signal()

    class _Celery:
        def __init__(self, *_args, **_kwargs):
            self.conf = types.SimpleNamespace(beat_schedule={})

        def task(self, func=None, **_kwargs):
            if func is None:

                def decorator(callback):
                    return callback

                return decorator
            return func

    class _AsyncResult:
        def __init__(self, task_id=None, *_args, **_kwargs):
            self.id = task_id
            self.status = "PENDING"
            self.state = "PENDING"
            self.result = None
            self.info = None

    def _chain(*_tasks, **_kwargs):
        return types.SimpleNamespace(apply_async=lambda: _AsyncResult("stub-chain"))

    celery_module.Celery = _Celery
    celery_module.signals = _Signals()
    celery_module.chain = _chain
    celery_result_module.AsyncResult = _AsyncResult
    sys.modules["celery"] = celery_module
    sys.modules["celery.result"] = celery_result_module


@pytest.fixture
def client(tmp_path, monkeypatch):
    backend_dir = Path(__file__).resolve().parents[2]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    _install_celery_stub()

    data_root = tmp_path / "data_root"
    monkeypatch.setenv("FLOAT_DATA_DIR", str(data_root))
    monkeypatch.setenv(
        "FLOAT_MEMORY_FILE",
        str(data_root / "databases" / "memory.sqlite3"),
    )

    from app import routes
    from app.main import app
    from app.services import rag_provider

    monkeypatch.setattr(rag_provider, "_rag_service", None, raising=False)
    monkeypatch.setattr(rag_provider, "_rag_signature", None, raising=False)
    monkeypatch.setattr(rag_provider, "_clip_rag_service", None, raising=False)
    monkeypatch.setattr(rag_provider, "_clip_rag_signature", None, raising=False)
    monkeypatch.setattr(rag_provider, "_config_snapshot", None, raising=False)

    monkeypatch.setattr(routes.subprocess, "Popen", lambda *_args, **_kwargs: None)
    return TestClient(app)


def test_knowledge_rag_rehydrate_reindexes_canonical_docs(client, monkeypatch):
    from app import routes

    calls = []

    class FakeStore:
        def list_items(self):
            return {
                "ids": ["doc-1"],
                "documents": ["synced text"],
                "metadatas": [{"source": "workspace/doc-1.md", "kind": "document"}],
            }

    class FakeService:
        canonical_store = FakeStore()

        def rehydrate_canonical_document(self, text, metadata, *, knowledge_id=None):
            calls.append((text, dict(metadata), knowledge_id))
            return True

    monkeypatch.setattr(routes, "_get_rag_service", lambda: FakeService())

    res = client.post("/knowledge/rag/rehydrate", json={})

    assert res.status_code == 200
    assert res.json() == {"scanned": 1, "reindexed": 1}
    assert calls == [
        (
            "synced text",
            {"source": "workspace/doc-1.md", "kind": "document"},
            "doc-1",
        )
    ]


def test_knowledge_reveal_local_file_under_data_files(client, tmp_path):
    data_root = tmp_path / "data_root"
    local_doc = data_root / "files" / "downloaded" / "notes.txt"
    local_doc.parent.mkdir(parents=True, exist_ok=True)
    local_doc.write_text("hello", encoding="utf-8")

    add_resp = client.post("/knowledge/add", json={"path": str(local_doc)})
    assert add_resp.status_code == 200
    doc_id = add_resp.json()["id"]

    reveal_resp = client.get(f"/knowledge/reveal/{doc_id}")
    assert reveal_resp.status_code == 200
    payload = reveal_resp.json()
    assert Path(payload["path"]).as_posix().endswith("files/downloaded/notes.txt")


def test_knowledge_reveal_rejects_source_outside_data_files(client, tmp_path):
    outside_doc = tmp_path / "outside.txt"
    outside_doc.write_text("outside", encoding="utf-8")

    add_resp = client.post("/knowledge/add", json={"path": str(outside_doc)})
    assert add_resp.status_code == 400
    assert "data/files" in str(add_resp.json().get("detail", ""))


def test_knowledge_reveal_rejects_non_local_source(client):
    add_resp = client.post(
        "/knowledge/text",
        json={
            "text": "remote source",
            "metadata": {"source": "https://example.com/doc"},
        },
    )
    assert add_resp.status_code == 200
    doc_id = add_resp.json()["id"]

    reveal_resp = client.get(f"/knowledge/reveal/{doc_id}")
    assert reveal_resp.status_code == 400
    assert "local file path" in str(reveal_resp.json().get("detail", "")).lower()


def test_knowledge_file_serves_local_file_under_data_files(client, tmp_path):
    data_root = tmp_path / "data_root"
    local_doc = data_root / "files" / "workspace" / "served.txt"
    local_doc.parent.mkdir(parents=True, exist_ok=True)
    local_doc.write_text("served by file endpoint", encoding="utf-8")

    add_resp = client.post("/knowledge/add", json={"path": str(local_doc)})
    assert add_resp.status_code == 200
    doc_id = add_resp.json()["id"]

    file_resp = client.get(f"/knowledge/file/{doc_id}")
    assert file_resp.status_code == 200
    assert file_resp.content == b"served by file endpoint"
    assert "text/plain" in str(file_resp.headers.get("content-type", "")).lower()


def test_knowledge_update_rewrites_local_workspace_text_file(client, tmp_path):
    data_root = tmp_path / "data_root"
    local_doc = data_root / "files" / "workspace" / "editable.txt"
    local_doc.parent.mkdir(parents=True, exist_ok=True)
    local_doc.write_text("before", encoding="utf-8")

    add_resp = client.post("/knowledge/add", json={"path": str(local_doc)})
    assert add_resp.status_code == 200
    doc_id = add_resp.json()["id"]

    update_resp = client.put(
        f"/knowledge/{doc_id}",
        json={"text": "after"},
    )
    assert update_resp.status_code == 200
    assert update_resp.json() == {"status": "updated"}
    assert local_doc.read_text(encoding="utf-8") == "after"

    fetch_resp = client.get(f"/knowledge/{doc_id}")
    assert fetch_resp.status_code == 200
    payload = (fetch_resp.json().get("metadatas") or [{}])[0]
    assert payload.get("source_last_saved_at")


def test_knowledge_file_rejects_non_local_source(client):
    add_resp = client.post(
        "/knowledge/text",
        json={
            "text": "remote source",
            "metadata": {"source": "https://example.com/doc"},
        },
    )
    assert add_resp.status_code == 200
    doc_id = add_resp.json()["id"]

    file_resp = client.get(f"/knowledge/file/{doc_id}")
    assert file_resp.status_code == 400
    assert "local file path" in str(file_resp.json().get("detail", "")).lower()


def test_knowledge_ingest_folder_uses_relative_metadata_source(client, tmp_path):
    data_root = tmp_path / "data_root"
    local_doc = data_root / "files" / "workspace" / "nested" / "notes.txt"
    local_doc.parent.mkdir(parents=True, exist_ok=True)
    local_doc.write_text("hello", encoding="utf-8")

    ingest_resp = client.post(
        "/knowledge/ingest-folder",
        json={"path": "workspace", "recursive": True},
    )
    assert ingest_resp.status_code == 200
    assert ingest_resp.json().get("count") == 1

    docs_resp = client.get("/knowledge/list")
    assert docs_resp.status_code == 200
    metadatas = docs_resp.json().get("metadatas") or []
    assert any(
        isinstance(meta, dict)
        and meta.get("relative_path") == "workspace/nested/notes.txt"
        and meta.get("source") == "workspace/nested/notes.txt"
        for meta in metadatas
    )


def test_knowledge_api_masks_external_absolute_source(client):
    add_resp = client.post(
        "/knowledge/text",
        json={
            "text": "hello from absolute path",
            "metadata": {"source": r"C:\\outside\\notes.txt"},
        },
    )
    assert add_resp.status_code == 200

    docs_resp = client.get("/knowledge/list")
    assert docs_resp.status_code == 200
    metadatas = docs_resp.json().get("metadatas") or []
    assert any(
        isinstance(meta, dict) and meta.get("source") == "[external-path]"
        for meta in metadatas
    )

    query_resp = client.get("/knowledge/query", params={"q": "absolute path", "k": 3})
    assert query_resp.status_code == 200
    matches = query_resp.json().get("matches") or []
    assert any(
        isinstance(match, dict)
        and (
            match.get("source") == "[external-path]"
            or (
                isinstance(match.get("metadata"), dict)
                and match["metadata"].get("source") == "[external-path]"
            )
        )
        for match in matches
    )


def test_knowledge_cleanup_dry_run_then_apply_external_exclusion(client):
    add_resp = client.post(
        "/knowledge/text",
        json={
            "text": "external source row",
            "metadata": {"source": r"C:\\outside\\notes.txt"},
        },
    )
    assert add_resp.status_code == 200
    doc_id = add_resp.json()["id"]

    dry_run_resp = client.post("/knowledge/cleanup", json={"dry_run": True})
    assert dry_run_resp.status_code == 200
    assert dry_run_resp.json().get("updated", 0) >= 1

    before_resp = client.get(f"/knowledge/{doc_id}")
    assert before_resp.status_code == 200
    before_meta = (before_resp.json().get("metadatas") or [{}])[0]
    assert before_meta.get("rag_excluded") is not True

    apply_resp = client.post("/knowledge/cleanup", json={"dry_run": False})
    assert apply_resp.status_code == 200
    assert apply_resp.json().get("excluded_external", 0) >= 1

    after_resp = client.get(f"/knowledge/{doc_id}")
    assert after_resp.status_code == 200
    after_meta = (after_resp.json().get("metadatas") or [{}])[0]
    assert after_meta.get("rag_excluded") is True


def test_knowledge_cleanup_normalizes_relative_source(client, tmp_path):
    data_root = tmp_path / "data_root"
    local_doc = data_root / "files" / "workspace" / "cleanup" / "doc.txt"
    local_doc.parent.mkdir(parents=True, exist_ok=True)
    local_doc.write_text("hello", encoding="utf-8")

    add_resp = client.post(
        "/knowledge/text",
        json={
            "text": "absolute path metadata",
            "metadata": {"source": str(local_doc), "kind": "document"},
        },
    )
    assert add_resp.status_code == 200
    doc_id = add_resp.json()["id"]

    cleanup_resp = client.post("/knowledge/cleanup", json={"dry_run": False})
    assert cleanup_resp.status_code == 200
    assert cleanup_resp.json().get("normalized", 0) >= 1

    doc_resp = client.get(f"/knowledge/{doc_id}")
    assert doc_resp.status_code == 200
    meta = (doc_resp.json().get("metadatas") or [{}])[0]
    assert meta.get("source") == "workspace/cleanup/doc.txt"
    assert meta.get("relative_path") == "workspace/cleanup/doc.txt"


def test_attachment_caption_crud(client, monkeypatch):
    from app import routes

    monkeypatch.setattr(
        routes, "_index_uploaded_attachment", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        routes, "_reindex_attachment_caption", lambda *args, **kwargs: None
    )

    upload_resp = client.post(
        "/attachments/upload",
        files={"file": ("sample.png", b"not-an-image-but-ok", "image/png")},
    )
    assert upload_resp.status_code == 200
    content_hash = upload_resp.json()["content_hash"]

    get_initial = client.get(f"/attachments/caption/{content_hash}")
    assert get_initial.status_code == 200
    assert get_initial.json()["exists"] is False

    put_resp = client.put(
        f"/attachments/caption/{content_hash}",
        json={"caption": "A generated sample caption."},
    )
    assert put_resp.status_code == 200
    assert put_resp.json()["caption"] == "A generated sample caption."

    get_saved = client.get(f"/attachments/caption/{content_hash}")
    assert get_saved.status_code == 200
    assert get_saved.json()["exists"] is True
    assert get_saved.json()["caption"] == "A generated sample caption."

    delete_resp = client.delete(f"/attachments/caption/{content_hash}")
    assert delete_resp.status_code == 200
    assert delete_resp.json()["deleted"] is True

    get_deleted = client.get(f"/attachments/caption/{content_hash}")
    assert get_deleted.status_code == 200
    assert get_deleted.json()["exists"] is False


def test_attachment_upload_writes_to_uploads_folder_and_returns_origin_metadata(
    client, monkeypatch
):
    from app import routes

    monkeypatch.setattr(
        routes, "_index_uploaded_attachment", lambda *args, **kwargs: None
    )

    resp = client.post(
        "/attachments/upload",
        data={"origin": "upload"},
        files={"file": ("sample.png", b"upload-bytes", "image/png")},
    )
    assert resp.status_code == 200
    payload = resp.json()

    content_hash = payload["content_hash"]
    expected_rel = f"uploads/{content_hash}/sample.png"
    expected_path = (
        routes._resolve_data_files_root() / "uploads" / content_hash / "sample.png"
    )
    assert expected_path.exists()
    assert expected_path.read_bytes() == b"upload-bytes"
    assert payload["origin"] == "upload"
    assert payload["relative_path"] == expected_rel

    meta = routes._read_attachment_meta(content_hash)
    assert meta.get("origin") == "upload"
    assert meta.get("relative_path") == expected_rel
    assert meta.get("filename") == "sample.png"
    assert meta.get("caption_status") == "pending"
    assert meta.get("index_status") == "indexing"


def test_attachment_upload_supports_captured_origin_and_list_status_fields(
    client, monkeypatch
):
    from app import routes

    monkeypatch.setattr(
        routes, "_index_uploaded_attachment", lambda *args, **kwargs: None
    )

    resp = client.post(
        "/attachments/upload",
        data={"origin": "captured", "capture_source": "chat_camera"},
        files={"file": ("camera.png", b"camera-bytes", "image/png")},
    )
    assert resp.status_code == 200
    payload = resp.json()

    content_hash = payload["content_hash"]
    expected_rel = f"captured/{content_hash}/camera.png"
    expected_path = (
        routes._resolve_data_files_root() / "captured" / content_hash / "camera.png"
    )
    assert expected_path.exists()
    assert payload["origin"] == "captured"
    assert payload["relative_path"] == expected_rel

    list_resp = client.get("/attachments")
    assert list_resp.status_code == 200
    entry = next(
        item
        for item in list_resp.json()["attachments"]
        if item["content_hash"] == content_hash
    )
    assert entry["origin"] == "captured"
    assert entry["relative_path"] == expected_rel
    assert entry["capture_source"] == "chat_camera"
    assert entry["caption"] == ""
    assert entry["caption_model"] == ""
    assert entry["caption_status"] == "pending"
    assert entry["index_status"] == "indexing"
    assert entry["index_warning"] == ""
    assert entry["placeholder_caption"] is False


def test_attachments_list_returns_caption_fields_from_metadata(client, monkeypatch):
    from app import routes

    monkeypatch.setattr(
        routes, "_index_uploaded_attachment", lambda *args, **kwargs: None
    )

    resp = client.post(
        "/attachments/upload",
        data={"origin": "upload"},
        files={"file": ("captioned.png", b"captioned-bytes", "image/png")},
    )
    assert resp.status_code == 200
    content_hash = resp.json()["content_hash"]
    routes._write_attachment_meta(
        content_hash,
        {
            **routes._read_attachment_meta(content_hash),
            "caption": "A small orange dog on a stair landing.",
            "caption_model": "local-caption-model",
            "caption_status": "generated",
            "index_status": "indexed",
            "index_warning": "clip-sync-pending",
        },
    )

    list_resp = client.get("/attachments")
    assert list_resp.status_code == 200
    entry = next(
        item
        for item in list_resp.json()["attachments"]
        if item["content_hash"] == content_hash
    )
    assert entry["caption"] == "A small orange dog on a stair landing."
    assert entry["caption_model"] == "local-caption-model"
    assert entry["caption_status"] == "generated"
    assert entry["index_status"] == "indexed"
    assert entry["index_warning"] == "clip-sync-pending"
    assert entry["placeholder_caption"] is False


def test_attachments_list_recovers_media_type_and_filename_for_hash_only_uploads(
    client,
):
    from app import routes

    png_bytes = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
        b"\x90wS\xde"
    )
    content_hash = "8afdae4fdbe1177c7e1cd7dc71134ac1f219bf92b666296104bea4f5c1ab07ee"
    target = routes._resolve_data_files_root() / "uploads" / content_hash / content_hash
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(png_bytes)
    routes._write_attachment_meta(
        content_hash,
        {
            "filename": content_hash,
            "origin": "upload",
            "relative_path": f"uploads/{content_hash}/{content_hash}",
        },
    )

    list_resp = client.get("/attachments")
    assert list_resp.status_code == 200
    entry = next(
        item
        for item in list_resp.json()["attachments"]
        if item["content_hash"] == content_hash
    )
    assert entry["content_type"] == "image/png"
    assert entry["filename"].endswith(".png")

    get_resp = client.get(entry["url"])
    assert get_resp.status_code == 200
    assert get_resp.headers["content-type"].startswith("image/png")


def test_attachment_download_still_resolves_legacy_blob_storage(client):
    from app import routes

    content_hash = "legacyvisionblob"
    blob_file = routes.BLOBS_DIR / content_hash
    blob_file.parent.mkdir(parents=True, exist_ok=True)
    blob_file.write_bytes(b"legacy blob bytes")

    resp = client.get(f"/attachments/{content_hash}/legacy-image.png")
    assert resp.status_code == 200
    assert resp.content == b"legacy blob bytes"


def test_attachments_reveal_supports_filename_fallback_for_legacy_files(client):
    from app import routes

    legacy_file = routes._resolve_data_files_root() / "uploads" / "legacy-image.jpg"
    legacy_file.parent.mkdir(parents=True, exist_ok=True)
    legacy_file.write_bytes(b"legacy image bytes")

    reveal_resp = client.get(
        "/attachments/reveal/missinghash",
        params={"filename": "legacy-image.jpg"},
    )
    assert reveal_resp.status_code == 200
    payload = reveal_resp.json()
    assert Path(payload["path"]).as_posix().endswith("files/uploads/legacy-image.jpg")


def test_attachments_reveal_prefers_relative_metadata_target_over_blob(client):
    from app import routes

    content_hash = "relpathhash"
    preferred_file = (
        routes._resolve_data_files_root()
        / "workspace"
        / "gallery"
        / "preferred-image.jpg"
    )
    preferred_file.parent.mkdir(parents=True, exist_ok=True)
    preferred_file.write_bytes(b"preferred bytes")

    blob_file = routes.BLOBS_DIR / content_hash
    blob_file.parent.mkdir(parents=True, exist_ok=True)
    blob_file.write_bytes(b"blob bytes")

    routes._write_attachment_meta(
        content_hash,
        {
            "filename": "preferred-image.jpg",
            "relative_path": "workspace/gallery/preferred-image.jpg",
        },
    )

    reveal_resp = client.get(
        f"/attachments/reveal/{content_hash}",
        params={"filename": "preferred-image.jpg"},
    )
    assert reveal_resp.status_code == 200
    payload = reveal_resp.json()
    assert (
        Path(payload["path"])
        .as_posix()
        .endswith("files/workspace/gallery/preferred-image.jpg")
    )


def test_rag_status_avoids_loading_embedding_models(client, monkeypatch):
    from app import routes

    class DummyStore:
        def __init__(self, *_args, **_kwargs):
            pass

        def list_items(self):
            return {"ids": ["doc-1", "doc-2"]}

    def fail_get_rag_service():
        raise AssertionError("rag service should not be initialized for rag/status")

    monkeypatch.setattr(routes, "KnowledgeStore", DummyStore)
    monkeypatch.setattr(routes, "_get_rag_service", fail_get_rag_service)
    monkeypatch.setattr(
        routes,
        "_get_aux_model_status",
        lambda _cfg: {
            "text_embeddings": {
                "model": "local:dummy",
                "mode": "sentence_transformer",
                "state": "idle",
                "loaded": False,
                "init_attempted": False,
                "error": None,
                "service_initialized": False,
            },
            "clip_embeddings": {
                "model": "clip:ViT-B-32",
                "mode": "clip",
                "state": "idle",
                "loaded": False,
                "init_attempted": False,
                "error": None,
                "service_initialized": False,
            },
        },
    )

    resp = client.get("/rag/status")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["documents"] == 2
    assert payload["aux_models"]["text_embeddings"]["state"] == "idle"
    assert payload["aux_models"]["clip_embeddings"]["state"] == "idle"


def test_rag_status_reports_weaviate_connection_settings(client, monkeypatch):
    from app import routes

    async def fake_celery_status():
        return {
            "online": False,
            "workers": [],
            "timeout": False,
            "details": {},
        }

    monkeypatch.setattr(
        routes,
        "_get_aux_model_status",
        lambda _cfg: {
            "text_embeddings": {"state": "idle"},
            "clip_embeddings": {"state": "idle"},
        },
    )
    monkeypatch.setattr(
        routes.app_config,
        "load_config",
        lambda: {
            "rag_backend": "weaviate",
            "rag_embedding_model": "simple",
            "weaviate_url": "http://127.0.0.1:8080",
            "weaviate_grpc_host": "127.0.0.1",
            "weaviate_grpc_port": 50051,
            "auto_start_weaviate": True,
        },
    )
    monkeypatch.setattr(
        routes,
        "celery_status",
        fake_celery_status,
    )

    resp = client.get("/rag/status")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["backend"] == "weaviate"
    assert payload["url"] == "http://127.0.0.1:8080"
    assert payload["grpc_host"] == "127.0.0.1"
    assert payload["grpc_port"] == 50051
    assert payload["auto_start"] is True


def test_knowledge_list_does_not_eager_load_embedding_models(client, monkeypatch):
    from app.services import rag_provider, rag_service

    def fail_init_encoder(self, model_name):
        raise AssertionError(
            "embedding encoder should not initialize for knowledge/list"
        )

    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "local:dummy")
    monkeypatch.setattr(rag_provider, "_rag_service", None, raising=False)
    monkeypatch.setattr(rag_provider, "_rag_signature", None, raising=False)
    monkeypatch.setattr(rag_provider, "_config_snapshot", None, raising=False)
    monkeypatch.setattr(
        rag_service.RAGService,
        "_init_embedding_encoder",
        fail_init_encoder,
    )

    resp = client.get("/knowledge/list")

    assert resp.status_code == 200
    assert isinstance(resp.json().get("ids"), list)
