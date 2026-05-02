"""Memory management tools (agent-invokable).

These functions require `user` and `signature` keyword arguments and will
raise `PermissionError` if the signature is invalid.

They operate on the global MemoryManager instance set via `set_manager`.

Sensitivity policy:
- Sensitivity levels: mundane, public, personal, protected, secret.
- protected/secret values must not be sent to external APIs by default.
  - protected can be explicitly allowed per call (allow_protected=true).
  - secret values are always redacted in external exports; store password
    hints via the optional `hint` at a protected level.
"""
from __future__ import annotations

import difflib
import hashlib
import html
import json
import re
import time
from typing import Any, Dict, Optional

from app.services import privacy_filter_service
from app.services.rag_provider import (
    get_clip_rag_service,
    get_rag_service,
    try_ingest_text,
)
from app.utils import verify_signature
from app.utils.workspace_registry import (
    load_workspace_state,
    workspace_item_exclusion_reason,
)

_MANAGER = None
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_DEFAULT = object()


def set_manager(manager) -> None:  # set at app startup
    global _MANAGER
    _MANAGER = manager


def _slugify(value: str) -> str:
    return _SLUG_RE.sub("-", value.lower()).strip("-")


def _normalize_optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _derive_memory_key(
    manager, provided_key: Optional[str], namespace: Optional[str], text: str
) -> str:
    text_value = text.strip()
    if provided_key:
        base_key = provided_key.strip()
    else:
        slug = _slugify(text_value) if text_value else ""
        if not slug:
            slug = hashlib.sha1(text_value.encode("utf-8")).hexdigest()[:10]
        ns_prefix = None
        if namespace:
            ns_slug = _slugify(namespace)
            ns_prefix = ns_slug or namespace.strip()
        base_key = f"{ns_prefix}:{slug}" if ns_prefix else slug
    candidate = base_key or hashlib.sha1(text_value.encode("utf-8")).hexdigest()[:10]
    suffix = 2
    while manager.get_item(candidate) is not None:
        candidate = f"{base_key}-{suffix}"
        suffix += 1
    return candidate


def _privacy_to_sensitivity(level: Optional[str]) -> Optional[str]:
    if not level:
        return None
    lookup = {
        "public": "public",
        "external": "public",
        "local": "personal",
        "private": "personal",
        "personal": "personal",
        "protected": "protected",
        "secret": "secret",
    }
    return lookup.get(level.strip().lower())


def _html_unescape_deep(value: Any) -> Any:
    """Recursively unescape HTML entities in string fields."""
    if isinstance(value, str):
        return html.unescape(value)
    if isinstance(value, list):
        return [_html_unescape_deep(item) for item in value]
    if isinstance(value, dict):
        return {key: _html_unescape_deep(val) for key, val in value.items()}
    return value


def _value_to_text(value: Any) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False)
        except Exception:
            text = str(value)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _make_snippet(
    text: str,
    query: str,
    limit: int = 250,
    max_sentences: int = 2,
) -> str:
    if not text or limit <= 0:
        return ""
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return ""

    def _split_sentences(value: str) -> list[str]:
        parts = re.split(r"(?<=[.!?])\s+", value)
        return [part.strip() for part in parts if part and part.strip()]

    sentences = _split_sentences(cleaned) or [cleaned]
    snippet = ""
    if query:
        q = query.lower()
        match_idx = -1
        for idx, sentence in enumerate(sentences):
            if q in sentence.lower():
                match_idx = idx
                break
        if match_idx != -1:
            start_idx = match_idx
            end_idx = min(start_idx + max_sentences, len(sentences))
            snippet = " ".join(sentences[start_idx:end_idx])
            if start_idx > 0:
                snippet = "..." + snippet
            if end_idx < len(sentences):
                snippet = snippet + "..."
        else:
            lowered = cleaned.lower()
            idx = lowered.find(q)
            if idx != -1:
                half = max(limit // 2, 1)
                start = max(idx - half, 0)
                end = min(start + limit, len(cleaned))
                snippet = cleaned[start:end]
                if start > 0:
                    snippet = "..." + snippet.lstrip()
                if end < len(cleaned):
                    snippet = snippet.rstrip() + "..."
            else:
                snippet = " ".join(sentences[:max_sentences])
    else:
        snippet = " ".join(sentences[:max_sentences])

    if len(snippet) > limit:
        snippet = snippet[:limit].rstrip()
        if not snippet.endswith("..."):
            snippet = snippet.rstrip(".") + "..."
    return snippet


def _vectorize_memory_entry(
    key: str,
    value: Any,
    *,
    namespace: Optional[str] = None,
    tags: Optional[list[str]] = None,
    mirror_vector: bool = True,
    hint: Optional[str] = None,
    sensitivity: Optional[str] = None,
    importance: Optional[float] = None,
    pinned: Optional[bool] = None,
    importance_floor: Optional[float] = None,
    lifecycle: Optional[str] = None,
    grounded_at: Optional[float] = None,
    occurs_at: Optional[float] = None,
    review_at: Optional[float] = None,
    decay_at: Optional[float] = None,
    last_confirmed_at: Optional[float] = None,
    pruned_at: Optional[float] = None,
    rag_excluded: Optional[bool] = None,
    privacy_metadata: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    value_text = _value_to_text(value)
    if not value_text:
        return None
    parts = [f"key: {key}"]
    if hint:
        parts.append(f"hint: {hint}")
    parts.append(value_text)
    cleaned = "\n".join(parts).strip()
    metadata: Dict[str, Any] = {
        "kind": "memory",
        "type": "memory",
        "memory_key": key,
        "key": key,
        "title": key,
        "source": f"memory:{key}",
    }
    if namespace:
        metadata["namespace"] = namespace
    if tags:
        metadata["tags"] = tags
    if sensitivity is not None:
        metadata["sensitivity"] = sensitivity
    if hint is not None:
        metadata["hint"] = hint
    if importance is not None:
        metadata["importance"] = importance
    if pinned is not None:
        metadata["pinned"] = pinned
    if importance_floor is not None:
        metadata["importance_floor"] = importance_floor
    if lifecycle is not None:
        metadata["lifecycle"] = lifecycle
    if grounded_at is not None:
        metadata["grounded_at"] = grounded_at
    if occurs_at is not None:
        metadata["occurs_at"] = occurs_at
    if review_at is not None:
        metadata["review_at"] = review_at
    if decay_at is not None:
        metadata["decay_at"] = decay_at
    if last_confirmed_at is not None:
        metadata["last_confirmed_at"] = last_confirmed_at
    if pruned_at is not None:
        metadata["pruned_at"] = pruned_at
    if rag_excluded is not None:
        metadata["rag_excluded"] = rag_excluded
    for field, value in dict(privacy_metadata or {}).items():
        if field == "sensitivity_source" or str(field).startswith("privacy_filter_"):
            metadata[field] = value
    doc_id = try_ingest_text(cleaned, metadata, mirror_vector=mirror_vector)
    if _MANAGER is not None and doc_id:
        try:
            _MANAGER.update_item_fields(
                key,
                {
                    "vectorize": bool(mirror_vector),
                    "vectorized_at": time.time() if mirror_vector else None,
                    "rag_doc_id": doc_id,
                },
            )
        except Exception:
            pass
    return doc_id


def _maybe_queue_reflection_after_save(
    *,
    key: str,
    prompt: Optional[str],
    run_now: bool,
    source_tool: str,
) -> Optional[Dict[str, Any]]:
    try:
        item = _MANAGER.get_item(key, include_pruned=True, touch=False)
    except TypeError:
        item = _MANAGER.get_item(key) if _MANAGER is not None else None
    except Exception:
        item = None
    if isinstance(item, dict):
        sensitivity = str(item.get("sensitivity") or "mundane").lower()
        if sensitivity in {"protected", "secret"}:
            return {
                "status": "skipped",
                "reason": f"{sensitivity}_memory_not_reflected",
            }
    try:
        from app.tools import reflections as reflection_tools

        service = reflection_tools._service()  # type: ignore[attr-defined]
        question = (
            _normalize_optional_str(prompt)
            or f"Review this newly saved memory for one useful follow-up: {key}"
        )
        task = service.create_task(
            title=f"Reflect on memory: {key}",
            question=question,
            source="tool",
            patience=1,
            memory_keys=[key],
            metadata={"created_by": source_tool, "memory_key": key},
        )
        result: Dict[str, Any] = {"status": "queued", "task_id": task.get("id")}
        if run_now:
            result["run"] = service.run_task(str(task.get("id")), force=True)
            result["status"] = "ran"
        return result
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}


def legacy_memory_save(*, user: str, signature: str, **payload: Any) -> Dict[str, Any]:
    """Compatibility tool that accepts the older `memory.save` schema."""

    args = dict(payload)
    verify_signature(signature, user, "memory.save", args)
    text = args.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("memory.save requires a non-empty 'text' field")
    namespace = _normalize_optional_str(args.get("namespace"))
    key_hint = _normalize_optional_str(args.get("key"))
    tags = args.get("tags")
    normalized_tags = None
    if isinstance(tags, list):
        normalized_tags = [str(tag) for tag in tags if isinstance(tag, str)]
    vectorize = args.get("vectorize")
    graph_triples = (
        args.get("graph_triples")
        if isinstance(args.get("graph_triples"), list)
        else None
    )
    privacy = _normalize_optional_str(args.get("privacy"))
    source = _normalize_optional_str(args.get("source"))
    reflect_after_save = bool(args.get("reflect_after_save"))
    reflection_prompt = _normalize_optional_str(args.get("reflection_prompt"))
    reflection_run_now = bool(args.get("reflection_run_now"))

    if _MANAGER is None:
        raise RuntimeError("memory manager not available")
    key = _derive_memory_key(_MANAGER, key_hint, namespace, text)
    explicit_sensitivity = _privacy_to_sensitivity(privacy)
    privacy_decision = privacy_filter_service.decide_sensitivity(
        text,
        explicit_sensitivity=explicit_sensitivity,
        purpose="memory.save",
    )
    effective_sensitivity = (
        privacy_decision.applied_sensitivity
        if privacy_decision.applied_sensitivity is not None
        else explicit_sensitivity
    )

    record: Dict[str, Any] = {"text": text.strip()}
    if namespace:
        record["namespace"] = namespace
    if normalized_tags is not None:
        record["tags"] = normalized_tags
    if vectorize is not None:
        record["vectorize"] = bool(vectorize)
    if graph_triples:
        record["graph_triples"] = graph_triples
    if privacy:
        record["privacy"] = privacy
    if source:
        record["source"] = source

    _MANAGER.upsert_item(
        key,
        record,
        None,
        None,
        None,
        None,
        effective_sensitivity,
        None,
    )
    privacy_updates = privacy_filter_service.metadata_updates(privacy_decision)
    if privacy_updates:
        try:
            _MANAGER.update_item_fields(key, privacy_updates)
        except Exception:
            pass
    try:
        indexed_item = _MANAGER.get_item(key, include_pruned=True, touch=False) or {}
    except TypeError:
        indexed_item = _MANAGER.get_item(key) or {}
    _vectorize_memory_entry(
        key,
        record,
        namespace=namespace,
        tags=normalized_tags,
        mirror_vector=bool(record.get("vectorize")),
        sensitivity=effective_sensitivity,
        privacy_metadata=indexed_item if isinstance(indexed_item, dict) else None,
    )
    result = {"status": "ok", "key": key}
    privacy_notice = privacy_filter_service.notice(privacy_decision)
    if privacy_notice:
        result["privacy_filter_notice"] = privacy_notice
    if reflect_after_save:
        result["reflection"] = _maybe_queue_reflection_after_save(
            key=key,
            prompt=reflection_prompt,
            run_now=reflection_run_now,
            source_tool="memory.save",
        )
    return result


def remember(
    key: str,
    value: Any,
    importance: Optional[float] | object = _DEFAULT,
    *,
    user: str,
    signature: str,
    sensitivity: Optional[str] | object = _DEFAULT,
    hint: Optional[str] | object = _DEFAULT,
    pinned: Optional[bool] | object = _DEFAULT,
    importance_floor: Optional[float] | object = _DEFAULT,
    vectorize: Optional[bool] | object = _DEFAULT,
    lifecycle: Optional[str] | object = _DEFAULT,
    grounded_at: Optional[float] | object = _DEFAULT,
    occurs_at: Optional[float] | object = _DEFAULT,
    review_at: Optional[float] | object = _DEFAULT,
    decay_at: Optional[float] | object = _DEFAULT,
    reflect_after_save: bool | object = _DEFAULT,
    reflection_prompt: Optional[str] | object = _DEFAULT,
    reflection_run_now: bool | object = _DEFAULT,
) -> str:
    """Store a memory item with optional importance, pinning, and sensitivity levels.

    Sensitivity levels: mundane, public, personal, protected, secret.
    - protected/secret: never included in external API exports; protected can be
      optionally allowed per request; secret values are always redacted and
      encouraged to be encrypted at rest. A password hint can be stored via
      the 'hint' parameter at a protected level.
    """
    payload: Dict[str, Any] = {"key": key, "value": value}
    if importance is not _DEFAULT:
        payload["importance"] = importance
    if sensitivity is not _DEFAULT:
        payload["sensitivity"] = sensitivity
    if hint is not _DEFAULT:
        payload["hint"] = hint
    if pinned is not _DEFAULT:
        payload["pinned"] = pinned
    if importance_floor is not _DEFAULT:
        payload["importance_floor"] = importance_floor
    if vectorize is not _DEFAULT:
        payload["vectorize"] = vectorize
    if lifecycle is not _DEFAULT:
        payload["lifecycle"] = lifecycle
    if grounded_at is not _DEFAULT:
        payload["grounded_at"] = grounded_at
    if occurs_at is not _DEFAULT:
        payload["occurs_at"] = occurs_at
    if review_at is not _DEFAULT:
        payload["review_at"] = review_at
    if decay_at is not _DEFAULT:
        payload["decay_at"] = decay_at
    if reflect_after_save is not _DEFAULT:
        payload["reflect_after_save"] = reflect_after_save
    if reflection_prompt is not _DEFAULT:
        payload["reflection_prompt"] = reflection_prompt
    if reflection_run_now is not _DEFAULT:
        payload["reflection_run_now"] = reflection_run_now
    verify_signature(
        signature,
        user,
        "remember",
        payload,
    )
    if _MANAGER is None:
        raise RuntimeError("memory manager not available")
    stored_value = _html_unescape_deep(value)
    explicit_sensitivity = None if sensitivity is _DEFAULT else sensitivity
    try:
        existing_item = _MANAGER.get_item(key, include_pruned=True, touch=False) or {}
    except TypeError:
        existing_item = _MANAGER.get_item(key) or {}
    privacy_decision = privacy_filter_service.decide_sensitivity(
        _value_to_text(stored_value),
        explicit_sensitivity=explicit_sensitivity,
        existing_sensitivity=existing_item.get("sensitivity")
        if isinstance(existing_item, dict)
        else None,
        existing_sensitivity_source=existing_item.get("sensitivity_source")
        if isinstance(existing_item, dict)
        else None,
        purpose="memory_tool",
    )
    effective_sensitivity = (
        privacy_decision.applied_sensitivity
        if privacy_decision.applied_sensitivity is not None
        else explicit_sensitivity
    )
    extra_kwargs = {}
    if pinned is not _DEFAULT:
        extra_kwargs["pinned"] = pinned
    if importance_floor is not _DEFAULT:
        extra_kwargs["importance_floor"] = importance_floor
    if lifecycle is not _DEFAULT:
        extra_kwargs["lifecycle"] = lifecycle
    if grounded_at is not _DEFAULT:
        extra_kwargs["grounded_at"] = grounded_at
    if occurs_at is not _DEFAULT:
        extra_kwargs["occurs_at"] = occurs_at
    if review_at is not _DEFAULT:
        extra_kwargs["review_at"] = review_at
    if decay_at is not _DEFAULT:
        extra_kwargs["decay_at"] = decay_at
    _MANAGER.upsert_item(
        key,
        stored_value,
        None if importance is _DEFAULT else importance,
        None,
        None,
        None,
        effective_sensitivity,
        None if hint is _DEFAULT else hint,
        **extra_kwargs,
    )
    privacy_updates = privacy_filter_service.metadata_updates(privacy_decision)
    if privacy_updates:
        try:
            _MANAGER.update_item_fields(key, privacy_updates)
        except Exception:
            pass
    try:
        item = _MANAGER.get_item(key, include_pruned=True, touch=False) or {}
    except TypeError:
        item = _MANAGER.get_item(key) or {}
    sensitivity_level = str(item.get("sensitivity", "mundane")).lower()
    default_vectorize = sensitivity_level not in {"protected", "secret"}
    should_vectorize = (
        bool(vectorize) if vectorize is not _DEFAULT else default_vectorize
    )
    if item.get("pruned_at") is None:
        _vectorize_memory_entry(
            key,
            item.get("value", stored_value),
            mirror_vector=should_vectorize,
            hint=item.get("hint"),
            sensitivity=item.get("sensitivity"),
            importance=item.get("importance"),
            pinned=item.get("pinned"),
            importance_floor=item.get("importance_floor"),
            lifecycle=item.get("lifecycle"),
            grounded_at=item.get("grounded_at"),
            occurs_at=item.get("occurs_at"),
            review_at=item.get("review_at"),
            decay_at=item.get("decay_at"),
            last_confirmed_at=item.get("last_confirmed_at"),
            pruned_at=item.get("pruned_at"),
            rag_excluded=item.get("rag_excluded"),
            privacy_metadata=item,
        )
    privacy_notice = privacy_filter_service.notice(privacy_decision)
    status_text = f"ok ({privacy_notice})" if privacy_notice else "ok"
    if reflect_after_save is not _DEFAULT and bool(reflect_after_save):
        reflection = _maybe_queue_reflection_after_save(
            key=key,
            prompt=None
            if reflection_prompt is _DEFAULT
            else _normalize_optional_str(reflection_prompt),
            run_now=False
            if reflection_run_now is _DEFAULT
            else bool(reflection_run_now),
            source_tool="remember",
        )
        if isinstance(reflection, dict):
            reflection_status = reflection.get("status") or "unknown"
            task_id = reflection.get("task_id")
            if task_id:
                status_text = (
                    f"{status_text}; reflection {reflection_status}: {task_id}"
                )
            else:
                status_text = f"{status_text}; reflection {reflection_status}"
    return status_text


def recall(
    key: Optional[str] | object = _DEFAULT,
    *,
    user: str,
    signature: str,
    for_external: bool | object = _DEFAULT,
    allow_protected: bool | object = _DEFAULT,
    mode: str | object = _DEFAULT,
    top_k: int | object = _DEFAULT,
    include_images: bool | object = _DEFAULT,
    image_top_k: int | object = _DEFAULT,
) -> Any:
    """Return a memory value or a hybrid recall search result.

    If for_external is True, secret values are redacted and protected values
    are omitted unless allow_protected=True.
    """
    payload: Dict[str, Any] = {}
    if key is not _DEFAULT:
        payload["key"] = key
    if for_external is not _DEFAULT:
        payload["for_external"] = for_external
    if allow_protected is not _DEFAULT:
        payload["allow_protected"] = allow_protected
    if mode is not _DEFAULT:
        payload["mode"] = mode
    if top_k is not _DEFAULT:
        payload["top_k"] = top_k
    if include_images is not _DEFAULT:
        payload["include_images"] = include_images
    if image_top_k is not _DEFAULT:
        payload["image_top_k"] = image_top_k
    verify_signature(signature, user, "recall", payload)
    if _MANAGER is None:
        raise RuntimeError("memory manager not available")
    for_external_flag = bool(for_external) if for_external is not _DEFAULT else False
    allow_protected_flag = (
        bool(allow_protected) if allow_protected is not _DEFAULT else False
    )
    normalized_mode = (
        str(mode or "hybrid").strip().lower() if mode is not _DEFAULT else "hybrid"
    )
    if normalized_mode not in {"hybrid", "canonical", "vector", "memory", "clip"}:
        normalized_mode = "hybrid"
    try:
        search_limit = int(top_k) if top_k is not _DEFAULT else 5
    except Exception:
        search_limit = 5
    search_limit = max(1, min(search_limit, 10))
    include_images_flag = (
        bool(include_images) if include_images is not _DEFAULT else False
    )
    try:
        image_limit = int(image_top_k) if image_top_k is not _DEFAULT else search_limit
    except Exception:
        image_limit = search_limit
    image_limit = max(1, min(image_limit, 10))
    requested_key = "" if key is _DEFAULT else str(key or "")
    candidate_key = requested_key.strip()
    workspace_recall_profiles = load_workspace_state()[0]

    def _memory_recall_candidates(key_name: str, item: Any) -> list[str]:
        payload = item if isinstance(item, dict) else {}
        values = [
            key_name,
            payload.get("source_sync_relative_path"),
            payload.get("source_path"),
            payload.get("source"),
            payload.get("root_source"),
            payload.get("title"),
            payload.get("hint"),
        ]
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value or "").strip().replace("\\", "/")
            if not text:
                continue
            lowered = text.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            normalized.append(text)
        return normalized

    def _item_allowed(item: Any, key_name: str = "") -> bool:
        if isinstance(item, dict) and (
            workspace_item_exclusion_reason(
                namespace=item.get("source_sync_namespace"),
                values=_memory_recall_candidates(key_name, item),
                profiles=workspace_recall_profiles,
                purpose="default_recall",
            )
            is not None
        ):
            return False
        if not for_external_flag:
            return True
        if not isinstance(item, dict):
            return True
        lvl = str(item.get("sensitivity", "mundane")).lower()
        if lvl == "secret":
            return False
        if lvl == "protected" and not allow_protected_flag:
            return False
        return True

    def _item_text(item: Any) -> str:
        if not isinstance(item, dict):
            return _value_to_text(item)
        sensitivity = str(item.get("sensitivity", "mundane")).lower()
        hint = item.get("hint")
        if sensitivity == "secret":
            if isinstance(hint, str) and hint.strip():
                return hint.strip()
            return ""
        value = item.get("value")
        if item.get("encrypted") and isinstance(hint, str) and hint.strip():
            return hint.strip()
        return _value_to_text(value)

    def _item_timestamp(item: Any) -> float:
        if not isinstance(item, dict):
            return 0.0
        for field in ("last_confirmed_at", "updated_at", "created_at"):
            ts = item.get(field)
            if isinstance(ts, (int, float)):
                return float(ts)
        return 0.0

    def _lifecycle_multiplier(item: Any) -> float:
        if not isinstance(item, dict):
            return 1.0
        if hasattr(_MANAGER, "lifecycle_multiplier"):
            try:
                return float(_MANAGER.lifecycle_multiplier(item))
            except Exception:
                return 1.0
        return 1.0

    def _score_match(query: str, text: str) -> float:
        if not query or not text:
            return 0.0
        ratio = difflib.SequenceMatcher(None, query, text).ratio()
        if query in text:
            ratio = max(ratio, 0.88)
        return ratio

    store_items: list[tuple[str, Any]] = []
    try:
        if hasattr(_MANAGER, "iter_items"):
            store_items = list(_MANAGER.iter_items(include_pruned=False, touch=False))
        else:
            raw_store = getattr(_MANAGER, "store", {})
            if isinstance(raw_store, dict):
                for key_name, raw_item in raw_store.items():
                    try:
                        key_str = str(key_name)
                    except Exception:
                        continue
                    store_items.append((key_str, raw_item))
    except Exception:
        store_items = []

    def _collect_recent(limit: int = 5) -> tuple[list[str], list[dict[str, Any]]]:
        ranked: list[tuple[float, float, str, Any]] = []
        for key_str, raw_item in store_items:
            item = raw_item if isinstance(raw_item, dict) else {"value": raw_item}
            if not _item_allowed(item, key_str):
                continue
            multiplier = _lifecycle_multiplier(item)
            if multiplier <= 0:
                continue
            ranked.append(
                (
                    _item_timestamp(item) * multiplier,
                    _item_timestamp(item),
                    key_str,
                    item,
                )
            )
        ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
        recent_keys = [row[2] for row in ranked[:limit]]
        details: list[dict[str, Any]] = []
        for _scored_ts, _ts, key_str, item in ranked[:limit]:
            snippet = _make_snippet(_item_text(item), "", 250)
            entry: Dict[str, Any] = {"key": key_str, "match": "recent"}
            if snippet:
                entry["snippet"] = snippet
            details.append(entry)
        return recent_keys, details

    def _collect_matches(
        query: str, limit: int = 5
    ) -> tuple[list[str], list[dict[str, Any]], list[tuple[str, float]]]:
        matches: list[tuple[float, float, str, Any, str]] = []
        key_scores: list[tuple[str, float]] = []
        q = query.lower()
        for key_str, raw_item in store_items:
            item = raw_item if isinstance(raw_item, dict) else {"value": raw_item}
            if not _item_allowed(item, key_str):
                continue
            multiplier = _lifecycle_multiplier(item)
            if multiplier <= 0:
                continue
            key_lower = key_str.lower()
            key_score = _score_match(q, key_lower)
            value_text = _item_text(item)
            value_lower = value_text.lower() if value_text else ""
            value_score = _score_match(q, value_lower)
            key_scores.append((key_str, key_score))
            score = max(key_score, value_score) * multiplier
            if score <= 0:
                continue
            match_type = "value" if value_score > key_score else "key"
            matches.append(
                (
                    score,
                    _item_timestamp(item),
                    key_str,
                    item,
                    match_type,
                )
            )
        matches.sort(key=lambda row: (row[0], row[1]), reverse=True)
        details: list[dict[str, Any]] = []
        seen: set[str] = set()
        for score, _ts, key_str, item, match_type in matches[:limit]:
            if key_str in seen:
                continue
            seen.add(key_str)
            snippet = _make_snippet(_item_text(item), query, 250)
            entry: Dict[str, Any] = {
                "key": key_str,
                "match": match_type,
                "score": round(score, 4),
            }
            if snippet:
                entry["snippet"] = snippet
            details.append(entry)
        return [entry["key"] for entry in details], details, key_scores

    def _memory_key_from_meta(meta: Dict[str, Any]) -> Optional[str]:
        if not isinstance(meta, dict):
            return None
        memory_key = _normalize_optional_str(meta.get("memory_key"))
        if memory_key:
            return memory_key
        source = _normalize_optional_str(meta.get("source") or meta.get("root_source"))
        if source and source.startswith("memory:"):
            return source.split("memory:", 1)[1] or None
        return None

    def _canonical_memory_item(
        meta: Dict[str, Any]
    ) -> tuple[Optional[str], Optional[dict[str, Any]]]:
        memory_key = _memory_key_from_meta(meta)
        if not memory_key:
            return None, None
        try:
            item = _MANAGER.get_item(memory_key, include_pruned=True, touch=False)
        except TypeError:
            item = _MANAGER.get_item(memory_key)
        if not isinstance(item, dict):
            return memory_key, None
        return memory_key, item

    def _knowledge_allowed(meta: Dict[str, Any]) -> bool:
        if not isinstance(meta, dict):
            return True
        if meta.get("rag_excluded") or meta.get("excluded"):
            return False
        memory_key, canonical_item = _canonical_memory_item(meta)
        if memory_key:
            if canonical_item is None:
                return False
            if _lifecycle_multiplier(canonical_item) <= 0:
                return False
            meta.setdefault("memory_key", memory_key)
            for field in (
                "lifecycle",
                "grounded_at",
                "occurs_at",
                "review_at",
                "decay_at",
                "pruned_at",
                "last_confirmed_at",
                "updated_at",
            ):
                value = canonical_item.get(field)
                if value is not None:
                    meta[field] = value
            if canonical_item.get("rag_excluded"):
                return False
            lvl = str(canonical_item.get("sensitivity", "mundane")).lower()
            if lvl == "secret" and for_external_flag:
                return False
            if lvl == "protected" and for_external_flag and not allow_protected_flag:
                return False
            if (
                workspace_item_exclusion_reason(
                    namespace=canonical_item.get("source_sync_namespace")
                    or meta.get("source_sync_namespace"),
                    values=_memory_recall_candidates(memory_key, canonical_item)
                    + [
                        value
                        for value in (
                            meta.get("source"),
                            meta.get("root_source"),
                            meta.get("source_path"),
                            meta.get("relative_path"),
                        )
                        if value
                    ],
                    profiles=workspace_recall_profiles,
                    purpose="default_recall",
                )
                is not None
            ):
                return False
            return True
        lvl = str(meta.get("sensitivity", "mundane")).lower()
        if lvl == "secret" and for_external_flag:
            return False
        if lvl == "protected" and for_external_flag and not allow_protected_flag:
            return False
        if (
            workspace_item_exclusion_reason(
                namespace=meta.get("source_sync_namespace"),
                values=[
                    value
                    for value in (
                        meta.get("source"),
                        meta.get("root_source"),
                        meta.get("source_path"),
                        meta.get("relative_path"),
                        meta.get("content_hash"),
                        meta.get("filename"),
                        meta.get("key"),
                        meta.get("title"),
                    )
                    if value
                ],
                profiles=workspace_recall_profiles,
                purpose="default_recall",
            )
            is not None
        ):
            return False
        return True

    def _knowledge_score(
        match: Dict[str, Any], meta: Dict[str, Any]
    ) -> Optional[float]:
        raw_score = match.get("score")
        if not isinstance(raw_score, (int, float)):
            return None
        score = float(raw_score)
        _memory_key, canonical_item = _canonical_memory_item(meta)
        if canonical_item is not None:
            score *= _lifecycle_multiplier(canonical_item)
        return score

    def _search_knowledge(query: str) -> list[dict[str, Any]]:
        if normalized_mode in {"memory", "clip"}:
            return []
        service = get_rag_service(raise_http=False)
        if not service:
            return []
        raw_matches: list[dict[str, Any]] = []
        if normalized_mode in {"hybrid", "canonical"}:
            try:
                raw_matches.extend(
                    service.search_canonical(query, top_k=search_limit) or []
                )
            except Exception:
                pass
        if normalized_mode in {"hybrid", "vector"}:
            try:
                vector_matches = service.query(query, top_k=search_limit) or []
            except Exception:
                vector_matches = []
            for match in vector_matches:
                if not isinstance(match, dict):
                    continue
                cloned = dict(match)
                meta = cloned.get("metadata")
                if isinstance(meta, dict):
                    merged_meta = dict(meta)
                else:
                    merged_meta = {}
                merged_meta.setdefault("retrieved_via", "vector")
                cloned["metadata"] = merged_meta
                raw_matches.append(cloned)
        normalized_matches: list[dict[str, Any]] = []
        seen: set[str] = set()
        for match in raw_matches:
            if not isinstance(match, dict):
                continue
            meta = match.get("metadata")
            meta = dict(meta) if isinstance(meta, dict) else {}
            if not _knowledge_allowed(meta):
                continue
            match_id = str(match.get("id") or meta.get("knowledge_id") or "")
            dedupe_key = match_id or str(
                meta.get("source") or meta.get("root_source") or ""
            )
            if dedupe_key and dedupe_key in seen:
                continue
            if dedupe_key:
                seen.add(dedupe_key)
            snippet = _make_snippet(str(match.get("text") or ""), query, 250)
            adjusted_score = _knowledge_score(match, meta)
            entry: Dict[str, Any] = {
                "id": match_id or None,
                "match": str(
                    match.get("retrieved_via") or meta.get("retrieved_via") or "vector"
                ),
                "kind": meta.get("kind") or meta.get("type"),
                "source": meta.get("source") or meta.get("root_source"),
                "score": round(adjusted_score, 4)
                if adjusted_score is not None
                else None,
            }
            if meta.get("memory_key"):
                entry["key"] = meta.get("memory_key")
            if snippet:
                entry["snippet"] = snippet
            normalized_matches.append(entry)
            if len(normalized_matches) >= search_limit:
                break
        return normalized_matches

    def _guess_image_mime(name: Any, url: Any) -> Optional[str]:
        for candidate in (name, url):
            text = str(candidate or "").strip()
            if not text:
                continue
            lowered = text.lower()
            if lowered.endswith((".png", ".apng")):
                return "image/png"
            if lowered.endswith((".jpg", ".jpeg")):
                return "image/jpeg"
            if lowered.endswith(".webp"):
                return "image/webp"
            if lowered.endswith(".gif"):
                return "image/gif"
            if lowered.endswith(".bmp"):
                return "image/bmp"
            if lowered.endswith(".svg"):
                return "image/svg+xml"
        return None

    def _build_image_attachment(meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(meta, dict):
            return None
        content_hash = str(meta.get("content_hash") or "").strip()
        filename = str(meta.get("filename") or content_hash or "image").strip()
        url = str(meta.get("url") or "").strip()
        content_type = (
            str(meta.get("content_type") or meta.get("mime_type") or "").strip()
            or _guess_image_mime(filename, url)
            or ""
        )
        if content_type and not content_type.lower().startswith("image/"):
            return None
        if not url and content_hash:
            url = f"/api/attachments/{content_hash}/{filename}"
        if not url and not content_hash:
            return None
        attachment: Dict[str, Any] = {"name": filename}
        if url:
            attachment["url"] = url
        if content_hash:
            attachment["content_hash"] = content_hash
        if content_type:
            attachment["type"] = content_type
        for field in ("origin", "relative_path", "capture_source"):
            value = meta.get(field)
            if isinstance(value, str) and value.strip():
                attachment[field] = value.strip()
        return attachment

    def _search_image_knowledge(
        query: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if not query:
            return [], []
        clip_service = get_clip_rag_service(raise_http=False)
        if not clip_service:
            return [], []
        caption_service = get_rag_service(raise_http=False)
        try:
            raw_matches = clip_service.query(query, top_k=image_limit) or []
        except Exception:
            raw_matches = []
        normalized: list[dict[str, Any]] = []
        attachments: list[dict[str, Any]] = []
        seen: set[str] = set()
        for match in raw_matches:
            if not isinstance(match, dict):
                continue
            meta = match.get("metadata")
            meta = dict(meta) if isinstance(meta, dict) else {}
            caption_id = meta.get("caption_doc_id") or match.get("id")
            trace = None
            if caption_service and caption_id:
                try:
                    trace = caption_service.trace(str(caption_id))
                except Exception:
                    trace = None
            trace_meta = (
                trace.get("metadata")
                if isinstance(trace, dict) and isinstance(trace.get("metadata"), dict)
                else {}
            )
            merged_meta = dict(trace_meta)
            for field in (
                "source",
                "root_source",
                "filename",
                "content_hash",
                "content_type",
                "url",
                "origin",
                "relative_path",
                "capture_source",
                "caption_model",
                "placeholder",
            ):
                if field in meta and field not in merged_meta:
                    merged_meta[field] = meta[field]
            merged_meta["retrieved_via"] = "clip"
            if not _knowledge_allowed(merged_meta):
                continue
            attachment = _build_image_attachment(merged_meta)
            dedupe_key = (
                str(merged_meta.get("content_hash") or "").strip().lower()
                or str((attachment or {}).get("url") or "").strip().lower()
                or str(merged_meta.get("source") or "").strip().lower()
                or str(caption_id or match.get("id") or "").strip().lower()
            )
            if dedupe_key and dedupe_key in seen:
                continue
            if dedupe_key:
                seen.add(dedupe_key)
            caption_text = ""
            if isinstance(trace, dict):
                caption_text = str(trace.get("text") or "")
            if not caption_text:
                caption_text = str(match.get("text") or "")
            entry: Dict[str, Any] = {
                "id": str(caption_id or match.get("id") or "") or None,
                "match": "clip",
                "kind": merged_meta.get("kind") or merged_meta.get("type") or "image",
                "source": merged_meta.get("source") or merged_meta.get("root_source"),
                "score": (
                    round(float(match.get("score")), 4)
                    if isinstance(match.get("score"), (int, float))
                    else None
                ),
                "caption": caption_text,
                "filename": merged_meta.get("filename"),
                "content_hash": merged_meta.get("content_hash"),
                "url": merged_meta.get("url"),
                "content_type": merged_meta.get("content_type"),
                "caption_model": merged_meta.get("caption_model"),
                "placeholder": bool(merged_meta.get("placeholder")),
            }
            if attachment:
                entry["attachment"] = attachment
                attachments.append(dict(attachment))
            normalized.append(entry)
            if len(normalized) >= image_limit:
                break
        return normalized, attachments

    if not candidate_key:
        recent_keys, recent_details = _collect_recent()
        return {
            "error": "missing_key",
            "requested_key": requested_key,
            "suggestions": recent_keys,
            "suggestions_detail": recent_details,
            "recent_keys": recent_keys,
            "mode": normalized_mode,
        }

    item = (
        None
        if normalized_mode in {"vector", "clip"}
        else _MANAGER.get_item(candidate_key)
    )
    resolved_key = candidate_key
    resolved_via: str | None = None
    suggestions: list[str] = []
    suggestions_detail: list[dict[str, Any]] = []
    recent_keys: list[str] = []
    if not item and normalized_mode not in {"vector", "clip"}:
        suggestions, suggestions_detail, key_scores = _collect_matches(candidate_key)
        recent_keys, recent_details = _collect_recent()
        if not suggestions_detail and recent_details:
            suggestions_detail = recent_details
            suggestions = [entry["key"] for entry in suggestions_detail]

        query = candidate_key.lower()
        allowed_keys: list[str] = []
        for key_str, raw_item in store_items:
            entry = raw_item if isinstance(raw_item, dict) else {"value": raw_item}
            if _item_allowed(entry, key_str):
                allowed_keys.append(key_str)
        prefix_hits = [k for k in allowed_keys if k.lower().startswith(query)]
        fuzzy_key: str | None = None
        if len(prefix_hits) == 1:
            fuzzy_key = prefix_hits[0]
        elif key_scores:
            key_scores.sort(key=lambda row: row[1], reverse=True)
            if key_scores[0][1] >= 0.9:
                fuzzy_key = key_scores[0][0]

        if fuzzy_key:
            resolved_key = fuzzy_key
            resolved_via = "fuzzy"
            item = _MANAGER.get_item(resolved_key)

    image_search_requested = include_images_flag or normalized_mode == "clip"
    if not item:
        knowledge_matches = _search_knowledge(candidate_key)
        image_matches: list[dict[str, Any]] = []
        image_attachments: list[dict[str, Any]] = []
        if image_search_requested:
            image_matches, image_attachments = _search_image_knowledge(candidate_key)
        if knowledge_matches or image_matches:
            return {
                "query": requested_key,
                "mode": normalized_mode,
                "matches": knowledge_matches,
                "image_matches": image_matches,
                "image_attachments": image_attachments,
                "suggestions": suggestions,
                "suggestions_detail": suggestions_detail,
                "recent_keys": recent_keys,
            }
        if suggestions or suggestions_detail or recent_keys:
            return {
                "error": "not_found",
                "requested_key": requested_key,
                "suggestions": suggestions,
                "suggestions_detail": suggestions_detail,
                "recent_keys": recent_keys,
                "mode": normalized_mode,
            }
        return None
    if not isinstance(item, dict):
        return item
    lvl = str(item.get("sensitivity", "mundane")).lower()
    if for_external_flag:
        if lvl == "secret":
            return None
        if lvl == "protected" and not allow_protected_flag:
            return None
    value = item.get("value")
    image_matches: list[dict[str, Any]] = []
    image_attachments: list[dict[str, Any]] = []
    if image_search_requested:
        image_matches, image_attachments = _search_image_knowledge(candidate_key)
    if include_images_flag:
        response: Dict[str, Any] = {
            "value": value,
            "mode": normalized_mode,
            "image_matches": image_matches,
            "image_attachments": image_attachments,
        }
        if resolved_via:
            response["match"] = resolved_via
            response["requested_key"] = requested_key
            response["resolved_key"] = resolved_key
        return response
    if resolved_via:
        return {
            "match": resolved_via,
            "requested_key": requested_key,
            "resolved_key": resolved_key,
            "value": value,
            "mode": normalized_mode,
        }
    return value


def decay_memories(
    rate: float | object = _DEFAULT, *, user: str, signature: str
) -> str:
    payload: Dict[str, Any] = {}
    if rate is not _DEFAULT:
        payload["rate"] = rate
    verify_signature(signature, user, "decay_memories", payload)
    if _MANAGER is None:
        raise RuntimeError("memory manager not available")
    return "deprecated: memory lifecycle is automatic"
