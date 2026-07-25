from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any, Dict, List, Optional
from uuid import uuid4

import jwt
from app.utils.device_registry import get_device_jwt_secret

SYNC_PLAN_RECEIPT_TTL_SECONDS = 15 * 60
_RECEIPT_ISSUER = "float-backend"
_RECEIPT_AUDIENCE = "float-sync-apply"
_RECEIPT_TYPE = "sync_plan"
_RECEIPT_KEY_LABEL = b"float-sync-plan-receipt-v1"


def _receipt_key() -> bytes:
    return hmac.new(
        get_device_jwt_secret().encode("utf-8"),
        _RECEIPT_KEY_LABEL,
        hashlib.sha256,
    ).digest()


def issue_sync_plan_receipt(
    *,
    context: Dict[str, Any],
    allowed: Dict[str, Dict[str, List[str]]],
    freshness: Dict[str, Dict[str, str]],
    now: Optional[int] = None,
) -> str:
    issued_at = int(time.time() if now is None else now)
    payload = {
        "iss": _RECEIPT_ISSUER,
        "aud": _RECEIPT_AUDIENCE,
        "typ": _RECEIPT_TYPE,
        "ver": 1,
        "jti": str(uuid4()),
        "iat": issued_at,
        "nbf": issued_at,
        "exp": issued_at + SYNC_PLAN_RECEIPT_TTL_SECONDS,
        "context": context,
        "allowed": allowed,
        "freshness": freshness,
    }
    return jwt.encode(payload, _receipt_key(), algorithm="HS256")


def decode_sync_plan_receipt(token: str) -> Dict[str, Any]:
    payload = jwt.decode(
        str(token or "").strip(),
        _receipt_key(),
        algorithms=["HS256"],
        issuer=_RECEIPT_ISSUER,
        audience=_RECEIPT_AUDIENCE,
        leeway=5,
        options={
            "require": ["iss", "aud", "typ", "ver", "jti", "iat", "nbf", "exp"],
        },
    )
    if payload.get("typ") != _RECEIPT_TYPE or payload.get("ver") != 1:
        raise jwt.InvalidTokenError("Invalid sync plan receipt type")
    return payload


def assert_sync_plan_authorized(
    claims: Dict[str, Any],
    *,
    context: Dict[str, Any],
    direction: str,
    sections: List[str],
    item_selections: Dict[str, List[str]],
    freshness: Dict[str, Dict[str, str]],
) -> None:
    if claims.get("context") != context:
        raise ValueError("Sync settings changed after preview. Preview changes again.")
    requested_sections = sorted(
        {str(section).strip() for section in sections if str(section).strip()}
    )
    selected_sections = sorted(
        str(section).strip() for section in item_selections if str(section).strip()
    )
    if not requested_sections or requested_sections != selected_sections:
        raise ValueError(
            "Every applied section requires an explicit reviewed item selection."
        )
    allowed_by_direction = claims.get("allowed") or {}
    allowed_sections = allowed_by_direction.get(direction) or {}
    for section in requested_sections:
        selected_ids = {
            str(item_id).strip()
            for item_id in item_selections.get(section) or []
            if str(item_id).strip()
        }
        allowed_ids = {
            str(item_id).strip()
            for item_id in allowed_sections.get(section) or []
            if str(item_id).strip()
        }
        if not selected_ids or not selected_ids.issubset(allowed_ids):
            raise ValueError("Selected changes were not included in this preview.")
    expected_freshness = claims.get("freshness") or {}
    for side in ("local", "remote"):
        expected_side = expected_freshness.get(side) or {}
        observed_side = freshness.get(side) or {}
        for section in requested_sections:
            if not hmac.compare_digest(
                str(expected_side.get(section) or ""),
                str(observed_side.get(section) or ""),
            ):
                raise ValueError(
                    "Sync data changed after preview. Preview changes again."
                )


__all__ = [
    "SYNC_PLAN_RECEIPT_TTL_SECONDS",
    "assert_sync_plan_authorized",
    "decode_sync_plan_receipt",
    "issue_sync_plan_receipt",
]
