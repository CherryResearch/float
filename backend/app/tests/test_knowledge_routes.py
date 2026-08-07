import asyncio
import hashlib
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

    blobs_dir = data_root / "blobs"
    blobs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(routes, "BLOBS_DIR", blobs_dir)
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


def test_reload_memory_manager_from_store_invokes_manager_loader():
    from app import routes

    calls = []

    class DummyMemoryManager:
        def _load_persisted_store(self):
            calls.append("reloaded")

    request = types.SimpleNamespace(
        app=types.SimpleNamespace(
            state=types.SimpleNamespace(memory_manager=DummyMemoryManager())
        )
    )

    routes._reload_memory_manager_from_store(request)

    assert calls == ["reloaded"]


@pytest.mark.parametrize(
    ("filename", "content_type", "expected_method"),
    [
        ("profile.md", "text/markdown", "markdown"),
        ("profile.markdown", "application/octet-stream", "markdown"),
        ("profile-empty.md", "", "markdown"),
        ("profile.txt", "application/octet-stream", "file"),
    ],
)
def test_knowledge_upload_accepts_safe_markdown_and_text_types(
    client, tmp_path, monkeypatch, filename, content_type, expected_method
):
    from app import routes

    calls = []

    class FakeService:
        def ingest_markdown(self, path, metadata):
            calls.append(("markdown", Path(path), dict(metadata)))
            return "markdown-doc"

        def ingest_file(self, path, metadata):
            calls.append(("file", Path(path), dict(metadata)))
            return "text-doc"

    monkeypatch.setattr(routes, "_get_rag_service", lambda: FakeService())

    response = client.post(
        "/knowledge/upload",
        files={
            "file": (
                filename,
                b"# Profile\n\nLocal-first document.",
                content_type,
            )
        },
    )

    assert response.status_code == 200
    assert len(calls) == 1
    method, stored_path, metadata = calls[0]
    assert method == expected_method
    assert stored_path == tmp_path / "data_root" / "files" / "workspace" / filename
    assert stored_path.read_text(encoding="utf-8").startswith("# Profile")
    assert metadata["kind"] == "document"
    assert metadata["type"] == "document"
    assert metadata["relative_path"] == f"workspace/{filename}"


def test_knowledge_upload_rejects_binary_markdown_before_write(
    client, tmp_path, monkeypatch
):
    from app import routes

    monkeypatch.setattr(
        routes,
        "_get_rag_service",
        lambda: pytest.fail("Invalid Markdown must not reach knowledge ingestion"),
    )

    response = client.post(
        "/knowledge/upload",
        files={
            "file": (
                "binary.md",
                b"\xff\xfe\x00\x01",
                "application/octet-stream",
            )
        },
    )

    assert response.status_code == 400
    assert "utf-8" in str(response.json().get("detail", "")).lower()
    target = tmp_path / "data_root" / "files" / "workspace" / "binary.md"
    assert not target.exists()


def test_knowledge_upload_uses_collision_safe_document_names(
    client, tmp_path, monkeypatch
):
    from app import routes

    stored_paths = []

    class FakeService:
        def ingest_markdown(self, path, metadata):
            stored_paths.append(Path(path))
            return str(metadata["source"])

    monkeypatch.setattr(routes, "_get_rag_service", lambda: FakeService())

    for body in (b"first", b"second", b"third"):
        response = client.post(
            "/knowledge/upload",
            files={"file": ("profile.md", body, "text/markdown")},
        )
        assert response.status_code == 200

    assert [path.name for path in stored_paths] == [
        "profile.md",
        "profile-2.md",
        "profile-3.md",
    ]
    assert [path.read_text(encoding="utf-8") for path in stored_paths] == [
        "first",
        "second",
        "third",
    ]
    workspace = tmp_path / "data_root" / "files" / "workspace"
    assert sorted(path.name for path in workspace.iterdir()) == [
        "profile-2.md",
        "profile-3.md",
        "profile.md",
    ]


def test_knowledge_upload_rejects_markdown_mime_for_arbitrary_suffix(
    client, tmp_path, monkeypatch
):
    from app import routes

    monkeypatch.setattr(
        routes,
        "_get_rag_service",
        lambda: pytest.fail("Rejected Markdown must not reach knowledge ingestion"),
    )

    response = client.post(
        "/knowledge/upload",
        files={"file": ("profile.bin", b"plain text", "text/markdown")},
    )

    assert response.status_code == 400
    assert ".md" in str(response.json().get("detail", ""))
    target = tmp_path / "data_root" / "files" / "workspace" / "profile.bin"
    assert not target.exists()


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


def test_knowledge_query_surfaces_canonical_title_and_source_matches(
    client, monkeypatch
):
    from app import routes

    class FakeService:
        def search_canonical(self, query, top_k=5):
            assert query == "tea party story"
            assert top_k == 4
            return [
                {
                    "id": "tea-doc",
                    "text": "Tea party planning notes",
                    "metadata": {
                        "source": "workspace/tea_party_story.md",
                        "title": "tea_party_story",
                    },
                    "score": 0.95,
                }
            ]

        def query(self, query, top_k=5):
            assert query == "tea party story"
            assert top_k == 4
            return [
                {
                    "id": "vector-doc",
                    "text": "General planning notes",
                    "metadata": {"source": "workspace/general.md"},
                    "score": 0.72,
                }
            ]

    monkeypatch.setattr(routes, "_get_rag_service", lambda: FakeService())

    query_resp = client.get(
        "/knowledge/query",
        params={"q": "tea party story", "k": 4, "mode": "text"},
    )
    assert query_resp.status_code == 200
    matches = query_resp.json().get("matches") or []
    assert [match.get("id") for match in matches[:2]] == ["tea-doc", "vector-doc"]


def test_knowledge_query_emits_operation_progress_notifications(client, monkeypatch):
    from app import routes

    asyncio.__float_notifications__ = []  # type: ignore[attr-defined]

    class FakeService:
        def search_canonical(self, query, top_k=5):
            assert query == "tea party story"
            assert top_k == 4
            return [
                {
                    "id": "tea-doc",
                    "text": "Tea party planning notes",
                    "metadata": {
                        "source": "workspace/tea_party_story.md",
                        "title": "tea_party_story",
                    },
                    "score": 0.95,
                }
            ]

        def query(self, query, top_k=5):
            assert query == "tea party story"
            assert top_k == 4
            return [
                {
                    "id": "vector-doc",
                    "text": "General planning notes",
                    "metadata": {"source": "workspace/general.md"},
                    "score": 0.72,
                }
            ]

    monkeypatch.setattr(routes, "_get_rag_service", lambda: FakeService())
    monkeypatch.setattr(routes, "_get_clip_rag_service", lambda **_kwargs: None)

    query_resp = client.get(
        "/knowledge/query",
        params={"q": "tea party story", "k": 4, "mode": "text"},
    )
    assert query_resp.status_code == 200

    notifications_resp = client.get("/notifications/recent")
    assert notifications_resp.status_code == 200
    notifications = notifications_resp.json().get("notifications") or []
    progress_entries = [
        entry
        for entry in notifications
        if entry.get("category") == "operation_progress"
        and entry.get("data", {}).get("kind") == "rag_query"
        and entry.get("title") == "Searching knowledge"
    ]
    assert progress_entries
    statuses = [entry.get("data", {}).get("status") for entry in progress_entries]
    assert "running" in statuses
    assert "complete" in statuses
    final_entry = progress_entries[-1]
    assert final_entry.get("data", {}).get("phase_label") == "RAG query finished"
    assert final_entry.get("data", {}).get("counts", {}).get("returned_matches") == 2
    assert str(final_entry.get("data", {}).get("operation_id") or "").startswith(
        "rag-query:knowledge:"
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
    assert get_saved.json()["caption_model"] == "manual-caption"
    assert get_saved.json()["caption_status"] == "manual"
    assert get_saved.json()["caption_updated_at"]

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


def test_attachment_upload_emits_operation_progress_notifications(client, monkeypatch):
    from app import routes

    asyncio.__float_notifications__ = []  # type: ignore[attr-defined]

    def fake_caption_and_index(*_args, **kwargs):
        progress_callback = kwargs.get("progress_callback")
        if callable(progress_callback):
            progress_callback(
                {
                    "status": "running",
                    "phase_label": "Generating image caption",
                    "phase_index": 2,
                    "phase_count": 4,
                    "detail": "Running the caption step for the uploaded image.",
                }
            )
            progress_callback(
                {
                    "status": "complete",
                    "phase_label": "Attachment indexing finished",
                    "phase_index": 4,
                    "phase_count": 4,
                    "detail": "Caption and index data are ready.",
                    "counts": {"clip_saved": True, "embedding_dim": 512},
                }
            )
        return {
            "id": "caption-doc",
            "caption": "Indexed image",
            "caption_model": "fake-captioner",
            "saved": True,
            "embedding_dim": 512,
            "placeholder": False,
            "clip": {"saved": True, "dim": 512, "error": None},
        }

    monkeypatch.setattr(
        routes, "_caption_and_index_image_bytes", fake_caption_and_index
    )

    resp = client.post(
        "/attachments/upload",
        data={"origin": "upload"},
        files={"file": ("progress.png", b"progress-bytes", "image/png")},
    )
    assert resp.status_code == 200
    content_hash = resp.json()["content_hash"]

    notifications_resp = client.get("/notifications/recent")
    assert notifications_resp.status_code == 200
    notifications = notifications_resp.json().get("notifications") or []
    progress_entries = [
        entry
        for entry in notifications
        if entry.get("category") == "operation_progress"
    ]
    assert progress_entries
    statuses = [entry.get("data", {}).get("status") for entry in progress_entries]
    assert "queued" in statuses
    assert "running" in statuses
    assert "complete" in statuses
    final_entry = progress_entries[-1]
    assert final_entry["title"] == "Indexing image attachment"
    assert final_entry["body"] == "progress.png"
    assert (
        final_entry.get("data", {}).get("operation_id")
        == f"attachment-index:{content_hash}"
    )
    assert final_entry.get("data", {}).get("phase_label") == (
        "Attachment indexing finished"
    )


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
            "caption_generated_at": "2026-04-23T17:20:00Z",
            "index_status": "indexed",
            "indexed_at": "2026-04-23T17:21:00Z",
            "index_warning": "clip-sync-pending",
            "clip_embedding_model": "clip:ViT-B-32",
            "clip_embedding_dim": 512,
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
    assert entry["caption_generated_at"] == "2026-04-23T17:20:00Z"
    assert entry["index_status"] == "indexed"
    assert entry["indexed_at"] == "2026-04-23T17:21:00Z"
    assert entry["index_warning"] == "clip-sync-pending"
    assert entry["embedding_model"] == "clip:ViT-B-32"
    assert entry["embedding_dim"] == 512
    assert entry["placeholder_caption"] is False


def test_attachment_rehydrate_preserves_existing_generated_caption(client, monkeypatch):
    from app import routes

    image_bytes = b"image bytes"
    content_hash = hashlib.sha256(image_bytes).hexdigest()
    target = routes._resolve_data_files_root() / "uploads" / content_hash / "image.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(image_bytes)
    routes._write_attachment_meta(
        content_hash,
        {
            "filename": "image.png",
            "content_type": "image/png",
            "relative_path": f"uploads/{content_hash}/image.png",
            "caption": "A green notebook beside a mug.",
            "caption_model": "local-vision-model",
            "caption_status": "generated",
            "placeholder_caption": False,
        },
    )

    calls = []

    class FakeRagService:
        def ingest_text(self, text, metadata):
            calls.append((text, dict(metadata)))
            return "caption-doc"

    def fail_caption(*_args, **_kwargs):
        raise AssertionError("existing generated caption should be reused")

    monkeypatch.setattr(routes, "_get_rag_service", lambda: FakeRagService())
    monkeypatch.setattr(routes, "_get_clip_rag_service", lambda **_kwargs: None)
    monkeypatch.setattr(routes, "_generate_image_caption", fail_caption)

    resp = client.post(
        "/attachments/rag/rehydrate",
        json={"content_hashes": [content_hash]},
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "scanned": 1,
        "reindexed": 1,
        "captions_generated": 0,
        "captions_unavailable": 0,
        "failed": 0,
        "dry_run": False,
    }
    assert calls == [
        (
            "A green notebook beside a mug.",
            {
                "kind": "image_caption",
                "type": "image_caption",
                "source": f"image:{content_hash}",
                "filename": "image.png",
                "content_type": "image/png",
                "caption_model": "local-vision-model",
                "placeholder": False,
                "content_hash": content_hash,
                "url": f"/api/attachments/{content_hash}/image.png",
                "relative_path": f"uploads/{content_hash}/image.png",
            },
        )
    ]
    meta = routes._read_attachment_meta(content_hash)
    assert meta["caption"] == "A green notebook beside a mug."
    assert meta["caption_model"] == "local-vision-model"
    assert meta["caption_status"] == "generated"
    assert meta["placeholder_caption"] is False


def test_configured_caption_model_ignores_clip_env(client, monkeypatch):
    from app import routes

    monkeypatch.setenv("VISION_CAPTION_MODEL", "clip-vit-base-patch32")
    monkeypatch.setattr(
        routes.app_config,
        "load_config",
        lambda: {"vision_model": "clip-vit-base-patch32"},
    )

    assert routes._configured_vision_caption_model() == "google/paligemma2-3b-pt-224"


def test_attachment_rehydrate_retries_low_quality_generated_caption(
    client, monkeypatch
):
    from app import routes

    image_bytes = b"image bytes"
    content_hash = hashlib.sha256(image_bytes).hexdigest()
    target = routes._resolve_data_files_root() / "uploads" / content_hash / "image.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(image_bytes)
    routes._write_attachment_meta(
        content_hash,
        {
            "filename": "image.png",
            "content_type": "image/png",
            "relative_path": f"uploads/{content_hash}/image.png",
            "caption": "caption\n# , # , # , # , # , # , # , # , # , #",
            "caption_model": "google/paligemma2-3b-pt-224",
            "caption_status": "generated",
            "placeholder_caption": False,
        },
    )

    calls = []

    class FakeRagService:
        def ingest_text(self, text, metadata):
            calls.append((text, dict(metadata)))
            return "caption-doc"

    monkeypatch.setattr(routes, "_get_rag_service", lambda: FakeRagService())
    monkeypatch.setattr(routes, "_get_clip_rag_service", lambda **_kwargs: None)
    monkeypatch.setattr(
        routes,
        "_generate_image_caption",
        lambda *_args, **_kwargs: (
            "striped tiger illustration on a pale background",
            False,
            "google/paligemma2-3b-pt-224",
        ),
    )

    resp = client.post(
        "/attachments/rag/rehydrate",
        json={"content_hashes": [content_hash]},
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "scanned": 1,
        "reindexed": 1,
        "captions_generated": 1,
        "captions_unavailable": 0,
        "failed": 0,
        "dry_run": False,
    }
    assert calls[0][0] == "striped tiger illustration on a pale background"
    meta = routes._read_attachment_meta(content_hash)
    assert meta["caption"] == "striped tiger illustration on a pale background"
    assert meta["caption_status"] == "generated"
    assert meta["placeholder_caption"] is False
    assert meta["index_status"] == "partial"
    assert meta["index_warning"] == "clip_index_unavailable"


def test_attachment_indexing_preserves_manual_caption_written_during_generation(
    client, monkeypatch
):
    from app import routes

    image_bytes = b"image bytes"
    content_hash = hashlib.sha256(image_bytes).hexdigest()
    target = routes._resolve_data_files_root() / "uploads" / content_hash / "image.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(image_bytes)
    routes._write_attachment_meta(
        content_hash,
        {
            "filename": "image.png",
            "content_type": "image/png",
            "relative_path": f"uploads/{content_hash}/image.png",
            "caption_status": "pending",
            "index_status": "indexing",
        },
    )
    calls = []

    class FakeRagService:
        def ingest_text(self, text, metadata):
            calls.append((text, dict(metadata)))
            return "caption-doc"

    def fake_generate(*_args, **_kwargs):
        routes._write_attachment_meta(
            content_hash,
            {
                "filename": "image.png",
                "content_type": "image/png",
                "caption": "manual caption written during background work",
                "caption_model": "manual-caption",
                "caption_status": "manual",
                "placeholder_caption": False,
            },
        )
        return (
            "auto caption should not win",
            False,
            "google/paligemma2-3b-pt-224",
        )

    monkeypatch.setattr(routes, "_get_rag_service", lambda: FakeRagService())
    monkeypatch.setattr(routes, "_get_clip_rag_service", lambda **_kwargs: None)
    monkeypatch.setattr(routes, "_generate_image_caption", fake_generate)

    result = routes._caption_and_index_image_bytes(
        image_bytes,
        filename="image.png",
        content_type="image/png",
        content_hash=content_hash,
    )

    assert result["caption"] == "manual caption written during background work"
    assert result["caption_model"] == "manual-caption"
    assert result["caption_status"] == "manual"
    assert calls[0][0] == "manual caption written during background work"
    assert calls[0][1]["caption_model"] == "manual-caption"
    meta = routes._read_attachment_meta(content_hash)
    assert meta["caption"] == "manual caption written during background work"
    assert meta["caption_model"] == "manual-caption"
    assert meta["caption_status"] == "manual"
    assert meta["index_status"] == "partial"


def test_attachment_rehydrate_limits_to_requested_hashes(client, monkeypatch):
    from app import routes

    indexed = []
    image_payloads = (b"image bytes one", b"image bytes two")
    content_hashes = tuple(
        hashlib.sha256(image_bytes).hexdigest() for image_bytes in image_payloads
    )
    for content_hash, image_bytes in zip(content_hashes, image_payloads):
        target = (
            routes._resolve_data_files_root() / "uploads" / content_hash / "image.png"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(image_bytes)
        routes._write_attachment_meta(
            content_hash,
            {
                "filename": "image.png",
                "content_type": "image/png",
                "relative_path": f"uploads/{content_hash}/image.png",
            },
        )

    def fake_caption_and_index(*_args, **kwargs):
        indexed.append(kwargs["content_hash"])
        return {"id": kwargs["content_hash"]}

    monkeypatch.setattr(
        routes, "_caption_and_index_image_bytes", fake_caption_and_index
    )

    resp = client.post(
        "/attachments/rag/rehydrate",
        json={"content_hashes": [content_hashes[0]]},
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "scanned": 1,
        "reindexed": 1,
        "captions_generated": 0,
        "captions_unavailable": 1,
        "failed": 0,
        "dry_run": False,
    }
    assert indexed == [content_hashes[0]]


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
    content_hash = hashlib.sha256(png_bytes).hexdigest()
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

    blob_bytes = b"legacy blob bytes"
    content_hash = hashlib.sha256(blob_bytes).hexdigest()
    blob_file = routes.BLOBS_DIR / content_hash
    blob_file.parent.mkdir(parents=True, exist_ok=True)
    blob_file.write_bytes(blob_bytes)

    resp = client.get(f"/attachments/{content_hash}/legacy-image.png")
    assert resp.status_code == 200
    assert resp.content == blob_bytes


def test_attachments_reveal_supports_filename_fallback_for_legacy_files(client):
    from app import routes

    image_bytes = b"legacy image bytes"
    content_hash = hashlib.sha256(image_bytes).hexdigest()
    legacy_file = routes._resolve_data_files_root() / "uploads" / "legacy-image.jpg"
    legacy_file.parent.mkdir(parents=True, exist_ok=True)
    legacy_file.write_bytes(image_bytes)

    reveal_resp = client.get(
        f"/attachments/reveal/{content_hash}",
        params={"filename": "legacy-image.jpg"},
    )
    assert reveal_resp.status_code == 200
    payload = reveal_resp.json()
    revealed_path = Path(payload["path"])
    assert revealed_path.as_posix().endswith(
        f"files/uploads/{content_hash}/legacy-image.jpg"
    )
    assert revealed_path.read_bytes() == image_bytes


def test_attachments_reveal_prefers_relative_metadata_target_over_blob(client):
    from app import routes

    preferred_bytes = b"preferred bytes"
    content_hash = hashlib.sha256(preferred_bytes).hexdigest()
    preferred_file = (
        routes._resolve_data_files_root()
        / "workspace"
        / "gallery"
        / "preferred-image.jpg"
    )
    preferred_file.parent.mkdir(parents=True, exist_ok=True)
    preferred_file.write_bytes(preferred_bytes)

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
    revealed_path = Path(payload["path"])
    assert revealed_path.as_posix().endswith(
        f"files/uploads/{content_hash}/preferred-image.jpg"
    )
    assert revealed_path.read_bytes() == preferred_bytes


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
