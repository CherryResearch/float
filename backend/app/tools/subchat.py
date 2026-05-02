from __future__ import annotations

from typing import Any, Dict

from app.utils import verify_signature

_RETURN_ALIASES = {
    "",
    "done",
    "finish",
    "finished",
    "main",
    "parent",
    "return",
    "stop",
    "terminate",
}
_CONTINUE_ALIASES = {
    "continue",
    "extend",
    "more",
    "more_time",
    "need_more_time",
    "request_more_time",
}


def _normalize_action(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if raw in _CONTINUE_ALIASES:
        return "continue"
    if raw in _RETURN_ALIASES:
        return "return"
    return "return"


def subchat(
    action: str = "",
    note: str = "",
    requested_minutes: int = 0,
    parent_session_id: str = "",
    parent_message_id: str = "",
    *,
    user: str,
    signature: str,
) -> Dict[str, Any]:
    """Signal subchat control intent to Float.

    With no arguments, this means the child chat is done and should return to
    the parent/main chat. Use ``action="continue"`` to ask for more time.
    """

    payload = {
        "action": action or "",
        "note": note or "",
        "requested_minutes": int(requested_minutes or 0),
        "parent_session_id": parent_session_id or "",
        "parent_message_id": parent_message_id or "",
    }
    verify_signature(signature, user, "subchat", payload)
    normalized = _normalize_action(action)
    control = {
        "kind": "subchat_control",
        "action": "continue" if normalized == "continue" else "return_to_parent",
        "parent_session_id": payload["parent_session_id"],
        "parent_message_id": payload["parent_message_id"],
    }
    if normalized == "continue":
        control["requested_minutes"] = max(0, payload["requested_minutes"])
        message = "Subchat requested more time."
    else:
        message = "Subchat marked complete; return to the main chat."
    return {
        "status": "ok",
        "action": normalized,
        "message": message,
        "note": payload["note"],
        "control": control,
    }


__all__ = ["subchat"]
