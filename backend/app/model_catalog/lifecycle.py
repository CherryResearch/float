from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, Iterable, Optional

LIFECYCLE_SOURCE_URL = "https://developers.openai.com/api/docs/deprecations"
LIFECYCLE_VERIFIED_AT = "2026-07-09"


@dataclass(frozen=True)
class LifecycleRule:
    shutdown_at: str
    replacement: Optional[str] = None
    announced_at: Optional[str] = None


def _rules(
    model_ids: Iterable[str],
    *,
    shutdown_at: str,
    replacement: Optional[str],
    announced_at: str,
) -> Dict[str, LifecycleRule]:
    rule = LifecycleRule(
        shutdown_at=shutdown_at,
        replacement=replacement,
        announced_at=announced_at,
    )
    return {model_id.lower(): rule for model_id in model_ids}


# This is intentionally an exact-ID policy. Family-wide guesses would hide valid
# provider models merely because a related snapshot was deprecated.
OPENAI_MODEL_LIFECYCLE_RULES: Dict[str, LifecycleRule] = {
    **_rules(
        [
            "computer-use-preview-2025-03-11",
            "computer-use-preview",
        ],
        shutdown_at="2026-07-23",
        replacement="gpt-5.4-mini",
        announced_at="2026-04-22",
    ),
    **_rules(
        [
            "gpt-4o-mini-search-preview-2025-03-11",
            "gpt-4o-search-preview-2025-03-11",
        ],
        shutdown_at="2026-07-23",
        replacement="gpt-5.4-mini",
        announced_at="2026-04-22",
    ),
    **_rules(
        ["gpt-4o-mini-tts-2025-03-20"],
        shutdown_at="2026-07-23",
        replacement="gpt-4o-mini-tts-2025-12-15",
        announced_at="2026-04-22",
    ),
    **_rules(
        [
            "gpt-5-chat-latest",
            "gpt-5-codex",
            "gpt-5.1-chat-latest",
            "gpt-5.1-codex",
            "gpt-5.1-codex-max",
        ],
        shutdown_at="2026-07-23",
        replacement="gpt-5.5",
        announced_at="2026-04-22",
    ),
    **_rules(
        ["gpt-5.1-codex-mini"],
        shutdown_at="2026-07-23",
        replacement="gpt-5.4-mini",
        announced_at="2026-04-22",
    ),
    **_rules(
        ["gpt-audio-mini-2025-10-06"],
        shutdown_at="2026-07-23",
        replacement="gpt-audio-1.5",
        announced_at="2026-04-22",
    ),
    **_rules(
        ["gpt-realtime-mini-2025-10-06"],
        shutdown_at="2026-07-23",
        replacement="gpt-realtime-mini",
        announced_at="2026-04-22",
    ),
    **_rules(
        ["o3-deep-research-2025-06-26", "o3-deep-research"],
        shutdown_at="2026-07-23",
        replacement="gpt-5.5-pro",
        announced_at="2026-04-22",
    ),
    **_rules(
        ["o4-mini-deep-research-2025-06-26", "o4-mini-deep-research"],
        shutdown_at="2026-07-23",
        replacement="gpt-5.5-pro",
        announced_at="2026-04-22",
    ),
    **_rules(
        ["gpt-5.2-codex"],
        shutdown_at="2026-07-23",
        replacement="gpt-5.5",
        announced_at="2026-04-22",
    ),
    **_rules(
        ["gpt-5.2-chat-latest", "gpt-5.3-chat-latest"],
        shutdown_at="2026-08-10",
        replacement="gpt-5.5",
        announced_at="2026-05-08",
    ),
    **_rules(
        ["gpt-3.5-turbo-instruct", "babbage-002", "davinci-002", "gpt-3.5-turbo-1106"],
        shutdown_at="2026-09-28",
        replacement="gpt-5.4-mini",
        announced_at="2025-09-15",
    ),
    **_rules(
        ["gpt-3.5-turbo-0125", "gpt-3.5-turbo", "gpt-3.5-turbo-completions"],
        shutdown_at="2026-10-23",
        replacement="gpt-5.4-mini",
        announced_at="2026-04-22",
    ),
    **_rules(
        [
            "gpt-4-0613",
            "gpt-4",
            "gpt-4-0613-completions",
            "gpt-4-completions",
            "gpt-4-1106-preview",
            "gpt-4-turbo",
            "gpt-4-turbo-2024-04-09",
            "gpt-4-turbo-completions",
            "gpt-4o-2024-05-13",
        ],
        shutdown_at="2026-10-23",
        replacement="gpt-5.5",
        announced_at="2026-04-22",
    ),
    **_rules(
        ["gpt-4.1-nano", "gpt-4.1-nano-2025-04-14"],
        shutdown_at="2026-10-23",
        replacement="gpt-5.4-nano",
        announced_at="2026-04-22",
    ),
    **_rules(
        ["o1-2024-12-17", "o1", "o3-mini-2025-01-31", "o3-mini"],
        shutdown_at="2026-10-23",
        replacement="gpt-5.5",
        announced_at="2026-04-22",
    ),
    **_rules(
        ["o1-pro-2025-03-19", "o1-pro"],
        shutdown_at="2026-10-23",
        replacement="gpt-5.5-pro",
        announced_at="2026-04-22",
    ),
    **_rules(
        ["ft-o4-mini-2025-04-16", "o4-mini-2025-04-16", "o4-mini"],
        shutdown_at="2026-10-23",
        replacement="gpt-5.4-mini",
        announced_at="2026-04-22",
    ),
    **_rules(
        ["gpt-5-2025-08-07"],
        shutdown_at="2026-12-11",
        replacement="gpt-5.5",
        announced_at="2026-06-11",
    ),
    **_rules(
        ["gpt-5-mini-2025-08-07"],
        shutdown_at="2026-12-11",
        replacement="gpt-5.4-mini",
        announced_at="2026-06-11",
    ),
    **_rules(
        ["gpt-5-nano-2025-08-07"],
        shutdown_at="2026-12-11",
        replacement="gpt-5.4-nano",
        announced_at="2026-06-11",
    ),
    **_rules(
        ["gpt-5-pro-2025-10-06", "o3-pro-2025-06-10"],
        shutdown_at="2026-12-11",
        replacement="gpt-5.5-pro",
        announced_at="2026-06-11",
    ),
    **_rules(
        ["o3-2025-04-16"],
        shutdown_at="2026-12-11",
        replacement="gpt-5.5",
        announced_at="2026-06-11",
    ),
    **_rules(
        [
            "gpt-4o-realtime-preview",
            "gpt-4o-realtime-preview-2025-06-03",
            "gpt-4o-realtime-preview-2024-12-17",
            "gpt-4o-realtime-preview-2024-10-01",
        ],
        shutdown_at="2026-05-07",
        replacement="gpt-realtime-1.5",
        announced_at="2025-09-15",
    ),
    **_rules(
        ["gpt-4o-mini-realtime-preview"],
        shutdown_at="2026-05-07",
        replacement="gpt-realtime-mini",
        announced_at="2025-09-15",
    ),
    **_rules(
        ["gpt-4o-audio-preview", "gpt-4o-audio-preview-2024-10-01"],
        shutdown_at="2026-05-07",
        replacement="gpt-audio-1.5",
        announced_at="2025-09-15",
    ),
    **_rules(
        ["gpt-4o-mini-audio-preview"],
        shutdown_at="2026-05-07",
        replacement="gpt-audio-mini",
        announced_at="2025-09-15",
    ),
}


def model_lifecycle(model_id: str, *, as_of: Optional[date] = None) -> Dict[str, Any]:
    normalized = str(model_id or "").strip().lower()
    today = as_of or date.today()
    rule = OPENAI_MODEL_LIFECYCLE_RULES.get(normalized)
    if rule:
        shutdown = date.fromisoformat(rule.shutdown_at)
        return {
            "status": "removed" if shutdown < today else "deprecated",
            "selectable": False,
            "replacement": rule.replacement,
            "deprecation_announced_at": rule.announced_at,
            "shutdown_at": rule.shutdown_at,
            "source": "official_deprecations",
            "last_verified_at": LIFECYCLE_VERIFIED_AT,
        }

    if normalized == "chat-latest" or normalized.startswith("gpt-5.6"):
        status = "recommended"
        replacement = None
    elif normalized.startswith("gpt-5.5"):
        status = "fallback"
        replacement = "chat-latest"
    elif normalized.startswith("gpt-5.4") or normalized == "gpt-4o-mini":
        status = "fallback"
        replacement = None
    else:
        status = "unknown"
        replacement = None
    return {
        "status": status,
        "selectable": True,
        "replacement": replacement,
        "source": "provider_inventory",
        "last_verified_at": LIFECYCLE_VERIFIED_AT,
    }


def build_model_catalog(
    model_ids: Iterable[str],
    *,
    selected_model: Optional[str] = None,
    as_of: Optional[date] = None,
) -> Dict[str, Any]:
    selected = str(selected_model or "").strip()
    selected_normalized = selected.lower()
    seen: set[str] = set()
    entries = []

    for raw_model_id in model_ids:
        model_id = str(raw_model_id or "").strip()
        normalized = model_id.lower()
        if not model_id or normalized in seen:
            continue
        seen.add(normalized)
        lifecycle = model_lifecycle(model_id, as_of=as_of)
        entries.append(
            {
                "id": model_id,
                "available": True,
                "persisted_selected": normalized == selected_normalized,
                **lifecycle,
            }
        )

    if selected and selected_normalized not in seen:
        lifecycle = model_lifecycle(selected, as_of=as_of)
        entries.append(
            {
                "id": selected,
                "available": False,
                "persisted_selected": True,
                **lifecycle,
                "selectable": False,
                "source": "persisted_selection",
            }
        )

    selectable_models = [
        entry["id"]
        for entry in entries
        if entry.get("available") and entry.get("selectable")
    ]
    selected_entry = next(
        (entry for entry in entries if entry.get("persisted_selected")), None
    )
    migration = None
    if selected_entry and selected_entry.get("replacement"):
        status = selected_entry.get("status")
        migration = {
            "from": selected_entry["id"],
            "to": selected_entry["replacement"],
            "kind": "required" if status in {"deprecated", "removed"} else "upgrade",
            "required": status in {"deprecated", "removed"},
            "shutdown_at": selected_entry.get("shutdown_at"),
        }

    return {
        "catalog": entries,
        "selectable_models": selectable_models,
        "selection": selected_entry,
        "migration": migration,
        "lifecycle_source": LIFECYCLE_SOURCE_URL,
        "lifecycle_verified_at": LIFECYCLE_VERIFIED_AT,
    }


__all__ = [
    "LIFECYCLE_SOURCE_URL",
    "LIFECYCLE_VERIFIED_AT",
    "OPENAI_MODEL_LIFECYCLE_RULES",
    "build_model_catalog",
    "model_lifecycle",
]
