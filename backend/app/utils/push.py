import json
from typing import Any, Dict, Optional

from app import config as app_config

try:
    # pywebpush raises ImportError if not installed in env
    from pywebpush import WebPushException, webpush
except Exception:  # pragma: no cover - optional dependency
    WebPushException = Exception  # type: ignore
    webpush = None  # type: ignore


def vapid_config() -> Dict[str, str]:
    cfg = app_config.load_config()
    return {
        "publicKey": cfg.get("vapid_public_key", ""),
        "privateKey": cfg.get("vapid_private_key", ""),
        "subject": cfg.get("vapid_subject", "mailto:admin@example.com"),
    }


def can_send_push() -> bool:
    vapid = vapid_config()
    return bool(vapid.get("publicKey") and vapid.get("privateKey") and webpush)


def send_web_push(subscription: Dict[str, Any], payload: Dict[str, Any]) -> Optional[str]:
    """Send a web push. Returns error message string on failure or None on success."""
    if not can_send_push():
        return "push_not_configured"
    vapid = vapid_config()
    try:
        webpush(  # type: ignore[misc]
            subscription_info=subscription,
            data=json.dumps(payload),
            vapid_private_key=vapid["privateKey"],
            vapid_claims={"sub": vapid["subject"]},
            ttl=300,
        )
        return None
    except WebPushException as e:  # pragma: no cover - network dependent
        return str(e)



