import hashlib
import re
import shutil
from pathlib import Path
from typing import Dict, Iterable, Iterator, Optional

from app import config as app_config
from app.utils.sync_paths import clean_relative_path, sync_attachment_relative_path

REPO_ROOT = Path(__file__).resolve().parents[3]
BLOBS_DIR = (REPO_ROOT / "blobs").resolve()
BLOBS_DIR.mkdir(parents=True, exist_ok=True)
ASSET_ORIGIN_DIRS = {
    "upload": "uploads",
    "captured": "captured",
    "screenshot": "screenshots",
}
LEGACY_FILES_RELATIVE_DIRS = {
    *ASSET_ORIGIN_DIRS.values(),
    "downloaded",
    "workspace",
}
_HEX_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


def _hex_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _resolve_data_root() -> Path:
    cfg = app_config.load_config()
    data_dir = Path(cfg.get("data_dir") or app_config.DEFAULT_DATA_DIR)
    if not data_dir.is_absolute():
        data_dir = (app_config.REPO_ROOT / data_dir).resolve()
    else:
        try:
            data_dir = data_dir.resolve()
        except Exception:
            pass
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def _resolve_data_files_root() -> Path:
    data_dir = _resolve_data_root()
    files_dir = (data_dir / "files").resolve()
    files_dir.mkdir(parents=True, exist_ok=True)
    for dirname in set(ASSET_ORIGIN_DIRS.values()) | {"downloaded", "workspace"}:
        try:
            (files_dir / dirname).mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
    return files_dir


def _resolve_managed_data_root() -> Path:
    files_dir = _resolve_data_files_root()
    return files_dir.parent.resolve()


def resolve_managed_path(value: str) -> Optional[Path]:
    cleaned = clean_relative_path(value)
    if not cleaned:
        return None
    parts = cleaned.split("/")
    first_segment = parts[0].lower()
    second_segment = parts[1].lower() if len(parts) > 1 else ""
    base = (
        _resolve_data_files_root()
        if first_segment in LEGACY_FILES_RELATIVE_DIRS
        or second_segment in LEGACY_FILES_RELATIVE_DIRS
        else _resolve_managed_data_root()
    )
    target = (base / cleaned).resolve()
    try:
        target.relative_to(base)
    except Exception:
        return None
    return target


def managed_relative_path(target: Path) -> str:
    resolved = target.resolve()
    files_dir = _resolve_data_files_root()
    try:
        return resolved.relative_to(files_dir).as_posix()
    except Exception:
        pass
    data_root = _resolve_managed_data_root()
    return resolved.relative_to(data_root).as_posix()


def normalize_asset_origin(value: str, default: str = "upload") -> str:
    raw = str(value or "").strip().lower()
    if raw in ASSET_ORIGIN_DIRS:
        return raw
    return default


def asset_origin_dirname(origin: str) -> str:
    return ASSET_ORIGIN_DIRS[normalize_asset_origin(origin)]


def _is_hash_dir(entry: Path) -> bool:
    return entry.is_dir() and bool(_HEX_SHA256_RE.fullmatch(entry.name.lower()))


def _iter_sync_attachment_roots(content_hash: str) -> Iterator[Path]:
    normalized_hash = str(content_hash or "").strip().lower()
    if not normalized_hash:
        return
    data_root = _resolve_managed_data_root()
    sync_root = data_root / "sync"
    if sync_root.exists():
        pattern = f"*/workspace/**/attachments/{normalized_hash}"
        for candidate_root in sync_root.glob(pattern):
            if _is_hash_dir(candidate_root):
                yield candidate_root
    legacy_sync_root = _resolve_data_files_root() / "workspace" / "sync"
    if legacy_sync_root.exists():
        for candidate_root in legacy_sync_root.rglob(normalized_hash):
            if _is_hash_dir(candidate_root):
                yield candidate_root


def iter_attachment_hashes() -> Iterator[str]:
    seen: set[str] = set()
    try:
        for entry in BLOBS_DIR.iterdir():
            if not entry.is_file():
                continue
            name = entry.name
            if name.lower() == "readme.md":
                continue
            candidate = entry.stem if name.endswith(".json") else name
            candidate = str(candidate or "").strip().lower()
            if candidate and candidate not in seen:
                seen.add(candidate)
                yield candidate
    except FileNotFoundError:
        pass
    files_dir = _resolve_data_files_root()
    for dirname in set(ASSET_ORIGIN_DIRS.values()) | {"downloaded"}:
        root = files_dir / dirname
        if not root.exists() or not root.is_dir():
            continue
        for child in root.iterdir():
            if _is_hash_dir(child) and child.name not in seen:
                seen.add(child.name)
                yield child.name
    legacy_sync_root = files_dir / "workspace" / "sync"
    if legacy_sync_root.exists():
        for child in legacy_sync_root.rglob("*"):
            if _is_hash_dir(child) and child.name not in seen:
                seen.add(child.name)
                yield child.name
    sync_root = _resolve_managed_data_root() / "sync"
    if sync_root.exists():
        for child in sync_root.glob("*/workspace/**/attachments/*"):
            if _is_hash_dir(child) and child.name not in seen:
                seen.add(child.name)
                yield child.name


def _migrate_legacy_sync_attachment(
    content_hash: str,
    *,
    filename: Optional[str] = None,
) -> Optional[Path]:
    normalized_hash = str(content_hash or "").strip().lower()
    legacy_root = _resolve_data_files_root() / "workspace" / "sync"
    if not normalized_hash or not legacy_root.exists():
        return None
    for candidate_root in legacy_root.rglob(normalized_hash):
        if not _is_hash_dir(candidate_root):
            continue
        files = [child for child in candidate_root.iterdir() if child.is_file()]
        if not files:
            continue
        selected = next(
            (child for child in files if child.name == Path(str(filename or "")).name),
            files[0],
        )
        relative = candidate_root.relative_to(legacy_root).parts
        if len(relative) < 2:
            continue
        namespace = "/".join(relative[:-1])
        new_rel = sync_attachment_relative_path(
            namespace, normalized_hash, selected.name
        )
        target = resolve_managed_path(new_rel)
        if target is None:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.move(str(selected), str(target))
        else:
            selected.unlink(missing_ok=True)
        current = candidate_root
        while current != legacy_root:
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent
        return target
    return None


def _iter_asset_candidates(content_hash: str) -> Iterator[Path]:
    files_dir = _resolve_data_files_root()
    normalized_hash = str(content_hash or "").strip().lower()
    if not normalized_hash:
        return
    for dirname in ASSET_ORIGIN_DIRS.values():
        candidate_root = files_dir / dirname / normalized_hash
        if candidate_root.exists() and candidate_root.is_dir():
            for candidate in candidate_root.iterdir():
                if candidate.is_file():
                    yield candidate
    migrated = _migrate_legacy_sync_attachment(normalized_hash)
    if migrated is not None and migrated.exists() and migrated.is_file():
        yield migrated
    for candidate_root in _iter_sync_attachment_roots(normalized_hash):
        for candidate in candidate_root.iterdir():
            if candidate.is_file():
                yield candidate


def find_asset_path(
    content_hash: str,
    *,
    filename: Optional[str] = None,
) -> Optional[Path]:
    normalized_hash = str(content_hash or "").strip().lower()
    if not normalized_hash:
        return None
    wanted_name = Path(str(filename or "")).name.strip()
    fallback: Optional[Path] = None
    for candidate in _iter_asset_candidates(normalized_hash):
        if fallback is None:
            fallback = candidate
        if wanted_name and candidate.name == wanted_name:
            return candidate
    return fallback


def put_asset(
    data: bytes,
    *,
    filename: str,
    origin: str = "upload",
) -> Dict[str, str]:
    content_hash = _hex_sha256(data)
    safe_name = Path(filename or "file").name or "file"
    dirname = asset_origin_dirname(origin)
    files_dir = _resolve_data_files_root()
    target_dir = (files_dir / dirname / content_hash).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = (target_dir / safe_name).resolve()
    try:
        target_path.relative_to(files_dir)
    except Exception as exc:
        raise ValueError("asset target escaped data/files root") from exc
    if not target_path.exists():
        target_path.write_bytes(data)
    relative_path = target_path.relative_to(files_dir).as_posix()
    return {
        "content_hash": content_hash,
        "filename": safe_name,
        "origin": normalize_asset_origin(origin),
        "path": str(target_path),
        "relative_path": relative_path,
    }


def put_blob(data: bytes) -> str:
    """Store a complete blob addressed by content hash; return hash."""
    h = _hex_sha256(data)
    path = BLOBS_DIR / h
    if not path.exists():
        path.write_bytes(data)
    return h


def get_blob(content_hash: str) -> bytes:
    asset_path = find_asset_path(content_hash)
    if asset_path is not None and asset_path.exists():
        return asset_path.read_bytes()
    return (BLOBS_DIR / content_hash).read_bytes()


def exists(content_hash: str) -> bool:
    if find_asset_path(content_hash) is not None:
        return True
    return (BLOBS_DIR / content_hash).exists()


def put_chunks(chunks: Iterable[bytes]) -> str:
    data = b"".join(chunks)
    return put_blob(data)


def delete(content_hash: str) -> bool:
    """Delete a blob by content hash; return True if removed or already missing."""
    normalized_hash = str(content_hash or "").strip().lower()
    try:
        (BLOBS_DIR / normalized_hash).unlink(missing_ok=True)
        files_dir = _resolve_data_files_root()
        for dirname in ASSET_ORIGIN_DIRS.values():
            shutil.rmtree(files_dir / dirname / normalized_hash, ignore_errors=True)
        shutil.rmtree(
            files_dir / "workspace" / "sync" / normalized_hash, ignore_errors=True
        )
        for candidate_root in _iter_sync_attachment_roots(normalized_hash):
            shutil.rmtree(candidate_root, ignore_errors=True)
        return True
    except Exception:
        return False
