from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Dict, Iterable, List, Optional

from app.tool_names import normalize_tool_name

CAPABILITY_SCOPE_VERSION = 1


def tool_definition_name(definition: Any) -> str:
    """Return the canonical name from a prompt or native tool definition."""

    value = definition
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        value = model_dump(exclude_none=True)
    if not isinstance(value, dict):
        return ""
    name = normalize_tool_name(value.get("name"))
    if name:
        return name
    function = value.get("function")
    if isinstance(function, dict):
        return normalize_tool_name(function.get("name"))
    return ""


def _definition_payload(definition: Any, canonical_name: str) -> Any:
    model_dump = getattr(definition, "model_dump", None)
    if callable(model_dump):
        definition = model_dump(exclude_none=True)
    if not isinstance(definition, dict):
        return definition
    payload = copy.deepcopy(definition)
    if payload.get("name"):
        payload["name"] = canonical_name
    elif isinstance(payload.get("function"), dict):
        payload["function"]["name"] = canonical_name
    return payload


def _normalized_modules(modules: Iterable[Any] | None) -> List[str]:
    return sorted(
        {
            str(module or "").strip()
            for module in (modules or [])
            if str(module or "").strip()
        }
    )


def build_capability_scope(
    *,
    workflow: str,
    channel: str,
    modules: Iterable[Any] | None,
    tool_definitions: Iterable[Any] | None,
) -> Dict[str, Any]:
    """Build a compact audit record for a model-facing tool catalog."""

    definitions = list(tool_definitions or [])
    definitions_by_name: Dict[str, Any] = {}
    for definition in definitions:
        name = tool_definition_name(definition)
        if not name:
            continue
        definitions_by_name.setdefault(name, _definition_payload(definition, name))
    named_definitions = sorted(definitions_by_name.items())
    catalog_json = json.dumps(
        [payload for _, payload in named_definitions],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return {
        "version": CAPABILITY_SCOPE_VERSION,
        "workflow": str(workflow or "default").strip() or "default",
        "channel": str(channel or "text").strip() or "text",
        "modules": _normalized_modules(modules),
        "tool_names": [name for name, _ in named_definitions],
        "tool_catalog_sha256": hashlib.sha256(catalog_json.encode("utf-8")).hexdigest(),
    }


def normalize_capability_scope(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    try:
        version = int(value.get("version"))
    except (TypeError, ValueError):
        return None
    if version != CAPABILITY_SCOPE_VERSION:
        return None
    raw_tool_names = value.get("tool_names")
    if not isinstance(raw_tool_names, list):
        return None
    catalog_hash = str(value.get("tool_catalog_sha256") or "").strip().lower()
    if len(catalog_hash) != 64 or any(
        character not in "0123456789abcdef" for character in catalog_hash
    ):
        return None
    tool_names = sorted(
        {
            normalize_tool_name(name)
            for name in raw_tool_names
            if normalize_tool_name(name)
        }
    )
    return {
        "version": version,
        "workflow": str(value.get("workflow") or "default").strip() or "default",
        "channel": str(value.get("channel") or "text").strip() or "text",
        "modules": _normalized_modules(value.get("modules") or []),
        "tool_names": tool_names,
        "tool_catalog_sha256": catalog_hash,
    }


def filter_tool_definitions_for_scope(
    tool_definitions: Iterable[Any] | None,
    scope: Any,
) -> List[Any]:
    """Keep a continuation catalog within its saved turn scope."""

    normalized = normalize_capability_scope(scope)
    definitions = list(tool_definitions or [])
    if normalized is None:
        return definitions
    allowed = set(normalized["tool_names"])
    return [
        definition
        for definition in definitions
        if tool_definition_name(definition) in allowed
    ]


__all__ = [
    "CAPABILITY_SCOPE_VERSION",
    "build_capability_scope",
    "filter_tool_definitions_for_scope",
    "normalize_capability_scope",
    "tool_definition_name",
]
