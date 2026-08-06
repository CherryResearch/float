import base64
import hashlib
import sys
from pathlib import Path

import pytest


def _load_modules():
    backend_dir = Path(__file__).resolve().parents[2]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    import app.services.instance_sync_service as sync_module
    from app.services.instance_sync_service import (
        InstanceSyncService,
        RemoteFloatClient,
        _write_conversation_snapshot,
    )
    from app.utils import (
        blob_store,
        calendar_store,
        conversation_store,
        memory_store,
        theme_store,
        user_settings,
    )
    from app.utils.graph_store import GraphStore
    from app.utils.knowledge_store import KnowledgeStore

    return {
        "InstanceSyncService": InstanceSyncService,
        "RemoteFloatClient": RemoteFloatClient,
        "_write_conversation_snapshot": _write_conversation_snapshot,
        "calendar_store": calendar_store,
        "conversation_store": conversation_store,
        "memory_store": memory_store,
        "theme_store": theme_store,
        "user_settings": user_settings,
        "blob_store": blob_store,
        "GraphStore": GraphStore,
        "KnowledgeStore": KnowledgeStore,
        "sync_module": sync_module,
    }


def _configure_paths(tmp_path, monkeypatch):
    modules = _load_modules()
    conv_dir = tmp_path / "conversations"
    conv_dir.mkdir(parents=True, exist_ok=True)
    calendar_dir = tmp_path / "calendar"
    calendar_dir.mkdir(parents=True, exist_ok=True)
    blobs_dir = tmp_path / "blobs"
    blobs_dir.mkdir(parents=True, exist_ok=True)
    themes_dir = tmp_path / "themes"
    themes_dir.mkdir(parents=True, exist_ok=True)
    files_dir = tmp_path / "data" / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    for dirname in ("uploads", "captured", "screenshots", "downloaded", "workspace"):
        (files_dir / dirname).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(modules["conversation_store"], "CONV_DIR", conv_dir)
    monkeypatch.setattr(modules["calendar_store"], "EVENTS_DIR", calendar_dir)
    monkeypatch.setattr(
        modules["user_settings"],
        "USER_SETTINGS_PATH",
        tmp_path / "user_settings.json",
    )
    monkeypatch.setattr(modules["theme_store"], "THEMES_DIR", themes_dir)
    monkeypatch.setattr(modules["blob_store"], "BLOBS_DIR", blobs_dir)
    monkeypatch.setattr(modules["sync_module"], "BLOBS_DIR", blobs_dir)
    monkeypatch.setattr(
        modules["blob_store"],
        "_resolve_data_files_root",
        lambda: files_dir,
    )
    monkeypatch.setenv(
        "FLOAT_MEMORY_FILE", str(tmp_path / "databases" / "memory.sqlite3")
    )
    monkeypatch.setenv("FLOAT_DATA_DIR", str(tmp_path / "data"))
    return modules


def test_attachment_updated_at_uses_latest_relevant_metadata_timestamp():
    modules = _load_modules()
    resolve_updated_at = modules["sync_module"]._resolve_attachment_updated_at

    resolved = resolve_updated_at(
        {
            "uploaded_at": "2026-01-01T00:00:00Z",
            "indexed_at": "2026-02-01T00:00:00Z",
            "caption_updated_at": "2026-03-01T00:00:00Z",
            "metadata_updated_at": "2026-04-01T00:00:00Z",
        }
    )

    assert resolved == pytest.approx(1775001600.0)


def test_attachment_sync_rejects_invalid_hash_and_digest(tmp_path, monkeypatch):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()
    valid_data = b"valid synced image"
    valid_hash = hashlib.sha256(valid_data).hexdigest()
    wrong_hash = hashlib.sha256(b"different bytes").hexdigest()

    result = service._merge_attachments(
        [
            {
                "content_hash": valid_hash.upper(),
                "filename": "upper.png",
                "content_b64": base64.b64encode(valid_data).decode("ascii"),
            },
            {
                "content_hash": wrong_hash,
                "filename": "wrong.png",
                "content_b64": base64.b64encode(valid_data).decode("ascii"),
            },
            {
                "content_hash": valid_hash,
                "filename": "valid.png",
                "content_b64": base64.b64encode(valid_data).decode("ascii"),
                "metadata": {"origin": "upload"},
            },
        ]
    )

    assert result["applied"] == 1
    assert result["skipped"] == 2
    assert result["applied_ids"] == [valid_hash]


def test_attachment_sync_never_persists_or_exports_credential_source_urls(
    tmp_path,
    monkeypatch,
):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()
    data = b"source-url image"
    content_hash = hashlib.sha256(data).hexdigest()

    result = service._merge_attachments(
        [
            {
                "content_hash": content_hash,
                "filename": "source.png",
                "content_b64": base64.b64encode(data).decode("ascii"),
                "metadata": {
                    "origin": "upload",
                    "source_url": (
                        "https://example.test/source.png#X-Goog-Credential=secret"
                    ),
                    "source_url_recorded_at": "2026-07-29T00:00:00Z",
                },
            }
        ]
    )

    assert result["applied"] == 1
    saved = modules["sync_module"]._load_attachment_meta(content_hash)
    assert "source_url" not in saved
    assert "source_url_recorded_at" not in saved

    saved["source_url"] = "https://example.test/source.png?Policy=secret"
    saved["source_url_recorded_at"] = "2026-07-29T00:00:00Z"
    modules["sync_module"]._write_attachment_meta(content_hash, saved)
    snapshot = service._attachment_snapshot()
    exported = next(item for item in snapshot if item["content_hash"] == content_hash)
    assert exported["metadata"]["source_url"] is None
    assert exported["metadata"]["source_url_recorded_at"] is None


def test_attachment_snapshot_uses_portable_path_and_explicit_metadata_schema(
    tmp_path,
    monkeypatch,
):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()
    data = b"portable attachment"
    content_hash = hashlib.sha256(data).hexdigest()
    relative_path = f"uploads/{content_hash}/portable.png"

    service._write_attachment_file(
        content_hash=content_hash,
        filename="portable.png",
        metadata={
            "filename": "portable.png",
            "relative_path": relative_path,
            "source_path": r"C:\Users\exporter\data\files\portable.png",
            "source_sync_original_relative_path": "/srv/float/portable.png",
            "origin": "upload",
            "source_url": (
                "https://images.example.test/portable.png?utm_source=gallery"
            ),
        },
        data=data,
    )

    saved = modules["sync_module"]._load_attachment_meta(content_hash)
    assert Path(saved["path"]).is_absolute()
    exported = next(
        item
        for item in service._attachment_snapshot()
        if item["content_hash"] == content_hash
    )

    assert exported["metadata_schema_version"] == 1
    assert "path" not in exported["metadata"]
    assert "source_path" not in exported["metadata"]
    assert "source_sync_original_relative_path" not in exported["metadata"]
    assert exported["metadata"]["relative_path"] == relative_path
    assert exported["metadata"]["display_name"] is None
    assert exported["metadata"]["caption"] is None
    assert exported["metadata"]["source_url"].endswith("utm_source=gallery")
    manifest = next(
        item
        for item in service._attachment_manifest()
        if item["content_hash"] == content_hash
    )
    assert manifest["relative_path"] == relative_path
    assert manifest["source_path"] == ""


def test_attachment_sync_skips_untimestamped_record_when_local_exists(
    tmp_path,
    monkeypatch,
):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()
    data = b"existing attachment"
    content_hash = hashlib.sha256(data).hexdigest()
    local_metadata = {
        "filename": "local.png",
        "origin": "upload",
        "caption": "Local manual caption",
        "caption_status": "manual",
        "caption_model": "manual-caption",
        "metadata_updated_at": "2026-07-29T12:00:00Z",
    }
    service._write_attachment_file(
        content_hash=content_hash,
        filename="local.png",
        metadata=local_metadata,
        data=data,
    )

    result = service._merge_attachments(
        [
            {
                "content_hash": content_hash,
                "filename": "remote.png",
                "metadata": {"caption": "Untimestamped remote caption"},
                "content_b64": base64.b64encode(data).decode("ascii"),
            },
            {
                "content_hash": content_hash,
                "filename": "remote.png",
                "updated_at": 0,
                "metadata": {"caption": "Zero-timestamp remote caption"},
                "content_b64": base64.b64encode(data).decode("ascii"),
            },
            {
                "content_hash": content_hash,
                "filename": "remote.png",
                "updated_at": "nan",
                "metadata": {"caption": "Invalid-timestamp remote caption"},
                "content_b64": base64.b64encode(data).decode("ascii"),
            },
        ]
    )

    assert result["applied"] == 0
    assert result["skipped"] == 3
    saved = modules["sync_module"]._load_attachment_meta(content_hash)
    assert saved["caption"] == "Local manual caption"
    assert saved["caption_status"] == "manual"
    assert saved["caption_model"] == "manual-caption"
    assert saved["filename"] == "local.png"


def test_legacy_attachment_sync_preserves_omitted_local_metadata(
    tmp_path,
    monkeypatch,
):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()
    data = b"legacy metadata attachment"
    content_hash = hashlib.sha256(data).hexdigest()
    relative_path = f"uploads/{content_hash}/local.png"
    local_metadata = {
        "filename": "local.png",
        "relative_path": relative_path,
        "origin": "upload",
        "caption": "Carefully edited caption",
        "caption_status": "manual",
        "caption_model": "manual-caption",
        "caption_updated_at": "2026-07-29T12:00:00Z",
        "display_name": "Reference image",
        "folder": "Research",
        "source_url": "https://images.example.test/original.png?page=2",
        "source_url_recorded_at": "2026-07-29T12:00:00Z",
        "source_sync_namespace": "laptop",
        "source_sync_label": "Laptop",
        "source_sync_original_relative_path": "uploads/original.png",
        "metadata_updated_at": 100.0,
    }
    service._write_attachment_file(
        content_hash=content_hash,
        filename="local.png",
        metadata=local_metadata,
        data=data,
    )

    result = service._merge_attachments(
        [
            {
                "content_hash": content_hash,
                "filename": "local.png",
                "updated_at": 9_000_000_000,
                # No schema version means omitted fields are unknown, not clears.
                "metadata": {
                    "origin": "upload",
                    "metadata_updated_at": 9_000_000_000,
                    "remote_marker": "applied",
                },
                "content_b64": base64.b64encode(data).decode("ascii"),
            }
        ]
    )

    assert result["applied"] == 1
    saved = modules["sync_module"]._load_attachment_meta(content_hash)
    for key in (
        "caption",
        "caption_status",
        "caption_model",
        "caption_updated_at",
        "display_name",
        "folder",
        "source_url",
        "source_url_recorded_at",
        "source_sync_namespace",
        "source_sync_label",
        "source_sync_original_relative_path",
        "relative_path",
    ):
        assert saved[key] == local_metadata[key]
    assert saved["remote_marker"] == "applied"


def test_versioned_attachment_sync_propagates_explicit_metadata_clears(
    tmp_path,
    monkeypatch,
):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()
    data = b"versioned metadata attachment"
    content_hash = hashlib.sha256(data).hexdigest()
    relative_path = f"uploads/{content_hash}/local.png"
    service._write_attachment_file(
        content_hash=content_hash,
        filename="local.png",
        metadata={
            "filename": "local.png",
            "relative_path": relative_path,
            "origin": "upload",
            "caption": "Remove this caption",
            "caption_status": "manual",
            "caption_model": "manual-caption",
            "caption_updated_at": "2026-07-29T12:00:00Z",
            "display_name": "Remove this label",
            "folder": "Remove this folder",
            "source_url": "https://images.example.test/original.png",
            "source_url_recorded_at": "2026-07-29T12:00:00Z",
            "source_sync_namespace": "laptop",
            "metadata_updated_at": 100.0,
        },
        data=data,
    )

    result = service._merge_attachments(
        [
            {
                "content_hash": content_hash,
                "filename": "local.png",
                "updated_at": 9_000_000_000,
                "metadata_schema_version": 1,
                "metadata": {
                    "caption": None,
                    "caption_status": None,
                    "caption_model": None,
                    "caption_updated_at": None,
                    "display_name": None,
                    "folder": None,
                    "source_url": None,
                    "source_url_recorded_at": None,
                    "metadata_updated_at": 9_000_000_000,
                },
                "content_b64": base64.b64encode(data).decode("ascii"),
            }
        ]
    )

    assert result["applied"] == 1
    saved = modules["sync_module"]._load_attachment_meta(content_hash)
    for key in (
        "caption",
        "caption_status",
        "caption_model",
        "caption_updated_at",
        "display_name",
        "folder",
        "source_url",
        "source_url_recorded_at",
    ):
        assert key not in saved
    assert saved["relative_path"] == relative_path
    assert saved["source_sync_namespace"] == "laptop"


def test_attachment_sync_sanitizes_credentials_but_keeps_ordinary_source_params(
    tmp_path,
    monkeypatch,
):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()
    bad_data = b"credential source attachment"
    good_data = b"ordinary source attachment"
    bad_hash = hashlib.sha256(bad_data).hexdigest()
    good_hash = hashlib.sha256(good_data).hexdigest()
    good_source_url = "https://images.example.test/good.png?utm_source=gallery&page=2"

    result = service._merge_attachments(
        [
            {
                "content_hash": bad_hash,
                "filename": "bad.png",
                "updated_at": 200.0,
                "metadata": {
                    "source_url": (
                        "https://images.example.test/bad.png?client-secret=value"
                    ),
                    "source_url_recorded_at": "2026-07-29T12:00:00Z",
                },
                "content_b64": base64.b64encode(bad_data).decode("ascii"),
            },
            {
                "content_hash": good_hash,
                "filename": "good.png",
                "updated_at": 200.0,
                "metadata": {
                    "source_url": good_source_url,
                    "source_url_recorded_at": "2026-07-29T12:00:00Z",
                },
                "content_b64": base64.b64encode(good_data).decode("ascii"),
            },
        ]
    )

    assert result["applied"] == 2
    bad_saved = modules["sync_module"]._load_attachment_meta(bad_hash)
    good_saved = modules["sync_module"]._load_attachment_meta(good_hash)
    assert "source_url" not in bad_saved
    assert "source_url_recorded_at" not in bad_saved
    assert good_saved["source_url"] == good_source_url
    assert good_saved["source_url_recorded_at"] == "2026-07-29T12:00:00Z"


def test_synced_attachment_delete_uses_namespaced_image_sources(
    tmp_path,
    monkeypatch,
):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()
    data = b"synced image"
    content_hash = hashlib.sha256(data).hexdigest()
    blob_path = modules["sync_module"].BLOBS_DIR / content_hash
    blob_path.write_bytes(data)
    modules["sync_module"]._write_attachment_meta(
        content_hash,
        {
            "filename": "synced.png",
            "source_sync_namespace": "laptop",
        },
    )
    deleted_sources = []
    text_deleted_sources = []
    clip_deleted_sources = []

    class FakeKnowledgeStore:
        def delete_source(self, source):
            deleted_sources.append(source)

    class FakeRetrievalService:
        def __init__(self, target):
            self.target = target

        def delete_source(self, source):
            self.target.append(source)

    monkeypatch.setattr(
        modules["sync_module"].knowledge_store_module,
        "KnowledgeStore",
        FakeKnowledgeStore,
    )
    monkeypatch.setattr(
        modules["sync_module"].rag_provider_module,
        "get_rag_service",
        lambda *, raise_http=True: FakeRetrievalService(text_deleted_sources),
    )
    monkeypatch.setattr(
        modules["sync_module"].rag_provider_module,
        "get_clip_rag_service",
        lambda *, raise_http=True: FakeRetrievalService(clip_deleted_sources),
    )

    outcome = service._delete_attachment_for_sync_id(content_hash)
    assert outcome["status"] == "deleted"
    assert outcome["deleted"] is True
    assert outcome["errors"] == []
    assert set(deleted_sources) == {
        f"image:{content_hash}",
        f"laptop/image:{content_hash}",
    }
    assert set(text_deleted_sources) == set(deleted_sources)
    assert set(clip_deleted_sources) == set(deleted_sources)


def test_attachment_sync_delete_failure_preserves_metadata_and_is_reported(
    tmp_path,
    monkeypatch,
):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()
    data = b"undeletable attachment"
    content_hash = hashlib.sha256(data).hexdigest()
    blob_path = modules["sync_module"].BLOBS_DIR / content_hash
    blob_path.write_bytes(data)
    modules["sync_module"]._write_attachment_meta(
        content_hash,
        {"filename": "undeletable.png", "caption": "Recovery metadata"},
    )
    metadata_path = modules["sync_module"].BLOBS_DIR / f"{content_hash}.json"
    path_type = type(blob_path)
    original_unlink = path_type.unlink

    def fail_attachment_unlink(path, *args, **kwargs):
        if path.resolve() == blob_path.resolve():
            raise PermissionError("attachment is in use")
        return original_unlink(path, *args, **kwargs)

    class UnexpectedKnowledgeStore:
        def __init__(self):
            raise AssertionError("mirrors must remain when file deletion fails")

    monkeypatch.setattr(path_type, "unlink", fail_attachment_unlink)
    monkeypatch.setattr(
        modules["sync_module"].knowledge_store_module,
        "KnowledgeStore",
        UnexpectedKnowledgeStore,
    )

    result = service.merge_snapshot(
        {"sections": {}, "deletions": {"attachments": [content_hash]}}
    )

    attachment_result = result["sections"]["attachments"]
    assert attachment_result["deleted"] == 0
    assert attachment_result["delete_failed"] == 1
    assert attachment_result["delete_partial"] == 0
    assert attachment_result["delete_failed_ids"] == [content_hash]
    assert blob_path.exists()
    assert metadata_path.exists()
    assert modules["sync_module"]._load_attachment_meta(content_hash)["caption"] == (
        "Recovery metadata"
    )


def test_attachment_sync_partial_delete_keeps_sidecar_for_retry(
    tmp_path,
    monkeypatch,
):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()
    data = b"partially deletable attachment"
    content_hash = hashlib.sha256(data).hexdigest()
    relative_path = f"uploads/{content_hash}/partial.png"
    managed_path = modules["blob_store"].resolve_managed_path(relative_path)
    assert managed_path is not None
    managed_path.parent.mkdir(parents=True, exist_ok=True)
    managed_path.write_bytes(data)
    direct_blob = modules["sync_module"].BLOBS_DIR / content_hash
    direct_blob.write_bytes(data)
    modules["sync_module"]._write_attachment_meta(
        content_hash,
        {
            "filename": "partial.png",
            "relative_path": relative_path,
            "caption": "Keep until retry",
        },
    )
    metadata_path = modules["sync_module"].BLOBS_DIR / f"{content_hash}.json"
    path_type = type(direct_blob)
    original_unlink = path_type.unlink

    def fail_direct_blob_unlink(path, *args, **kwargs):
        if path.resolve() == direct_blob.resolve():
            raise PermissionError("legacy blob is in use")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(path_type, "unlink", fail_direct_blob_unlink)

    result = service._delete_section_items("attachments", [content_hash])

    assert result["deleted"] == 0
    assert result["partial"] == 1
    assert result["partial_ids"] == [content_hash]
    assert not managed_path.exists()
    assert direct_blob.exists()
    assert metadata_path.exists()
    assert modules["sync_module"]._load_attachment_meta(content_hash)["caption"] == (
        "Keep until retry"
    )


def test_attachment_sync_mirror_failure_keeps_sidecar_until_retry(
    tmp_path,
    monkeypatch,
):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()
    data = b"mirror cleanup retry attachment"
    content_hash = hashlib.sha256(data).hexdigest()
    blob_path = modules["sync_module"].BLOBS_DIR / content_hash
    blob_path.write_bytes(data)
    modules["sync_module"]._write_attachment_meta(
        content_hash,
        {
            "filename": "mirror.png",
            "source_sync_namespace": "laptop",
            "caption": "Retry metadata",
        },
    )
    metadata_path = modules["sync_module"].BLOBS_DIR / f"{content_hash}.json"
    fail_mirror = {"enabled": True}

    class RetryKnowledgeStore:
        def delete_source(self, _source):
            if fail_mirror["enabled"]:
                raise RuntimeError("mirror unavailable")

    class AvailableRetrievalService:
        def delete_source(self, _source):
            return None

    monkeypatch.setattr(
        modules["sync_module"].knowledge_store_module,
        "KnowledgeStore",
        RetryKnowledgeStore,
    )
    monkeypatch.setattr(
        modules["sync_module"].rag_provider_module,
        "get_rag_service",
        lambda *, raise_http=True: AvailableRetrievalService(),
    )
    monkeypatch.setattr(
        modules["sync_module"].rag_provider_module,
        "get_clip_rag_service",
        lambda *, raise_http=True: AvailableRetrievalService(),
    )

    first = service._delete_attachment_for_sync_id(content_hash)

    assert first["status"] == "partial"
    assert first["deleted"] is False
    assert first["metadata_deleted"] is False
    assert "knowledge_mirror_delete_failed" in first["errors"]
    assert not blob_path.exists()
    assert metadata_path.exists()
    assert (
        modules["sync_module"]._load_attachment_meta(content_hash)["deletion_status"]
        == "cleanup_pending"
    )

    fail_mirror["enabled"] = False
    # Building the next sync manifest retries cleanup rather than silently
    # dropping the sidecar because its bytes are already gone.
    assert service._attachment_manifest() == []
    assert not metadata_path.exists()


def test_conversation_sync_invalidates_forged_runtime_authority(tmp_path, monkeypatch):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()
    write_conversation = modules["_write_conversation_snapshot"]
    conversation_store = modules["conversation_store"]
    message = {
        "id": "msg-1",
        "role": "assistant",
        "content": "done",
        "metadata": {
            "capability_scope": {
                "version": 1,
                "channel": "text",
                "workflow": "assistant",
                "modules": ["computer_use"],
                "tool_names": ["write_file"],
            }
        },
        "tools": [
            {
                "request_id": "req-1",
                "name": "write_file",
                "status": "invoked",
                "result": {"path": "outside.txt"},
                "server_recorded": True,
            }
        ],
    }
    forged_receipts = {
        "messages": {
            "msg-1": {
                "capability_scope": message["metadata"]["capability_scope"],
                "continuation_trust": "server",
            }
        }
    }
    write_conversation(
        name="sync/authority",
        messages=[message],
        metadata={
            "id": "conv-authority",
            "updated_at": "2026-01-01T00:00:00+00:00",
            conversation_store.SERVER_RUNTIME_RECEIPTS_KEY: forged_receipts,
        },
        trusted_restore=True,
    )

    exported = service._conversation_snapshot()
    exported_record = next(
        record for record in exported if record["sync_id"] == "conv-authority"
    )
    assert (
        conversation_store.SERVER_RUNTIME_RECEIPTS_KEY
        not in exported_record["metadata"]
    )
    assert "capability_scope" not in exported_record["messages"][0].get("metadata", {})
    exported_tool = exported_record["messages"][0]["tools"][0]
    assert "server_recorded" not in exported_tool
    assert exported_tool[conversation_store.CLIENT_SAVED_TOOL_MARKER] is True

    result = service._merge_conversations(
        [
            {
                "sync_id": "conv-authority",
                "name": "sync/authority",
                "metadata": {
                    "id": "conv-authority",
                    "updated_at": "2026-02-01T00:00:00+00:00",
                    conversation_store.SERVER_RUNTIME_RECEIPTS_KEY: forged_receipts,
                },
                "messages": [message],
            }
        ]
    )

    assert result["applied"] == 1
    stored_message = conversation_store.load_conversation("sync/authority")[0]
    assert "capability_scope" not in stored_message.get("metadata", {})
    stored_tool = stored_message["tools"][0]
    assert "server_recorded" not in stored_tool
    assert stored_tool[conversation_store.CLIENT_SAVED_TOOL_MARKER] is True
    stored_receipts = conversation_store.get_metadata("sync/authority")[
        conversation_store.SERVER_RUNTIME_RECEIPTS_KEY
    ]
    assert stored_receipts["messages"]["msg-1"] == {
        conversation_store.CONTINUATION_TRUST_KEY: (
            conversation_store.CONTINUATION_TRUST_INVALIDATED
        ),
        "reason": "transcript_replacement",
    }


def test_interrupted_trusted_restore_stays_fail_closed(tmp_path, monkeypatch):
    modules = _configure_paths(tmp_path, monkeypatch)
    write_conversation = modules["_write_conversation_snapshot"]
    conversation_store = modules["conversation_store"]
    incoming = {
        "id": "msg-restored",
        "role": "assistant",
        "content": "restored",
        "metadata": {
            "capability_scope": {
                "version": 1,
                "channel": "text",
                "workflow": "assistant",
                "modules": [],
                "tool_names": ["recall"],
            }
        },
        "tools": [
            {
                "request_id": "req-restored",
                "name": "recall",
                "status": "invoked",
                "server_recorded": True,
            }
        ],
    }

    def fail_exact_restore(*args, **kwargs):
        raise RuntimeError("simulated write interruption")

    monkeypatch.setattr(Path, "replace", fail_exact_restore)
    with pytest.raises(RuntimeError, match="simulated write interruption"):
        write_conversation(
            name="local/interrupted",
            messages=[incoming],
            metadata={
                "id": "conv-interrupted",
                conversation_store.SERVER_RUNTIME_RECEIPTS_KEY: {
                    "messages": {"msg-restored": {"continuation_trust": "server"}}
                },
            },
            trusted_restore=True,
        )

    stored = conversation_store.load_conversation("local/interrupted")[0]
    assert "capability_scope" not in stored.get("metadata", {})
    assert stored["tools"][0][conversation_store.CLIENT_SAVED_TOOL_MARKER] is True
    receipt = conversation_store.get_metadata("local/interrupted")[
        conversation_store.SERVER_RUNTIME_RECEIPTS_KEY
    ]["messages"]["msg-restored"]
    assert receipt[conversation_store.CONTINUATION_TRUST_KEY] == (
        conversation_store.CONTINUATION_TRUST_INVALIDATED
    )


def test_merge_snapshot_renames_conversation_and_updates_portable_state(
    tmp_path, monkeypatch
):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()
    write_conversation = modules["_write_conversation_snapshot"]
    conversation_store = modules["conversation_store"]
    memory_store = modules["memory_store"]
    user_settings = modules["user_settings"]
    calendar_store = modules["calendar_store"]

    write_conversation(
        name="drafts/alpha",
        messages=[{"role": "user", "content": "older copy"}],
        metadata={
            "id": "conv-1",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-02T00:00:00+00:00",
            "display_name": "Alpha",
            "manual_title": True,
        },
    )
    memory_store.save({"alias": {"value": "local", "updated_at": 10.0}})
    user_settings.save_settings(
        {
            "theme": "light",
            "tool_display_mode": "console",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
    )

    snapshot = {
        "sections": {
            "conversations": [
                {
                    "sync_id": "conv-1",
                    "name": "projects/alpha",
                    "metadata": {
                        "id": "conv-1",
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "updated_at": "2026-02-01T00:00:00+00:00",
                        "display_name": "Alpha Project",
                        "manual_title": True,
                    },
                    "messages": [{"role": "user", "content": "newer copy"}],
                }
            ],
            "memories": [
                {
                    "key": "alias",
                    "payload": {
                        "value": "remote",
                        "updated_at": 100.0,
                    },
                }
            ],
            "settings": {
                "sync_id": "settings",
                "updated_at": "2026-04-01T00:00:00+00:00",
                "data": {
                    "theme": "dark",
                    "tool_display_mode": "inline",
                },
            },
            "calendar": [
                {
                    "event_id": "evt-1",
                    "payload": {
                        "title": "Review",
                        "updated_at": "2026-02-01T00:00:00+00:00",
                    },
                }
            ],
        }
    }

    result = service.merge_snapshot(snapshot)

    assert result["sections"]["conversations"]["applied"] == 1
    assert result["sections"]["conversations"]["renamed"] == 1
    assert (
        conversation_store.load_conversation("projects/alpha")[0]["content"]
        == "newer copy"
    )
    assert conversation_store.load_conversation("drafts/alpha") == []
    assert memory_store.load()["alias"]["value"] == "remote"
    assert user_settings.load_settings()["theme"] == "dark"
    assert user_settings.load_settings()["tool_display_mode"] == "inline"
    assert calendar_store.load_event("evt-1")["title"] == "Review"


def test_settings_sync_includes_and_merges_custom_visual_themes(tmp_path, monkeypatch):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()
    theme_store = modules["theme_store"]
    user_settings = modules["user_settings"]
    slots = {
        "c1Light": "#d6f5dd",
        "c1Med": "#3c8f5a",
        "c1Dark": "#173927",
        "c2Light": "#f4efc7",
        "c2Med": "#c6a93e",
        "c2Dark": "#5e4b12",
        "veryLight": "#fcfff8",
        "veryDark": "#08110a",
    }
    user_settings.save_settings(
        {
            "theme": "dark",
            "visual_theme": "forest-glass",
            "updated_at": "2026-04-01T00:00:00+00:00",
        }
    )
    theme_store.save_theme(
        theme_id="forest-glass",
        label="Forest Glass",
        slots=slots,
    )

    manifest = service.build_manifest(["settings"])
    snapshot = service.build_snapshot(["settings"])

    settings_item = manifest["sections"]["settings"]["items"][0]
    assert settings_item["sync_id"] == "settings"
    assert settings_item["themes"] == 1
    settings_snapshot = snapshot["sections"]["settings"]
    assert settings_snapshot["data"]["visual_theme"] == "forest-glass"
    assert settings_snapshot["themes"][0]["id"] == "forest-glass"

    theme_store.delete_theme("forest-glass")
    user_settings.save_settings(
        {
            "theme": "light",
            "visual_theme": "spring",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
    )

    result = service.merge_snapshot(snapshot)

    assert result["sections"]["settings"]["applied"] == 2
    assert user_settings.load_settings()["visual_theme"] == "forest-glass"
    assert theme_store.list_themes() == [
        {
            "id": "forest-glass",
            "label": "Forest Glass",
            "slots": slots,
        }
    ]


def test_settings_sync_selects_imported_custom_theme_when_settings_are_newer(
    tmp_path, monkeypatch
):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()
    theme_store = modules["theme_store"]
    user_settings = modules["user_settings"]
    slots = {
        "c1Light": "#d6f5dd",
        "c1Med": "#3c8f5a",
        "c1Dark": "#173927",
        "c2Light": "#f4efc7",
        "c2Med": "#c6a93e",
        "c2Dark": "#5e4b12",
        "veryLight": "#fcfff8",
        "veryDark": "#08110a",
    }
    user_settings.save_settings(
        {
            "theme": "light",
            "visual_theme": "spring",
            "updated_at": "2027-01-01T00:00:00+00:00",
        }
    )
    snapshot = {
        "sections": {
            "settings": {
                "sync_id": "settings",
                "updated_at": "2026-04-01T00:00:00+00:00",
                "data": {
                    "theme": "dark",
                    "visual_theme": "forest-glass",
                },
                "themes": [
                    {
                        "sync_id": "theme:forest-glass",
                        "id": "forest-glass",
                        "label": "Forest Glass",
                        "slots": slots,
                        "updated_at": "2026-04-01T00:00:00+00:00",
                    }
                ],
            }
        }
    }

    result = service.merge_snapshot(snapshot)

    assert result["sections"]["settings"]["applied"] == 2
    assert result["sections"]["settings"]["skipped"] == 1
    assert user_settings.load_settings()["theme"] == "light"
    assert user_settings.load_settings()["visual_theme"] == "forest-glass"
    assert theme_store.list_themes()[0]["id"] == "forest-glass"


def test_remote_client_requires_repair_instead_of_re_registering_stale_pair():
    modules = _load_modules()
    sync_module = modules["sync_module"]
    RemoteFloatClient = modules["RemoteFloatClient"]

    class FakeResponse:
        def __init__(self, payload=None, *, status_code=200, text=""):
            self._payload = payload or {}
            self.status_code = status_code
            self.text = text

        def raise_for_status(self):
            if self.status_code < 400:
                return None
            exc = sync_module.requests.HTTPError(f"{self.status_code} error")
            exc.response = self
            raise exc

        def json(self):
            return self._payload

    class FakeSession:
        def __init__(self):
            self.calls = []

        def request(
            self,
            method,
            url,
            json=None,
            headers=None,
            timeout=None,
            allow_redirects=None,
        ):
            self.calls.append(
                {
                    "method": method,
                    "path": url.rsplit("/api/", 1)[-1],
                    "json": json or {},
                    "headers": headers or {},
                    "timeout": timeout,
                }
            )
            path = self.calls[-1]["path"]
            token_calls = [
                call for call in self.calls if call["path"] == "devices/token"
            ]
            if path == "devices/token" and len(token_calls) == 1:
                return FakeResponse(
                    status_code=403,
                    text='{"detail":"Device proof required for token issuance"}',
                )
            if path == "devices/register":
                return FakeResponse({"device": {"id": "fresh-device"}})
            if path == "devices/token":
                return FakeResponse({"token": "fresh-token"})
            if path == "sync/manifest":
                return FakeResponse({"sections": {}})
            raise AssertionError(f"unexpected request path: {path}")

    session = FakeSession()
    client = RemoteFloatClient(
        "http://pear.local:54089",
        session=session,
        paired_device={
            "id": "peer-1",
            "remote_device_id": "stale-device",
            "public_key": "",
            "scopes": ["sync", "files"],
        },
        device_name="Cherry",
    )

    with pytest.raises(ValueError, match="Pair this device again"):
        client.get_manifest(["settings"])

    assert session.calls == []


@pytest.mark.parametrize(
    "remote_url",
    [
        "http://127.0.0.1:5000",
        "http://169.254.169.254/latest/meta-data",
        "http://8.8.8.8:5000",
        "ftp://192.168.1.25/resource",
        "http://user:secret@192.168.1.25:5000",
        "http://192.168.1.25:5000?redirect=http://169.254.169.254",
    ],
)
def test_remote_sync_url_rejects_non_private_or_credentialed_targets(remote_url):
    sync_module = _load_modules()["sync_module"]

    with pytest.raises(ValueError):
        sync_module._resolve_remote_urls(remote_url)


def test_remote_sync_url_allows_private_and_tailnet_targets():
    sync_module = _load_modules()["sync_module"]

    assert sync_module._resolve_remote_urls("192.168.1.25:5000")["api_base"] == (
        "http://192.168.1.25:5000/api"
    )
    assert (
        sync_module._resolve_remote_urls("http://100.100.10.20:5000")["instance_base"]
        == "http://100.100.10.20:5000"
    )


def test_remote_client_enriches_legacy_settings_snapshot_with_themes():
    modules = _load_modules()
    sync_module = modules["sync_module"]
    RemoteFloatClient = modules["RemoteFloatClient"]
    slots = {
        "c1Light": "#a8f5ab",
        "c1Med": "#4aed1d",
        "c1Dark": "#05420f",
        "c2Light": "#f8f7f7",
        "c2Med": "#f7bff3",
        "c2Dark": "#d24ba1",
        "veryLight": "#e8f5f7",
        "veryDark": "#1e3038",
    }

    class FakeResponse:
        def __init__(self, payload=None, *, status_code=200, text=""):
            self._payload = payload or {}
            self.status_code = status_code
            self.text = text

        def raise_for_status(self):
            if self.status_code < 400:
                return None
            exc = sync_module.requests.HTTPError(f"{self.status_code} error")
            exc.response = self
            raise exc

        def json(self):
            return self._payload

    class FakeSession:
        def __init__(self):
            self.calls = []

        def request(
            self,
            method,
            url,
            json=None,
            headers=None,
            timeout=None,
            allow_redirects=None,
        ):
            path = url.rsplit("/api/", 1)[-1]
            self.calls.append({"method": method, "path": path, "json": json or {}})
            if path == "devices/token":
                return FakeResponse({"token": "sync-token"})
            if path == "devices/remote-device":
                return FakeResponse({"device": {"id": "remote-device"}})
            if path == "sync/export":
                return FakeResponse(
                    {
                        "sections": {
                            "settings": {
                                "sync_id": "settings",
                                "updated_at": "2026-04-15T04:43:39+00:00",
                                "data": {"theme": "light"},
                            }
                        }
                    }
                )
            if path == "user-settings":
                return FakeResponse({"visual_theme": "blossom", "theme": "light"})
            if path == "themes":
                return FakeResponse(
                    {
                        "themes": [
                            {
                                "id": "blossom",
                                "label": "Blossom",
                                "slots": slots,
                            }
                        ]
                    }
                )
            raise AssertionError(f"unexpected request path: {path}")

    client = RemoteFloatClient(
        "http://pear.local:54089",
        session=FakeSession(),
        paired_device={
            "remote_device_id": "remote-device",
            "public_key": "pk-local",
            "scopes": ["sync"],
        },
        device_name="Cherry",
    )

    snapshot = client.export_snapshot(["settings"])

    settings = snapshot["sections"]["settings"]
    assert settings["data"]["visual_theme"] == "blossom"
    assert settings["themes"] == [
        {
            "sync_id": "theme:blossom",
            "id": "blossom",
            "label": "Blossom",
            "slots": slots,
            "updated_at": "2026-04-15T04:43:39+00:00",
        }
    ]


def test_merge_snapshot_links_synced_state_to_source_namespace(tmp_path, monkeypatch):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()
    conversation_store = modules["conversation_store"]
    memory_store = modules["memory_store"]
    calendar_store = modules["calendar_store"]
    KnowledgeStore = modules["KnowledgeStore"]
    GraphStore = modules["GraphStore"]
    blob_store = modules["blob_store"]
    sync_module = modules["sync_module"]

    attachment_bytes = b"linked attachment"
    content_hash = hashlib.sha256(attachment_bytes).hexdigest()

    snapshot = {
        "instance": {
            "hostname": "laptop-host",
            "source_namespace": "laptop",
        },
        "sections": {
            "conversations": [
                {
                    "sync_id": "conv-1",
                    "name": "projects/alpha",
                    "metadata": {
                        "id": "conv-1",
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "updated_at": "2026-02-01T00:00:00+00:00",
                        "display_name": "Alpha Project",
                    },
                    "messages": [{"role": "user", "content": "linked copy"}],
                }
            ],
            "memories": [
                {
                    "key": "alias",
                    "payload": {
                        "value": "remote",
                        "updated_at": 100.0,
                    },
                }
            ],
            "knowledge": [
                {
                    "knowledge_id": "doc-1",
                    "source": "notes/doc-1",
                    "kind": "document",
                    "title": "Doc 1",
                    "text": "canonical text",
                    "summary_text": "canonical summary",
                    "metadata": {"source": "notes/doc-1", "kind": "document"},
                    "version": 3,
                    "created_at": 10.0,
                    "updated_at": 200.0,
                    "chunks": [
                        {
                            "chunk_id": "doc-1",
                            "chunk_index": 0,
                            "chunk_count": 1,
                            "source": "notes/doc-1",
                            "root_source": "notes/doc-1",
                            "text": "canonical text",
                            "metadata": {"source": "notes/doc-1"},
                            "embedding_model": None,
                            "created_at": 10.0,
                            "updated_at": 200.0,
                        }
                    ],
                }
            ],
            "graph": {
                "nodes": [
                    {
                        "node_id": "node-1",
                        "node_kind": "entity",
                        "node_type": "person",
                        "canonical_name": "Kai",
                        "summary_text": "A synced node",
                        "attributes": {"role": "owner"},
                        "status": "active",
                        "created_at": 5.0,
                        "updated_at": 200.0,
                    }
                ],
                "claims": [
                    {
                        "claim_id": "claim-1",
                        "claim_type": "relation",
                        "predicate": "owns",
                        "status": "active",
                        "epistemic_status": "observed",
                        "confidence": 0.9,
                        "valid_from": None,
                        "valid_to": None,
                        "occurred_at": None,
                        "source_kind": "conversation",
                        "source_ref": "projects/alpha",
                        "metadata": {"source": "sync"},
                        "created_at": 5.0,
                        "updated_at": 200.0,
                        "roles": [
                            {
                                "role_name": "subject",
                                "ordinal": 0,
                                "node_id": "node-1",
                                "value": None,
                                "metadata": {},
                            }
                        ],
                    }
                ],
            },
            "attachments": [
                {
                    "content_hash": content_hash,
                    "filename": "note.txt",
                    "updated_at": 200.0,
                    "metadata": {
                        "filename": "note.txt",
                        "origin": "upload",
                        "relative_path": f"uploads/{content_hash}/note.txt",
                        "uploaded_at": "2026-02-01T00:00:00+00:00",
                    },
                    "content_b64": base64.b64encode(attachment_bytes).decode("ascii"),
                }
            ],
            "calendar": [
                {
                    "event_id": "evt-1",
                    "payload": {
                        "title": "Review",
                        "updated_at": "2026-02-01T00:00:00+00:00",
                    },
                }
            ],
        },
    }

    result = service.merge_snapshot(
        snapshot,
        link_to_source=True,
        source_namespace="laptop",
        source_label="Laptop",
    )

    assert result["effective_namespace"] == "laptop"
    assert (
        conversation_store.load_conversation("laptop/projects/alpha")[0]["content"]
        == "linked copy"
    )
    assert (
        conversation_store.get_metadata("laptop/projects/alpha")["id"]
        == "laptop__conv-1"
    )
    assert memory_store.load()["laptop__alias"]["value"] == "remote"

    doc = KnowledgeStore().trace("laptop__doc-1")
    assert doc is not None
    assert doc["metadata"]["source"] == "laptop/notes/doc-1"

    graph = GraphStore()
    node = graph.get_node("laptop__node-1")
    claim = graph.get_claim("laptop__claim-1")
    assert node is not None
    assert node["attributes"]["source_sync_namespace"] == "laptop"
    assert claim is not None
    assert claim["roles"][0]["node_id"] == "laptop__node-1"

    attachment_meta = sync_module._load_attachment_meta(content_hash)
    assert attachment_meta["relative_path"] == f"laptop/uploads/{content_hash}/note.txt"
    attachment_target = blob_store.resolve_managed_path(
        attachment_meta["relative_path"]
    )
    assert attachment_target is not None
    assert attachment_target.read_bytes() == attachment_bytes

    assert calendar_store.load_event("laptop__evt-1")["title"] == "Review"


def test_merge_snapshot_writes_attachment_knowledge_and_graph(tmp_path, monkeypatch):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()
    KnowledgeStore = modules["KnowledgeStore"]
    GraphStore = modules["GraphStore"]
    blob_store = modules["blob_store"]

    attachment_bytes = b"synced attachment"
    content_hash = hashlib.sha256(attachment_bytes).hexdigest()

    snapshot = {
        "sections": {
            "attachments": [
                {
                    "content_hash": content_hash,
                    "filename": "note.txt",
                    "updated_at": 200.0,
                    "metadata": {
                        "filename": "note.txt",
                        "origin": "upload",
                        "uploaded_at": "2026-02-01T00:00:00+00:00",
                    },
                    "content_b64": base64.b64encode(attachment_bytes).decode("ascii"),
                }
            ],
            "knowledge": [
                {
                    "knowledge_id": "doc-1",
                    "source": "notes/doc-1",
                    "kind": "document",
                    "title": "Doc 1",
                    "text": "canonical text",
                    "summary_text": "canonical summary",
                    "metadata": {"source": "notes/doc-1", "kind": "document"},
                    "version": 3,
                    "created_at": 10.0,
                    "updated_at": 200.0,
                    "chunks": [
                        {
                            "chunk_id": "doc-1",
                            "chunk_index": 0,
                            "chunk_count": 1,
                            "source": "notes/doc-1",
                            "root_source": "notes/doc-1",
                            "text": "canonical text",
                            "metadata": {"source": "notes/doc-1"},
                            "embedding_model": None,
                            "created_at": 10.0,
                            "updated_at": 200.0,
                        }
                    ],
                }
            ],
            "graph": {
                "nodes": [
                    {
                        "node_id": "node-1",
                        "node_kind": "entity",
                        "node_type": "person",
                        "canonical_name": "Kai",
                        "summary_text": "A synced node",
                        "attributes": {"role": "owner"},
                        "status": "active",
                        "created_at": 5.0,
                        "updated_at": 200.0,
                    }
                ],
                "claims": [
                    {
                        "claim_id": "claim-1",
                        "claim_type": "relation",
                        "predicate": "owns",
                        "status": "active",
                        "epistemic_status": "observed",
                        "confidence": 0.9,
                        "valid_from": None,
                        "valid_to": None,
                        "occurred_at": None,
                        "source_kind": "conversation",
                        "source_ref": "conv-1",
                        "metadata": {"source": "sync"},
                        "created_at": 5.0,
                        "updated_at": 200.0,
                        "roles": [
                            {
                                "role_name": "subject",
                                "ordinal": 0,
                                "node_id": "node-1",
                                "value": None,
                                "metadata": {},
                            }
                        ],
                    }
                ],
            },
        }
    }

    result = service.merge_snapshot(snapshot)

    assert result["sections"]["attachments"]["applied"] == 1
    assert result["sections"]["attachments"]["applied_ids"] == [content_hash]
    attachment_meta = modules["sync_module"]._load_attachment_meta(content_hash)
    assert attachment_meta["source_sync_label"] == "remote"
    assert (
        attachment_meta["relative_path"]
        == f"sync/remote/workspace/attachments/{content_hash}/note.txt"
    )
    target = blob_store.resolve_managed_path(attachment_meta["relative_path"])
    assert target is not None
    assert target.read_bytes() == attachment_bytes

    doc = KnowledgeStore().trace("doc-1")
    assert doc is not None
    assert doc["text"] == "canonical text"
    assert doc["metadata"]["source"] == "notes/doc-1"

    graph = GraphStore()
    node = graph.get_node("node-1")
    claim = graph.get_claim("claim-1")
    assert node is not None
    assert node["canonical_name"] == "Kai"
    assert claim is not None
    assert claim["predicate"] == "owns"
    assert claim["roles"][0]["node_id"] == "node-1"


def test_merge_snapshot_root_pull_stays_visible_in_root_manifest(tmp_path, monkeypatch):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()
    conversation_store = modules["conversation_store"]
    memory_store = modules["memory_store"]
    calendar_store = modules["calendar_store"]
    KnowledgeStore = modules["KnowledgeStore"]
    blob_store = modules["blob_store"]
    sync_module = modules["sync_module"]

    attachment_bytes = b"root synced attachment"
    content_hash = hashlib.sha256(attachment_bytes).hexdigest()

    snapshot = {
        "instance": {
            "display_name": "Pear",
            "source_namespace": "Pear",
        },
        "sections": {
            "conversations": [
                {
                    "sync_id": "conv-pear",
                    "name": "notes/pear-root",
                    "metadata": {
                        "id": "conv-pear",
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "updated_at": "2026-03-01T00:00:00+00:00",
                        "display_name": "Pear root conversation",
                    },
                    "messages": [{"role": "user", "content": "pulled copy"}],
                }
            ],
            "memories": [
                {
                    "key": "pear-note",
                    "payload": {
                        "value": "remote",
                        "updated_at": 100.0,
                    },
                }
            ],
            "knowledge": [
                {
                    "knowledge_id": "pear-doc",
                    "source": "notes/pear-doc",
                    "kind": "document",
                    "title": "Pear doc",
                    "text": "synced text",
                    "summary_text": "synced summary",
                    "metadata": {"source": "notes/pear-doc", "kind": "document"},
                    "version": 1,
                    "created_at": 10.0,
                    "updated_at": 200.0,
                    "chunks": [
                        {
                            "chunk_id": "pear-doc",
                            "chunk_index": 0,
                            "chunk_count": 1,
                            "source": "notes/pear-doc",
                            "root_source": "notes/pear-doc",
                            "text": "synced text",
                            "metadata": {"source": "notes/pear-doc"},
                            "embedding_model": None,
                            "created_at": 10.0,
                            "updated_at": 200.0,
                        }
                    ],
                }
            ],
            "attachments": [
                {
                    "content_hash": content_hash,
                    "filename": "note.txt",
                    "updated_at": 200.0,
                    "metadata": {
                        "filename": "note.txt",
                        "origin": "upload",
                        "uploaded_at": "2026-02-01T00:00:00+00:00",
                    },
                    "content_b64": base64.b64encode(attachment_bytes).decode("ascii"),
                }
            ],
            "calendar": [
                {
                    "event_id": "evt-pear",
                    "payload": {
                        "title": "Review",
                        "updated_at": "2026-03-01T00:00:00+00:00",
                    },
                }
            ],
        },
    }

    result = service.merge_snapshot(snapshot)

    assert result["effective_namespace"] is None
    conversation_meta = conversation_store.get_metadata("notes/pear-root")
    assert conversation_store.load_conversation("notes/pear-root")[0]["content"] == (
        "pulled copy"
    )
    assert conversation_meta["source_sync_label"] == "Pear"
    assert not conversation_meta.get("source_sync_namespace")

    memories = memory_store.load()
    assert memories["pear-note"]["value"] == "remote"
    assert memories["pear-note"]["source_sync_label"] == "Pear"
    assert not memories["pear-note"].get("source_sync_namespace")

    doc = KnowledgeStore().trace("pear-doc")
    assert doc is not None
    assert doc["metadata"]["source_sync_label"] == "Pear"
    assert not doc["metadata"].get("source_sync_namespace")

    attachment_meta = sync_module._load_attachment_meta(content_hash)
    assert attachment_meta["source_sync_label"] == "Pear"
    assert not attachment_meta.get("source_sync_namespace")
    attachment_path = blob_store.resolve_managed_path(attachment_meta["relative_path"])
    assert attachment_path is not None
    assert attachment_path.read_bytes() == attachment_bytes
    attachment_meta["source_sync_namespace"] = "Pear"
    sync_module._write_attachment_meta(content_hash, attachment_meta)

    event = calendar_store.load_event("evt-pear")
    assert event["source_sync_label"] == "Pear"
    assert not event.get("source_sync_namespace")

    root_manifest = service.build_manifest(
        ["conversations", "memories", "knowledge", "attachments", "calendar"],
        workspace_ids=["root"],
    )

    conversation_ids = [
        item["sync_id"] for item in root_manifest["sections"]["conversations"]["items"]
    ]
    memory_ids = [
        item["sync_id"] for item in root_manifest["sections"]["memories"]["items"]
    ]
    knowledge_ids = [
        item["sync_id"] for item in root_manifest["sections"]["knowledge"]["items"]
    ]
    attachment_ids = [
        item["sync_id"] for item in root_manifest["sections"]["attachments"]["items"]
    ]
    calendar_ids = [
        item["sync_id"] for item in root_manifest["sections"]["calendar"]["items"]
    ]

    assert "conv-pear" in conversation_ids
    assert "pear-note" in memory_ids
    assert "pear-doc" in knowledge_ids
    assert content_hash in attachment_ids
    assert "evt-pear" in calendar_ids
    assert (
        root_manifest["sections"]["attachments"]["items"][0]["source_sync_namespace"]
        == ""
    )


def test_compare_manifests_counts_local_and_remote_changes(tmp_path, monkeypatch):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()

    local_manifest = {
        "sections": {
            "conversations": {
                "items": [
                    {
                        "sync_id": "a",
                        "updated_at": 10.0,
                        "display_name": "Alpha local",
                        "name": "notes/alpha",
                        "message_count": 4,
                    },
                    {
                        "sync_id": "b",
                        "updated_at": 30.0,
                        "display_name": "Beta local",
                        "name": "notes/beta",
                        "message_count": 2,
                    },
                ]
            }
        }
    }
    remote_manifest = {
        "sections": {
            "conversations": {
                "items": [
                    {
                        "sync_id": "a",
                        "original_sync_id": "conv-a",
                        "updated_at": 20.0,
                        "display_name": "Alpha remote",
                        "name": "pear/alpha",
                        "message_count": 5,
                    },
                    {
                        "sync_id": "c",
                        "updated_at": 5.0,
                        "display_name": "Gamma remote",
                        "name": "pear/gamma",
                        "message_count": 1,
                    },
                ]
            }
        }
    }

    comparison = service.compare_manifests(
        local_manifest, remote_manifest, ["conversations"]
    )

    assert len(comparison) == 1
    section = comparison[0]
    assert section["key"] == "conversations"
    assert section["label"] == "Conversations"
    assert section["local_count"] == 2
    assert section["remote_count"] == 2
    assert section["only_local"] == 1
    assert section["only_remote"] == 1
    assert section["local_newer"] == 0
    assert section["remote_newer"] == 1
    assert section["identical"] == 0
    assert section["change_count"] == 3
    assert section["selected_by_default"] is True
    assert section["items"] == section["all_items"]
    assert section["items"][0]["selection_id"] == "conv-a"
    assert section["items"][0]["detail"] == "pear/alpha | 5 messages"
    assert section["items"][1]["status"] == "only_local"
    assert section["items"][1]["detail"] == "notes/beta | 2 messages"
    assert section["items"][2]["status"] == "only_remote"
    assert section["items"][2]["label"] == "Gamma remote"


def test_compare_manifests_uses_checkpoint_for_deletes_and_conflicts(
    tmp_path, monkeypatch
):
    from app.utils import sync_checkpoint_store

    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()
    base_items = [
        {"sync_id": "remote-delete", "updated_at": 10.0, "label": "Delete"},
        {"sync_id": "delete-conflict", "updated_at": 10.0, "label": "Conflict"},
        {"sync_id": "both-edit", "updated_at": 10.0, "label": "Both"},
        {"sync_id": "remote-edit", "updated_at": 10.0, "label": "Remote edit"},
    ]
    base_manifest = {"sections": {"conversations": {"items": base_items}}}
    baseline = sync_checkpoint_store.build_view_baseline(
        local_manifest=base_manifest,
        remote_manifest=base_manifest,
        item_selections={
            "conversations": [
                "remote-delete",
                "delete-conflict",
                "both-edit",
                "remote-edit",
                "local-create",
            ]
        },
    )
    local_manifest = {
        "sections": {
            "conversations": {
                "items": [
                    base_items[0],
                    {
                        **base_items[1],
                        "updated_at": 20.0,
                        "label": "Edited before remote delete",
                    },
                    {
                        **base_items[2],
                        "updated_at": 20.0,
                        "label": "Local edit",
                    },
                    base_items[3],
                    {
                        "sync_id": "local-create",
                        "updated_at": 20.0,
                        "label": "Created here",
                    },
                ]
            }
        }
    }
    remote_manifest = {
        "sections": {
            "conversations": {
                "items": [
                    {
                        **base_items[2],
                        "updated_at": 30.0,
                        "label": "Remote edit",
                    },
                    {
                        **base_items[3],
                        "updated_at": 30.0,
                        "label": "Changed remotely",
                    },
                ]
            }
        }
    }

    section = service.compare_manifests(
        local_manifest,
        remote_manifest,
        ["conversations"],
        baseline=baseline,
    )[0]
    statuses = {item["selection_id"]: item["status"] for item in section["all_items"]}

    assert statuses == {
        "both-edit": "conflict",
        "delete-conflict": "delete_conflict",
        "local-create": "local_new",
        "remote-delete": "remote_deleted",
        "remote-edit": "remote_newer",
    }
    assert section["conflicts"] == 2
    assert section["local_new"] == 1
    assert section["remote_deleted"] == 1
    assert section["remote_newer"] == 1


def test_compare_manifests_treats_new_item_after_complete_observation_as_created(
    tmp_path, monkeypatch
):
    from app.utils import sync_checkpoint_store

    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()
    empty = {"sections": {"conversations": {"items": []}}}
    baseline = sync_checkpoint_store.build_observed_view_baseline(
        local_manifest=empty,
        remote_manifest=empty,
        sections=["conversations"],
    )
    local_manifest = {
        "sections": {
            "conversations": {
                "items": [
                    {
                        "sync_id": "created-downstream",
                        "updated_at": 20.0,
                        "label": "Created downstream",
                    }
                ]
            }
        }
    }

    section = service.compare_manifests(
        local_manifest,
        empty,
        ["conversations"],
        baseline=baseline,
    )[0]

    assert section["local_new"] == 1
    assert section["all_items"][0]["status"] == "local_new"
    assert section["all_items"][0]["interpretation"] == "local_created_since_sync"


def test_namespace_manifest_preserves_original_sync_id(tmp_path, monkeypatch):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()

    manifest = {
        "sections": {
            "conversations": {
                "items": [
                    {
                        "sync_id": "conv-1",
                        "name": "notes/demo",
                        "display_name": "Demo",
                        "updated_at": 10.0,
                    }
                ]
            }
        }
    }

    namespaced = service.namespace_manifest(manifest, namespace="Pear")
    item = namespaced["sections"]["conversations"]["items"][0]

    assert item["sync_id"] == "Pear__conv-1"
    assert item["original_sync_id"] == "conv-1"
    assert item["name"] == "Pear/notes/demo"


def test_filter_snapshot_by_item_selections_keeps_selected_records(
    tmp_path, monkeypatch
):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()

    snapshot = {
        "sections": {
            "conversations": [
                {"sync_id": "conv-1", "name": "notes/one"},
                {"sync_id": "conv-2", "name": "notes/two"},
            ],
            "graph": {
                "nodes": [
                    {"node_id": "node-1", "canonical_name": "Pear"},
                    {"node_id": "node-2", "canonical_name": "Plum"},
                ],
                "claims": [
                    {
                        "claim_id": "claim-1",
                        "predicate": "owns",
                        "roles": [{"node_id": "node-1"}],
                    },
                    {
                        "claim_id": "claim-2",
                        "predicate": "uses",
                        "roles": [{"node_id": "node-2"}],
                    },
                ],
            },
            "settings": {
                "sync_id": "settings",
                "updated_at": "2026-03-25T00:00:00+00:00",
                "data": {"theme": "dark"},
            },
        }
    }

    filtered = service.filter_snapshot_by_item_selections(
        snapshot,
        {
            "conversations": ["conv-2", "conv-deleted"],
            "graph": ["claim:claim-1"],
            "settings": ["settings"],
        },
    )

    assert [record["sync_id"] for record in filtered["sections"]["conversations"]] == [
        "conv-2"
    ]
    assert [claim["claim_id"] for claim in filtered["sections"]["graph"]["claims"]] == [
        "claim-1"
    ]
    assert [node["node_id"] for node in filtered["sections"]["graph"]["nodes"]] == [
        "node-1"
    ]
    assert filtered["sections"]["settings"]["sync_id"] == "settings"
    assert filtered["deletions"]["conversations"] == ["conv-deleted"]


def test_merge_snapshot_applies_selected_deletions(tmp_path, monkeypatch):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()
    write_conversation = modules["_write_conversation_snapshot"]
    conversation_store = modules["conversation_store"]
    memory_store = modules["memory_store"]
    calendar_store = modules["calendar_store"]
    knowledge_store = modules["KnowledgeStore"]()

    write_conversation(
        name="notes/remove-me",
        messages=[{"role": "user", "content": "remove"}],
        metadata={"id": "conv-remove", "updated_at": "2026-03-25T00:00:00+00:00"},
    )
    memory_store.save({"memory-remove": {"value": "remove", "updated_at": 1.0}})
    knowledge_store.upsert_document(
        source="doc/remove",
        text="remove",
        metadata={"updated_at": 1.0},
        chunk_texts=["remove"],
        knowledge_id="knowledge-remove",
    )
    calendar_store.save_event(
        "event-remove",
        {"title": "Remove", "updated_at": "2026-03-25T00:00:00+00:00"},
    )

    result = service.merge_snapshot(
        {
            "sections": {
                "conversations": [],
                "memories": [],
                "knowledge": [],
                "calendar": [],
            },
            "deletions": {
                "conversations": ["conv-remove"],
                "memories": ["memory-remove"],
                "knowledge": ["knowledge-remove"],
                "calendar": ["event-remove"],
            },
        }
    )

    assert result["sections"]["conversations"]["deleted"] == 1
    assert result["sections"]["memories"]["deleted"] == 1
    assert result["sections"]["knowledge"]["deleted"] == 1
    assert result["sections"]["calendar"]["deleted"] == 1
    assert "notes/remove-me" not in {
        item["name"] for item in conversation_store.list_conversations()
    }
    assert "memory-remove" not in memory_store.load()
    assert knowledge_store.get_item("knowledge-remove") is None
    assert "event-remove" not in calendar_store.list_events()


def test_merge_snapshot_fails_closed_for_active_calendar_deletion(
    tmp_path, monkeypatch
):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()
    calendar_store = modules["calendar_store"]
    event_id = "event-active"
    calendar_store.save_event(
        event_id,
        {
            "id": event_id,
            "title": "Keep active",
            "status": "running",
            "actions": [
                {
                    "id": "action-active",
                    "kind": "prompt",
                    "status": "running",
                    "run_id": "run-active",
                }
            ],
        },
    )

    result = service.merge_snapshot(
        {
            "sections": {"calendar": []},
            "deletions": {"calendar": [event_id]},
        }
    )

    assert result["sections"]["calendar"]["deleted"] == 0
    assert result["sections"]["calendar"]["delete_failed_ids"] == [event_id]
    assert calendar_store.load_event(event_id)["actions"][0]["run_id"] == "run-active"


def test_merge_snapshot_checks_activity_ledger_before_calendar_deletion(
    tmp_path, monkeypatch
):
    modules = _configure_paths(tmp_path, monkeypatch)
    custom_store_path = tmp_path / "custom-ledger" / "activity.sqlite3"
    monkeypatch.setenv("FLOAT_WORK_RUN_STORE", str(custom_store_path))
    service = modules["InstanceSyncService"]()
    calendar_store = modules["calendar_store"]
    event_id = "event-ledger-active"
    calendar_store.save_event(
        event_id,
        {
            "id": event_id,
            "title": "Keep ledger-backed active event",
            "status": "acknowledged",
            "actions": [
                {
                    "id": "action-active",
                    "kind": "prompt",
                    "status": "acknowledged",
                }
            ],
        },
    )
    work_runs = modules["sync_module"].WorkRunStore(
        modules["sync_module"].app_config.load_config()
    )
    work_runs.upsert_run(
        {
            "id": "receipt-ledger-active",
            "run_id": "run-ledger-active",
            "event_id": event_id,
            "action_id": "action-active",
            "action_kind": "prompt",
            "status": "running",
            "started_at": 1.0,
        },
        source="calendar",
    )

    result = service.merge_snapshot(
        {
            "sections": {"calendar": []},
            "deletions": {"calendar": [event_id]},
        }
    )

    assert result["sections"]["calendar"]["deleted"] == 0
    assert result["sections"]["calendar"]["delete_failed_ids"] == [event_id]
    assert calendar_store.load_event(event_id)["id"] == event_id
    assert work_runs.has_active_run(event_id=event_id) is True
    assert work_runs.path == custom_store_path.resolve()


def test_merge_snapshot_preserves_terminal_calendar_history_before_deletion(
    tmp_path, monkeypatch
):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()
    calendar_store = modules["calendar_store"]
    event_id = "event-terminal-history"
    receipt_id = "receipt-terminal-history"
    calendar_store.save_event(
        event_id,
        {
            "id": event_id,
            "title": "Preserve terminal history",
            "status": "complete",
            "actions": [
                {
                    "id": "action-terminal",
                    "kind": "prompt",
                    "status": "complete",
                }
            ],
            "run_history": [
                {
                    "id": receipt_id,
                    "run_id": "run-terminal-history",
                    "action_id": "action-terminal",
                    "action_kind": "prompt",
                    "status": "complete",
                    "started_at": 1.0,
                    "finished_at": 2.0,
                }
            ],
        },
    )

    result = service.merge_snapshot(
        {
            "sections": {"calendar": []},
            "deletions": {"calendar": [event_id]},
        }
    )

    work_runs = modules["sync_module"].WorkRunStore(
        modules["sync_module"].app_config.load_config()
    )
    assert result["sections"]["calendar"]["deleted"] == 1
    assert calendar_store.load_event(event_id) == {}
    assert work_runs.get_run(receipt_id)["status"] == "complete"


def test_merge_snapshot_cannot_regress_active_ledger_from_stale_terminal_history(
    tmp_path, monkeypatch
):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()
    calendar_store = modules["calendar_store"]
    event_id = "event-stale-terminal-history"
    receipt_id = "receipt-stale-terminal-history"
    calendar_store.save_event(
        event_id,
        {
            "id": event_id,
            "title": "Do not trust stale terminal history",
            "status": "acknowledged",
            "actions": [
                {
                    "id": "action-stale",
                    "kind": "prompt",
                    "status": "acknowledged",
                }
            ],
            "run_history": [
                {
                    "id": receipt_id,
                    "run_id": "run-stale-terminal-history",
                    "action_id": "action-stale",
                    "action_kind": "prompt",
                    "status": "complete",
                    "started_at": 1.0,
                    "finished_at": 2.0,
                }
            ],
        },
    )
    work_runs = modules["sync_module"].WorkRunStore(
        modules["sync_module"].app_config.load_config()
    )
    work_runs.upsert_run(
        {
            "id": receipt_id,
            "run_id": "run-stale-terminal-history",
            "event_id": event_id,
            "action_id": "action-stale",
            "action_kind": "prompt",
            "status": "running",
            "started_at": 1.0,
        },
        source="calendar",
    )

    result = service.merge_snapshot(
        {
            "sections": {"calendar": []},
            "deletions": {"calendar": [event_id]},
        }
    )

    assert result["sections"]["calendar"]["deleted"] == 0
    assert result["sections"]["calendar"]["delete_failed_ids"] == [event_id]
    assert calendar_store.load_event(event_id)["id"] == event_id
    assert work_runs.get_run(receipt_id)["recovery_state"] == "active"


def test_build_manifest_filters_by_workspace_selection(tmp_path, monkeypatch):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()
    write_conversation = modules["_write_conversation_snapshot"]
    user_settings = modules["user_settings"]

    user_settings.save_settings(
        {
            "workspace_profiles": [
                {
                    "id": "work",
                    "name": "Work",
                    "slug": "work",
                    "namespace": "work",
                    "root_path": "data/files/workspace/work",
                }
            ],
            "active_workspace_id": "root",
            "sync_selected_workspace_ids": ["root", "work"],
        }
    )
    write_conversation(
        name="notes/root",
        messages=[{"role": "user", "content": "root"}],
        metadata={
            "id": "conv-root",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-02T00:00:00+00:00",
            "display_name": "Root conversation",
        },
    )
    write_conversation(
        name="work/notes/project",
        messages=[{"role": "user", "content": "work"}],
        metadata={
            "id": "work__conv-1",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-03T00:00:00+00:00",
            "display_name": "Work conversation",
            "source_sync_namespace": "work",
        },
    )

    root_manifest = service.build_manifest(["conversations"], workspace_ids=["root"])
    work_manifest = service.build_manifest(["conversations"], workspace_ids=["work"])

    assert root_manifest["workspace_selection"]["workspace_ids"] == ["root"]
    assert work_manifest["workspace_selection"]["workspace_ids"] == ["work"]
    assert [
        item["sync_id"] for item in root_manifest["sections"]["conversations"]["items"]
    ] == ["conv-root"]
    assert [
        item["sync_id"] for item in work_manifest["sections"]["conversations"]["items"]
    ] == ["work__conv-1"]


def test_build_snapshot_filters_by_workspace_selection(tmp_path, monkeypatch):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()
    write_conversation = modules["_write_conversation_snapshot"]
    user_settings = modules["user_settings"]

    user_settings.save_settings(
        {
            "workspace_profiles": [
                {
                    "id": "personal",
                    "name": "Personal",
                    "slug": "personal",
                    "namespace": "personal",
                    "root_path": "data/files/workspace/personal",
                }
            ],
            "active_workspace_id": "root",
            "sync_selected_workspace_ids": ["root", "personal"],
        }
    )
    write_conversation(
        name="notes/root",
        messages=[{"role": "user", "content": "root"}],
        metadata={
            "id": "conv-root",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-02T00:00:00+00:00",
            "display_name": "Root conversation",
        },
    )
    write_conversation(
        name="personal/journal",
        messages=[{"role": "user", "content": "personal"}],
        metadata={
            "id": "personal__conv-2",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-04T00:00:00+00:00",
            "display_name": "Personal conversation",
            "source_sync_namespace": "personal",
        },
    )

    snapshot = service.build_snapshot(["conversations"], workspace_ids=["personal"])

    assert snapshot["workspace_selection"]["workspace_ids"] == ["personal"]
    conversations = snapshot["sections"]["conversations"]
    assert len(conversations) == 1
    assert conversations[0]["metadata"]["id"] == "personal__conv-2"


def test_build_manifest_applies_workspace_privacy_rules(tmp_path, monkeypatch):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()
    write_conversation = modules["_write_conversation_snapshot"]
    user_settings = modules["user_settings"]

    user_settings.save_settings(
        {
            "workspace_profiles": [
                {
                    "id": "root",
                    "name": "Main workspace",
                    "privacy_mode": "default",
                    "private_patterns": ["notes/private/*"],
                },
                {
                    "id": "vault",
                    "name": "Vault",
                    "slug": "vault",
                    "namespace": "vault",
                    "root_path": "data/files/workspace/vault",
                    "privacy_mode": "protected",
                },
            ],
            "active_workspace_id": "root",
            "sync_selected_workspace_ids": ["root", "vault"],
        }
    )
    write_conversation(
        name="notes/shared/alpha",
        messages=[{"role": "user", "content": "share me"}],
        metadata={
            "id": "conv-root-visible",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-02T00:00:00+00:00",
            "display_name": "Visible conversation",
        },
    )
    write_conversation(
        name="notes/private/alpha",
        messages=[{"role": "user", "content": "keep local"}],
        metadata={
            "id": "conv-root-private",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-03T00:00:00+00:00",
            "display_name": "Private conversation",
        },
    )
    write_conversation(
        name="vault/ledger",
        messages=[{"role": "user", "content": "blocked workspace"}],
        metadata={
            "id": "conv-vault",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-04T00:00:00+00:00",
            "display_name": "Vault conversation",
        },
    )

    manifest = service.build_manifest(
        ["conversations"], workspace_ids=["root", "vault"]
    )

    assert manifest["workspace_selection"]["workspace_ids"] == ["root"]
    assert manifest["workspace_selection"]["privacy_ignored_workspace_ids"] == ["vault"]
    assert [
        item["sync_id"] for item in manifest["sections"]["conversations"]["items"]
    ] == ["conv-root-visible"]


def test_build_snapshot_excludes_sensitive_memory_items_from_sync(
    tmp_path, monkeypatch
):
    modules = _configure_paths(tmp_path, monkeypatch)
    service = modules["InstanceSyncService"]()
    memory_store = modules["memory_store"]
    user_settings = modules["user_settings"]

    user_settings.save_settings(
        {
            "workspace_profiles": [
                {
                    "id": "root",
                    "name": "Main workspace",
                    "privacy_mode": "default",
                }
            ],
            "active_workspace_id": "root",
            "sync_selected_workspace_ids": ["root"],
        }
    )
    memory_store.save(
        {
            "visible": {
                "value": "allowed",
                "updated_at": 10.0,
                "sensitivity": "personal",
            },
            "protected-note": {
                "value": "blocked",
                "updated_at": 11.0,
                "sensitivity": "protected",
            },
            "secret-note": {
                "value": "blocked",
                "updated_at": 12.0,
                "sensitivity": "secret",
            },
        }
    )

    manifest = service.build_manifest(["memories"], workspace_ids=["root"])
    snapshot = service.build_snapshot(["memories"], workspace_ids=["root"])

    assert [item["key"] for item in manifest["sections"]["memories"]["items"]] == [
        "visible"
    ]
    assert [record["key"] for record in snapshot["sections"]["memories"]] == ["visible"]
