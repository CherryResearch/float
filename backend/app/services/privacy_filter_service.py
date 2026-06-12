"""On-write text privacy classification helpers.

This is intentionally a first-pass aid: it can suggest or apply sensitivity
escalations, but explicit user choices always win.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from app.model_registry import resolve_model_alias
from app.utils import user_settings

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "openai/privacy-filter"
VALID_MODES = {"off", "auto", "always"}
DEFAULT_MODE = "off"
DEFAULT_MIN_SCORE = 0.5
DEFAULT_MAX_CHARS = 60000

SENSITIVITY_RANK = {
    "mundane": 0,
    "public": 0,
    "personal": 1,
    "protected": 2,
    "secret": 3,
}
RANK_TO_SENSITIVITY = {
    0: "mundane",
    1: "personal",
    2: "protected",
    3: "secret",
}
LABEL_SENSITIVITY = {
    "account_number": "secret",
    "secret": "secret",
    "private_address": "protected",
    "private_email": "protected",
    "private_phone": "protected",
    "private_url": "protected",
    "private_person": "personal",
    "private_date": "personal",
}

_CLASSIFIER: Any = None
_CLASSIFIER_MODEL: Optional[str] = None
_CLASSIFIER_ERROR: Optional[str] = None


@dataclass
class PrivacyDecision:
    mode: str
    status: str
    action: str
    model: str
    checked_at: Optional[float] = None
    suggested_sensitivity: Optional[str] = None
    applied_sensitivity: Optional[str] = None
    applied_source: Optional[str] = None
    labels: List[str] = field(default_factory=list)
    label_counts: Dict[str, int] = field(default_factory=dict)
    max_score: Optional[float] = None
    truncated: bool = False
    error: Optional[str] = None


def normalize_mode(value: Any) -> str:
    raw = str(value or os.getenv("FLOAT_PRIVACY_FILTER_MODE") or DEFAULT_MODE)
    mode = raw.strip().lower()
    return mode if mode in VALID_MODES else DEFAULT_MODE


def _load_settings(settings: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if isinstance(settings, dict):
        return settings
    try:
        loaded = user_settings.load_settings()
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _settings(settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    loaded = _load_settings(settings)
    mode = normalize_mode(loaded.get("privacy_filter_mode", DEFAULT_MODE))
    model = str(loaded.get("privacy_filter_model") or DEFAULT_MODEL).strip()
    model = str(resolve_model_alias(model) or model or DEFAULT_MODEL).strip()
    try:
        min_score = float(loaded.get("privacy_filter_min_score", DEFAULT_MIN_SCORE))
    except Exception:
        min_score = DEFAULT_MIN_SCORE
    try:
        max_chars = int(loaded.get("privacy_filter_max_chars", DEFAULT_MAX_CHARS))
    except Exception:
        max_chars = DEFAULT_MAX_CHARS
    return {
        "mode": mode,
        "model": model or DEFAULT_MODEL,
        "min_score": max(0.0, min(1.0, min_score)),
        "max_chars": max(1000, max_chars),
    }


def normalize_sensitivity(value: Any) -> Optional[str]:
    text = str(value or "").strip().lower()
    return text if text in SENSITIVITY_RANK else None


def _rank(value: Any) -> int:
    return SENSITIVITY_RANK.get(normalize_sensitivity(value) or "mundane", 0)


def _label_from_entity(value: Any) -> str:
    label = str(value or "").strip()
    if "-" in label:
        prefix, rest = label.split("-", 1)
        if prefix.upper() in {"B", "I", "E", "S"}:
            label = rest
    return label.strip().lower()


def _suggested_from_labels(labels: Iterable[str]) -> Optional[str]:
    max_rank = 0
    for label in labels:
        sensitivity = LABEL_SENSITIVITY.get(str(label or "").strip().lower())
        max_rank = max(max_rank, _rank(sensitivity))
    if max_rank <= 0:
        return None
    return RANK_TO_SENSITIVITY[max_rank]


def _get_classifier(model_name: str):
    global _CLASSIFIER, _CLASSIFIER_ERROR, _CLASSIFIER_MODEL
    if _CLASSIFIER is not None and _CLASSIFIER_MODEL == model_name:
        return _CLASSIFIER
    if _CLASSIFIER_ERROR and _CLASSIFIER_MODEL == model_name:
        raise RuntimeError(_CLASSIFIER_ERROR)
    try:
        from transformers import pipeline

        _CLASSIFIER = pipeline(
            task="token-classification",
            model=model_name,
        )
        _CLASSIFIER_MODEL = model_name
        _CLASSIFIER_ERROR = None
        return _CLASSIFIER
    except Exception as exc:  # pragma: no cover - depends on local model env
        _CLASSIFIER = None
        _CLASSIFIER_MODEL = model_name
        _CLASSIFIER_ERROR = str(exc)
        raise


def _classify_with_transformers(
    text: str,
    *,
    model: str,
    min_score: float,
    max_chars: int,
) -> PrivacyDecision:
    checked_at = time.time()
    candidate = str(text or "")
    truncated = len(candidate) > max_chars
    if truncated:
        candidate = candidate[:max_chars]
    classifier = _get_classifier(model)
    output = classifier(candidate, aggregation_strategy="simple")
    if output and isinstance(output, list) and output and isinstance(output[0], list):
        output = output[0]

    labels: list[str] = []
    counts: dict[str, int] = {}
    max_score: Optional[float] = None
    for item in output if isinstance(output, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            score = float(item.get("score", 0.0))
        except Exception:
            score = 0.0
        if score < min_score:
            continue
        label = _label_from_entity(item.get("entity_group") or item.get("entity"))
        if not label or label == "o":
            continue
        labels.append(label)
        counts[label] = counts.get(label, 0) + 1
        max_score = score if max_score is None else max(max_score, score)

    unique_labels = sorted(counts)
    suggested = _suggested_from_labels(unique_labels)
    return PrivacyDecision(
        mode="auto",
        status="matched" if suggested else "no_match",
        action="checked",
        model=model,
        checked_at=checked_at,
        suggested_sensitivity=suggested,
        labels=unique_labels,
        label_counts=counts,
        max_score=max_score,
        truncated=truncated,
    )


def decide_sensitivity(
    text: Any,
    *,
    explicit_sensitivity: Any = None,
    existing_sensitivity: Any = None,
    existing_sensitivity_source: Any = None,
    settings: Optional[Dict[str, Any]] = None,
    purpose: str = "write",
) -> PrivacyDecision:
    cfg = _settings(settings)
    mode = cfg["mode"]
    model = cfg["model"]
    explicit = normalize_sensitivity(explicit_sensitivity)
    existing = normalize_sensitivity(existing_sensitivity)
    existing_source = str(existing_sensitivity_source or "").strip().lower()

    if mode == "off":
        return PrivacyDecision(
            mode=mode,
            status="disabled",
            action="not_checked",
            model=model,
            applied_sensitivity=explicit or existing,
            applied_source="user" if explicit else None,
        )
    if explicit and mode != "always":
        return PrivacyDecision(
            mode=mode,
            status="skipped",
            action="kept_user",
            model=model,
            applied_sensitivity=explicit,
            applied_source="user",
        )

    try:
        decision = _classify_with_transformers(
            str(text or ""),
            model=model,
            min_score=float(cfg["min_score"]),
            max_chars=int(cfg["max_chars"]),
        )
    except Exception as exc:
        logger.warning("privacy filter unavailable for %s: %s", purpose, exc)
        return PrivacyDecision(
            mode=mode,
            status="unavailable",
            action="not_checked",
            model=model,
            checked_at=time.time(),
            applied_sensitivity=explicit or existing,
            applied_source="user" if explicit else None,
            error=str(exc),
        )

    decision.mode = mode
    suggested = normalize_sensitivity(decision.suggested_sensitivity)
    base = explicit or existing
    if explicit:
        decision.action = "kept_user"
        decision.applied_sensitivity = explicit
        decision.applied_source = "user"
        return decision
    if existing_source == "user":
        decision.action = "kept_existing_user"
        decision.applied_sensitivity = existing
        decision.applied_source = "user"
        return decision
    if suggested and _rank(suggested) > _rank(base):
        decision.action = "applied"
        decision.applied_sensitivity = suggested
        decision.applied_source = "privacy_filter"
        return decision
    decision.action = "kept_existing" if base else "none"
    decision.applied_sensitivity = base
    decision.applied_source = existing_source or None
    return decision


def metadata_updates(decision: PrivacyDecision) -> Dict[str, Any]:
    updates: Dict[str, Any] = {}
    if decision.applied_source:
        updates["sensitivity_source"] = decision.applied_source
    if decision.status in {"disabled", "skipped"}:
        return updates
    updates.update(
        {
            "privacy_filter_status": decision.status,
            "privacy_filter_mode": decision.mode,
            "privacy_filter_model": decision.model,
            "privacy_filter_action": decision.action,
            "privacy_filter_checked_at": decision.checked_at,
            "privacy_filter_suggested_sensitivity": decision.suggested_sensitivity,
            "privacy_filter_applied_sensitivity": decision.applied_sensitivity,
            "privacy_filter_detected_labels": ",".join(decision.labels),
            "privacy_filter_max_score": decision.max_score,
            "privacy_filter_truncated": bool(decision.truncated),
        }
    )
    if decision.label_counts:
        updates["privacy_filter_label_counts"] = dict(decision.label_counts)
    if decision.error:
        updates["privacy_filter_error"] = decision.error
    return {key: value for key, value in updates.items() if value is not None}


def apply_to_metadata(
    text: Any,
    metadata: Optional[Dict[str, Any]],
    *,
    settings: Optional[Dict[str, Any]] = None,
    purpose: str = "knowledge",
) -> Dict[str, Any]:
    meta = dict(metadata or {})
    metadata_sensitivity = normalize_sensitivity(meta.get("sensitivity"))
    sensitivity_source = str(meta.get("sensitivity_source") or "").strip().lower()
    explicit = (
        metadata_sensitivity
        if metadata_sensitivity and sensitivity_source != "privacy_filter"
        else None
    )
    decision = decide_sensitivity(
        text,
        explicit_sensitivity=explicit,
        existing_sensitivity=meta.get("sensitivity"),
        existing_sensitivity_source=meta.get("sensitivity_source"),
        settings=settings,
        purpose=purpose,
    )
    if decision.applied_sensitivity:
        meta["sensitivity"] = decision.applied_sensitivity
    meta.update(metadata_updates(decision))
    return meta


def notice(decision: PrivacyDecision) -> Optional[str]:
    if decision.status == "disabled":
        return None
    if decision.status == "skipped":
        return None
    if decision.status == "unavailable":
        return "privacy filter unavailable; sensitivity unchanged"
    labels = ", ".join(decision.labels) if decision.labels else "no private spans"
    if decision.action == "applied" and decision.applied_sensitivity:
        return (
            f"privacy filter set sensitivity to {decision.applied_sensitivity} "
            f"from {labels}"
        )
    if decision.action in {"kept_user", "kept_existing_user"}:
        kept = decision.applied_sensitivity or "existing"
        if decision.suggested_sensitivity:
            return (
                f"privacy filter suggested {decision.suggested_sensitivity} "
                f"from {labels}; kept user sensitivity {kept}"
            )
        return f"privacy filter checked; kept user sensitivity {kept}"
    if decision.suggested_sensitivity:
        return (
            f"privacy filter suggested {decision.suggested_sensitivity} "
            f"from {labels}; sensitivity unchanged"
        )
    return "privacy filter checked; sensitivity unchanged"


__all__ = [
    "PrivacyDecision",
    "apply_to_metadata",
    "decide_sensitivity",
    "metadata_updates",
    "normalize_mode",
    "normalize_sensitivity",
    "notice",
]
