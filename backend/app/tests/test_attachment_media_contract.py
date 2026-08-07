import hashlib
import io
import json
import sys
import threading
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app import routes  # noqa: E402
from app.base_services import _resolve_attachment_bytes  # noqa: E402
from app.main import app  # noqa: E402
from app.services import clip_embeddings  # noqa: E402
from app.utils import blob_store  # noqa: E402
from workers import multimodal as multimodal_workers  # noqa: E402

_INDEX_UPLOADED_ATTACHMENT_IMPL = routes._index_uploaded_attachment


@pytest.fixture
def media_client(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    blobs_root = tmp_path / "blobs"
    blobs_root.mkdir(parents=True)
    monkeypatch.setenv("FLOAT_DATA_DIR", str(data_root))
    monkeypatch.setattr(routes, "BLOBS_DIR", blobs_root)
    monkeypatch.setattr(blob_store, "BLOBS_DIR", blobs_root)
    config = {
        **dict(app.state.config),
        "data_dir": str(data_root),
        "image_caption_engine": "off",
        "image_caption_cloud_model": "gpt-5.4-nano",
    }
    monkeypatch.setattr(app.state, "config", config)
    monkeypatch.setattr(routes.llm_service, "config", config)
    multimodal_workers.reset_shared_vision_captioner()
    monkeypatch.setattr(
        routes,
        "_index_uploaded_attachment",
        lambda *_args, **_kwargs: None,
    )
    return TestClient(app)


def _real_image_bytes() -> bytes:
    image_path = BACKEND_ROOT.parent / "docs" / "resources" / "floatlogo.png"
    image_bytes = image_path.read_bytes()
    with Image.open(io.BytesIO(image_bytes)) as image:
        image.load()
        assert image.width >= 32
        assert image.height >= 32
        assert any(low != high for low, high in image.convert("RGB").getextrema())
    return image_bytes


def _upload_image(
    client: TestClient,
    *,
    source_url: str | None = None,
) -> dict:
    image_bytes = _real_image_bytes()
    data = {"origin": "upload"}
    if source_url is not None:
        data["source_url"] = source_url
    response = client.post(
        "/attachments/upload",
        data=data,
        files={"file": ("source.png", image_bytes, "image/png")},
    )
    assert response.status_code == 200, response.json()
    return response.json()


def test_capture_promote_tool_uses_shared_caption_and_index_lifecycle(
    media_client,
    tmp_path,
    monkeypatch,
):
    from app.tools import computer_tools

    image_bytes = _real_image_bytes()
    capture_path = tmp_path / "floatlogo.png"
    capture_path.write_bytes(image_bytes)

    class FakeCaptureService:
        def __init__(self):
            self.capture = {
                "capture_id": "capture-real-image",
                "filename": "floatlogo.png",
                "content_type": "image/png",
                "capture_source": "computer",
                "sensitivity": "personal",
            }

        def get_capture(self, capture_id):
            return dict(self.capture) if capture_id == "capture-real-image" else None

        def capture_path(self, capture_id):
            return capture_path if capture_id == "capture-real-image" else None

        def mark_promoted(self, capture_id, *, attachment_ref, memory_refs=None):
            assert capture_id == "capture-real-image"
            self.capture["promoted"] = True
            self.capture["attachment_ref"] = dict(attachment_ref)
            self.capture["memory_refs"] = list(memory_refs or [])
            return dict(self.capture)

    service = FakeCaptureService()
    queued = []
    local_config = {**dict(app.state.config), "image_caption_engine": "local"}
    monkeypatch.setattr(app.state, "config", local_config)
    monkeypatch.setattr(computer_tools, "get_capture_service", lambda: service)
    monkeypatch.setattr(computer_tools, "verify_signature", lambda *_args: None)
    monkeypatch.setattr(
        computer_tools,
        "_queue_capture_attachment_index",
        lambda request: queued.append(request) or True,
    )

    expected_hash = hashlib.sha256(image_bytes).hexdigest()
    corrupt_target = (
        blob_store._resolve_data_files_root()
        / "captured"
        / expected_hash
        / "floatlogo.png"
    )
    corrupt_target.parent.mkdir(parents=True, exist_ok=True)
    corrupt_target.write_bytes(b"corrupt prior capture")

    result = computer_tools.capture_promote(
        "capture-real-image",
        user="tester",
        signature="signature",
    )

    content_hash = result["attachment"]["content_hash"]
    assert content_hash == expected_hash
    assert corrupt_target.read_bytes() == image_bytes
    assert result["attachment"]["relative_path"].startswith("captured/")
    assert len(queued) == 1
    assert queued[0].data == image_bytes
    assert queued[0].content_hash == content_hash
    metadata = routes._read_attachment_meta(content_hash)
    assert metadata["caption_status"] == "pending"
    assert metadata["index_status"] == "indexing"

    # A fresh generation is deduplicated across repeated promotion calls.
    computer_tools.capture_promote(
        "capture-real-image",
        user="tester",
        signature="signature",
    )
    assert len(queued) == 1

    # A legacy/stalled promotion remains retryable instead of returning early.
    def _mark_legacy_stall(current):
        current["index_status"] = "not_applicable"
        current["caption_status"] = "pending"
        current.pop("index_generation", None)
        current.pop("index_generation_started_at", None)
        return current

    routes._mutate_attachment_meta(content_hash, _mark_legacy_stall)
    computer_tools.capture_promote(
        "capture-real-image",
        user="tester",
        signature="signature",
    )
    assert len(queued) == 2

    routes._mutate_attachment_meta(
        content_hash,
        lambda current: {
            **current,
            "caption": "The Float logo.",
            "caption_status": "generated",
            "index_status": "indexed",
        },
    )
    computer_tools.capture_promote(
        "capture-real-image",
        user="tester",
        signature="signature",
    )
    assert len(queued) == 2


def test_delayed_attachment_index_cannot_resurrect_deleted_media(
    media_client,
    monkeypatch,
):
    image_bytes = _real_image_bytes()
    uploaded = _upload_image(media_client)
    content_hash = uploaded["content_hash"]
    metadata = routes._read_attachment_meta(content_hash)
    generation = metadata["index_generation"]
    locked_calls = []
    monkeypatch.setattr(
        routes,
        "_caption_and_index_image_bytes_locked",
        lambda *_args, **_kwargs: locked_calls.append(True),
    )
    monkeypatch.setattr(
        routes,
        "_forget_attachment_knowledge",
        lambda *_args, **_kwargs: None,
    )

    deleted = media_client.delete(f"/attachments/{content_hash}")
    assert deleted.status_code == 200
    assert routes._read_attachment_meta(content_hash) == {}

    _INDEX_UPLOADED_ATTACHMENT_IMPL(
        app,
        image_bytes,
        filename="source.png",
        content_type="image/png",
        url=uploaded["url"],
        content_hash=content_hash,
        started_at=str(metadata.get("uploaded_at") or ""),
        index_generation=generation,
    )

    assert locked_calls == []
    assert routes._read_attachment_meta(content_hash) == {}
    assert blob_store.exists(content_hash) is False


def test_superseded_attachment_index_generation_is_skipped(
    media_client,
    monkeypatch,
):
    image_bytes = _real_image_bytes()
    uploaded = _upload_image(media_client)
    content_hash = uploaded["content_hash"]
    metadata = routes._read_attachment_meta(content_hash)
    stale_generation = metadata["index_generation"]
    current_generation = str(uuid4())
    routes._mutate_attachment_meta(
        content_hash,
        lambda current: {
            **current,
            "index_generation": current_generation,
            "index_status": "indexing",
        },
    )
    locked_calls = []
    monkeypatch.setattr(
        routes,
        "_caption_and_index_image_bytes_locked",
        lambda *_args, **_kwargs: locked_calls.append(True),
    )

    _INDEX_UPLOADED_ATTACHMENT_IMPL(
        app,
        image_bytes,
        filename="source.png",
        content_type="image/png",
        url=uploaded["url"],
        content_hash=content_hash,
        started_at=str(metadata.get("uploaded_at") or ""),
        index_generation=stale_generation,
    )

    refreshed = routes._read_attachment_meta(content_hash)
    assert locked_calls == []
    assert refreshed["index_generation"] == current_generation
    assert refreshed["index_status"] == "indexing"


def test_attachment_contract_distinguishes_storage_route_and_web_provenance(
    media_client,
):
    source_url = "https://images.example.test/gallery/source.png?version=2"
    uploaded = _upload_image(media_client, source_url=source_url)

    assert uploaded["content_hash"]
    assert uploaded["relative_path"].startswith("uploads/")
    assert uploaded["url"].startswith("/api/attachments/")
    assert uploaded["url"] != uploaded["relative_path"]
    assert uploaded["source_url"] == source_url
    assert uploaded["source_url_recorded_at"]

    response = media_client.patch(
        f"/attachments/{uploaded['content_hash']}/metadata",
        json={
            "display_name": "Ravine owl",
            "folder": "Friends/Owls",
            "source_url": "https://example.test/owl",
        },
    )
    assert response.status_code == 200
    attachment = response.json()["attachment"]
    assert attachment["display_name"] == "Ravine owl"
    assert attachment["folder"] == "Friends/Owls"
    assert attachment["source_url"] == "https://example.test/owl"
    assert attachment["filename"] == "source.png"
    assert attachment["content_hash"] == uploaded["content_hash"]
    assert attachment["relative_path"] == uploaded["relative_path"]

    listed = media_client.get("/attachments").json()["attachments"]
    listed_attachment = next(
        item for item in listed if item["content_hash"] == uploaded["content_hash"]
    )
    assert listed_attachment == attachment


@pytest.mark.parametrize(
    "endpoint",
    ["/knowledge/caption-image", "/knowledge/caption-image-preview"],
)
def test_caption_upload_endpoints_reject_svg_and_oversized_files(
    media_client,
    monkeypatch,
    endpoint,
):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    rejected_svg = media_client.post(
        endpoint,
        files={"file": ("active.svg", svg, "image/svg+xml")},
    )
    assert rejected_svg.status_code == 400
    assert rejected_svg.json()["detail"] == "Unsupported image type"

    monkeypatch.setattr(routes, "MAX_UPLOAD_SIZE", 16)
    oversized = media_client.post(
        endpoint,
        files={"file": ("large.png", b"x" * 17, "image/png")},
    )
    assert oversized.status_code == 400
    assert oversized.json()["detail"] == "File too large"


def test_active_attachment_content_is_forced_to_download(media_client):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    asset = blob_store.put_asset(svg, filename="active.svg", origin="upload")
    content_hash = asset["content_hash"]
    routes._write_attachment_meta(
        content_hash,
        {
            **asset,
            "content_type": "image/svg+xml",
            "size": len(svg),
        },
    )

    response = media_client.get(f"/attachments/{content_hash}/active.svg")

    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "sandbox; default-src 'none'"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.content == svg


@pytest.mark.parametrize("caption_status", ["manual", "generated"])
def test_deduplicated_upload_preserves_user_and_sync_metadata(
    media_client,
    caption_status,
):
    uploaded = _upload_image(
        media_client,
        source_url="https://example.test/original",
    )
    content_hash = uploaded["content_hash"]
    metadata = routes._read_attachment_meta(content_hash)
    metadata.update(
        {
            "caption": "A preserved caption.",
            "caption_model": (
                "manual-caption" if caption_status == "manual" else "caption-model"
            ),
            "caption_status": caption_status,
            "placeholder_caption": False,
            "display_name": "Keep this label",
            "folder": "Keep/This",
            "source_sync_namespace": "laptop",
            "source_sync_label": "Laptop",
            "source_sync_original_relative_path": "uploads/original.png",
        }
    )
    routes._write_attachment_meta(content_hash, metadata)

    repeated = media_client.post(
        "/attachments/upload",
        data={"source_url": "https://example.test/new-source"},
        files={"file": ("renamed.png", _real_image_bytes(), "image/png")},
    )

    assert repeated.status_code == 200
    saved = routes._read_attachment_meta(content_hash)
    assert saved["caption"] == "A preserved caption."
    assert saved["caption_status"] == caption_status
    assert saved["display_name"] == "Keep this label"
    assert saved["folder"] == "Keep/This"
    assert saved["source_url"] == "https://example.test/original"
    assert saved["source_sync_namespace"] == "laptop"
    assert saved["source_sync_label"] == "Laptop"
    assert saved["source_sync_original_relative_path"] == "uploads/original.png"


@pytest.mark.parametrize(
    "content_hash",
    ["a" * 63, "A" * 64, ("a" * 64) + "x", "not-a-hash"],
)
def test_attachment_api_rejects_noncanonical_content_hashes(
    media_client,
    content_hash,
):
    metadata = media_client.get(f"/attachments/{content_hash}/metadata")
    caption = media_client.get(f"/attachments/caption/{content_hash}")
    delete = media_client.delete(f"/attachments/{content_hash}")

    assert metadata.status_code == 400
    assert caption.status_code == 400
    assert delete.status_code == 400


def test_attachment_resolver_never_binds_wrong_bytes_to_requested_hash(media_client):
    uploaded = _upload_image(media_client)
    wrong_hash = hashlib.sha256(b"different attachment identity").hexdigest()
    uploaded_meta = routes._read_attachment_meta(uploaded["content_hash"])
    routes._write_attachment_meta(
        wrong_hash,
        {
            "filename": uploaded_meta["filename"],
            "relative_path": uploaded_meta["relative_path"],
            "path": uploaded_meta["path"],
        },
    )

    assert (
        routes._resolve_attachment_target(
            wrong_hash,
            filename=uploaded_meta["filename"],
        )
        is None
    )

    expected_bytes = b"expected legacy attachment bytes"
    expected_hash = hashlib.sha256(expected_bytes).hexdigest()
    legacy_target = blob_store._resolve_data_files_root() / "uploads" / "legacy.png"
    legacy_target.write_bytes(b"wrong")
    assert (
        routes._resolve_attachment_target(expected_hash, filename="legacy.png") is None
    )

    legacy_target.write_bytes(expected_bytes)
    resolved = routes._resolve_attachment_target(
        expected_hash,
        filename="legacy.png",
    )
    assert resolved is not None
    assert resolved.parent.name == expected_hash
    assert resolved.read_bytes() == expected_bytes


def test_verified_legacy_flat_image_is_canonicalized_for_chat_delivery(media_client):
    image_bytes = _real_image_bytes()
    content_hash = hashlib.sha256(image_bytes).hexdigest()
    files_root = blob_store._resolve_data_files_root()
    legacy_target = files_root / "uploads" / "legacy-logo.png"
    legacy_target.write_bytes(image_bytes)
    routes._write_attachment_meta(
        content_hash,
        {
            "filename": legacy_target.name,
            "content_type": "image/png",
        },
    )

    descriptor = routes._attachment_public_descriptor(content_hash)
    assert descriptor is not None
    canonical_target = routes._resolve_attachment_target(
        content_hash,
        filename=legacy_target.name,
    )
    assert canonical_target is not None
    assert canonical_target.parent.name == content_hash

    enriched = routes._enrich_attachment_reference(
        {"content_hash": content_hash, "type": "image/png"}
    )
    assert enriched["_canonical_attachment_resolved"] is True
    raw, reason = _resolve_attachment_bytes(enriched)
    assert raw == image_bytes
    assert reason == "blob"


def test_metadata_patch_sets_sync_visible_mutation_timestamp(media_client):
    uploaded = _upload_image(media_client)
    before = routes._read_attachment_meta(uploaded["content_hash"])

    response = media_client.patch(
        f"/attachments/{uploaded['content_hash']}/metadata",
        json={"display_name": "Updated label"},
    )

    assert response.status_code == 200
    after = routes._read_attachment_meta(uploaded["content_hash"])
    assert after["metadata_updated_at"]
    assert after["metadata_updated_at"] >= before["metadata_updated_at"]


def test_metadata_patch_does_not_report_success_when_atomic_write_fails(
    media_client,
    monkeypatch,
):
    uploaded = _upload_image(media_client)
    content_hash = uploaded["content_hash"]

    def fail_write(*_args, **_kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(routes, "mutate_attachment_metadata", fail_write)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.patch(
        f"/attachments/{content_hash}/metadata",
        json={"display_name": "Must not claim success"},
    )

    assert response.status_code == 500
    assert routes._read_attachment_meta(content_hash).get("display_name") is None


def test_attachment_cleanup_removes_root_and_namespaced_rag_sources(
    media_client,
    monkeypatch,
):
    uploaded = _upload_image(media_client)
    content_hash = uploaded["content_hash"]
    metadata = routes._read_attachment_meta(content_hash)
    metadata["source_sync_namespace"] = "laptop"
    routes._write_attachment_meta(content_hash, metadata)
    text_deleted = []
    clip_deleted = []

    class Service:
        def __init__(self, deleted):
            self.deleted = deleted

        def delete_source(self, source):
            self.deleted.append(source)

    monkeypatch.setattr(routes, "_get_rag_service", lambda: Service(text_deleted))
    monkeypatch.setattr(
        routes,
        "_get_clip_rag_service",
        lambda *, raise_http=True: Service(clip_deleted),
    )

    routes._forget_attachment_knowledge(content_hash)

    expected = {f"image:{content_hash}", f"laptop/image:{content_hash}"}
    assert set(text_deleted) == expected
    assert set(clip_deleted) == expected


def test_attachment_create_undo_uses_canonical_media_cleanup(
    media_client,
    tmp_path,
    monkeypatch,
):
    from app.services.action_history_service import ActionHistoryService

    uploaded = _upload_image(media_client)
    content_hash = uploaded["content_hash"]
    text_deleted = []
    clip_deleted = []

    class Service:
        def __init__(self, deleted):
            self.deleted = deleted

        def delete_source(self, source):
            self.deleted.append(source)

    monkeypatch.setattr(routes, "_get_rag_service", lambda: Service(text_deleted))
    monkeypatch.setattr(
        routes,
        "_get_clip_rag_service",
        lambda *, raise_http=True: Service(clip_deleted),
    )
    service = ActionHistoryService({"data_dir": str(tmp_path / "history")})

    service._apply_item_snapshot(
        {
            "section": "attachments",
            "resource_type": "attachment",
            "resource_id": content_hash,
        },
        None,
    )

    assert routes._read_attachment_meta(content_hash) == {}
    assert blob_store.exists(content_hash) is False
    expected_source = f"image:{content_hash}"
    assert text_deleted == [expected_source]
    assert clip_deleted == [expected_source]


def test_attachment_delete_preserves_metadata_and_knowledge_when_blob_delete_fails(
    media_client,
    monkeypatch,
):
    uploaded = _upload_image(media_client)
    content_hash = uploaded["content_hash"]
    knowledge_calls = []
    monkeypatch.setattr(routes, "blob_delete", lambda _content_hash: False)
    monkeypatch.setattr(
        routes,
        "_forget_attachment_knowledge",
        lambda value: knowledge_calls.append(value),
    )

    response = media_client.delete(f"/attachments/{content_hash}")

    assert response.status_code == 500
    assert response.json()["detail"] == {
        "status": "failed",
        "message": "Failed to delete attachment content",
        "content_hash": content_hash,
        "metadata_preserved": True,
        "knowledge_preserved": True,
    }
    assert knowledge_calls == []
    assert routes._read_attachment_meta(content_hash)["filename"] == "source.png"


def test_attachment_delete_reports_partial_state_when_metadata_cleanup_fails(
    media_client,
    monkeypatch,
):
    uploaded = _upload_image(media_client)
    content_hash = uploaded["content_hash"]
    monkeypatch.setattr(routes, "blob_delete", lambda _content_hash: True)
    monkeypatch.setattr(
        routes,
        "_forget_attachment_knowledge",
        lambda _value, **_kwargs: None,
    )
    monkeypatch.setattr(
        routes,
        "delete_attachment_metadata",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk unavailable")),
    )

    response = media_client.delete(f"/attachments/{content_hash}")

    assert response.status_code == 500
    assert response.json()["detail"] == {
        "status": "partial",
        "message": "Attachment content was deleted, but cleanup was incomplete",
        "content_hash": content_hash,
        "content_deleted": True,
        "cleanup_failed": ["metadata"],
    }


def test_attachment_delete_reports_partial_state_when_knowledge_cleanup_fails(
    media_client,
    monkeypatch,
):
    uploaded = _upload_image(media_client)
    content_hash = uploaded["content_hash"]

    attempts = {"count": 0}

    class RetriableKnowledge:
        def delete_source(self, _source):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise RuntimeError("index unavailable")

    monkeypatch.setattr(routes, "_get_rag_service", lambda: RetriableKnowledge())
    monkeypatch.setattr(
        routes,
        "_get_clip_rag_service",
        lambda *, raise_http=True: None,
    )

    response = media_client.delete(f"/attachments/{content_hash}")

    assert response.status_code == 500
    assert response.json()["detail"] == {
        "status": "partial",
        "message": "Attachment content was deleted, but cleanup was incomplete",
        "content_hash": content_hash,
        "content_deleted": True,
        "metadata_preserved": True,
        "cleanup_failed": ["knowledge"],
    }
    assert routes._read_attachment_meta(content_hash)["filename"] == "source.png"
    pending = media_client.get("/attachments").json()["attachments"]
    pending_attachment = next(
        item for item in pending if item["content_hash"] == content_hash
    )
    assert pending_attachment["content_available"] is False
    assert pending_attachment["deletion_status"] == "cleanup_pending"
    assert pending_attachment["cleanup_failed"] == ["knowledge"]
    assert pending_attachment["url"] == ""

    retried = media_client.delete(f"/attachments/{content_hash}")

    assert retried.status_code == 200
    assert retried.json() == {"status": "deleted", "content_hash": content_hash}
    assert routes._read_attachment_meta(content_hash) == {}
    assert all(
        item["content_hash"] != content_hash
        for item in media_client.get("/attachments").json()["attachments"]
    )


def test_attachment_delete_keeps_retry_receipt_when_clip_mirror_is_unavailable(
    media_client,
    monkeypatch,
):
    uploaded = _upload_image(media_client)
    content_hash = uploaded["content_hash"]
    routes._mutate_attachment_meta(
        content_hash,
        lambda current: {
            **current,
            "clip_embedding_model": "clip:ViT-B-32",
            "clip_embedding_dim": 512,
            "clip_indexed_at": "2026-07-30T00:00:00Z",
        },
    )
    clip_available = {"value": False}
    clip_deleted = []

    class Service:
        def __init__(self, deleted=None):
            self.deleted = deleted

        def delete_source(self, source):
            if self.deleted is not None:
                self.deleted.append(source)

    monkeypatch.setattr(routes, "_get_rag_service", lambda: Service())
    monkeypatch.setattr(
        routes,
        "_get_clip_rag_service",
        lambda *, raise_http=True: (
            Service(clip_deleted) if clip_available["value"] else None
        ),
    )

    first = media_client.delete(f"/attachments/{content_hash}")

    assert first.status_code == 500
    assert first.json()["detail"]["status"] == "partial"
    assert first.json()["detail"]["cleanup_failed"] == ["knowledge"]
    assert blob_store.exists(content_hash) is False
    pending = routes._read_attachment_meta(content_hash)
    assert pending["deletion_status"] == "cleanup_pending"
    assert pending["clip_indexed_at"] == "2026-07-30T00:00:00Z"

    clip_available["value"] = True
    retried = media_client.delete(f"/attachments/{content_hash}")

    assert retried.status_code == 200
    assert clip_deleted == [f"image:{content_hash}"]
    assert routes._read_attachment_meta(content_hash) == {}


def test_attachment_delete_does_not_wait_for_clip_when_index_write_never_succeeded(
    media_client,
    monkeypatch,
):
    uploaded = _upload_image(media_client)
    content_hash = uploaded["content_hash"]
    routes._mutate_attachment_meta(
        content_hash,
        lambda current: {
            **current,
            "index_status": "partial",
            "index_warning": "clip_index_unavailable",
            "clip_embedding_model": "clip:ViT-B-32",
            "clip_embedding_dim": 512,
        },
    )

    class TextService:
        def delete_source(self, _source):
            return None

    monkeypatch.setattr(routes, "_get_rag_service", lambda: TextService())
    monkeypatch.setattr(
        routes,
        "_get_clip_rag_service",
        lambda *, raise_http=True: None,
    )

    response = media_client.delete(f"/attachments/{content_hash}")

    assert response.status_code == 200
    assert response.json() == {"status": "deleted", "content_hash": content_hash}
    assert routes._read_attachment_meta(content_hash) == {}
    assert blob_store.exists(content_hash) is False


@pytest.mark.parametrize(
    "source_url",
    [
        "file:///tmp/image.png",
        "https://user:secret@example.test/image.png",
        "https://example.test/" + ("a" * 2050),
        "https://example.test/image.png?access_token=secret",
        "https://example.test/image.png?X-Amz-Signature=secret",
        "https://example.test/image.png?X-Goog-Credential=secret",
        "https://example.test/image.png?GoogleAccessId=secret",
        "https://example.test/image.png?AWSAccessKeyId=secret",
        "https://example.test/image.png?Policy=secret",
        "https://example.test/image.png?SIG=secret",
        "https://example.test/image.png?client-secret=secret",
        "https://example.test/image.png?x_api_key=secret",
        "https://example.test/image.png?password=secret",
        "https://example.test/image.png#access_token=secret",
        "https://example.test/image.png#jwtAssertion=secret",
        "https://example.test/image.png#/callback?api_key%3Dsecret",
    ],
)
def test_attachment_source_url_rejects_unsafe_passive_metadata(
    media_client,
    source_url,
):
    response = media_client.post(
        "/attachments/upload",
        data={"source_url": source_url},
        files={"file": ("source.png", b"image-bytes", "image/png")},
    )
    assert response.status_code == 400


def test_attachment_metadata_patch_rejects_immutable_fields(media_client):
    uploaded = _upload_image(media_client)
    response = media_client.patch(
        f"/attachments/{uploaded['content_hash']}/metadata",
        json={
            "filename": "renamed-on-disk.png",
            "relative_path": "somewhere/else.png",
        },
    )
    assert response.status_code == 422


def test_saved_attachment_reference_is_enriched_from_canonical_metadata(
    media_client,
):
    uploaded = _upload_image(media_client)
    stored = routes._read_attachment_meta(uploaded["content_hash"])
    stored.update(
        {
            "capture_id": "capture-canonical-1",
            "capture_source": "chat_camera",
        }
    )
    routes._write_attachment_meta(uploaded["content_hash"], stored)
    media_client.patch(
        f"/attachments/{uploaded['content_hash']}/metadata",
        json={
            "display_name": "Canonical label",
            "folder": "Archive/Photos",
            "source_url": "https://example.test/original",
        },
    )

    enriched = routes._enrich_attachment_reference(
        {
            "content_hash": uploaded["content_hash"],
            "filename": "stale-name.png",
            "url": "/api/attachments/stale/stale-name.png",
            "source_url": "https://stale.example.test/image",
            "type": "image/png",
        }
    )

    assert enriched["filename"] == "source.png"
    assert enriched["url"] == uploaded["url"]
    assert enriched["relative_path"] == uploaded["relative_path"]
    assert enriched["display_name"] == "Canonical label"
    assert enriched["folder"] == "Archive/Photos"
    assert enriched["source_url"] == "https://example.test/original"
    assert enriched["source_url_recorded_at"]
    assert enriched["capture_id"] == "capture-canonical-1"
    assert enriched["capture_source"] == "chat_camera"
    assert enriched["_canonical_attachment_resolved"] is True

    uppercase_hash = uploaded["content_hash"].upper()
    uppercase_reference = routes._enrich_attachment_reference(
        {
            "name": "source.png",
            "type": "image/png",
            "content_hash": uppercase_hash,
            "url": f"/api/attachments/{uppercase_hash}/source.png",
            "_canonical_attachment_resolved": True,
        }
    )
    assert "content_hash" not in uppercase_reference
    assert "url" not in uppercase_reference
    assert "_canonical_attachment_resolved" not in uppercase_reference


def test_unresolved_chat_attachment_drops_client_canonical_and_secret_fields(
    media_client,
):
    missing_hash = "f" * 64

    enriched = routes._enrich_attachment_reference(
        {
            "name": "../camera.png",
            "type": "image/png",
            "size": 42,
            "content_hash": missing_hash,
            "url": f"/api/attachments/{missing_hash}/camera.png",
            "relative_path": "../../outside/camera.png",
            "path": "C:/private/camera.png",
            "origin": "downloaded",
            "source_url": "https://example.test/image.png?token=secret",
            "source_url_recorded_at": "2026-07-29T00:00:00Z",
            "display_name": "Unverified label",
            "folder": "Unverified/folder",
            "_canonical_attachment_resolved": True,
            "attacker_field": "must not survive",
        }
    )

    assert enriched == {
        "name": "camera.png",
        "type": "image/png",
        "content_type": "image/png",
        "size": 42,
    }

    uppercase = routes._enrich_attachment_reference(
        {
            "name": "uppercase.png",
            "type": "image/png",
            "content_hash": missing_hash.upper(),
            "url": f"/api/attachments/{missing_hash.upper()}/uppercase.png",
            "_canonical_attachment_resolved": True,
        }
    )
    assert uppercase == {
        "name": "uppercase.png",
        "type": "image/png",
        "content_type": "image/png",
    }


def test_direct_chat_never_forwards_unresolved_attachment_provenance(
    media_client,
    monkeypatch,
    tmp_path,
):
    missing_hash = "7" * 64
    captured = {}
    message_id = f"attachment-turn-{uuid4().hex}"
    conversation_root = tmp_path / "conversations"
    conversation_root.mkdir()
    monkeypatch.setattr(routes.conversation_store, "CONV_DIR", conversation_root)

    def fake_generate(
        _prompt,
        *,
        attachments=None,
        **_kwargs,
    ):
        captured["attachments"] = attachments
        return {"text": "ok", "thought": "", "tools_used": [], "metadata": {}}

    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)
    app.state.pending_tools = {}

    response = media_client.post(
        "/chat",
        json={
            "message": "describe the image",
            "session_id": "unresolved-attachment-provenance-test",
            "message_id": message_id,
            "use_rag": False,
            "attachments": [
                {
                    "name": "camera.png",
                    "type": "image/png",
                    "size": 42,
                    "content_hash": missing_hash,
                    "url": f"/api/attachments/{missing_hash}/camera.png",
                    "relative_path": "../../outside/camera.png",
                    "origin": "downloaded",
                    "source_url": "https://example.test/image.png?token=secret",
                    "source_url_recorded_at": "2026-07-29T00:00:00Z",
                }
            ],
        },
    )

    assert response.status_code == 200, response.json()
    assert captured["attachments"] == [
        {
            "name": "camera.png",
            "type": "image/png",
            "content_type": "image/png",
            "size": 42,
        }
    ]


def test_caption_status_reports_missing_local_weights_honestly(
    media_client,
    monkeypatch,
):
    monkeypatch.setattr(
        routes,
        "_local_caption_model_weights_available",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        routes.importlib.util,
        "find_spec",
        lambda _name: object(),
    )
    app.state.config["image_caption_engine"] = "local"

    response = media_client.get("/attachments/caption/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["engine"] == "local"
    assert payload["ready"] is False
    assert payload["automatic_downloads"] is False
    assert payload["local"]["dependencies_available"] is True
    assert payload["local"]["weights_available"] is False
    assert payload["local"]["loadable"] is False
    assert payload["local"]["reason"] == "model_weights_unavailable"


def test_local_caption_install_requires_complete_snapshot(tmp_path, monkeypatch):
    model_root = tmp_path / "models"
    snapshot = (
        model_root / "models--google--paligemma2-3b-pt-224" / "snapshots" / "revision"
    )
    snapshot.mkdir(parents=True)
    config = {"models_folder": str(model_root)}
    model = "google/paligemma2-3b-pt-224"
    monkeypatch.setattr(
        routes.app_config,
        "model_search_dirs",
        lambda _configured=None: [model_root],
    )

    assert routes._local_caption_model_weights_available(model, config) is False
    (snapshot / "config.json").write_text("{}", encoding="utf-8")
    assert routes._local_caption_model_weights_available(model, config) is False
    (snapshot / "preprocessor_config.json").write_text("{}", encoding="utf-8")
    assert routes._local_caption_model_weights_available(model, config) is False
    (snapshot / "tokenizer_config.json").write_text("{}", encoding="utf-8")
    (snapshot / "tokenizer.model").write_bytes(b"tokenizer")
    assert routes._local_caption_model_weights_available(model, config) is False
    (snapshot / "model.safetensors.index.json").write_text(
        json.dumps(
            {
                "weight_map": {
                    "layer.0": "model-00001-of-00002.safetensors",
                    "layer.1": "model-00002-of-00002.safetensors",
                }
            }
        ),
        encoding="utf-8",
    )
    (snapshot / "model-00001-of-00002.safetensors").write_bytes(b"weights")
    assert routes._local_caption_model_weights_available(model, config) is False
    (snapshot / "model-00002-of-00002.safetensors").write_bytes(b"weights")
    assert routes._local_caption_model_weights_available(model, config) is True
    assert routes._installed_caption_model_path(model, config) == snapshot


def test_local_caption_status_distinguishes_installed_from_loaded(monkeypatch):
    monkeypatch.setattr(
        routes,
        "_local_caption_model_weights_available",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(routes.importlib.util, "find_spec", lambda _name: object())

    class UnloadedCaptioner:
        _loaded = False
        _proc = None
        _net = None
        _verified = False

    monkeypatch.setattr(
        routes,
        "_shared_local_captioner",
        lambda *_args, **_kwargs: UnloadedCaptioner(),
    )

    payload = routes._image_caption_status(
        {
            "image_caption_engine": "local",
            "vision_model": "google/paligemma2-3b-pt-224",
        }
    )

    assert payload["ready"] is False
    assert payload["can_generate"] is True
    assert payload["can_attempt"] is True
    assert payload["local"]["installed"] is True
    assert payload["local"]["configured"] is True
    assert payload["local"]["loaded"] is False
    assert payload["local"]["verified"] is False
    assert payload["local"]["loadable"] is False
    assert payload["local"]["reason"] == "model_installed_not_loaded"

    class LoadedCaptioner:
        _loaded = True
        _proc = object()
        _net = object()
        _verified = False

    monkeypatch.setattr(
        routes,
        "_shared_local_captioner",
        lambda *_args, **_kwargs: LoadedCaptioner(),
    )
    loaded_payload = routes._image_caption_status(
        {
            "image_caption_engine": "local",
            "vision_model": "google/paligemma2-3b-pt-224",
        }
    )
    assert loaded_payload["ready"] is True
    assert loaded_payload["local"]["loaded"] is True
    assert loaded_payload["local"]["loadable"] is True
    assert loaded_payload["local"]["verified"] is False


def test_local_captioner_is_reused_and_swapped_when_model_changes(monkeypatch):
    created = []

    class DummyCaptioner:
        def __init__(self, model=None):
            self.model = model
            created.append(model)

        def run(self, data):
            return f"{self.model}:{data.decode('ascii')}"

    monkeypatch.setattr(multimodal_workers, "VisionCaptioner", DummyCaptioner)
    multimodal_workers.reset_shared_vision_captioner()

    assert routes._run_local_captioner("model-a", b"one") == "model-a:one"
    assert routes._run_local_captioner("model-a", b"two") == "model-a:two"
    assert routes._run_local_captioner("model-b", b"three") == "model-b:three"
    assert created == ["model-a", "model-b"]
    multimodal_workers.reset_shared_vision_captioner()


def test_cloud_caption_default_matches_supported_model_lifecycle():
    assert routes._configured_cloud_caption_model({}) == "gpt-5.4-nano"


def test_caption_engine_only_sends_pixels_after_explicit_cloud_opt_in(monkeypatch):
    calls = []

    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": "A small brown owl."}
                        ],
                    }
                ]
            }

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return DummyResponse()

    class DummyCaptioner:
        model = "local-caption-model"

        def __init__(self, model=None):
            self.model = model or self.model

        def run(self, _data):
            return {"image_caption": "A local caption.", "placeholder": False}

    monkeypatch.setattr(routes.http_session, "post", fake_post)
    dummy_captioner = DummyCaptioner("local-caption-model")
    monkeypatch.setattr(
        routes,
        "run_shared_vision_captioner",
        lambda data, **_kwargs: (dummy_captioner, dummy_captioner.run(data)),
    )

    local_config = {
        "image_caption_engine": "local",
        "vision_model": "local-caption-model",
    }
    monkeypatch.setattr(routes.llm_service, "config", local_config)
    local_caption = routes._generate_image_caption(
        b"pixels",
        content_type="image/png",
    )
    assert local_caption[0] == "A local caption."
    assert calls == []

    off_config = {"image_caption_engine": "off"}
    monkeypatch.setattr(routes.llm_service, "config", off_config)
    assert routes._generate_image_caption(b"pixels") == ("", False, "disabled")
    assert calls == []

    cloud_config = {
        "image_caption_engine": "cloud",
        "image_caption_cloud_model": "gpt-5.4-nano",
        "api_key": "server-secret",
        "api_url": "https://api.openai.com/v1/chat/completions",
        "timeout": 20,
    }
    monkeypatch.setattr(routes.llm_service, "config", cloud_config)
    cloud_caption = routes._generate_image_caption(
        b"pixels",
        content_type="image/png",
    )

    assert cloud_caption == (
        "A small brown owl.",
        False,
        "cloud:gpt-5.4-nano",
    )
    assert len(calls) == 1
    url, request = calls[0]
    assert url == "https://api.openai.com/v1/responses"
    assert request["headers"]["Authorization"] == "Bearer server-secret"
    image_part = request["json"]["input"][0]["content"][1]
    assert image_part["type"] == "input_image"
    assert image_part["image_url"].startswith("data:image/png;base64,")


def test_caption_engine_off_still_indexes_clip_without_fake_caption(
    tmp_path,
    monkeypatch,
):
    blobs_root = tmp_path / "blobs"
    blobs_root.mkdir()
    monkeypatch.setenv("FLOAT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(routes, "BLOBS_DIR", blobs_root)
    monkeypatch.setattr(blob_store, "BLOBS_DIR", blobs_root)
    monkeypatch.setattr(
        routes.llm_service,
        "config",
        {"image_caption_engine": "off"},
    )

    class TextService:
        def __init__(self):
            self.deleted = []

        def delete_source(self, source):
            self.deleted.append(source)

        def ingest_text(self, *_args, **_kwargs):
            raise AssertionError("off mode must not create a caption text record")

    class ClipService:
        embedding_model = "clip:ViT-B-32"

        def __init__(self):
            self.ingested = []

        def ingest_text(self, text, metadata):
            self.ingested.append((text, metadata))
            return "clip-doc"

    text_service = TextService()
    clip_service = ClipService()
    monkeypatch.setattr(routes, "_get_rag_service", lambda: text_service)
    monkeypatch.setattr(
        routes,
        "_get_clip_rag_service",
        lambda *, raise_http=True: clip_service,
    )
    monkeypatch.setattr(
        clip_embeddings,
        "embed_clip_image_bytes",
        lambda *_args, **_kwargs: [0.1, 0.2, 0.3],
    )

    image_bytes = _real_image_bytes()
    asset = blob_store.put_asset(image_bytes, filename="off.png", origin="upload")
    content_hash = asset["content_hash"]
    result = routes._caption_and_index_image_bytes(
        image_bytes,
        filename="off.png",
        content_type="image/png",
        content_hash=content_hash,
    )

    metadata = routes._read_attachment_meta(content_hash)
    assert result["caption"] == ""
    assert result["caption_status"] == "disabled"
    assert metadata["caption_status"] == "disabled"
    assert metadata["placeholder_caption"] is False
    assert "caption" not in metadata
    assert text_service.deleted == [f"image:{content_hash}"]
    assert clip_service.ingested[0][0] == "off.png"
    assert clip_service.ingested[0][1]["caption_available"] is False
    assert clip_service.ingested[0][1]["embedding_model"] == "clip:ViT-B-32"
    assert metadata["index_status"] == "indexed"
    assert metadata["clip_embedding_dim"] == 3
    assert metadata["clip_indexed_at"]


def test_captionless_image_is_recalled_through_clip_by_default(
    media_client,
    monkeypatch,
):
    uploaded = _upload_image(media_client)
    content_hash = uploaded["content_hash"]
    clip_query_top_k = []
    captured = {}

    class TextService:
        embedding_model = "local:test"

        def query(self, *_args, **_kwargs):
            raise AssertionError("text retrieval is disabled for this request")

        def trace(self, _doc_id):
            return None

    class ClipService:
        embedding_model = "clip:ViT-B-32"
        _embedding_encoder = None

        def query(self, _text, top_k=3):
            # A real RAGService lazily initializes its CLIP text encoder here.
            assert self._embedding_encoder is None
            self._embedding_encoder = object()
            clip_query_top_k.append(top_k)
            return [
                {
                    "id": "clip-doc",
                    "text": "source.png",
                    "metadata": {
                        "source": f"image:{content_hash}",
                        "content_hash": content_hash,
                        "content_type": "image/png",
                        "caption_available": False,
                    },
                    "score": 0.98,
                }
            ]

    def fake_generate(
        prompt,
        session_id="default",
        model=None,
        attachments=None,
        context=None,
        **kwargs,
    ):
        captured["context"] = context
        return {
            "text": "I found the saved logo image.",
            "thought": "",
            "tools_used": [],
            "metadata": {},
        }

    monkeypatch.delitem(
        media_client.app.state.config,
        "rag_chat_clip_top_k",
        raising=False,
    )
    monkeypatch.setattr(routes, "_get_rag_service", lambda: TextService())
    monkeypatch.setattr(
        routes,
        "_get_clip_rag_service",
        lambda *, raise_http=True: ClipService(),
    )
    monkeypatch.setattr(routes.llm_service, "mode", "api", raising=False)
    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    response = media_client.post(
        "/chat",
        json={
            "message": "Find the saved logo image.",
            "session_id": f"captionless-clip-{content_hash[:8]}",
            "use_rag": True,
            "use_text_rag": False,
            "use_vision_rag": True,
        },
    )

    assert response.status_code == 200, response.json()
    rag = response.json()["metadata"]["rag"]
    assert rag["clip_top_k"] == 2
    assert clip_query_top_k
    match = rag["matches"][0]
    assert match["metadata"]["retrieved_via"] == "clip"
    assert match["metadata"]["content_hash"] == content_hash
    assert "caption_doc_id" not in match["metadata"]
    assert match["text"] == "source.png"
    assert captured["context"] is not None


def test_caption_index_failure_is_terminal_and_preserves_real_caption(
    media_client,
    monkeypatch,
):
    uploaded = _upload_image(media_client)
    content_hash = uploaded["content_hash"]
    image_bytes = _real_image_bytes()

    def fail_rag_service():
        raise RuntimeError("index unavailable")

    monkeypatch.setattr(routes, "_get_rag_service", fail_rag_service)
    routes._mutate_attachment_meta(
        content_hash,
        lambda current: {
            **current,
            "caption_status": "pending",
            "index_status": "indexing",
        },
    )

    with pytest.raises(RuntimeError, match="index unavailable"):
        routes._caption_and_index_image_bytes(
            image_bytes,
            filename="source.png",
            content_type="image/png",
            content_hash=content_hash,
        )
    failed = routes._read_attachment_meta(content_hash)
    assert failed["caption_status"] == "error"
    assert failed["index_status"] == "error"
    assert failed["index_warning"] == "attachment_index_failed"

    routes._mutate_attachment_meta(
        content_hash,
        lambda current: {
            **current,
            "caption": "A hand-written Float logo caption.",
            "caption_model": "manual-caption",
            "caption_status": "manual",
            "placeholder_caption": False,
        },
    )
    with pytest.raises(RuntimeError, match="index unavailable"):
        routes._caption_and_index_image_bytes(
            image_bytes,
            filename="source.png",
            content_type="image/png",
            content_hash=content_hash,
        )
    preserved = routes._read_attachment_meta(content_hash)
    assert preserved["caption"] == "A hand-written Float logo caption."
    assert preserved["caption_status"] == "manual"
    assert preserved["index_status"] == "error"


def test_placeholder_caption_is_status_only_and_not_searchable(
    tmp_path,
    monkeypatch,
):
    blobs_root = tmp_path / "blobs"
    blobs_root.mkdir()
    monkeypatch.setenv("FLOAT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(routes, "BLOBS_DIR", blobs_root)
    monkeypatch.setattr(blob_store, "BLOBS_DIR", blobs_root)
    image_bytes = _real_image_bytes()
    asset = blob_store.put_asset(
        image_bytes,
        filename="fallback.png",
        origin="upload",
    )
    content_hash = asset["content_hash"]
    routes._write_attachment_meta(
        content_hash,
        {
            "display_name": "Friendly image name",
            "source_url": "https://example.test/image.png?token=secret",
            "source_url_recorded_at": "2026-07-29T00:00:00Z",
        },
    )

    class TextService:
        def __init__(self):
            self.ingested = []
            self.deleted = []

        def ingest_text(self, text, metadata):
            self.ingested.append((text, metadata))
            return "caption-doc"

        def delete_source(self, source):
            self.deleted.append(source)

    class ClipService:
        embedding_model = "clip:test-model"

        def __init__(self):
            self.ingested = []

        def ingest_text(self, text, metadata):
            self.ingested.append((text, metadata))
            return "clip-doc"

    text_service = TextService()
    clip_service = ClipService()
    monkeypatch.setattr(routes, "_get_rag_service", lambda: text_service)
    monkeypatch.setattr(
        routes,
        "_get_clip_rag_service",
        lambda *, raise_http=True: clip_service,
    )
    monkeypatch.setattr(
        routes,
        "_generate_image_caption",
        lambda *_args, **_kwargs: (
            "[placeholder] Unable to generate caption "
            "(vision model offline). ref bbbbbbbb",
            True,
            "local-model",
        ),
    )
    monkeypatch.setattr(
        clip_embeddings,
        "embed_clip_image_bytes",
        lambda *_args, **_kwargs: [0.1, 0.2],
    )

    result = routes._caption_and_index_image_bytes(
        image_bytes,
        filename="fallback.png",
        content_type="image/png",
        content_hash=content_hash,
    )

    metadata = routes._read_attachment_meta(content_hash)
    assert result["caption"] == ""
    assert result["caption_status"] == "placeholder"
    assert text_service.ingested == []
    assert text_service.deleted == [f"image:{content_hash}"]
    assert clip_service.ingested[0][0] == "Friendly image name"
    assert clip_service.ingested[0][1]["caption_available"] is False
    assert "caption_doc_id" not in clip_service.ingested[0][1]
    assert "source_url" not in clip_service.ingested[0][1]
    assert "caption" not in metadata
    assert "source_url" not in metadata
    assert metadata["caption_status"] == "placeholder"
    assert metadata["placeholder_caption"] is True


def test_background_caption_and_manual_metadata_updates_do_not_erase_each_other(
    tmp_path,
    monkeypatch,
):
    blobs_root = tmp_path / "blobs"
    blobs_root.mkdir()
    monkeypatch.setenv("FLOAT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(routes, "BLOBS_DIR", blobs_root)
    monkeypatch.setattr(blob_store, "BLOBS_DIR", blobs_root)
    image_bytes = _real_image_bytes()
    asset = blob_store.put_asset(
        image_bytes,
        filename="background.png",
        origin="upload",
    )
    content_hash = asset["content_hash"]
    routes._write_attachment_meta(
        content_hash,
        {
            "display_name": "Keep label",
            "folder": "Keep/Folder",
            "source_url": "https://example.test/source.png?version=2",
        },
    )
    generation_started = threading.Event()
    release_generation = threading.Event()
    manual_finished = threading.Event()

    class TextService:
        def ingest_text(self, _text, _metadata):
            return "caption-doc"

        def delete_source(self, _source):
            return None

    def generate(*_args, **_kwargs):
        generation_started.set()
        assert release_generation.wait(timeout=5)
        return "Generated caption.", False, "local-model"

    monkeypatch.setattr(routes, "_get_rag_service", lambda: TextService())
    monkeypatch.setattr(
        routes,
        "_get_clip_rag_service",
        lambda *, raise_http=True: None,
    )
    monkeypatch.setattr(routes, "_generate_image_caption", generate)

    background = threading.Thread(
        target=routes._caption_and_index_image_bytes,
        kwargs={
            "data": image_bytes,
            "filename": "background.png",
            "content_type": "image/png",
            "content_hash": content_hash,
        },
    )

    def save_manual_caption():
        def mutate(metadata):
            metadata.update(
                {
                    "caption": "Manual caption.",
                    "caption_model": "manual-caption",
                    "caption_status": "manual",
                    "placeholder_caption": False,
                }
            )
            return metadata

        routes._mutate_attachment_meta(content_hash, mutate)
        manual_finished.set()

    background.start()
    assert generation_started.wait(timeout=5)
    manual = threading.Thread(target=save_manual_caption)
    manual.start()
    assert manual_finished.wait(timeout=0.05) is False
    release_generation.set()
    background.join(timeout=5)
    manual.join(timeout=5)

    assert not background.is_alive()
    assert not manual.is_alive()
    metadata = routes._read_attachment_meta(content_hash)
    assert metadata["caption"] == "Manual caption."
    assert metadata["caption_status"] == "manual"
    assert metadata["display_name"] == "Keep label"
    assert metadata["folder"] == "Keep/Folder"
    assert metadata["source_url"] == "https://example.test/source.png?version=2"


def test_attachment_rehydrate_reports_caption_outcomes_truthfully(
    media_client,
    tmp_path,
    monkeypatch,
):
    hashes = [character * 64 for character in "abcdef"]
    targets = {}
    for content_hash in hashes:
        target = tmp_path / f"{content_hash}.png"
        target.write_bytes(_real_image_bytes())
        targets[content_hash] = target

    monkeypatch.setattr(routes, "_iter_attachment_hashes", lambda: hashes)
    monkeypatch.setattr(
        routes,
        "_resolve_attachment_target",
        lambda content_hash, **_kwargs: targets[content_hash],
    )
    monkeypatch.setattr(
        routes,
        "_repair_attachment_storage_metadata",
        lambda _content_hash, metadata, _target: metadata,
    )
    monkeypatch.setattr(
        routes,
        "build_attachment_media_descriptor",
        lambda content_hash, _target, **_kwargs: {
            "filename": f"{content_hash}.png",
            "content_type": "image/png",
            "metadata": {},
            "metadata_changed": False,
        },
    )

    outcomes = {
        hashes[0]: {"caption": "Generated.", "caption_status": "generated"},
        hashes[1]: {"caption": "Manual.", "caption_status": "manual"},
        hashes[2]: {"caption": "Reused.", "caption_status": "preserved"},
        hashes[3]: {"caption": "", "caption_status": "placeholder"},
        hashes[4]: {"caption": "", "caption_status": "disabled"},
    }

    def reindex(_data, *, content_hash, **_kwargs):
        if content_hash == hashes[5]:
            raise RuntimeError("index failed")
        return outcomes[content_hash]

    monkeypatch.setattr(routes, "_caption_and_index_image_bytes", reindex)

    response = media_client.post(
        "/attachments/rag/rehydrate",
        json={"dry_run": False},
    )

    assert response.status_code == 200
    assert response.json() == {
        "scanned": 6,
        "reindexed": 5,
        "captions_generated": 1,
        "captions_unavailable": 2,
        "failed": 1,
        "dry_run": False,
    }

    dry_run = media_client.post(
        "/attachments/rag/rehydrate",
        json={"dry_run": True, "limit": 2},
    )
    assert dry_run.status_code == 200
    assert dry_run.json() == {
        "scanned": 2,
        "reindexed": 0,
        "captions_generated": 0,
        "captions_unavailable": 0,
        "failed": 0,
        "dry_run": True,
    }


def test_attachment_rehydrate_dry_run_does_not_migrate_or_repair_storage(
    media_client,
    monkeypatch,
):
    image_bytes = _real_image_bytes()
    content_hash = hashlib.sha256(image_bytes).hexdigest()
    files_root = blob_store._resolve_data_files_root()
    legacy_root = files_root / "workspace" / "sync"
    legacy_target = legacy_root / "peer-a" / content_hash / "legacy.png"
    legacy_target.parent.mkdir(parents=True)
    legacy_target.write_bytes(image_bytes)
    routes._write_attachment_meta(
        content_hash,
        {
            "filename": "legacy.png",
            "content_type": "image/png",
        },
    )
    metadata_path = routes.BLOBS_DIR / f"{content_hash}.json"
    metadata_before = metadata_path.read_bytes()

    def storage_snapshot():
        return {
            path.relative_to(files_root).as_posix(): (
                None if path.is_dir() else path.read_bytes()
            )
            for path in files_root.rglob("*")
        }

    files_before = storage_snapshot()
    monkeypatch.setattr(routes, "_iter_attachment_hashes", lambda: [content_hash])
    monkeypatch.setattr(
        routes,
        "_repair_attachment_storage_metadata",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("dry run must not repair metadata")
        ),
    )
    monkeypatch.setattr(
        routes,
        "_mutate_attachment_meta",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("dry run must not write metadata")
        ),
    )

    response = media_client.post(
        "/attachments/rag/rehydrate",
        json={"dry_run": True, "content_hashes": [content_hash]},
    )

    assert response.status_code == 200
    assert response.json() == {
        "scanned": 1,
        "reindexed": 0,
        "captions_generated": 0,
        "captions_unavailable": 0,
        "failed": 0,
        "dry_run": True,
    }
    assert metadata_path.read_bytes() == metadata_before
    assert storage_snapshot() == files_before
    assert legacy_target.exists()


def test_attachment_rehydrate_counts_unreadable_image_as_failed(
    media_client,
    monkeypatch,
):
    content_hash = "8" * 64

    class UnreadableTarget:
        name = "unreadable.png"

        @staticmethod
        def exists():
            return True

        @staticmethod
        def is_file():
            return True

        @staticmethod
        def read_bytes():
            raise OSError("read failed")

    monkeypatch.setattr(routes, "_iter_attachment_hashes", lambda: [content_hash])
    monkeypatch.setattr(
        routes,
        "_resolve_attachment_target",
        lambda *_args, **_kwargs: UnreadableTarget(),
    )
    monkeypatch.setattr(
        routes,
        "_repair_attachment_storage_metadata",
        lambda _content_hash, metadata, _target: metadata,
    )
    monkeypatch.setattr(
        routes,
        "build_attachment_media_descriptor",
        lambda *_args, **_kwargs: {
            "filename": "unreadable.png",
            "content_type": "image/png",
            "metadata": {},
            "metadata_changed": False,
        },
    )

    response = media_client.post(
        "/attachments/rag/rehydrate",
        json={"content_hashes": [content_hash]},
    )

    assert response.status_code == 200
    assert response.json() == {
        "scanned": 1,
        "reindexed": 0,
        "captions_generated": 0,
        "captions_unavailable": 0,
        "failed": 1,
        "dry_run": False,
    }


def test_legacy_clip_offline_caption_is_classified_as_placeholder():
    defaults = routes._attachment_status_defaults(
        {
            "caption": (
                "[placeholder] Unable to generate caption "
                "(vision model offline). ref 01234567"
            ),
            "caption_model": "clip-vit-base-patch32",
            "caption_status": "generated",
            "placeholder_caption": False,
        },
        content_type="image/png",
    )

    assert defaults["caption_status"] == "placeholder"
    assert defaults["placeholder_caption"] is True


def test_selective_caption_generate_preserves_generated_and_manual(
    media_client,
    monkeypatch,
):
    uploaded = _upload_image(media_client)
    content_hash = uploaded["content_hash"]
    metadata = routes._read_attachment_meta(content_hash)
    metadata.update(
        {
            "caption": "An existing generated caption.",
            "caption_model": "local-caption-model",
            "caption_status": "generated",
            "placeholder_caption": False,
        }
    )
    routes._write_attachment_meta(content_hash, metadata)
    monkeypatch.setattr(
        routes,
        "_reindex_attachment_caption",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("preserved caption must not be regenerated")
        ),
    )

    preserved = media_client.post(
        f"/attachments/caption/{content_hash}/generate",
        json={"replace_generated": False},
    )
    assert preserved.status_code == 200
    assert preserved.json()["status"] == "preserved"

    metadata["caption"] = "A user caption."
    metadata["caption_model"] = "manual-caption"
    metadata["caption_status"] = "manual"
    routes._write_attachment_meta(content_hash, metadata)
    protected = media_client.post(
        f"/attachments/caption/{content_hash}/generate",
        json={"replace_generated": True},
    )
    assert protected.status_code == 409
