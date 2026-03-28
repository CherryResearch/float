"""Thread generation utilities built atop semantic tags service."""

# isort: skip_file

from __future__ import annotations

import json
import logging
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..utils import conversation_store
from .semantic_tags_service import (
    SemanticTagsService,
    cluster_embeddings,
    chunk_text,
    cluster_texts,
    embed_texts,
    resolve_cluster_backend,
    summarize_clusters,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def _resolve_data_dir() -> Path:
    raw = os.getenv("FLOAT_DATA_DIR")
    if not raw:
        return (REPO_ROOT / "data").resolve()
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = REPO_ROOT / candidate
    try:
        return candidate.resolve()
    except Exception:
        return candidate


DATA_DIR = _resolve_data_dir()
DEFAULT_SUMMARY_PATH = DATA_DIR / "threads" / "threads_summary.json"
LEGACY_SUMMARY_PATH = REPO_ROOT / "summary.json"
DEFAULT_PREFERRED_K = 16
DEFAULT_MAX_K = 30
DEFAULT_THREAD_SIGNAL_BLEND = 0.7
DEFAULT_SAE_PROXY_TOPK = 20
THREADS_SUMMARY_SCHEMA_VERSION = 2
THREAD_OVERVIEW_SCHEMA_VERSION = 1


logger = logging.getLogger(__name__)
semantic_service = SemanticTagsService()

# Simple in-memory cache for embeddings and nugget metadata to power search
_CACHE: Dict[str, Any] = {
    "embeddings": None,  # List[List[float]]
    "nuggets": None,  # List[Dict]
    "thread_names": None,  # Optional[List[str]]
    "cluster_centroids": None,  # Optional[Dict[str, List[float]]]
}

MEAL_PARTY_KEYWORDS = {
    "tea",
    "meal",
    "menu",
    "vegan",
    "recipe",
    "snack",
    "dinner",
    "lunch",
    "brunch",
}
EVENT_KEYWORDS = {
    "party",
    "event",
    "gathering",
    "hosting",
    "host",
    "guest",
    "guests",
    "planner",
    "planning",
}


def _normalize_conversation_reference(value: Any) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        return ""
    raw = raw.split("#", 1)[0].strip()
    if raw.lower().endswith(".json"):
        raw = raw[:-5]
    return raw


def _as_score(value: Any) -> Optional[float]:
    try:
        score = float(value)
    except Exception:
        return None
    if score != score:  # NaN guard
        return None
    return score


def _make_thread_id(label: str, used_ids: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", str(label or "").strip().lower()).strip("-")
    if not base:
        base = "thread"
    candidate = base
    suffix = 2
    while candidate in used_ids:
        candidate = f"{base}-{suffix}"
        suffix += 1
    used_ids.add(candidate)
    return candidate


def _normalize_conversation_summary(summary: Dict[str, Any]) -> None:
    conversations = summary.get("conversations")
    if not isinstance(conversations, dict):
        summary["conversations"] = {}
        return

    merged: dict[str, dict[str, Any]] = {}
    for raw_name, raw_info in conversations.items():
        name = _normalize_conversation_reference(raw_name)
        if not name:
            continue
        info = raw_info if isinstance(raw_info, dict) else {}
        bucket = merged.setdefault(name, {"nugget_count": 0, "topics": {}})
        try:
            bucket["nugget_count"] += int(info.get("nugget_count") or 0)
        except Exception:
            pass
        topics = info.get("topics")
        if isinstance(topics, dict):
            for topic, count in topics.items():
                topic_name = str(topic or "").strip()
                if not topic_name:
                    continue
                try:
                    parsed_count = int(count or 0)
                except Exception:
                    parsed_count = 0
                bucket_topics = bucket.setdefault("topics", {})
                bucket_topics[topic_name] = (
                    int(bucket_topics.get(topic_name, 0) or 0) + parsed_count
                )

    summary["conversations"] = {
        name: merged[name]
        for name in sorted(
            merged.keys(),
            key=lambda conv: (
                -int(merged[conv].get("nugget_count", 0) or 0),
                conv.lower(),
            ),
        )
    }


def _build_thread_overview(summary: Dict[str, Any]) -> Dict[str, Any]:
    threads = summary.get("threads")
    if not isinstance(threads, dict):
        return {
            "schema_version": THREAD_OVERVIEW_SCHEMA_VERSION,
            "total_threads": 0,
            "threads": [],
        }

    thread_rows: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    ordered_threads = sorted(
        (
            (str(name), items if isinstance(items, list) else [])
            for name, items in threads.items()
        ),
        key=lambda item: (-len(item[1]), item[0].lower()),
    )
    for palette_index, (label, items) in enumerate(ordered_threads):
        normalized_items: list[dict[str, Any]] = []
        message_pairs: set[tuple[str, int]] = set()
        conversation_breakdown: dict[str, dict[str, Any]] = {}
        for raw_item in items:
            if not isinstance(raw_item, dict):
                continue
            conversation = _normalize_conversation_reference(
                raw_item.get("conversation")
            )
            if not conversation:
                conversation = "(unknown)"
            parsed_message_index: Optional[int]
            try:
                parsed_message_index = int(raw_item.get("message_index"))
            except Exception:
                parsed_message_index = None
            date = str(raw_item.get("date") or "").strip()
            score = _as_score(raw_item.get("score"))
            excerpt = str(raw_item.get("excerpt") or "").strip()
            item = {
                "conversation": conversation,
                "message_index": parsed_message_index,
                "date": date,
                "score": round(score, 4) if score is not None else None,
                "excerpt": excerpt,
            }
            normalized_items.append(item)

            row = conversation_breakdown.setdefault(
                conversation,
                {
                    "conversation": conversation,
                    "item_count": 0,
                    "message_refs": set(),
                    "latest_date": "",
                    "score_total": 0.0,
                    "score_count": 0,
                    "preview_excerpt": "",
                },
            )
            row["item_count"] += 1
            if parsed_message_index is not None:
                row["message_refs"].add(parsed_message_index)
                message_pairs.add((conversation, parsed_message_index))
            if date and (not row["latest_date"] or date > row["latest_date"]):
                row["latest_date"] = date
            if score is not None:
                row["score_total"] += float(score)
                row["score_count"] += 1
            if excerpt and not row["preview_excerpt"]:
                row["preview_excerpt"] = excerpt

        normalized_items.sort(
            key=lambda item: (
                str(item.get("date") or ""),
                float(item.get("score") if item.get("score") is not None else -1.0),
                str(item.get("conversation") or ""),
                int(item.get("message_index") or -1),
            ),
            reverse=True,
        )
        top_examples = normalized_items[:3]

        sorted_conversations = sorted(
            conversation_breakdown.values(),
            key=lambda row: (
                -int(row.get("item_count", 0) or 0),
                str(row.get("conversation") or "").lower(),
            ),
        )
        conversation_rows: list[dict[str, Any]] = []
        for row in sorted_conversations:
            score_count = int(row.get("score_count", 0) or 0)
            avg_score = None
            if score_count > 0:
                avg_score = round(
                    float(row.get("score_total", 0.0) or 0.0) / score_count, 4
                )
            conversation_rows.append(
                {
                    "conversation": row.get("conversation") or "(unknown)",
                    "item_count": int(row.get("item_count", 0) or 0),
                    "message_count": len(row.get("message_refs", set())),
                    "latest_date": row.get("latest_date") or "",
                    "avg_score": avg_score,
                    "preview_excerpt": row.get("preview_excerpt") or "",
                }
            )

        thread_rows.append(
            {
                "id": _make_thread_id(label, used_ids),
                "label": label,
                "item_count": len(normalized_items),
                "conversation_count": len(conversation_rows),
                "message_count": len(message_pairs) or len(normalized_items),
                "palette_index": palette_index,
                "top_examples": top_examples,
                "conversation_breakdown": conversation_rows,
            }
        )

    return {
        "schema_version": THREAD_OVERVIEW_SCHEMA_VERSION,
        "total_threads": len(thread_rows),
        "threads": thread_rows,
    }


def _ensure_summary_schema(summary: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(summary, dict):
        summary = {}
    if not isinstance(summary.get("tag_counts"), dict):
        summary["tag_counts"] = {}
    if not isinstance(summary.get("clusters"), dict):
        summary["clusters"] = {}
    if not isinstance(summary.get("threads"), dict):
        summary["threads"] = {}
    if not isinstance(summary.get("metadata"), dict):
        summary["metadata"] = {}
    _normalize_conversation_summary(summary)
    raw_cluster_count = summary.get("cluster_count")
    try:
        parsed_cluster_count = int(raw_cluster_count or 0)
    except Exception:
        parsed_cluster_count = 0
    if parsed_cluster_count <= 0 and summary.get("clusters"):
        parsed_cluster_count = len(summary.get("clusters", {}))
    summary["cluster_count"] = max(0, parsed_cluster_count)

    overview = _build_thread_overview(summary)
    summary["thread_overview"] = overview

    schema = summary.get("schema")
    if not isinstance(schema, dict):
        schema = {}
        summary["schema"] = schema
    schema["threads_summary_version"] = THREADS_SUMMARY_SCHEMA_VERSION
    schema["thread_overview_version"] = THREAD_OVERVIEW_SCHEMA_VERSION

    metadata = summary.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        summary["metadata"] = metadata
    ui_hints = metadata.get("ui_hints")
    if not isinstance(ui_hints, dict):
        ui_hints = {}
        metadata["ui_hints"] = ui_hints
    ui_hints["thread_overview_version"] = THREAD_OVERVIEW_SCHEMA_VERSION
    ui_hints["thread_count"] = int(overview.get("total_threads", 0) or 0)

    generated_at = metadata.get("generated_at_utc")
    if isinstance(generated_at, str) and generated_at.strip():
        ui_hints["generated_at_utc"] = generated_at.strip()
    elif isinstance(ui_hints.get("generated_at_utc"), str) and str(
        ui_hints.get("generated_at_utc")
    ).strip():
        metadata["generated_at_utc"] = str(ui_hints.get("generated_at_utc")).strip()

    normalized_sae = _normalize_sae_options(
        ui_hints.get("experimental_sae", metadata.get("experimental_sae"))
    )

    normalized_signal_mode = _normalize_thread_signal_mode(
        ui_hints.get("thread_signal_mode", normalized_sae.get("retrieval_mode"))
    )
    ui_hints["thread_signal_mode"] = normalized_signal_mode

    normalized_signal_blend = _normalize_thread_signal_blend(
        ui_hints.get("thread_signal_blend", normalized_sae.get("retrieval_blend"))
    )
    ui_hints["thread_signal_blend"] = normalized_signal_blend
    normalized_sae["retrieval_mode"] = normalized_signal_mode
    normalized_sae["retrieval_blend"] = normalized_signal_blend

    normalized_sae_combo = str(
        ui_hints.get("sae_model_combo", normalized_sae.get("model_combo")) or ""
    ).strip()
    ui_hints["sae_model_combo"] = normalized_sae_combo

    fallback_raw = ui_hints.get(
        "sae_embeddings_fallback", normalized_sae.get("embeddings_fallback")
    )
    if isinstance(fallback_raw, bool):
        normalized_fallback = fallback_raw
    elif fallback_raw is None:
        normalized_fallback = True
    else:
        normalized_fallback = str(fallback_raw).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    ui_hints["sae_embeddings_fallback"] = normalized_fallback

    live_inspect_raw = ui_hints.get(
        "sae_live_inspect_console", normalized_sae.get("live_inspect_console")
    )
    if isinstance(live_inspect_raw, bool):
        normalized_live_inspect = live_inspect_raw
    elif live_inspect_raw is None:
        normalized_live_inspect = False
    else:
        normalized_live_inspect = str(live_inspect_raw).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    ui_hints["sae_live_inspect_console"] = normalized_live_inspect
    ui_hints["experimental_sae"] = normalized_sae
    metadata["experimental_sae"] = normalized_sae
    return summary


def _as_int(value: Any, *, min_value: int = 1) -> Optional[int]:
    if isinstance(value, int):
        parsed = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
    else:
        return None
    if parsed < min_value:
        return None
    return parsed


def _normalize_thread_signal_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    if mode == "hybrid":
        return "hybrid"
    if mode == "sae":
        return "sae"
    return "embeddings"


def _normalize_thread_signal_blend(value: Any) -> float:
    if value is None:
        return DEFAULT_THREAD_SIGNAL_BLEND
    try:
        parsed = float(value)
    except Exception:
        return DEFAULT_THREAD_SIGNAL_BLEND
    if parsed != parsed:  # NaN guard
        return DEFAULT_THREAD_SIGNAL_BLEND
    return max(0.0, min(parsed, 1.0))


def _normalize_sae_options(value: Any) -> Dict[str, Any]:
    """Normalize experimental SAE options stored in threads UI hints."""

    if not isinstance(value, dict):
        return {
            "enabled": False,
            "retrieval_mode": "embeddings",
            "retrieval_blend": DEFAULT_THREAD_SIGNAL_BLEND,
        }

    mode = str(value.get("mode") or "inspect").strip().lower()
    if mode not in {"inspect", "steer"}:
        mode = "inspect"

    token_positions = str(value.get("token_positions") or "all").strip() or "all"
    features = str(value.get("features") or "").strip()
    layer = _as_int(value.get("layer"), min_value=0)
    topk = _as_int(value.get("topk"), min_value=1)
    retrieval_mode = _normalize_thread_signal_mode(value.get("retrieval_mode"))
    retrieval_blend = _normalize_thread_signal_blend(value.get("retrieval_blend"))
    model_combo = str(value.get("model_combo") or "").strip()

    dry_run_raw = value.get("dry_run")
    if isinstance(dry_run_raw, bool):
        dry_run = dry_run_raw
    elif dry_run_raw is None:
        dry_run = True
    else:
        dry_run = str(dry_run_raw).strip().lower() in {"1", "true", "yes", "on"}

    normalized: Dict[str, Any] = {
        "enabled": bool(value.get("enabled")),
        "mode": mode,
        "token_positions": token_positions,
        "dry_run": dry_run,
        "retrieval_mode": retrieval_mode,
        "retrieval_blend": retrieval_blend,
    }
    if layer is not None:
        normalized["layer"] = int(layer)
    if topk is not None:
        normalized["topk"] = int(topk)
    if features:
        normalized["features"] = features
    if model_combo:
        normalized["model_combo"] = model_combo

    embeddings_fallback_raw = value.get("embeddings_fallback")
    if isinstance(embeddings_fallback_raw, bool):
        normalized["embeddings_fallback"] = embeddings_fallback_raw
    elif embeddings_fallback_raw is None:
        normalized["embeddings_fallback"] = True
    else:
        normalized["embeddings_fallback"] = str(embeddings_fallback_raw).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    live_inspect_raw = value.get("live_inspect_console")
    if isinstance(live_inspect_raw, bool):
        normalized["live_inspect_console"] = live_inspect_raw
    elif live_inspect_raw is None:
        normalized["live_inspect_console"] = False
    else:
        normalized["live_inspect_console"] = str(live_inspect_raw).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    return normalized


def _cosine_similarity(left: List[float], right: List[float]) -> float:
    if not left or not right:
        return 0.0
    numerator = 0.0
    left_norm_sq = 0.0
    right_norm_sq = 0.0
    for l_val, r_val in zip(left, right):
        l = float(l_val)
        r = float(r_val)
        numerator += l * r
        left_norm_sq += l * l
        right_norm_sq += r * r
    if left_norm_sq <= 0.0 or right_norm_sq <= 0.0:
        return 0.0
    return float(numerator / (math.sqrt(left_norm_sq) * math.sqrt(right_norm_sq)))


def _sparse_topk_identity_features(
    vector: List[float],
    *,
    topk: int,
) -> Dict[int, float]:
    if not vector or topk <= 0:
        return {}
    ranked = sorted(
        ((idx, float(value)) for idx, value in enumerate(vector) if float(value) != 0.0),
        key=lambda item: abs(item[1]),
        reverse=True,
    )
    if not ranked:
        return {}
    return {idx: value for idx, value in ranked[:topk]}


def _sparse_cosine_similarity(
    left: List[float],
    right: List[float],
    *,
    topk: int,
) -> float:
    left_sparse = _sparse_topk_identity_features(left, topk=topk)
    right_sparse = _sparse_topk_identity_features(right, topk=topk)
    if not left_sparse or not right_sparse:
        return 0.0

    numerator = sum(
        float(left_sparse[idx]) * float(right_sparse.get(idx, 0.0))
        for idx in left_sparse.keys()
    )
    left_norm_sq = sum(float(value) * float(value) for value in left_sparse.values())
    right_norm_sq = sum(float(value) * float(value) for value in right_sparse.values())
    if left_norm_sq <= 0.0 or right_norm_sq <= 0.0:
        return 0.0
    return float(numerator / (math.sqrt(left_norm_sq) * math.sqrt(right_norm_sq)))


def _combine_thread_signal_score(
    *,
    mode: str,
    embedding_score: float,
    sae_score: Optional[float],
    blend: float,
    embeddings_fallback: bool,
) -> float:
    normalized_mode = _normalize_thread_signal_mode(mode)
    normalized_blend = _normalize_thread_signal_blend(blend)
    if normalized_mode == "embeddings":
        return float(embedding_score)

    if normalized_mode == "sae":
        if sae_score is not None:
            return float(sae_score)
        return float(embedding_score) if embeddings_fallback else 0.0

    # Hybrid mode: SAE weight = `blend`, embedding weight = `1 - blend`.
    if sae_score is None:
        if embeddings_fallback:
            return float(embedding_score)
        return float(embedding_score) * (1.0 - normalized_blend)
    return (float(embedding_score) * (1.0 - normalized_blend)) + (
        float(sae_score) * normalized_blend
    )


def _normalize_scope_folder(value: Any) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        return ""
    return "/".join(segment for segment in raw.split("/") if segment)


def _in_scope_folder(conversation_name: str, folder_scope: str) -> bool:
    if not folder_scope:
        return True
    conv = _normalize_scope_folder(conversation_name)
    if not conv:
        return False
    return conv == folder_scope or conv.startswith(f"{folder_scope}/")


def _resolve_thread_scope(
    scope_thread: Optional[str],
    summary: Dict[str, Any],
) -> tuple[Optional[str], Optional[set[tuple[str, int]]]]:
    name = str(scope_thread or "").strip()
    if not name:
        return None, None
    threads = summary.get("threads")
    if not isinstance(threads, dict) or not threads:
        raise ValueError("No thread summary is available to refine.")

    resolved = None
    if name in threads:
        resolved = name
    else:
        lower = name.lower()
        for candidate in threads.keys():
            if str(candidate).strip().lower() == lower:
                resolved = str(candidate)
                break
    if not resolved:
        raise ValueError(f"Thread '{name}' was not found in the current summary.")

    entries = threads.get(resolved)
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"Thread '{resolved}' has no entries to refine.")

    allowed_pairs: set[tuple[str, int]] = set()
    for item in entries:
        if not isinstance(item, dict):
            continue
        conv = str(item.get("conversation") or "").strip()
        try:
            idx = int(item.get("message_index"))
        except Exception:
            continue
        if conv:
            allowed_pairs.add((conv, idx))
    if not allowed_pairs:
        raise ValueError(
            f"Thread '{resolved}' does not contain usable message references."
        )

    return resolved, allowed_pairs


def _canonicalize_thread_label(label: str) -> str:
    text = str(label or "").strip()
    if not text:
        return ""
    tokens = set(re.findall(r"[a-z0-9]+", text.lower()))
    has_meal = bool(tokens.intersection(MEAL_PARTY_KEYWORDS))
    has_event = bool(tokens.intersection(EVENT_KEYWORDS))
    if has_meal and has_event:
        return "Meal Party"
    if {"tea", "party"}.issubset(tokens):
        return "Meal Party"
    return text


def _dedupe_thread_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, Any, str]] = set()
    deduped: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = (
            str(item.get("conversation") or ""),
            item.get("message_index"),
            str(item.get("excerpt") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _coalesce_related_threads(summary: Dict[str, Any]) -> int:
    threads = summary.get("threads")
    if not isinstance(threads, dict) or not threads:
        return 0
    aliases: Dict[str, str] = {}
    for name in threads.keys():
        canonical = _canonicalize_thread_label(str(name))
        if canonical and canonical != name:
            aliases[name] = canonical
    if not aliases:
        return 0

    merged_threads: Dict[str, list[dict[str, Any]]] = {}
    for name, items in threads.items():
        target = aliases.get(name, name)
        bucket = merged_threads.setdefault(target, [])
        if isinstance(items, list):
            bucket.extend(item for item in items if isinstance(item, dict))
    summary["threads"] = {
        name: _dedupe_thread_items(items) for name, items in merged_threads.items()
    }

    clusters = summary.get("clusters")
    if isinstance(clusters, dict):
        for cid, label in list(clusters.items()):
            if isinstance(label, str) and label in aliases:
                clusters[cid] = aliases[label]

    conversations = summary.get("conversations")
    if isinstance(conversations, dict):
        for conv_info in conversations.values():
            if not isinstance(conv_info, dict):
                continue
            topics = conv_info.get("topics")
            if not isinstance(topics, dict):
                continue
            merged_topics: Dict[str, int] = {}
            for topic, count in topics.items():
                target = aliases.get(str(topic), str(topic))
                merged_topics[target] = int(merged_topics.get(target, 0) or 0) + int(
                    count or 0
                )
            conv_info["topics"] = merged_topics

    tag_counts = summary.get("tag_counts")
    if isinstance(tag_counts, dict):
        merged_counts: Dict[str, int] = {}
        for tag, count in tag_counts.items():
            target = aliases.get(str(tag), str(tag))
            merged_counts[target] = int(merged_counts.get(target, 0) or 0) + int(
                count or 0
            )
        summary["tag_counts"] = merged_counts

    return len(aliases)


def _resolve_summary_path(summary_path: Optional[Path]) -> Path:
    return Path(summary_path) if summary_path else DEFAULT_SUMMARY_PATH


def _read_summary_file(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return None
    if isinstance(payload, dict):
        return payload
    return None


def _empty_summary() -> Dict[str, Any]:
    return _ensure_summary_schema(
        {
            "tag_counts": {},
            "cluster_count": 0,
            "clusters": {},
            "conversations": {},
            "threads": {},
            "metadata": {},
        }
    )


def _generate_threads_via_float(
    conv_dir: Path,
    out_path: Path,
    infer_topics: bool,
    tags: Optional[list[str]],
    openai_key: Optional[str],
    k_option: Optional[int] = None,
    preferred_k: Optional[int] = None,
    max_k: Optional[int] = None,
    scope_folder: Optional[str] = None,
    scope_pairs: Optional[set[tuple[str, int]]] = None,
    cluster_backend: Optional[str] = None,
    cluster_device: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Float-native path: read conversations via conversation_store, build
    nuggets using SemanticTagsService primitives to embed, cluster, and
    return a rich summary.

    Returns a summary dict when successful, or None if dependencies are
    missing.
    """
    try:
        # Construct nuggets from conversations
        conversation_names = conversation_store.list_conversations()
        nuggets_text: list[str] = []
        nug_speakers: list[str | None] = []
        nug_sources: list[Path] = []
        nug_conversations: list[str] = []
        nug_msg_indices: list[int] = []
        nug_datestamps: list[str] = []  # YYYY-MM-DD extracted from timestamps

        normalized_folder_scope = _normalize_scope_folder(scope_folder)

        for name in conversation_names:
            if normalized_folder_scope and not _in_scope_folder(
                name,
                normalized_folder_scope,
            ):
                continue
            msgs = conversation_store.load_conversation(name)
            for idx, m in enumerate(msgs):
                if scope_pairs is not None and (name, idx) not in scope_pairs:
                    continue
                text = m.get("content") or m.get("text") or ""
                role = m.get("role") or m.get("speaker") or None
                ts = m.get("timestamp")
                if not text:
                    continue
                chunks = chunk_text(text)
                for c in chunks:
                    nuggets_text.append(c)
                    nug_speakers.append(role)
                    # Keep source scoped to conversation file for stable
                    # per-conversation aggregation in the UI.
                    nug_sources.append(Path(f"{name}.json"))
                    nug_conversations.append(name)
                    nug_msg_indices.append(idx)
                    if isinstance(ts, str) and len(ts) >= 10:
                        date_only = str(ts)[:10]
                    else:
                        date_only = ""
                    nug_datestamps.append(date_only)

        if not nuggets_text:
            out = {
                "tag_counts": {},
                "cluster_count": 0,
                "clusters": {},
                "conversations": {},
                "threads": {},
                "metadata": {},
            }
            out_path.write_text(
                json.dumps(out, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return out

        # Embed text nuggets
        embeddings, embedder = embed_texts(nuggets_text)
        # Cache for search
        _CACHE["embeddings"] = embeddings
        _CACHE["nuggets"] = [
            {
                "text": t,
                "speaker": s,
                "source": str(src),
                "conversation": conv,
                "message_index": mi,
                "date": d,
            }
            for t, s, src, conv, mi, d in zip(
                nuggets_text,
                nug_speakers,
                nug_sources,
                nug_conversations,
                nug_msg_indices,
                nug_datestamps,
            )
        ]

        selected_k: Optional[int] = None
        if isinstance(k_option, int):
            selected_k = k_option
        elif isinstance(k_option, str) and str(k_option).strip().isdigit():
            try:
                selected_k = int(str(k_option).strip())
            except Exception:
                selected_k = None

        if isinstance(selected_k, int) and selected_k >= 1:
            n_vectors = max(1, len(embeddings))
            forced_k = min(max(1, int(selected_k)), n_vectors)
            if forced_k == 1:
                labels = [0] * len(embeddings)
            else:
                forced_labels, _ = cluster_embeddings(
                    embeddings,
                    forced_k,
                    cluster_backend=cluster_backend,
                    cluster_device=cluster_device,
                )
                labels = (
                    forced_labels.tolist()
                    if hasattr(forced_labels, "tolist")
                    else list(forced_labels)
                )
            k = forced_k
        else:
            labels, k = cluster_texts(
                embeddings,
                preferred_k=preferred_k,
                max_k=max_k,
                cluster_backend=cluster_backend,
                cluster_device=cluster_device,
            )
        base_summary, centroids = summarize_clusters(
            nuggets_text,
            labels,
            embeddings,
            embedder,
            k,
            tags,
            infer_topics,
            openai_key,
            nug_sources,
            nug_speakers,
            nug_conversations,
            nug_msg_indices,
            nug_datestamps,
        )
        _CACHE["cluster_centroids"] = centroids

        out_path.write_text(
            json.dumps(base_summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return base_summary
    except Exception:
        return None


def generate_threads(
    *,
    summary_out: Optional[Path] = None,
    infer_topics: bool = True,
    tags: Optional[list[str]] = None,
    openai_key: Optional[str] = None,
    k_option: Optional[int] = None,
    preferred_k: Optional[int] = DEFAULT_PREFERRED_K,
    max_k: Optional[int] = DEFAULT_MAX_K,
    manual_threads: Optional[List[str]] = None,
    top_n: Optional[int] = None,
    coalesce_related: bool = True,
    scope_folder: Optional[str] = None,
    scope_thread: Optional[str] = None,
    thread_signal_mode: Optional[str] = None,
    thread_signal_blend: Optional[float] = None,
    sae_model_combo: Optional[str] = None,
    sae_embeddings_fallback: Optional[bool] = None,
    sae_live_inspect_console: Optional[bool] = None,
    sae_options: Optional[Dict[str, Any]] = None,
    cluster_backend: Optional[str] = None,
    cluster_device: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run semantic-tag generation against the Float conversations directory using
    direct service calls, producing a summary JSON that includes tag counts and
    cluster labels. The parsed summary dictionary is returned and also written
    to ``summary_out``.
    """
    conv_dir = conversation_store.CONV_DIR
    if not conv_dir.exists():
        msg = f"Conversations directory not found: {conv_dir}"
        raise FileNotFoundError(msg)

    out_path = _resolve_summary_path(summary_out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Topic inference can still run without an API key using local heuristics.
    topic_inference = bool(infer_topics)
    if infer_topics and not openai_key:
        logger.info(
            "Topic inference requested without an OpenAI key; using local heuristic labels."
        )

    preferred_k_value = _as_int(preferred_k, min_value=2)
    max_k_value = _as_int(max_k, min_value=2)
    if (
        isinstance(preferred_k_value, int)
        and isinstance(max_k_value, int)
        and preferred_k_value > max_k_value
    ):
        preferred_k_value = max_k_value
    clustering_state = resolve_cluster_backend(cluster_backend, cluster_device)

    normalized_scope_folder = _normalize_scope_folder(scope_folder)
    current_summary = read_summary(out_path)
    resolved_scope_thread, scope_pairs = _resolve_thread_scope(
        scope_thread,
        current_summary,
    )

    # Generate threads directly via SemanticTagsService
    float_summary = _generate_threads_via_float(
        conv_dir,
        out_path,
        topic_inference,
        tags,
        openai_key,
        k_option,
        preferred_k_value,
        max_k_value,
        normalized_scope_folder,
        scope_pairs,
        str(clustering_state.get("backend") or "sklearn"),
        str(clustering_state.get("device") or "cpu"),
    )
    if float_summary is None:
        raise RuntimeError("SemanticTagsService is required")

    merged_label_count = 0
    if coalesce_related and not manual_threads:
        merged_label_count = _coalesce_related_threads(float_summary)

    merged_sae_options = dict(sae_options or {})
    normalized_signal_mode = _normalize_thread_signal_mode(
        thread_signal_mode or merged_sae_options.get("retrieval_mode")
    )
    normalized_signal_blend = _normalize_thread_signal_blend(
        thread_signal_blend
        if thread_signal_blend is not None
        else merged_sae_options.get("retrieval_blend")
    )
    normalized_sae_combo = str(sae_model_combo or "").strip()
    normalized_sae_fallback = (
        bool(sae_embeddings_fallback)
        if isinstance(sae_embeddings_fallback, bool)
        else True
    )
    normalized_sae_live_inspect = (
        bool(sae_live_inspect_console)
        if isinstance(sae_live_inspect_console, bool)
        else False
    )
    signal_scoring_hints: Dict[str, Any] = {
        "mode": normalized_signal_mode,
        "blend": normalized_signal_blend,
        "sae_proxy_available": False,
        "sae_proxy_topk": None,
        "embeddings_fallback": normalized_sae_fallback,
    }

    if manual_threads:
        try:
            thread_embs, _ = embed_texts(manual_threads)
            thread_vecs = {t: v for t, v in zip(manual_threads, thread_embs)}
            _CACHE["thread_names"] = list(thread_vecs.keys())
            threads_map: Dict[str, List[Dict[str, Any]]] = {
                t: [] for t in manual_threads
            }
            seen: set[Tuple[str, str]] = set()
            embeddings: List[List[float]] = _CACHE.get("embeddings") or []
            nuggets: List[Dict[str, Any]] = _CACHE.get("nuggets") or []
            sae_proxy_topk = _as_int(
                merged_sae_options.get("topk"),
                min_value=1,
            ) or DEFAULT_SAE_PROXY_TOPK
            use_sae_proxy = normalized_signal_mode in {"hybrid", "sae"}
            signal_scoring_hints["sae_proxy_available"] = bool(use_sae_proxy)
            signal_scoring_hints["sae_proxy_topk"] = (
                int(sae_proxy_topk) if use_sae_proxy else None
            )
            for i, meta in enumerate(nuggets):
                best_t = None
                best_s = -1.0
                vec = embeddings[i]
                best_embedding_score: Optional[float] = None
                best_sae_score: Optional[float] = None
                for t, tv in thread_vecs.items():
                    embedding_score = _cosine_similarity(vec, tv)
                    sae_score = (
                        _sparse_cosine_similarity(
                            vec,
                            tv,
                            topk=sae_proxy_topk,
                        )
                        if use_sae_proxy
                        else None
                    )
                    s = _combine_thread_signal_score(
                        mode=normalized_signal_mode,
                        embedding_score=embedding_score,
                        sae_score=sae_score,
                        blend=normalized_signal_blend,
                        embeddings_fallback=normalized_sae_fallback,
                    )
                    if s > best_s:
                        best_s = s
                        best_t = t
                        best_embedding_score = embedding_score
                        best_sae_score = sae_score
                if best_t is None:
                    continue
                key = (best_t, meta["conversation"])
                if key in seen:
                    continue
                seen.add(key)
                lines = meta["text"].splitlines()
                excerpt = "\n".join(lines[:4])
                item: Dict[str, Any] = {
                    "conversation": meta["conversation"],
                    "message_index": meta["message_index"],
                    "date": meta["date"],
                    "score": round(float(best_s), 4),
                    "excerpt": excerpt,
                }
                if best_embedding_score is not None:
                    item["embedding_score"] = round(float(best_embedding_score), 4)
                if best_sae_score is not None:
                    item["sae_score"] = round(float(best_sae_score), 4)
                threads_map[best_t].append(item)
            float_summary["threads"] = threads_map
        except Exception:
            pass

    if top_n and isinstance(float_summary.get("threads"), dict):
        tmap = float_summary["threads"]
        order = sorted(
            tmap.keys(),
            key=lambda t: len(tmap[t]),
            reverse=True,
        )
        keep = set(order[: max(1, int(top_n))])
        float_summary["threads"] = {t: tmap[t] for t in order if t in keep}

    float_summary = _ensure_summary_schema(float_summary)
    metadata = float_summary.setdefault("metadata", {})
    ui_hints = metadata.setdefault("ui_hints", {})
    ui_hints["k_option"] = k_option or "auto"
    ui_hints["k_selected"] = int(float_summary.get("cluster_count", 0) or 0)
    ui_hints["preferred_k"] = preferred_k_value
    ui_hints["max_k"] = max_k_value
    ui_hints["coalesce_related"] = bool(coalesce_related)
    ui_hints["merged_label_count"] = int(merged_label_count)
    ui_hints["infer_topics"] = bool(topic_inference)
    scope_mode = "all"
    if resolved_scope_thread:
        scope_mode = "thread"
    elif normalized_scope_folder:
        scope_mode = "folder"
    ui_hints["scope_mode"] = scope_mode
    ui_hints["scope_folder"] = normalized_scope_folder or ""
    ui_hints["scope_thread"] = resolved_scope_thread or ""
    ui_hints["top_n"] = int(top_n) if isinstance(top_n, int) and top_n >= 1 else None
    ui_hints["thread_signal_mode"] = normalized_signal_mode
    ui_hints["thread_signal_blend"] = normalized_signal_blend
    ui_hints["cluster_backend"] = str(clustering_state.get("backend") or "sklearn")
    ui_hints["cluster_device"] = str(clustering_state.get("device") or "cpu")
    ui_hints["cluster_backend_requested"] = str(
        clustering_state.get("requested_backend") or "sklearn"
    )
    ui_hints["cluster_device_requested"] = str(
        clustering_state.get("requested_device") or "auto"
    )
    ui_hints["cluster_backend_fallback"] = bool(clustering_state.get("fallback"))
    ui_hints["cluster_backend_reason"] = clustering_state.get("reason")
    ui_hints["sae_model_combo"] = normalized_sae_combo
    ui_hints["sae_embeddings_fallback"] = normalized_sae_fallback
    ui_hints["sae_live_inspect_console"] = normalized_sae_live_inspect
    ui_hints["signal_scoring"] = signal_scoring_hints
    generated_at_utc = datetime.now(timezone.utc).isoformat()
    metadata["generated_at_utc"] = generated_at_utc
    ui_hints["generated_at_utc"] = generated_at_utc
    merged_sae_options.setdefault("retrieval_mode", normalized_signal_mode)
    merged_sae_options.setdefault("retrieval_blend", normalized_signal_blend)
    if normalized_sae_combo:
        merged_sae_options.setdefault("model_combo", normalized_sae_combo)
    merged_sae_options.setdefault("embeddings_fallback", normalized_sae_fallback)
    merged_sae_options.setdefault("live_inspect_console", normalized_sae_live_inspect)
    ui_hints["experimental_sae"] = _normalize_sae_options(merged_sae_options)
    float_summary = _ensure_summary_schema(float_summary)

    _ = semantic_service.summarize_clusters(
        {
            t: [n["excerpt"] for n in v]
            for t, v in float_summary.get("threads", {}).items()
        }
    )

    # Persist final summary (including ui_hints/manual-thread overrides) so
    # /threads/summary and on-disk state match /threads/generate responses.
    out_path.write_text(
        json.dumps(float_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return float_summary


def read_summary(summary_path: Optional[Path] = None) -> Dict[str, Any]:
    path = _resolve_summary_path(summary_path)
    payload = _read_summary_file(path)
    if payload is not None:
        normalized = _ensure_summary_schema(payload)
        metadata = normalized.setdefault("metadata", {})
        ui_hints = metadata.setdefault("ui_hints", {})
        generated_at = metadata.get("generated_at_utc")
        if not isinstance(generated_at, str) or not generated_at.strip():
            try:
                generated_at = datetime.fromtimestamp(
                    path.stat().st_mtime,
                    tz=timezone.utc,
                ).isoformat()
            except Exception:
                generated_at = ""
            if isinstance(generated_at, str) and generated_at:
                metadata["generated_at_utc"] = generated_at
                ui_hints["generated_at_utc"] = generated_at
        return normalized

    # Best-effort one-time migration from the legacy repo-root path.
    if summary_path is None:
        legacy_payload = _read_summary_file(LEGACY_SUMMARY_PATH)
        if legacy_payload is not None:
            normalized_legacy = _ensure_summary_schema(legacy_payload)
            legacy_generated_at = (
                normalized_legacy.get("metadata", {}).get("generated_at_utc")
                if isinstance(normalized_legacy.get("metadata"), dict)
                else None
            )
            if not isinstance(legacy_generated_at, str) or not legacy_generated_at.strip():
                try:
                    legacy_generated_at = datetime.fromtimestamp(
                        LEGACY_SUMMARY_PATH.stat().st_mtime,
                        tz=timezone.utc,
                    ).isoformat()
                except Exception:
                    legacy_generated_at = ""
                if isinstance(legacy_generated_at, str) and legacy_generated_at:
                    metadata = normalized_legacy.setdefault("metadata", {})
                    ui_hints = metadata.setdefault("ui_hints", {})
                    metadata["generated_at_utc"] = legacy_generated_at
                    ui_hints["generated_at_utc"] = legacy_generated_at
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(normalized_legacy, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                LEGACY_SUMMARY_PATH.unlink(missing_ok=True)
            except Exception:
                pass
            return normalized_legacy

    return _empty_summary()


def rename_thread(
    old_name: str,
    new_name: str,
    *,
    summary_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Rename a thread label inside the stored summary."""
    old = (old_name or "").strip()
    new = (new_name or "").strip()
    if not old or not new:
        raise ValueError("Thread names must be non-empty")
    summary = read_summary(summary_path)
    threads = summary.get("threads")
    if not isinstance(threads, dict) or old not in threads:
        raise KeyError(old)
    if old != new:
        existing = threads.get(new, [])
        merged = list(existing) + list(threads.get(old, []))
        # De-duplicate entries by conversation + message_index
        seen: set[tuple[str, Any]] = set()
        deduped = []
        for item in merged:
            if not isinstance(item, dict):
                continue
            key = (str(item.get("conversation") or ""), item.get("message_index"))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        threads[new] = deduped
        threads.pop(old, None)
    tag_counts = summary.get("tag_counts")
    if isinstance(tag_counts, dict) and old in tag_counts:
        tag_counts[new] = int(tag_counts.get(new, 0) or 0) + int(
            tag_counts.get(old, 0) or 0
        )
        tag_counts.pop(old, None)
    clusters = summary.get("clusters")
    if isinstance(clusters, dict):
        for cid, label in list(clusters.items()):
            if label == old:
                clusters[cid] = new
    if isinstance(_CACHE.get("thread_names"), list):
        _CACHE["thread_names"] = [
            (new if t == old else t) for t in _CACHE.get("thread_names", [])
        ]
    summary = _ensure_summary_schema(summary)
    path = _resolve_summary_path(summary_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def search_threads(query: str, top_k: int = 10) -> Dict[str, Any]:
    """Return top-k nugget matches by semantic similarity for the given
    topic/query.

    Requires a prior generate run to populate the in-memory cache.
    """
    try:
        from math import fsum

        def cosine(a: List[float], b: List[float]) -> float:
            ax = fsum(x * x for x in a) ** 0.5
            bx = fsum(x * x for x in b) ** 0.5
            if ax == 0 or bx == 0:
                return 0.0
            return fsum(x * y for x, y in zip(a, b)) / (ax * bx)

        embeddings: List[List[float]] = _CACHE.get("embeddings") or []
        nuggets: List[Dict[str, Any]] = _CACHE.get("nuggets") or []
        if not embeddings or not nuggets:
            return {"matches": []}
        qv_list, _ = embed_texts([query])
        qv = qv_list[0]
        scored: List[Tuple[float, int]] = []
        for i, vec in enumerate(embeddings):
            s = cosine(qv, vec)
            scored.append((s, i))
        scored.sort(reverse=True)
        out = []
        for s, i in scored[: max(1, int(top_k))]:
            meta = nuggets[i]
            out.append(
                {
                    "conversation": meta.get("conversation"),
                    "message_index": meta.get("message_index"),
                    "date": meta.get("date"),
                    "score": round(float(s), 4),
                    "excerpt": "\n".join(
                        [ln for ln in meta.get("text", "").splitlines()][:4]
                    ),
                }
            )
        return {"matches": out}
    except Exception:
        return {"matches": []}
