"""Build visualization-ready memory graph summaries."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .rag_provider import get_rag_service

DEFAULT_MEMORY_GRAPH_LIMIT = 72
DEFAULT_SEMANTIC_LINKS_PER_NODE = 3
DEFAULT_SAE_PROXY_TOPK = 12
DEFAULT_SIGNAL_BLEND = 0.45
DEFAULT_SIGNAL_THRESHOLD = 0.34
MEMORY_GRAPH_SCHEMA_VERSION = 1

_TOKEN_RE = re.compile(r"[a-z0-9_./:-]+")
_PATH_HINT_RE = re.compile(r"[\\/]|[.](txt|md|json|csv|pdf|png|jpg|jpeg|py|tsx?|jsx?)$", re.I)
_CONVERSATION_HINT_RE = re.compile(r"(^|/)[^/]+[.]json$", re.I)

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "with",
}

_CONVERSATION_FIELDS = (
    "conversation",
    "conversation_id",
    "conversation_ids",
    "conversation_path",
    "conversation_paths",
    "session_id",
    "session_ids",
    "source_chat",
    "chat",
    "thread_conversation",
)
_FILE_FIELDS = (
    "file",
    "file_path",
    "files",
    "path",
    "paths",
    "relative_path",
    "relative_paths",
    "attachment",
    "attachments",
)
_TOOL_FIELDS = (
    "tool",
    "tool_name",
    "tool_names",
    "tools",
    "tool_call",
    "tool_calls",
)
_NAMESPACE_FIELDS = ("namespace",)


def _simple_embed(text: str) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [byte / 255.0 for byte in digest[:32]]


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except Exception:
        return default
    if not math.isfinite(parsed):
        return default
    return parsed


def _normalize_text(value: Any) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except Exception:
            text = str(value or "")
    return re.sub(r"\s+", " ", text).strip()


def _memory_value_to_text(item: Dict[str, Any]) -> str:
    sensitivity = str(item.get("sensitivity") or "mundane").strip().lower()
    if sensitivity == "secret" and (item.get("encrypted") or item.get("decrypt_error")):
        return _normalize_text(item.get("hint") or "")
    return _normalize_text(item.get("value"))


def _memory_similarity_text(key: str, item: Dict[str, Any]) -> str:
    parts = [f"key: {key}"]
    hint = _normalize_text(item.get("hint"))
    if hint:
        parts.append(f"hint: {hint}")
    value_text = _memory_value_to_text(item)
    if value_text:
        parts.append(value_text)
    return "\n".join(parts)


def _tokenize(value: str) -> set[str]:
    tokens = {
        token
        for token in _TOKEN_RE.findall(str(value or "").lower())
        if len(token) > 1 and token not in _STOPWORDS
    }
    return tokens


def _normalize_conversation_reference(value: Any) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        return ""
    raw = raw.split("#", 1)[0].strip()
    if raw.lower().endswith(".json"):
        raw = raw[:-5]
    return raw


def _normalize_anchor_value(category: str, value: Any) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    if category == "conversation":
        normalized = _normalize_conversation_reference(raw)
        return normalized or None
    if category == "file":
        normalized = raw.replace("\\", "/")
        return normalized or None
    if category == "tool":
        return raw
    if category == "namespace":
        return raw
    return raw


def _flatten_values(value: Any, *, limit: int = 8) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, int, float, bool)):
        return [value]
    if isinstance(value, dict):
        out: list[Any] = []
        for nested in value.values():
            out.extend(_flatten_values(nested, limit=limit))
            if len(out) >= limit:
                break
        return out[:limit]
    if isinstance(value, (list, tuple, set)):
        out = []
        for nested in value:
            out.extend(_flatten_values(nested, limit=limit))
            if len(out) >= limit:
                break
        return out[:limit]
    return [value]


def _infer_source_category(value: str) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    lowered = raw.lower()
    if lowered.startswith("memory:"):
        return None
    if _CONVERSATION_HINT_RE.search(raw.replace("\\", "/")):
        return "conversation"
    if _PATH_HINT_RE.search(raw):
        return "file"
    return None


def _collect_refs_from_fields(
    payload: Dict[str, Any],
    fields: Iterable[str],
    category: str,
) -> set[str]:
    refs: set[str] = set()
    for field_name in fields:
        if field_name not in payload:
            continue
        for raw in _flatten_values(payload.get(field_name)):
            normalized = _normalize_anchor_value(category, raw)
            if normalized:
                refs.add(normalized)
    return refs


def _extract_explicit_refs(key: str, item: Dict[str, Any]) -> Dict[str, list[str]]:
    value = item.get("value")
    nested = value if isinstance(value, dict) else {}
    refs = {
        "conversation": set(),
        "file": set(),
        "tool": set(),
        "namespace": set(),
    }

    for payload in (item, nested):
        if not isinstance(payload, dict):
            continue
        refs["conversation"].update(
            _collect_refs_from_fields(payload, _CONVERSATION_FIELDS, "conversation")
        )
        refs["file"].update(_collect_refs_from_fields(payload, _FILE_FIELDS, "file"))
        refs["tool"].update(_collect_refs_from_fields(payload, _TOOL_FIELDS, "tool"))
        refs["namespace"].update(
            _collect_refs_from_fields(payload, _NAMESPACE_FIELDS, "namespace")
        )
        if "source" in payload:
            source_value = str(payload.get("source") or "").strip()
            inferred = _infer_source_category(source_value)
            if inferred and source_value:
                refs[inferred].add(
                    _normalize_anchor_value(inferred, source_value) or source_value
                )

    if item.get("rag_doc_id"):
        refs["namespace"].add(f"rag:{item['rag_doc_id']}")
    if item.get("vectorize") or item.get("vectorized_at"):
        refs["namespace"].add("memorized")
    if item.get("pinned"):
        refs["namespace"].add("pinned")

    normalized: Dict[str, list[str]] = {}
    for category, values in refs.items():
        ordered = sorted(
            (value for value in values if value),
            key=lambda current: current.lower(),
        )
        normalized[category] = ordered[:8]
    if not any(normalized.values()):
        normalized["namespace"] = [f"memory:{key}"]
    return normalized


def _cosine_similarity(left: List[float], right: List[float]) -> float:
    if not left or not right:
        return 0.0
    numerator = 0.0
    left_norm_sq = 0.0
    right_norm_sq = 0.0
    for left_value, right_value in zip(left, right):
        numerator += float(left_value) * float(right_value)
        left_norm_sq += float(left_value) * float(left_value)
        right_norm_sq += float(right_value) * float(right_value)
    if left_norm_sq <= 0.0 or right_norm_sq <= 0.0:
        return 0.0
    return float(numerator / (math.sqrt(left_norm_sq) * math.sqrt(right_norm_sq)))


def _sparse_topk_features(vector: List[float], *, topk: int) -> Dict[int, float]:
    if not vector or topk <= 0:
        return {}
    ranked = sorted(
        ((idx, float(value)) for idx, value in enumerate(vector) if float(value) != 0.0),
        key=lambda item: abs(item[1]),
        reverse=True,
    )
    return {idx: value for idx, value in ranked[:topk]}


def _sparse_cosine_similarity(left: List[float], right: List[float], *, topk: int) -> float:
    left_sparse = _sparse_topk_features(left, topk=topk)
    right_sparse = _sparse_topk_features(right, topk=topk)
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


def _hybrid_score(embedding_score: float, sae_score: float, *, blend: float) -> float:
    clamped_blend = max(0.0, min(float(blend), 1.0))
    return (float(embedding_score) * (1.0 - clamped_blend)) + (
        float(sae_score) * clamped_blend
    )


def _jaccard_overlap(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    intersection = len(left.intersection(right))
    if intersection <= 0:
        return 0.0
    union = len(left.union(right))
    if union <= 0:
        return 0.0
    return float(intersection / union)


def _select_memory_items(
    items: list[Dict[str, Any]],
    *,
    limit: int,
    focus_key: Optional[str] = None,
) -> list[Dict[str, Any]]:
    ranked = sorted(
        items,
        key=lambda item: (
            -int(bool(item.get("pinned"))),
            -_as_float(item.get("importance"), 0.0),
            -_as_float(item.get("updated_at"), 0.0),
            str(item.get("key") or "").lower(),
        ),
    )
    max_items = max(1, int(limit))
    selected = ranked[:max_items]
    normalized_focus = str(focus_key or "").strip()
    if not normalized_focus:
        return selected
    if any(str(item.get("key") or "") == normalized_focus for item in selected):
        return selected
    focused_item = next(
        (
            item
            for item in ranked
            if str(item.get("key") or "").strip() == normalized_focus
        ),
        None,
    )
    if focused_item is None:
        return selected
    if max_items <= 1:
        return [focused_item]
    selected = selected[:-1] if selected else []
    selected.append(focused_item)
    return selected


def _anchor_label(category: str, value: str) -> str:
    if category == "file":
        basename = Path(str(value)).name
        return basename or str(value)
    return str(value)


def _anchor_match_key(category: str, value: str) -> Optional[str]:
    if category == "conversation":
        return f"conversation:{value}"
    return None


def _anchor_id(category: str, value: str) -> str:
    safe = hashlib.sha1(f"{category}:{value}".encode("utf-8")).hexdigest()[:12]
    return f"memory:{category}:{safe}"


def _thread_match_key(label: str) -> Optional[str]:
    normalized = re.sub(r"\s+", " ", str(label or "").strip()).lower()
    if not normalized:
        return None
    return f"thread:{normalized}"


def _thread_node_id(label: str) -> str:
    safe = hashlib.sha1(f"thread:{label}".encode("utf-8")).hexdigest()[:12]
    return f"memory:thread:{safe}"


def _memory_node_id(key: str) -> str:
    return f"memory:item:{key}"


def _build_thread_projection_index(
    thread_summary: Optional[Dict[str, Any]],
) -> Dict[str, list[Dict[str, Any]]]:
    index: defaultdict[str, list[Dict[str, Any]]] = defaultdict(list)
    if not isinstance(thread_summary, dict):
        return {}

    overview = thread_summary.get("thread_overview")
    threads = overview.get("threads") if isinstance(overview, dict) else None
    if not isinstance(threads, list):
        return {}

    for raw_thread in threads:
        if not isinstance(raw_thread, dict):
            continue
        label = str(raw_thread.get("label") or "").strip()
        if not label:
            continue
        item_count = int(_as_float(raw_thread.get("item_count"), 0.0))
        conversation_count = int(_as_float(raw_thread.get("conversation_count"), 0.0))
        breakdown = raw_thread.get("conversation_breakdown")
        if not isinstance(breakdown, list):
            continue
        for raw_row in breakdown:
            if not isinstance(raw_row, dict):
                continue
            conversation = _normalize_conversation_reference(
                raw_row.get("conversation")
            )
            if not conversation:
                continue
            conversation_item_count = int(_as_float(raw_row.get("item_count"), 0.0))
            index[conversation].append(
                {
                    "label": label,
                    "item_count": max(item_count, conversation_item_count),
                    "conversation_count": max(conversation_count, 1),
                    "conversation_item_count": max(conversation_item_count, 1),
                    "latest_date": str(raw_row.get("latest_date") or "").strip(),
                }
            )

    return {
        conversation: sorted(
            rows,
            key=lambda row: (
                -int(row.get("conversation_item_count", 0) or 0),
                str(row.get("label") or "").lower(),
            ),
        )
        for conversation, rows in index.items()
    }


def build_memory_graph(
    raw_items: list[Dict[str, Any]],
    *,
    limit: int = DEFAULT_MEMORY_GRAPH_LIMIT,
    focus_key: Optional[str] = None,
    thread_summary: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    items = _select_memory_items(
        raw_items or [],
        limit=max(1, int(limit or DEFAULT_MEMORY_GRAPH_LIMIT)),
        focus_key=focus_key,
    )
    rag_service = get_rag_service(raise_http=False)
    embed_text = getattr(rag_service, "_embed_text", None)
    if not callable(embed_text):
        embed_text = _simple_embed

    nodes: list[Dict[str, Any]] = []
    links: list[Dict[str, Any]] = []
    anchor_nodes: dict[str, Dict[str, Any]] = {}
    item_embeddings: dict[str, list[float]] = {}
    item_tokens: dict[str, set[str]] = {}
    item_refs: dict[str, set[tuple[str, str]]] = {}
    candidate_links_by_node: defaultdict[str, list[Dict[str, Any]]] = defaultdict(list)

    for entry in items:
        key = str(entry.get("key") or "").strip()
        if not key:
            continue
        node_id = _memory_node_id(key)
        refs = _extract_explicit_refs(key, entry)
        flattened_refs = {
            (category, value)
            for category, values in refs.items()
            for value in values
            if value
        }
        similarity_text = _memory_similarity_text(key, entry)
        item_embeddings[node_id] = list(embed_text(similarity_text or key))
        item_tokens[node_id] = _tokenize(similarity_text)
        item_refs[node_id] = flattened_refs

        nodes.append(
            {
                "id": node_id,
                "label": key,
                "type": "memory",
                "graph_key": "memory",
                "level": 0,
                "weight": round(_as_float(entry.get("importance"), 1.0), 3),
                "importance": round(_as_float(entry.get("importance"), 1.0), 3),
                "pinned": bool(entry.get("pinned")),
                "memorized": bool(entry.get("vectorize") or entry.get("vectorized_at")),
                "explicit_ref_count": sum(len(values) for values in refs.values()),
                "sensitivity": str(entry.get("sensitivity") or "mundane"),
                "hint": str(entry.get("hint") or ""),
                "updated_at": _as_float(entry.get("updated_at"), 0.0),
            }
        )

        for category, values in refs.items():
            for value in values:
                anchor_id = _anchor_id(category, value)
                bucket = anchor_nodes.setdefault(
                    anchor_id,
                    {
                        "id": anchor_id,
                        "label": _anchor_label(category, value),
                        "type": f"{category}_anchor",
                        "graph_key": "memory",
                        "category": category,
                        "level": 1,
                        "weight": 0,
                        "match_key": _anchor_match_key(category, value),
                        "ref_value": value,
                    },
                )
                bucket["weight"] = int(bucket.get("weight", 0) or 0) + 1
                links.append(
                    {
                        "source": node_id,
                        "target": anchor_id,
                        "weight": 1.0,
                        "type": "explicit",
                        "category": category,
                        "graph_key": "memory",
                    }
                )

    nodes.extend(anchor_nodes.values())

    thread_projection_index = _build_thread_projection_index(thread_summary)
    thread_nodes: dict[str, Dict[str, Any]] = {}
    thread_projection_count = 0
    thread_projection_conversation_count = 0
    for anchor_id, anchor in anchor_nodes.items():
        if str(anchor.get("category") or "") != "conversation":
            continue
        conversation = _normalize_conversation_reference(
            anchor.get("ref_value") or anchor.get("label")
        )
        if not conversation:
            continue
        matches = thread_projection_index.get(conversation) or []
        if not matches:
            continue
        thread_projection_conversation_count += 1
        for match in matches:
            label = str(match.get("label") or "").strip()
            if not label:
                continue
            thread_id = _thread_node_id(label)
            bucket = thread_nodes.setdefault(
                thread_id,
                {
                    "id": thread_id,
                    "label": label,
                    "type": "thread",
                    "graph_key": "memory",
                    "level": 2,
                    "weight": max(
                        1.0, _as_float(match.get("conversation_item_count"), 1.0)
                    ),
                    "item_count": int(_as_float(match.get("item_count"), 0.0)),
                    "conversation_count": int(
                        _as_float(match.get("conversation_count"), 0.0)
                    ),
                    "latest_date": str(match.get("latest_date") or "").strip(),
                    "match_key": _thread_match_key(label),
                },
            )
            if str(match.get("latest_date") or "").strip():
                latest_date = str(match.get("latest_date") or "").strip()
                if not str(bucket.get("latest_date") or "") or latest_date > str(
                    bucket.get("latest_date") or ""
                ):
                    bucket["latest_date"] = latest_date
            bucket["weight"] = max(
                _as_float(bucket.get("weight"), 1.0),
                max(1.0, _as_float(match.get("conversation_item_count"), 1.0)),
            )
            links.append(
                {
                    "source": anchor_id,
                    "target": thread_id,
                    "weight": round(
                        max(1.0, _as_float(match.get("conversation_item_count"), 1.0)),
                        4,
                    ),
                    "type": "projection",
                    "category": "thread",
                    "graph_key": "memory",
                }
            )
            thread_projection_count += 1

    nodes.extend(thread_nodes.values())

    memory_nodes = [node for node in nodes if node.get("type") == "memory"]
    for index, left in enumerate(memory_nodes):
        left_id = str(left.get("id"))
        left_embedding = item_embeddings.get(left_id) or []
        left_tokens = item_tokens.get(left_id) or set()
        left_refs = item_refs.get(left_id) or set()
        for right in memory_nodes[index + 1 :]:
            right_id = str(right.get("id"))
            right_embedding = item_embeddings.get(right_id) or []
            right_tokens = item_tokens.get(right_id) or set()
            right_refs = item_refs.get(right_id) or set()
            embedding_score = _cosine_similarity(left_embedding, right_embedding)
            sae_score = _sparse_cosine_similarity(
                left_embedding,
                right_embedding,
                topk=DEFAULT_SAE_PROXY_TOPK,
            )
            hybrid_score = _hybrid_score(
                embedding_score,
                sae_score,
                blend=DEFAULT_SIGNAL_BLEND,
            )
            overlap_score = _jaccard_overlap(left_tokens, right_tokens)
            shared_refs = left_refs.intersection(right_refs)
            if (
                hybrid_score < DEFAULT_SIGNAL_THRESHOLD
                and overlap_score < 0.12
                and not shared_refs
            ):
                continue
            weight = max(hybrid_score, overlap_score * 0.9)
            candidate = {
                "source": left_id,
                "target": right_id,
                "weight": round(weight, 4),
                "type": "semantic",
                "graph_key": "memory",
                "embedding_score": round(embedding_score, 4),
                "sae_score": round(sae_score, 4),
                "token_overlap": round(overlap_score, 4),
                "shared_explicit_count": len(shared_refs),
            }
            candidate_links_by_node[left_id].append(candidate)
            candidate_links_by_node[right_id].append(candidate)

    selected_semantic_keys: set[Tuple[str, str]] = set()
    selected_semantic_links: list[Dict[str, Any]] = []
    for node_id, candidates in candidate_links_by_node.items():
        ordered = sorted(
            candidates,
            key=lambda link: (
                -_as_float(link.get("weight"), 0.0),
                -_as_float(link.get("shared_explicit_count"), 0.0),
                str(link.get("target") or ""),
            ),
        )
        for link in ordered[:DEFAULT_SEMANTIC_LINKS_PER_NODE]:
            edge_key = tuple(sorted((str(link.get("source")), str(link.get("target")))))
            if edge_key in selected_semantic_keys:
                continue
            selected_semantic_keys.add(edge_key)
            selected_semantic_links.append(link)

    links.extend(selected_semantic_links)

    metadata = {
        "schema_version": MEMORY_GRAPH_SCHEMA_VERSION,
        "memory_count": len(memory_nodes),
        "anchor_count": len(anchor_nodes),
        "thread_count": len(thread_nodes),
        "thread_projection_count": thread_projection_count,
        "thread_projection_conversation_count": thread_projection_conversation_count,
        "semantic_edge_count": len(selected_semantic_links),
        "explicit_edge_count": len(
            [link for link in links if str(link.get("type")) == "explicit"]
        ),
        "signal_mode": "hybrid",
        "signal_blend": DEFAULT_SIGNAL_BLEND,
        "sae_proxy_topk": DEFAULT_SAE_PROXY_TOPK,
        "focus_key": str(focus_key or "").strip() or None,
        "focused_included": any(
            str(node.get("label") or "") == str(focus_key or "").strip()
            for node in memory_nodes
        ),
        "embeddings_source": (
            "rag_service"
            if rag_service is not None and callable(getattr(rag_service, "_embed_text", None))
            else "hash_fallback"
        ),
        "limitations": [
            "Explicit provenance is inferred from current memory fields/value payloads.",
            "Thread context is projected from the latest threads summary snapshot when available.",
            "Deeper thread/subthread levels need persisted graph snapshots to render faithfully.",
        ],
    }

    return {
        "schema_version": MEMORY_GRAPH_SCHEMA_VERSION,
        "nodes": nodes,
        "links": links,
        "metadata": metadata,
    }
