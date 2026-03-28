import hashlib
import shutil
from pathlib import Path
from typing import Dict, Iterable, Iterator, Optional

from app import config as app_config


REPO_ROOT = Path(__file__).resolve().parents[3]
BLOBS_DIR = (REPO_ROOT / "blobs").resolve()
BLOBS_DIR.mkdir(parents=True, exist_ok=True)
ASSET_ORIGIN_DIRS = {
    "upload": "uploads",
    "captured": "captured",
    "screenshot": "screenshots",
}


def _hex_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _resolve_data_files_root() -> Path:
    cfg = app_config.load_config()
    data_dir = Path(cfg.get("data_dir") or app_config.DEFAULT_DATA_DIR)
    if not data_dir.is_absolute():
        data_dir = (app_config.REPO_ROOT / data_dir).resolve()
    else:
        try:
            data_dir = data_dir.resolve()
        except Exception:
            pass
    files_dir = (data_dir / "files").resolve()
    files_dir.mkdir(parents=True, exist_ok=True)
    for dirname in set(ASSET_ORIGIN_DIRS.values()) | {"downloaded", "workspace"}:
        try:
            (files_dir / dirname).mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
    return files_dir


def normalize_asset_origin(value: str, default: str = "upload") -> str:
    raw = str(value or "").strip().lower()
    if raw in ASSET_ORIGIN_DIRS:
        return raw
    return default


def asset_origin_dirname(origin: str) -> str:
    return ASSET_ORIGIN_DIRS[normalize_asset_origin(origin)]


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
        return True
    except Exception:
        return False


