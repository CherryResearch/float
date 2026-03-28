import json
import time
import uuid
from typing import Any, Dict, List, Optional

from app import config as app_config

REVIEWS_PATH = app_config.DEFAULT_DATABASES_DIR / "sync_reviews.json"


def _load_state() -> Dict[str, Any]:
    if not REVIEWS_PATH.exists():
        return {"reviews": {}}
    try:
        payload = json.loads(REVIEWS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"reviews": {}}
    if not isinstance(payload, dict):
        return {"reviews": {}}
    payload.setdefault("reviews", {})
    return payload


def _save_state(state: Dict[str, Any]) -> None:
    REVIEWS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = REVIEWS_PATH.with_suffix(f"{REVIEWS_PATH.suffix}.tmp")
    tmp_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp_path.replace(REVIEWS_PATH)


def _now() -> float:
    return time.time()


def create_review(payload: Dict[str, Any]) -> Dict[str, Any]:
    state = _load_state()
    now = _now()
    review_id = str(uuid.uuid4())
    review = {
        "id": review_id,
        "status": "pending",
        "created_at": now,
        "updated_at": now,
        **dict(payload or {}),
    }
    state["reviews"][review_id] = review
    _save_state(state)
    return review


def get_review(review_id: str) -> Optional[Dict[str, Any]]:
    state = _load_state()
    review = state.get("reviews", {}).get(str(review_id or "").strip())
    return dict(review) if isinstance(review, dict) else None


def update_review(review_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    state = _load_state()
    review = state.get("reviews", {}).get(str(review_id or "").strip())
    if not isinstance(review, dict):
        return None
    next_review = {**review, **dict(updates or {}), "updated_at": _now()}
    state["reviews"][review_id] = next_review
    _save_state(state)
    return next_review


def list_reviews(
    *, status: Optional[str] = None, limit: Optional[int] = None
) -> List[Dict[str, Any]]:
    reviews = [
        dict(value)
        for value in (_load_state().get("reviews") or {}).values()
        if isinstance(value, dict)
    ]
    if status:
        needle = str(status).strip().lower()
        reviews = [
            review
            for review in reviews
            if str(review.get("status") or "").strip().lower() == needle
        ]
    reviews.sort(
        key=lambda review: (
            float(review.get("updated_at") or 0),
            float(review.get("created_at") or 0),
        ),
        reverse=True,
    )
    if isinstance(limit, int) and limit >= 0:
        return reviews[:limit]
    return reviews
