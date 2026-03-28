import json
import os
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from app import config as app_config

import jwt

REPO_ROOT = Path(__file__).resolve().parents[3]
DEVICES_PATH = app_config.DEFAULT_DATABASES_DIR / "devices.json"
LEGACY_DEVICES_PATH = REPO_ROOT / "devices.json"


@dataclass
class DeviceRecord:
    id: str
    name: str
    public_key: str
    capabilities: Dict[str, Any]
    created_at: float
    last_seen: float


def _load_all() -> Dict[str, Any]:
    for path in (DEVICES_PATH, LEGACY_DEVICES_PATH):
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                continue
            if path == LEGACY_DEVICES_PATH and path != DEVICES_PATH:
                _save_all(payload)
                try:
                    path.unlink()
                except Exception:
                    pass
            return payload
        except Exception:
            continue
    return {}


def _save_all(data: Dict[str, Any]) -> None:
    DEVICES_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEVICES_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    if LEGACY_DEVICES_PATH.exists() and LEGACY_DEVICES_PATH != DEVICES_PATH:
        try:
            LEGACY_DEVICES_PATH.unlink()
        except Exception:
            pass


def _normalize_capabilities(value: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _coerce_record(value: Dict[str, Any]) -> DeviceRecord:
    return DeviceRecord(
        id=str(value.get("id") or "").strip(),
        name=str(value.get("name") or "").strip() or "device",
        public_key=str(value.get("public_key") or "").strip(),
        capabilities=_normalize_capabilities(value.get("capabilities")),
        created_at=float(value.get("created_at") or 0),
        last_seen=float(value.get("last_seen") or 0),
    )


def register_device(
    public_key: str,
    name: Optional[str] = None,
    capabilities: Optional[Dict[str, Any]] = None,
) -> DeviceRecord:
    devices = _load_all()
    device_id = str(uuid.uuid4())
    now = time.time()
    record = DeviceRecord(
        id=device_id,
        name=name or f"device-{device_id[:8]}",
        public_key=public_key,
        capabilities=_normalize_capabilities(capabilities),
        created_at=now,
        last_seen=now,
    )
    devices[device_id] = asdict(record)
    _save_all(devices)
    return record


def get_device(device_id: str) -> Optional[Dict[str, Any]]:
    return _load_all().get(device_id)


def get_device_by_public_key(public_key: str) -> Optional[Dict[str, Any]]:
    needle = str(public_key or "").strip()
    if not needle:
        return None
    for record in _load_all().values():
        if not isinstance(record, dict):
            continue
        if str(record.get("public_key") or "").strip() == needle:
            return record
    return None


def register_or_update_device(
    public_key: str,
    *,
    name: Optional[str] = None,
    capabilities: Optional[Dict[str, Any]] = None,
) -> DeviceRecord:
    existing = get_device_by_public_key(public_key)
    if existing and existing.get("id"):
        updated = update_device(
            str(existing["id"]),
            name=name or str(existing.get("name") or "").strip(),
            capabilities=capabilities
            if capabilities is not None
            else _normalize_capabilities(existing.get("capabilities")),
        )
        if updated is not None:
            return updated
    return register_device(public_key, name=name, capabilities=capabilities)


def update_device(
    device_id: str,
    *,
    name: Optional[str] = None,
    capabilities: Optional[Dict[str, Any]] = None,
) -> Optional[DeviceRecord]:
    devices = _load_all()
    rec = devices.get(device_id)
    if not isinstance(rec, dict):
        return None
    if name is not None:
        next_name = str(name).strip()
        if next_name:
            rec["name"] = next_name
    if capabilities is not None:
        rec["capabilities"] = _normalize_capabilities(capabilities)
    rec["last_seen"] = time.time()
    devices[device_id] = rec
    _save_all(devices)
    return _coerce_record(rec)


def delete_device(device_id: str) -> bool:
    devices = _load_all()
    if device_id not in devices:
        return False
    del devices[device_id]
    _save_all(devices)
    return True


def touch_device(device_id: str) -> None:
    devices = _load_all()
    rec = devices.get(device_id)
    if not rec:
        return
    rec["last_seen"] = time.time()
    devices[device_id] = rec
    _save_all(devices)


def list_devices() -> Dict[str, Any]:
    return _load_all()


def issue_device_token(
    device_id: str, scopes: Optional[list[str]] = None, ttl_seconds: int = 3600
) -> str:
    secret = os.getenv("DEVICE_JWT_SECRET", "dev-secret-change-me")
    now = int(time.time())
    payload = {
        "iss": "float-backend",
        "sub": device_id,
        "typ": "device",
        "scopes": scopes or ["sync", "stream"],
        "iat": now,
        "nbf": now,
        "exp": now + int(ttl_seconds),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, secret, algorithm="HS256")
