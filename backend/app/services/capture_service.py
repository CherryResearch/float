from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from app import config as app_config
from app.utils import user_settings

SENSITIVITY_VALUES = {"mundane", "public", "personal", "protected", "secret"}


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_name(value: str, default: str = "capture.png") -> str:
    raw = Path(str(value or default)).name.strip()
    raw = raw or default
    parsed = Path(raw)
    suffix = "".join(parsed.suffixes) or Path(default).suffix or ".png"
    stem = (
        parsed.name[: -len(suffix)]
        if suffix and parsed.name.endswith(suffix)
        else parsed.stem
    )
    stem = stem.strip(" ._-") or "capture"
    max_name_length = 120
    if len(stem) + len(suffix) <= max_name_length:
        return f"{stem}{suffix}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    keep = max(1, max_name_length - len(suffix) - len(digest) - 1)
    trimmed_stem = stem[:keep].rstrip(" ._-") or "capture"
    return f"{trimmed_stem}-{digest}{suffix}"


def _iso(value: float) -> str:
    return (
        datetime.fromtimestamp(float(value), tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


class CaptureService:
    def __init__(self, *, data_dir: str | Path | None = None):
        root = Path(data_dir or app_config.DEFAULT_DATA_DIR)
        if not root.is_absolute():
            root = (app_config.REPO_ROOT / root).resolve()
        else:
            root = root.resolve()
        self.files_root = (root / "files" / "captures").resolve()
        self.transient_root = (self.files_root / "transient").resolve()
        self.metadata_root = (app_config.REPO_ROOT / "blobs" / "captures").resolve()
        self.files_root.mkdir(parents=True, exist_ok=True)
        self.transient_root.mkdir(parents=True, exist_ok=True)
        self.metadata_root.mkdir(parents=True, exist_ok=True)

    def default_retention_days(self) -> int:
        settings = user_settings.load_settings()
        try:
            value = int(settings.get("capture_retention_days") or 7)
        except Exception:
            value = 7
        return max(0, value)

    def default_sensitivity(self) -> str:
        settings = user_settings.load_settings()
        value = (
            str(settings.get("capture_default_sensitivity") or "personal")
            .strip()
            .lower()
        )
        return value if value in SENSITIVITY_VALUES else "personal"

    def _meta_path(self, capture_id: str) -> Path:
        return self.metadata_root / f"{capture_id}.json"

    def _read_meta(self, capture_id: str) -> Optional[Dict[str, Any]]:
        path = self._meta_path(capture_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    def _write_meta(self, payload: Dict[str, Any]) -> None:
        capture_id = str(payload.get("capture_id") or "").strip()
        if not capture_id:
            raise ValueError("capture_id is required")
        self._meta_path(capture_id).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _resolve_path(self, payload: Dict[str, Any]) -> Optional[Path]:
        path_value = str(payload.get("path") or "").strip()
        if not path_value:
            return None
        target = Path(path_value)
        try:
            target = target.resolve()
        except Exception:
            target = Path(path_value)
        return target

    def _descriptor(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        capture_id = str(payload.get("capture_id") or "")
        content_type = (
            str(payload.get("content_type") or "image/png").strip() or "image/png"
        )
        descriptor = {
            "capture_id": capture_id,
            "source": str(payload.get("source") or "").strip() or "capture",
            "filename": str(payload.get("filename") or "capture.png").strip()
            or "capture.png",
            "content_type": content_type,
            "content_hash": str(payload.get("content_hash") or "").strip(),
            "transient": bool(payload.get("transient", True)),
            "promoted": bool(payload.get("promoted", False)),
            "sensitivity": str(
                payload.get("sensitivity") or self.default_sensitivity()
            ),
            "created_at": payload.get("created_at"),
            "created_at_iso": payload.get("created_at_iso"),
            "updated_at": payload.get("updated_at"),
            "updated_at_iso": payload.get("updated_at_iso"),
            "expires_at": payload.get("expires_at"),
            "expires_at_iso": payload.get("expires_at_iso"),
            "conversation_id": payload.get("conversation_id"),
            "message_id": payload.get("message_id"),
            "computer_session_id": payload.get("computer_session_id"),
            "current_url": payload.get("current_url"),
            "active_window": payload.get("active_window"),
            "capture_source": payload.get("capture_source"),
            "attachment_ref": payload.get("attachment_ref"),
            "memory_refs": list(payload.get("memory_refs") or []),
            "url": f"/api/captures/{capture_id}/content",
        }
        descriptor["attachment"] = {
            "name": descriptor["filename"],
            "type": descriptor["content_type"],
            "url": descriptor["url"],
            "origin": "captured",
            "capture_id": capture_id,
            "capture_source": descriptor["capture_source"] or descriptor["source"],
        }
        return descriptor

    def prune_expired(self) -> Dict[str, Any]:
        now = time.time()
        pruned = 0
        kept = 0
        for meta_path in sorted(self.metadata_root.glob("*.json")):
            try:
                payload = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                payload = None
            if not isinstance(payload, dict):
                continue
            expires_at = payload.get("expires_at")
            if payload.get("promoted"):
                kept += 1
                continue
            if isinstance(expires_at, (int, float)) and float(expires_at) <= now:
                target = self._resolve_path(payload)
                if target is not None:
                    try:
                        target.unlink(missing_ok=True)
                    except Exception:
                        pass
                try:
                    meta_path.unlink(missing_ok=True)
                except Exception:
                    pass
                pruned += 1
            else:
                kept += 1
        return {"pruned": pruned, "remaining": kept}

    def create_capture_from_bytes(
        self,
        data: bytes,
        *,
        filename: str,
        source: str,
        content_type: str = "image/png",
        capture_source: str | None = None,
        sensitivity: str | None = None,
        retention_days: int | None = None,
        conversation_id: str | None = None,
        message_id: str | None = None,
        computer_session_id: str | None = None,
        current_url: str | None = None,
        active_window: str | None = None,
    ) -> Dict[str, Any]:
        self.prune_expired()
        capture_id = str(uuid4())
        safe_name = _safe_name(filename)
        now = time.time()
        retention_value = (
            self.default_retention_days()
            if retention_days is None
            else max(0, int(retention_days))
        )
        expires_at = (
            datetime.now(tz=timezone.utc) + timedelta(days=retention_value)
        ).timestamp()
        normalized_source = str(source or "capture").strip().lower() or "capture"
        target_dir = (self.transient_root / normalized_source).resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        target = (target_dir / f"{capture_id}-{safe_name}").resolve()
        target.write_bytes(data)
        normalized_sensitivity = (
            str(sensitivity or self.default_sensitivity()).strip().lower()
        )
        if normalized_sensitivity not in SENSITIVITY_VALUES:
            normalized_sensitivity = self.default_sensitivity()
        payload = {
            "capture_id": capture_id,
            "source": normalized_source,
            "filename": safe_name,
            "content_type": content_type,
            "content_hash": _sha256_bytes(data),
            "path": str(target),
            "transient": True,
            "promoted": False,
            "sensitivity": normalized_sensitivity,
            "created_at": now,
            "created_at_iso": _iso(now),
            "updated_at": now,
            "updated_at_iso": _iso(now),
            "expires_at": expires_at,
            "expires_at_iso": _iso(expires_at),
            "conversation_id": conversation_id,
            "message_id": message_id,
            "computer_session_id": computer_session_id,
            "current_url": current_url,
            "active_window": active_window,
            "capture_source": capture_source or normalized_source,
            "attachment_ref": None,
            "memory_refs": [],
        }
        self._write_meta(payload)
        return self._descriptor(payload)

    def register_existing_file(
        self,
        path: str | Path,
        *,
        source: str,
        content_type: str = "image/png",
        filename: str | None = None,
        capture_source: str | None = None,
        sensitivity: str | None = None,
        retention_days: int | None = None,
        conversation_id: str | None = None,
        message_id: str | None = None,
        computer_session_id: str | None = None,
        current_url: str | None = None,
        active_window: str | None = None,
    ) -> Dict[str, Any]:
        target = Path(path).resolve()
        if not target.exists() or not target.is_file():
            raise FileNotFoundError(f"Capture source file does not exist: {target}")
        data = target.read_bytes()
        capture = self.create_capture_from_bytes(
            data,
            filename=filename or target.name,
            source=source,
            content_type=content_type,
            capture_source=capture_source,
            sensitivity=sensitivity,
            retention_days=retention_days,
            conversation_id=conversation_id,
            message_id=message_id,
            computer_session_id=computer_session_id,
            current_url=current_url,
            active_window=active_window,
        )
        # Reuse the existing runtime screenshot path when it is already under the transient tree.
        capture_meta = self._read_meta(str(capture["capture_id"])) or {}
        reused_target = self._resolve_path(capture_meta)
        if reused_target and reused_target != target:
            try:
                reused_target.unlink(missing_ok=True)
            except Exception:
                pass
            try:
                target_parent = target.parent.resolve()
                transient_parent = self.transient_root.resolve()
                target_parent.relative_to(transient_parent)
                capture_meta["path"] = str(target)
                capture_meta["filename"] = target.name
                capture_meta["updated_at"] = time.time()
                capture_meta["updated_at_iso"] = _iso(capture_meta["updated_at"])
                self._write_meta(capture_meta)
            except Exception:
                pass
        return self._descriptor(
            self._read_meta(str(capture["capture_id"])) or capture_meta
        )

    def list_captures(self, *, source: str | None = None) -> List[Dict[str, Any]]:
        self.prune_expired()
        items: List[Dict[str, Any]] = []
        for meta_path in sorted(self.metadata_root.glob("*.json"), reverse=True):
            try:
                payload = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            if (
                source
                and str(payload.get("source") or "").strip().lower()
                != str(source).strip().lower()
            ):
                continue
            target = self._resolve_path(payload)
            if target is None or not target.exists():
                continue
            items.append(self._descriptor(payload))
        items.sort(
            key=lambda item: (
                float(item.get("created_at") or 0.0),
                str(item.get("capture_id") or ""),
            ),
            reverse=True,
        )
        return items

    def get_capture(self, capture_id: str) -> Optional[Dict[str, Any]]:
        self.prune_expired()
        payload = self._read_meta(capture_id)
        if not isinstance(payload, dict):
            return None
        target = self._resolve_path(payload)
        if target is None or not target.exists():
            return None
        return self._descriptor(payload)

    def capture_path(self, capture_id: str) -> Optional[Path]:
        payload = self._read_meta(capture_id)
        if not isinstance(payload, dict):
            return None
        target = self._resolve_path(payload)
        if target is None or not target.exists():
            return None
        return target

    def delete_capture(self, capture_id: str) -> Dict[str, Any]:
        payload = self._read_meta(capture_id)
        if not isinstance(payload, dict):
            raise FileNotFoundError(f"Unknown capture '{capture_id}'")
        target = self._resolve_path(payload)
        if target is not None:
            try:
                target.unlink(missing_ok=True)
            except Exception:
                pass
        self._meta_path(capture_id).unlink(missing_ok=True)
        return {"capture_id": capture_id, "status": "deleted"}

    def mark_promoted(
        self,
        capture_id: str,
        *,
        attachment_ref: Dict[str, Any],
        memory_refs: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        payload = self._read_meta(capture_id)
        if not isinstance(payload, dict):
            raise FileNotFoundError(f"Unknown capture '{capture_id}'")
        now = time.time()
        payload["promoted"] = True
        payload["attachment_ref"] = dict(attachment_ref or {})
        payload["memory_refs"] = list(memory_refs or payload.get("memory_refs") or [])
        payload["updated_at"] = now
        payload["updated_at_iso"] = _iso(now)
        self._write_meta(payload)
        return self._descriptor(payload)


_capture_service: Optional[CaptureService] = None


def get_capture_service(config: Optional[Dict[str, Any]] = None) -> CaptureService:
    del config
    global _capture_service
    if _capture_service is None:
        _capture_service = CaptureService()
    return _capture_service


def set_capture_service(service: Optional[CaptureService]) -> None:
    global _capture_service
    _capture_service = service
