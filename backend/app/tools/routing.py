from __future__ import annotations

from typing import Any, Dict

from app.utils import verify_signature


def route_to_local_model(
    target_mode: str = "local",
    target_model: str = "",
    reason: str = "",
    sensitivity: str = "",
    labels: str = "",
    source_mode: str = "",
    source_model: str = "",
    *,
    user: str,
    signature: str,
) -> Dict[str, Any]:
    """Record a user-approved model routing choice for chat continuation."""

    raw_mode = str(target_mode or "local").strip()
    normalized_mode = raw_mode.lower() or "local"
    if normalized_mode not in {"local", "server", "api"}:
        normalized_mode = "local"
    payload = {
        "target_mode": raw_mode or "local",
        "target_model": str(target_model or "").strip(),
        "reason": str(reason or "").strip(),
        "sensitivity": str(sensitivity or "").strip(),
        "labels": str(labels or "").strip(),
        "source_mode": str(source_mode or "").strip(),
        "source_model": str(source_model or "").strip(),
    }
    verify_signature(signature, user, "route_to_local_model", payload)
    return {
        "status": "accepted",
        "route": {
            "mode": normalized_mode,
            "model": payload["target_model"],
        },
        "reason": payload["reason"],
        "sensitivity": payload["sensitivity"],
        "labels": payload["labels"],
        "source": {
            "mode": payload["source_mode"],
            "model": payload["source_model"],
        },
    }
