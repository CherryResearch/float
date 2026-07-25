from __future__ import annotations

from typing import Any, Dict, Iterable, List


def _field(value: Any, *names: str) -> Any:
    if isinstance(value, dict):
        for name in names:
            if name in value:
                return value.get(name)
        return None
    for name in names:
        candidate = getattr(value, name, None)
        if candidate is not None:
            return candidate
    return None


def _items(value: Any, *names: str) -> Iterable[Any]:
    raw = _field(value, *names)
    return raw if isinstance(raw, (list, tuple)) else []


def _future_result(value: Any) -> Any:
    result = getattr(value, "result", None)
    return result() if callable(result) else value


def _dedupe(values: Iterable[Any]) -> List[str]:
    result: List[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def list_tinker_account_models(api_key: str) -> Dict[str, Any]:
    """List Tinker base models and sampler checkpoints for the authenticated user."""

    token = str(api_key or "").strip()
    if not token:
        return {
            "reachable": False,
            "models": [],
            "loaded_model": "",
            "provider": "tinker",
            "inventory_source": "tinker-sdk",
            "error": "TINKER_API_KEY is not set.",
        }

    try:
        import tinker
    except Exception as exc:
        return {
            "reachable": False,
            "models": [],
            "loaded_model": "",
            "provider": "tinker",
            "inventory_source": "tinker-sdk",
            "error": f"Tinker SDK is unavailable: {exc}",
        }

    client: Any = None
    try:
        # Inventory is REST-only. Avoid consuming a long-lived Tinker training
        # session every time Settings refreshes the account model list.
        client = tinker.ServiceClient(api_key=token, _skip_session=True)
        capabilities = _future_result(client.get_server_capabilities())
        supported_model_items = list(_items(capabilities, "supported_models", "models"))
        supported_models = _dedupe(
            _field(item, "model_name", "name", "id") for item in supported_model_items
        )
        supported_model_details: List[Dict[str, Any]] = []
        for item in supported_model_items:
            model_id = str(_field(item, "model_name", "name", "id") or "").strip()
            if not model_id:
                continue
            detail: Dict[str, Any] = {
                "id": model_id,
                "kind": "base",
                "source": "tinker-sdk",
            }
            max_context_length = _positive_int(
                _field(item, "max_context_length", "context_length")
            )
            if max_context_length is not None:
                detail["max_context_length"] = max_context_length
            supported_model_details.append(detail)

        rest_client = client.create_rest_client()
        checkpoint_items: List[Any] = []
        page_size = 200
        for offset in range(0, 2000, page_size):
            checkpoint_response = _future_result(
                rest_client.list_user_checkpoints(limit=page_size, offset=offset)
            )
            page = list(_items(checkpoint_response, "checkpoints"))
            checkpoint_items.extend(page)
            if len(page) < page_size:
                break
        sampler_checkpoints = _dedupe(
            _field(item, "tinker_path", "path")
            for item in checkpoint_items
            if str(_field(item, "checkpoint_type", "type") or "").strip().lower()
            in {"sampler", "sampler_weights", "save_weights_for_sampler"}
        )
        checkpoint_details = [
            {
                "id": checkpoint,
                "kind": "checkpoint",
                "source": "tinker-sdk",
            }
            for checkpoint in sampler_checkpoints
        ]
        return {
            "reachable": True,
            # Fine-tunes come first because they are the account-specific part of
            # Tinker's inventory and are the most likely selection in Float.
            "models": _dedupe([*sampler_checkpoints, *supported_models]),
            "loaded_model": "",
            "provider": "tinker",
            "inventory_source": "tinker-sdk",
            "base_models": supported_models,
            "sampler_checkpoints": sampler_checkpoints,
            "model_details": [*checkpoint_details, *supported_model_details],
        }
    except Exception as exc:
        return {
            "reachable": False,
            "models": [],
            "loaded_model": "",
            "provider": "tinker",
            "inventory_source": "tinker-sdk",
            "error": str(exc) or exc.__class__.__name__,
        }
    finally:
        holder = getattr(client, "holder", None)
        close = getattr(holder, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


__all__ = ["list_tinker_account_models"]
