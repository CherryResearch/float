import mimetypes
import re
from pathlib import Path
from typing import Any, Dict, Optional

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "application/pdf": ".pdf",
    "audio/wav": ".wav",
    "audio/mpeg": ".mp3",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "text/plain": ".txt",
    "application/json": ".json",
}


def _normalize_content_type(value: Any) -> str:
    raw = str(value or "").strip().lower()
    return raw if "/" in raw else ""


def _read_prefix(target: Path, size: int = 4096) -> bytes:
    try:
        with target.open("rb") as handle:
            return handle.read(size)
    except Exception:
        return b""


def _sniff_content_type(target: Path) -> str:
    head = _read_prefix(target)
    if not head:
        return "application/octet-stream"
    stripped = head.lstrip()
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return "audio/wav"
    if head.startswith(b"ID3") or (
        len(head) > 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0
    ):
        return "audio/mpeg"
    if b"ftyp" in head[:32]:
        return "video/mp4"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "video/webm"
    if stripped.startswith(b"<svg") or stripped.startswith(b"<?xml"):
        snippet = stripped[:512].lower()
        if b"<svg" in snippet:
            return "image/svg+xml"
    try:
        decoded = head.decode("utf-8")
    except Exception:
        decoded = ""
    if decoded:
        if decoded.lstrip().startswith(("{", "[")):
            return "application/json"
        printable = sum(1 for char in decoded if char.isprintable() or char in "\r\n\t")
        if printable / max(len(decoded), 1) > 0.9:
            return "text/plain"
    return "application/octet-stream"


def infer_attachment_content_type(
    target: Path,
    *,
    filename: Optional[str] = None,
    declared_type: Optional[str] = None,
) -> str:
    normalized = _normalize_content_type(declared_type)
    if normalized and normalized != "application/octet-stream":
        return normalized
    for candidate in (filename, target.name):
        clean = Path(str(candidate or "")).name.strip()
        if not clean:
            continue
        guessed = _normalize_content_type(mimetypes.guess_type(clean)[0])
        if guessed:
            return guessed
    return _sniff_content_type(target)


def content_type_extension(content_type: Optional[str]) -> str:
    normalized = _normalize_content_type(content_type)
    if not normalized:
        return ""
    mapped = _CONTENT_TYPE_EXTENSIONS.get(normalized)
    if mapped:
        return mapped
    guessed = mimetypes.guess_extension(normalized) or ""
    if guessed == ".jpe":
        return ".jpg"
    if guessed == ".svgz":
        return ".svg"
    return guessed


def attachment_filename_needs_recovery(
    content_hash: str, filename: Optional[str]
) -> bool:
    clean = Path(str(filename or "")).name.strip()
    if not clean:
        return True
    if "." not in clean:
        return True
    stem = Path(clean).stem.strip().lower()
    suffix = Path(clean).suffix.strip().lower()
    if not suffix:
        return True
    return bool(content_hash) and stem == str(content_hash).strip().lower()


def choose_attachment_display_filename(
    content_hash: str,
    *,
    filename: Optional[str] = None,
    content_type: Optional[str] = None,
    target: Optional[Path] = None,
) -> str:
    provided = Path(str(filename or "")).name.strip()
    if provided and not attachment_filename_needs_recovery(content_hash, provided):
        return provided
    target_name = Path(str(target.name if target else "")).name.strip()
    if target_name and not attachment_filename_needs_recovery(
        content_hash, target_name
    ):
        return target_name
    extension = (
        content_type_extension(content_type) or Path(target_name).suffix.strip().lower()
    )
    if extension and not extension.startswith("."):
        extension = f".{extension}"
    base_name = (
        Path(provided).stem.strip()
        or Path(target_name).stem.strip()
        or str(content_hash or "").strip()
        or "attachment"
    )
    if _SHA256_RE.fullmatch(base_name.lower()) or not base_name:
        base_name = str(content_hash or "").strip() or "attachment"
    if extension and not base_name.lower().endswith(extension.lower()):
        return f"{base_name}{extension}"
    return base_name


def build_attachment_media_descriptor(
    content_hash: str,
    target: Path,
    *,
    metadata: Optional[Dict[str, Any]] = None,
    preferred_filename: Optional[str] = None,
) -> Dict[str, Any]:
    meta = dict(metadata or {})
    content_type = infer_attachment_content_type(
        target,
        filename=preferred_filename or meta.get("filename"),
        declared_type=meta.get("content_type"),
    )
    filename = choose_attachment_display_filename(
        content_hash,
        filename=preferred_filename or meta.get("filename"),
        content_type=content_type,
        target=target,
    )
    stat_size = 0
    try:
        stat_size = int(target.stat().st_size)
    except Exception:
        stat_size = 0
    raw_size = meta.get("size")
    size = raw_size if isinstance(raw_size, int) and raw_size >= 0 else stat_size
    healed = dict(meta)
    changed = False
    if filename and str(healed.get("filename") or "").strip() != filename:
        healed["filename"] = filename
        changed = True
    if (
        content_type
        and str(healed.get("content_type") or "").strip().lower() != content_type
    ):
        healed["content_type"] = content_type
        changed = True
    if stat_size and healed.get("size") != stat_size:
        healed["size"] = stat_size
        changed = True
    return {
        "filename": filename,
        "content_type": content_type,
        "size": size,
        "metadata": healed,
        "metadata_changed": changed,
    }
