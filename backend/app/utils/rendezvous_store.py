import json
import secrets
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from app import config as app_config

REPO_ROOT = Path(__file__).resolve().parents[3]
RENDEZVOUS_PATH = app_config.DEFAULT_DATABASES_DIR / "gateway_rendezvous.json"
LEGACY_RENDEZVOUS_PATH = REPO_ROOT / "gateway_rendezvous.json"


def _load_state() -> Dict[str, Any]:
    for path in (RENDEZVOUS_PATH, LEGACY_RENDEZVOUS_PATH):
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                payload.setdefault("offers", {})
                payload.setdefault("sessions", {})
                if path == LEGACY_RENDEZVOUS_PATH and path != RENDEZVOUS_PATH:
                    _save_state(payload)
                    try:
                        path.unlink()
                    except Exception:
                        pass
                return payload
        except Exception:
            pass
    return {"offers": {}, "sessions": {}}


def _save_state(state: Dict[str, Any]) -> None:
    RENDEZVOUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = RENDEZVOUS_PATH.with_suffix(RENDEZVOUS_PATH.suffix + ".tmp")
    tmp_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp_path.replace(RENDEZVOUS_PATH)
    if LEGACY_RENDEZVOUS_PATH.exists() and LEGACY_RENDEZVOUS_PATH != RENDEZVOUS_PATH:
        try:
            LEGACY_RENDEZVOUS_PATH.unlink()
        except Exception:
            pass


def _cleanup_legacy_state() -> None:
    if not LEGACY_RENDEZVOUS_PATH.exists():
        return
    if RENDEZVOUS_PATH.exists():
        try:
            LEGACY_RENDEZVOUS_PATH.unlink()
        except Exception:
            pass
        return
    try:
        payload = json.loads(LEGACY_RENDEZVOUS_PATH.read_text(encoding="utf-8"))
    except Exception:
        try:
            LEGACY_RENDEZVOUS_PATH.unlink()
        except Exception:
            pass
        return
    if isinstance(payload, dict):
        payload.setdefault("offers", {})
        payload.setdefault("sessions", {})
        _save_state(payload)
    try:
        LEGACY_RENDEZVOUS_PATH.unlink()
    except Exception:
        pass


_cleanup_legacy_state()


def _now() -> float:
    return time.time()


def _pairing_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(8))


def _purge_expired(state: Dict[str, Any]) -> Dict[str, Any]:
    now = _now()
    offers = {
        key: value
        for key, value in (state.get("offers") or {}).items()
        if isinstance(value, dict) and float(value.get("expires_at") or 0) > now
    }
    sessions = {
        key: value
        for key, value in (state.get("sessions") or {}).items()
        if isinstance(value, dict) and float(value.get("expires_at") or 0) > now
    }
    next_state = {"offers": offers, "sessions": sessions}
    if next_state != state:
        _save_state(next_state)
    return next_state


def create_offer(
    *,
    device_name: str,
    public_key: str,
    requested_scopes: List[str],
    candidate_urls: Optional[List[str]] = None,
    relay_url: Optional[str] = None,
    ttl_seconds: int = 600,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    state = _purge_expired(_load_state())
    offer_id = str(uuid.uuid4())
    code = _pairing_code()
    now = _now()
    offer = {
        "offer_id": offer_id,
        "code": code,
        "device_name": str(device_name or "").strip() or "device",
        "public_key": str(public_key or "").strip(),
        "requested_scopes": list(requested_scopes or []),
        "candidate_urls": list(candidate_urls or []),
        "relay_url": str(relay_url or "").strip() or None,
        "created_at": now,
        "expires_at": now + int(ttl_seconds),
        "accepted_at": None,
        "accepted_by": None,
        "metadata": dict(metadata or {}),
    }
    state["offers"][offer_id] = offer
    _save_state(state)
    return offer


def get_offer_by_code(code: str) -> Optional[Dict[str, Any]]:
    state = _purge_expired(_load_state())
    needle = str(code or "").strip().upper()
    for offer in (state.get("offers") or {}).values():
        if not isinstance(offer, dict):
            continue
        if str(offer.get("code") or "").strip().upper() == needle:
            return offer
    return None


def accept_offer(
    code: str,
    *,
    device_name: str,
    public_key: str,
    candidate_urls: Optional[List[str]] = None,
    relay_url: Optional[str] = None,
) -> Dict[str, Any]:
    state = _purge_expired(_load_state())
    needle = str(code or "").strip().upper()
    for offer_id, offer in (state.get("offers") or {}).items():
        if not isinstance(offer, dict):
            continue
        if str(offer.get("code") or "").strip().upper() != needle:
            continue
        if offer.get("accepted_at"):
            raise ValueError("Pairing offer has already been used")
        offer["accepted_at"] = _now()
        offer["accepted_by"] = {
            "device_name": str(device_name or "").strip() or "device",
            "public_key": str(public_key or "").strip(),
            "candidate_urls": list(candidate_urls or []),
            "relay_url": str(relay_url or "").strip() or None,
        }
        state["offers"][offer_id] = offer
        _save_state(state)
        return offer
    raise ValueError("Pairing offer was not found or has expired")


def create_session(
    *,
    peer_device_id: str,
    scopes: List[str],
    candidate_urls: Optional[List[str]] = None,
    relay_url: Optional[str] = None,
    ttl_seconds: int = 900,
) -> Dict[str, Any]:
    state = _purge_expired(_load_state())
    session_id = str(uuid.uuid4())
    now = _now()
    session = {
        "session_id": session_id,
        "session_token": secrets.token_urlsafe(24),
        "peer_device_id": str(peer_device_id or "").strip(),
        "scopes": list(scopes or []),
        "candidate_urls": list(candidate_urls or []),
        "relay_url": str(relay_url or "").strip() or None,
        "created_at": now,
        "expires_at": now + int(ttl_seconds),
    }
    state["sessions"][session_id] = session
    _save_state(state)
    return session
