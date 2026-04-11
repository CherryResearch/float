from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def _coerce_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(float(text))
        except Exception:
            return None
    try:
        return int(value)
    except Exception:
        return None


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except Exception:
            return None
    try:
        return float(value)
    except Exception:
        return None


def _coerce_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y", "on"}:
            return True
        if lowered in {"false", "0", "no", "n", "off"}:
            return False
    return None


def _schema_for_tool(tool_name: str) -> Optional[dict]:
    try:
        from app.tool_specs import BUILTIN_TOOL_SPECS

        spec = BUILTIN_TOOL_SPECS.get(tool_name)
        if not isinstance(spec, dict):
            return None
        params = spec.get("parameters")
        if isinstance(params, dict) and params.get("type") == "object":
            return params
    except Exception:
        return None
    return None


def _apply_aliases(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    if tool_name == "search_web":
        alias_map = {
            "topn": "max_results",
            "top_n": "max_results",
            "top_k": "max_results",
            "num_results": "max_results",
            "limit": "max_results",
        }
        for alias, canonical in alias_map.items():
            if alias in args and canonical not in args:
                args[canonical] = args.get(alias)
        for alias in list(alias_map.keys()):
            args.pop(alias, None)
        # Common non-schema args emitted by some models/providers.
        args.pop("source", None)
    elif tool_name == "tool_info":
        if "tool_name" not in args:
            tools_value = args.get("tools")
            if isinstance(tools_value, str) and tools_value.strip():
                args["tool_name"] = tools_value.strip()
            elif (
                isinstance(tools_value, list)
                and len(tools_value) == 1
                and isinstance(tools_value[0], str)
                and tools_value[0].strip()
            ):
                args["tool_name"] = tools_value[0].strip()
            elif isinstance(args.get("name"), str) and str(args.get("name")).strip():
                args["tool_name"] = str(args["name"]).strip()
        args.pop("tools", None)
        args.pop("name", None)
    return args


def normalize_tool_args(tool_name: str, raw_args: Any) -> Dict[str, Any]:
    """Normalize + validate tool args before signature generation/invocation.

    Why: tool functions may verify signatures against a canonical arg set (incl. defaults),
    and Python will raise TypeError if unexpected keys slip through. This helper keeps
    tool execution resilient across model variants (e.g., `topn` vs `max_results`).
    """

    if not isinstance(tool_name, str) or not tool_name.strip():
        raise ValueError("Tool name is required")

    base: Dict[str, Any] = {}
    if isinstance(raw_args, dict):
        base = dict(raw_args)
    else:
        base = {}

    base = _apply_aliases(tool_name.strip(), base)
    schema = _schema_for_tool(tool_name.strip())
    if not schema:
        return base

    props = schema.get("properties")
    properties: Dict[str, Any] = props if isinstance(props, dict) else {}
    required = schema.get("required")
    required_keys = [str(k) for k in required] if isinstance(required, list) else []

    additional = schema.get("additionalProperties")
    if additional is False:
        base = {k: v for k, v in base.items() if k in properties}

    # Fill defaults so signatures match tool-side canonicalization.
    for key, prop_schema in properties.items():
        if key in base:
            continue
        if isinstance(prop_schema, dict) and "default" in prop_schema:
            base[key] = prop_schema.get("default")

    missing = []
    for key in required_keys:
        if key not in base:
            missing.append(key)
            continue
        value = base.get(key)
        if value is None:
            missing.append(key)
            continue
        if isinstance(value, str) and not value.strip():
            missing.append(key)
    if missing:
        raise ValueError(f"Missing required argument(s): {', '.join(missing)}")

    # Coerce simple scalar types to match JSON-schema expectations.
    for key, prop_schema in properties.items():
        if key not in base or not isinstance(prop_schema, dict):
            continue
        expected = prop_schema.get("type")
        if isinstance(expected, list):
            expected = expected[0] if expected else None
        if not expected:
            continue
        value = base.get(key)
        if value is None:
            continue
        if expected == "integer":
            coerced = _coerce_int(value)
            if coerced is None:
                raise ValueError(f"Argument '{key}' must be an integer")
            minimum = _coerce_int(prop_schema.get("minimum"))
            maximum = _coerce_int(prop_schema.get("maximum"))
            if minimum is not None:
                coerced = max(minimum, coerced)
            if maximum is not None:
                coerced = min(maximum, coerced)
            base[key] = coerced
        elif expected == "number":
            coerced = _coerce_float(value)
            if coerced is None:
                raise ValueError(f"Argument '{key}' must be a number")
            minimum = _coerce_float(prop_schema.get("minimum"))
            maximum = _coerce_float(prop_schema.get("maximum"))
            if minimum is not None:
                coerced = max(minimum, coerced)
            if maximum is not None:
                coerced = min(maximum, coerced)
            base[key] = coerced
        elif expected == "boolean":
            coerced = _coerce_bool(value)
            if coerced is None:
                raise ValueError(f"Argument '{key}' must be true/false")
            base[key] = coerced
        elif expected == "string":
            if not isinstance(value, str):
                base[key] = str(value)
        elif expected == "array":
            if not isinstance(value, list):
                raise ValueError(f"Argument '{key}' must be a list")
        elif expected == "object":
            if not isinstance(value, dict) or isinstance(value, list):
                raise ValueError(f"Argument '{key}' must be an object")

    return base


def normalize_and_sanitize_tool_args(
    tool_name: str, raw_args: Any
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Return (normalized_args, sanitized_args)."""

    from app.utils.security import sanitize_args

    normalized = normalize_tool_args(tool_name, raw_args)
    sanitized = sanitize_args(normalized)
    return normalized, sanitized
