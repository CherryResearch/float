"""Shared capture-to-attachment promotion lifecycle.

Both the HTTP capture endpoint and the model-facing ``capture.promote`` tool use
this module so a promoted image cannot bypass captioning or retrieval indexing.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from uuid import uuid4

from app.utils import blob_store
from app.utils.attachment_metadata import (
    attachment_index_generation_is_active,
    mutate_attachment_metadata,
)


@dataclass(frozen=True)
class AttachmentIndexRequest:
    data: bytes
    filename: str
    content_type: str
    url: str
    content_hash: str
    started_at: str
    index_generation: str


@dataclass(frozen=True)
class CapturePromotion:
    capture: Dict[str, Any]
    attachment: Dict[str, Any]
    metadata: Dict[str, Any]
    index_request: Optional[AttachmentIndexRequest]


def _utc_now_compact_iso() -> str:
    return (
        datetime.now(tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _normalized_caption_engine(value: Any) -> str:
    normalized = str(value or "local").strip().lower()
    return normalized if normalized in {"local", "off", "cloud"} else "local"


def _read_if_hash_matches(path: Optional[Path], content_hash: str) -> bytes | None:
    if path is None or not path.exists() or not path.is_file():
        return None
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != content_hash:
        return None
    return data


def _existing_asset(
    existing_ref: Dict[str, Any],
    *,
    capture_path: Optional[Path],
) -> tuple[bytes, Dict[str, str]] | None:
    content_hash = str(existing_ref.get("content_hash") or "").strip()
    if not blob_store.is_canonical_content_hash(content_hash):
        return None
    filename = Path(str(existing_ref.get("filename") or "capture.png")).name
    asset_path = blob_store.find_asset_path(content_hash, filename=filename)
    data = _read_if_hash_matches(asset_path, content_hash)
    if data is not None and asset_path is not None:
        try:
            relative_path = blob_store.managed_relative_path(asset_path)
        except Exception:
            asset_info = blob_store.put_asset(
                data,
                filename=filename,
                origin="captured",
            )
        else:
            asset_info = {
                "content_hash": content_hash,
                "filename": asset_path.name,
                "origin": str(existing_ref.get("origin") or "captured"),
                "path": str(asset_path),
                "relative_path": relative_path,
            }
        return data, asset_info

    data = _read_if_hash_matches(capture_path, content_hash)
    if data is None:
        return None
    return data, blob_store.put_asset(
        data,
        filename=filename,
        origin="captured",
    )


def promote_capture_to_attachment(
    service: Any,
    capture_id: str,
    *,
    metadata_root: Path,
    memory_refs: Optional[Iterable[str]] = None,
    caption_engine: str = "local",
) -> CapturePromotion:
    """Persist one capture and prepare any caption/index work it requires."""

    capture = service.get_capture(capture_id)
    if capture is None:
        raise FileNotFoundError(f"Unknown capture '{capture_id}'")
    capture_path = service.capture_path(capture_id)
    existing_ref = capture.get("attachment_ref")
    existing_asset = (
        _existing_asset(existing_ref, capture_path=capture_path)
        if isinstance(existing_ref, dict)
        else None
    )

    if existing_asset is not None:
        data, asset_info = existing_asset
    else:
        if capture_path is None:
            raise FileNotFoundError(f"Unknown capture '{capture_id}'")
        data = capture_path.read_bytes()
        filename = (
            Path(str(capture.get("filename") or capture_path.name)).name
            or capture_path.name
        )
        asset_info = blob_store.put_asset(
            data,
            filename=filename,
            origin="captured",
        )

    content_hash = str(asset_info["content_hash"])
    filename = Path(
        str(
            capture.get("filename")
            or (existing_ref or {}).get("filename")
            or asset_info.get("filename")
            or "capture.png"
        )
    ).name
    content_type = (
        str(
            capture.get("content_type")
            or (existing_ref or {}).get("content_type")
            or "image/png"
        ).strip()
        or "image/png"
    )
    url = f"/api/attachments/{content_hash}/{filename}"
    now = _utc_now_compact_iso()
    engine = _normalized_caption_engine(caption_engine)
    image_attachment = content_type.lower().startswith("image/")
    should_index = False
    index_generation = ""

    def _merge_capture(existing_metadata: Dict[str, Any]) -> Dict[str, Any]:
        nonlocal index_generation, should_index
        metadata = dict(existing_metadata)
        for cleanup_key in ("deletion_status", "cleanup_failed"):
            metadata.pop(cleanup_key, None)
        caption = str(metadata.get("caption") or "").strip()
        caption_status = str(metadata.get("caption_status") or "").strip().lower()
        preserves_caption = bool(caption) and caption_status in {
            "manual",
            "generated",
        }
        index_status = str(metadata.get("index_status") or "").strip().lower()
        needs_index = index_status != "indexed" or (
            engine != "off" and not preserves_caption
        )
        should_index = bool(
            image_attachment
            and needs_index
            and not attachment_index_generation_is_active(metadata)
        )
        metadata.update(
            {
                "filename": filename,
                "content_type": content_type,
                "size": len(data),
                "uploaded_at": metadata.get("uploaded_at") or now,
                "origin": "captured",
                "relative_path": asset_info.get("relative_path"),
                "path": asset_info.get("path"),
                "capture_source": capture.get("capture_source")
                or capture.get("source"),
                "capture_id": capture_id,
                "capture_sensitivity": capture.get("sensitivity"),
                "metadata_updated_at": now,
            }
        )
        if image_attachment and should_index:
            index_generation = str(uuid4())
            metadata["index_status"] = "indexing"
            metadata["index_generation"] = index_generation
            metadata["index_generation_started_at"] = now
        if image_attachment and not preserves_caption:
            metadata["caption_status"] = "disabled" if engine == "off" else "pending"
        elif not image_attachment:
            metadata.setdefault("index_status", "not_applicable")
            metadata.setdefault("caption_status", "not_applicable")
        return metadata

    metadata = mutate_attachment_metadata(
        Path(metadata_root),
        content_hash,
        _merge_capture,
    )
    attachment_ref = {
        "content_hash": content_hash,
        "filename": filename,
        "content_type": content_type,
        "size": len(data),
        "url": url,
        "uploaded_at": metadata.get("uploaded_at") or now,
        "origin": "captured",
        "relative_path": asset_info.get("relative_path"),
    }
    promoted = service.mark_promoted(
        capture_id,
        attachment_ref=attachment_ref,
        memory_refs=list(memory_refs or []),
    )
    index_request = (
        AttachmentIndexRequest(
            data=data,
            filename=filename,
            content_type=content_type,
            url=url,
            content_hash=content_hash,
            started_at=str(metadata.get("uploaded_at") or now),
            index_generation=index_generation,
        )
        if should_index
        else None
    )
    return CapturePromotion(
        capture=promoted,
        attachment=attachment_ref,
        metadata=metadata,
        index_request=index_request,
    )
