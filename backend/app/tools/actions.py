from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.utils import verify_signature

_ACTION_HISTORY_SERVICE = None


def set_action_history_service(service) -> None:
    global _ACTION_HISTORY_SERVICE
    _ACTION_HISTORY_SERVICE = service


def _service():
    if _ACTION_HISTORY_SERVICE is None:
        raise RuntimeError("action history service not available")
    return _ACTION_HISTORY_SERVICE


def list_actions(
    conversation_id: str = "",
    response_id: str = "",
    include_reverted: bool = True,
    limit: int = 20,
    *,
    user: str,
    signature: str,
) -> Dict[str, Any]:
    payload = {
        "conversation_id": conversation_id or "",
        "response_id": response_id or "",
        "include_reverted": bool(include_reverted),
        "limit": int(limit or 20),
    }
    verify_signature(signature, user, "list_actions", payload)
    actions = _service().list_actions(
        conversation_id=conversation_id or None,
        response_id=response_id or None,
        include_reverted=bool(include_reverted),
        limit=max(1, min(int(limit or 20), 200)),
    )
    return {"actions": actions, "count": len(actions)}


def read_action_diff(
    action_id: str,
    *,
    user: str,
    signature: str,
) -> Dict[str, Any]:
    payload = {"action_id": action_id}
    verify_signature(signature, user, "read_action_diff", payload)
    detail = _service().get_action_detail(action_id)
    if detail is None:
        raise ValueError(f"Unknown action: {action_id}")
    return detail


def revert_actions(
    action_ids: Optional[List[str]] = None,
    response_id: str = "",
    conversation_id: str = "",
    force: bool = False,
    *,
    user: str,
    signature: str,
) -> Dict[str, Any]:
    payload = {
        "action_ids": list(action_ids or []),
        "response_id": response_id or "",
        "conversation_id": conversation_id or "",
        "force": bool(force),
    }
    verify_signature(signature, user, "revert_actions", payload)
    if not payload["action_ids"] and not response_id and not conversation_id:
        raise ValueError("Provide action_ids, response_id, or conversation_id")
    return _service().revert_actions(
        action_ids=payload["action_ids"] or None,
        response_id=response_id or None,
        conversation_id=conversation_id or None,
        force=bool(force),
    )


__all__ = [
    "list_actions",
    "read_action_diff",
    "revert_actions",
    "set_action_history_service",
]
