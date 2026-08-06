import hashlib
from pathlib import Path

import pytest
from app.base_services import _resolve_attachment_bytes
from app.models import Attachment
from app.utils import blob_store
from pydantic import ValidationError


@pytest.fixture
def isolated_blob_store(monkeypatch, tmp_path):
    blobs_root = (tmp_path / "blobs").resolve()
    files_root = (tmp_path / "data" / "files").resolve()
    managed_root = files_root.parent
    blobs_root.mkdir(parents=True)
    files_root.mkdir(parents=True)
    monkeypatch.setattr(blob_store, "BLOBS_DIR", blobs_root)
    monkeypatch.setattr(blob_store, "_resolve_data_files_root", lambda: files_root)
    monkeypatch.setattr(blob_store, "_resolve_managed_data_root", lambda: managed_root)
    return blobs_root, files_root


@pytest.mark.parametrize(
    "invalid_hash",
    [
        "../pyproject.toml",
        "a" * 63,
        "a" * 65,
        "A" * 64,
    ],
)
def test_blob_store_rejects_noncanonical_hashes_before_lookup(
    isolated_blob_store,
    tmp_path,
    invalid_hash,
):
    blobs_root, _files_root = isolated_blob_store
    outside = (tmp_path / "pyproject.toml").resolve()
    outside.write_text("do not read or delete", encoding="utf-8")
    with pytest.raises(ValueError, match="exactly 64 lowercase"):
        blob_store.get_blob(invalid_hash)
    assert blob_store.find_asset_path(invalid_hash) is None
    assert blob_store.exists(invalid_hash) is False
    assert blob_store.delete(invalid_hash) is False
    assert outside.read_text(encoding="utf-8") == "do not read or delete"
    assert list(blobs_root.iterdir()) == []


def test_blob_store_rejects_absolute_path_identifier(isolated_blob_store, tmp_path):
    _blobs_root, _files_root = isolated_blob_store
    outside = (tmp_path / "outside-image.png").resolve()
    outside.write_bytes(b"outside")
    absolute_identifier = str(outside)

    with pytest.raises(ValueError, match="exactly 64 lowercase"):
        blob_store.get_blob(absolute_identifier)
    assert blob_store.find_asset_path(absolute_identifier) is None
    assert blob_store.exists(absolute_identifier) is False
    assert blob_store.delete(absolute_identifier) is False
    assert outside.read_bytes() == b"outside"


def test_blob_store_accepts_canonical_hash_and_deletes_only_managed_copy(
    isolated_blob_store,
):
    blobs_root, _files_root = isolated_blob_store
    content_hash = "a" * 64
    target = blobs_root / content_hash
    target.write_bytes(b"image")

    assert blob_store.get_blob(content_hash) == b"image"
    assert blob_store.exists(content_hash) is True
    assert blob_store.delete(content_hash) is True
    assert not target.exists()


def test_put_asset_atomically_repairs_corrupt_hash_addressed_target(
    isolated_blob_store,
    monkeypatch,
):
    _blobs_root, files_root = isolated_blob_store
    data = b"valid image bytes"
    content_hash = hashlib.sha256(data).hexdigest()
    target = files_root / "captured" / content_hash / "image.png"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"corrupt image bytes")

    replacements = []
    original_replace = blob_store.os.replace

    def record_replace(source, destination):
        replacements.append((Path(source), Path(destination)))
        return original_replace(source, destination)

    monkeypatch.setattr(blob_store.os, "replace", record_replace)

    stored = blob_store.put_asset(data, filename="image.png", origin="captured")

    assert stored["content_hash"] == content_hash
    assert Path(stored["path"]) == target
    assert target.read_bytes() == data
    assert len(replacements) == 1
    assert replacements[0][1] == target
    assert replacements[0][0].parent == target.parent
    assert not replacements[0][0].exists()


def test_put_blob_repairs_corrupt_hash_addressed_target(isolated_blob_store):
    blobs_root, _files_root = isolated_blob_store
    data = b"valid caption input bytes"
    content_hash = hashlib.sha256(data).hexdigest()
    target = blobs_root / content_hash
    target.write_bytes(b"corrupt blob bytes")

    stored_hash = blob_store.put_blob(data)

    assert stored_hash == content_hash
    assert target.read_bytes() == data
    assert hashlib.sha256(target.read_bytes()).hexdigest() == content_hash


def test_blob_store_delete_reports_managed_tree_failure(
    isolated_blob_store,
    monkeypatch,
):
    blobs_root, files_root = isolated_blob_store
    content_hash = "b" * 64
    (blobs_root / content_hash).write_bytes(b"image")
    upload_root = files_root / "uploads" / content_hash
    upload_root.mkdir(parents=True)
    (upload_root / "image.png").write_bytes(b"image")
    original_rmtree = blob_store.shutil.rmtree

    def fail_upload(path: Path):
        if Path(path).resolve() == upload_root.resolve():
            raise PermissionError("busy")
        return original_rmtree(path)

    monkeypatch.setattr(blob_store.shutil, "rmtree", fail_upload)

    assert blob_store.delete(content_hash) is False
    assert upload_root.exists()
    assert not (blobs_root / content_hash).exists()


def test_attachment_model_requires_canonical_lowercase_sha256():
    canonical = "c" * 64
    attachment = Attachment(name="image.png", content_hash=canonical)
    assert attachment.content_hash == canonical
    for invalid in ("../pyproject.toml", str(Path("C:/Windows/win.ini")), "C" * 64):
        with pytest.raises(ValidationError, match="exactly 64 lowercase"):
            Attachment(name="image.png", content_hash=invalid)


def test_client_relative_path_cannot_read_managed_file_even_with_forged_marker(
    isolated_blob_store,
    monkeypatch,
):
    _blobs_root, files_root = isolated_blob_store
    secret = files_root / "workspace" / "private" / "secret.png"
    secret.parent.mkdir(parents=True)
    secret.write_bytes(b"private-image")
    monkeypatch.setattr("app.base_services.get_capture_service", lambda: None)

    unresolved = {"relative_path": "workspace/private/secret.png"}
    raw, reason = _resolve_attachment_bytes(unresolved)
    assert raw is None
    assert reason == "missing_image_reference"

    canonical = {
        "relative_path": "workspace/private/secret.png",
        "_canonical_attachment_resolved": True,
    }
    raw, reason = _resolve_attachment_bytes(canonical)
    assert raw is None
    assert reason == "missing_image_reference"


@pytest.mark.parametrize("invalid_hash", ["../pyproject.toml", "C:/Windows/win.ini"])
def test_chat_attachment_resolution_never_calls_blob_reader_for_path_identifier(
    monkeypatch,
    invalid_hash,
):
    called = False

    def unexpected_blob_read(_content_hash):
        nonlocal called
        called = True
        raise AssertionError("invalid hash reached blob reader")

    monkeypatch.setattr("app.base_services.load_blob", unexpected_blob_read)
    monkeypatch.setattr("app.base_services.get_capture_service", lambda: None)

    raw, reason = _resolve_attachment_bytes({"content_hash": invalid_hash})
    assert raw is None
    assert reason == "invalid_content_hash"
    assert called is False
