import io
from pathlib import Path

import pytest
from app import base_services, routes
from app.services import capture_service
from PIL import Image


@pytest.fixture
def stored_camera(tmp_path, monkeypatch):
    image_path = Path(__file__).resolve().parents[3] / "docs/resources/floatlogo.png"
    image_bytes = image_path.read_bytes()
    with Image.open(io.BytesIO(image_bytes)) as image:
        image.load()
        assert image.width >= 32 and image.height >= 32
        assert any(low != high for low, high in image.convert("RGB").getextrema())
    monkeypatch.setattr(capture_service.app_config, "REPO_ROOT", tmp_path)
    service = capture_service.CaptureService(data_dir=tmp_path / "data")
    capture = service.create_capture_from_bytes(
        image_bytes,
        filename="camera.png",
        source="camera",
        capture_source="chat_camera",
        sensitivity="public",
        retention_days=7,
    )
    monkeypatch.setattr(routes, "get_capture_service", lambda: service)
    monkeypatch.setattr(base_services, "get_capture_service", lambda: service)
    # A transient camera has no durable blob record. Do not fake one here.
    monkeypatch.setattr(routes, "_attachment_public_descriptor", lambda _hash: None)
    return service, capture, image_bytes


def test_transient_camera_survives_request_and_history_normalization(stored_camera):
    _, capture, image_bytes = stored_camera
    submitted = {
        **capture["attachment"],
        "url": f"http://localhost:12345{capture['url']}",
        "relative_path": "client-supplied-path",
        "origin": "client-supplied-origin",
        "type": "text/plain",
    }
    canonical = routes._enrich_attachment_reference(submitted)
    assert canonical["_canonical_attachment_resolved"] is True
    assert canonical["type"] == "image/png"
    assert canonical["origin"] == "captured"
    assert canonical["url"] == capture["url"]
    assert canonical["relative_path"] == capture["relative_path"]
    assert canonical["transient"] is True
    assert canonical["expires_at"] == capture["expires_at"]
    restored = routes._enrich_attachment_references([canonical])[0]
    assert restored == canonical
    resolved_bytes, source = base_services._resolve_attachment_bytes(restored)
    assert source == "capture"
    assert resolved_bytes == image_bytes


def test_expired_capture_lookup_does_not_prune_or_trust_client_hints(stored_camera):
    service, capture, _ = stored_camera
    record = service._read_meta(capture["capture_id"])
    record["expires_at"] = 1
    service._write_meta(record)
    metadata_path = service._meta_path(capture["capture_id"])
    image_path = service.capture_path(capture["capture_id"])
    assert service.peek_capture(capture["capture_id"]) is None
    canonical = routes._enrich_attachment_reference(capture["attachment"])
    assert "_canonical_attachment_resolved" not in canonical
    assert "url" not in canonical
    assert "relative_path" not in canonical
    assert metadata_path.exists() and image_path.exists()


@pytest.mark.parametrize("capture_id", ["../other", "..", "C:/outside", "missing"])
def test_capture_lookup_does_not_resolve_unmanaged_identifiers(
    stored_camera, capture_id
):
    service, _, _ = stored_camera
    assert service.peek_capture(capture_id) is None
