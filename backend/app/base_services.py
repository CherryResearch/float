# flake8: noqa

import base64
import copy
import gc
import hashlib
import importlib.util
import json
import logging
import mimetypes
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from subprocess import Popen
from time import sleep
from typing import (
    Any,
    Callable,
    Dict,
    Iterator,
    List,
    Literal,
    Optional,
    Sequence,
    Set,
    Tuple,
    TypedDict,
)
from urllib.parse import urlparse, urlunparse

import requests
from app import hooks
from app.utils.blob_store import get_blob as load_blob
from app.utils.graph_store import GraphStore
from app.utils.hardware import gpu_memory_snapshot, system_memory_snapshot
from app.utils.http_client import http_session as _real_http_session
from app.utils.llm_server_log import log_event as log_llm_server_event
from app.utils.stream_sanitize import InlineToolStreamFilter


# Provide a shim so tests can monkeypatch either app.base_services.http_session.post
# or requests.post and still intercept the network call.
class _HttpShim:
    def post(self, url, headers=None, json=None, timeout=None, **kwargs):
        return requests.post(url, headers=headers, json=json, timeout=timeout, **kwargs)


# Exported name used by tests
http_session = _HttpShim()
from app.utils.harmony import Message, Role  # envelope utilities (shimmed)

_TRANSFORMERS_COMPONENTS_LOADED = False
_AUTO_MODEL_FOR_CAUSAL_LM = None
_AUTO_TOKENIZER = None
AutoModelForCausalLM = None
AutoTokenizer = None
_TRANSFORMERS_ATTN_LOADED = False
_FLASH_ATTN_AVAILABLE_FN = None
_TORCH_SDPA_AVAILABLE_FN = None
_TORCH_IMPORT_ATTEMPTED = False
_TORCH_MODULE = None


def _get_transformers_components():
    global _TRANSFORMERS_COMPONENTS_LOADED
    global _AUTO_MODEL_FOR_CAUSAL_LM
    global _AUTO_TOKENIZER
    global AutoModelForCausalLM
    global AutoTokenizer
    if AutoModelForCausalLM is not None or AutoTokenizer is not None:
        _AUTO_MODEL_FOR_CAUSAL_LM = AutoModelForCausalLM
        _AUTO_TOKENIZER = AutoTokenizer
        _TRANSFORMERS_COMPONENTS_LOADED = True
        return _AUTO_MODEL_FOR_CAUSAL_LM, _AUTO_TOKENIZER
    if not _TRANSFORMERS_COMPONENTS_LOADED:
        try:  # pragma: no cover - optional dependency
            from transformers import AutoModelForCausalLM, AutoTokenizer

            _AUTO_MODEL_FOR_CAUSAL_LM = AutoModelForCausalLM
            _AUTO_TOKENIZER = AutoTokenizer
        except Exception:  # pragma: no cover - allow tests without transformers
            _AUTO_MODEL_FOR_CAUSAL_LM = None
            _AUTO_TOKENIZER = None
        _TRANSFORMERS_COMPONENTS_LOADED = True
    AutoModelForCausalLM = _AUTO_MODEL_FOR_CAUSAL_LM
    AutoTokenizer = _AUTO_TOKENIZER
    return _AUTO_MODEL_FOR_CAUSAL_LM, _AUTO_TOKENIZER


def _get_transformers_attention_helpers():
    global _TRANSFORMERS_ATTN_LOADED
    global _FLASH_ATTN_AVAILABLE_FN
    global _TORCH_SDPA_AVAILABLE_FN
    if not _TRANSFORMERS_ATTN_LOADED:
        try:  # pragma: no cover - optional dependency
            from transformers.utils import (
                is_flash_attn_2_available as _flash_attn_available,
            )
            from transformers.utils import (
                is_torch_sdpa_available as _torch_sdpa_available,
            )

            _FLASH_ATTN_AVAILABLE_FN = _flash_attn_available
            _TORCH_SDPA_AVAILABLE_FN = _torch_sdpa_available
        except Exception:  # pragma: no cover
            _FLASH_ATTN_AVAILABLE_FN = lambda: False
            _TORCH_SDPA_AVAILABLE_FN = lambda: False
        _TRANSFORMERS_ATTN_LOADED = True
    return _FLASH_ATTN_AVAILABLE_FN, _TORCH_SDPA_AVAILABLE_FN


def is_flash_attn_2_available() -> bool:  # type: ignore
    flash_attn_available, _ = _get_transformers_attention_helpers()
    return bool(flash_attn_available and flash_attn_available())


def is_torch_sdpa_available() -> bool:  # type: ignore
    _, torch_sdpa_available = _get_transformers_attention_helpers()
    return bool(torch_sdpa_available and torch_sdpa_available())


def _get_torch():
    global _TORCH_IMPORT_ATTEMPTED
    global _TORCH_MODULE
    if not _TORCH_IMPORT_ATTEMPTED:
        try:  # pragma: no cover - optional dependency
            import torch as _torch

            _TORCH_MODULE = _torch
        except Exception:  # pragma: no cover
            _TORCH_MODULE = None
        _TORCH_IMPORT_ATTEMPTED = True
    return _TORCH_MODULE


class _LazyTorchProxy:
    def __bool__(self) -> bool:
        return _get_torch() is not None

    def __getattr__(self, name: str):
        module = _get_torch()
        if module is None:
            raise AttributeError(name)
        return getattr(module, name)


torch = _LazyTorchProxy()  # type: ignore

from workers.multimodal import (
    VisionCaptioner,
    is_placeholder_caption,
    placeholder_caption,
)

from . import config as app_config
from .model_registry import resolve_model_alias
from .tool_catalog import get_tool_catalog_entry
from .utils import memory_store, verify_signature
from .utils.local_model_registry import resolve_registered_model_path
from .utils.time_resolution import normalize_temporal_references

_ALLOWED_OPENAI_ROLES = {"assistant", "user", "system", "tool", "developer", "function"}
_VISION_NATIVE_MODEL_HINTS = (
    "gpt-4o",
    "gpt-4.1",
    "gpt-5",
    "omni",
    "vision",
    "vlm",
    "gemini",
    "claude-3",
    "claude-4",
    "paligemma",
    "pixtral",
    "llava",
    "bakllava",
    "internvl",
    "minicpm-v",
    "qwen-vl",
    "qwen2-vl",
    "molmo",
)
_VISION_EXCLUDED_MODEL_HINTS = (
    "clip",
    "embedding",
    "whisper",
    "tts",
    "bge",
    "e5",
)


def _mxfp4_kernels_available() -> bool:
    try:
        return (
            importlib.util.find_spec("triton") is not None
            and importlib.util.find_spec("triton_kernels") is not None
        )
    except Exception:
        return False


def _normalize_chat_role(role_value: Any, default: str = "user") -> str:
    """Coerce role values (including enums) into strings accepted by OpenAI APIs."""
    candidate = role_value
    try:
        if hasattr(candidate, "value"):
            value = candidate.value
            if value is not None:
                candidate = value
    except Exception:
        pass
    if isinstance(candidate, bytes):
        try:
            candidate = candidate.decode("utf-8")
        except Exception:
            candidate = ""
    if not isinstance(candidate, str):
        candidate = str(candidate or "")
    candidate = candidate.strip()
    if "." in candidate:
        suffix = candidate.split(".")[-1]
        if suffix.lower() in _ALLOWED_OPENAI_ROLES:
            candidate = suffix
    lowered = candidate.lower()
    if lowered not in _ALLOWED_OPENAI_ROLES:
        return default
    return lowered or default


def _normalize_vision_workflow(value: Any, default: str = "auto") -> str:
    candidate = str(value or "").strip().lower()
    if candidate in {"auto", "image_qa", "ocr", "compare", "caption"}:
        return candidate
    return default


def _model_supports_native_images(model_name: Any) -> bool:
    normalized = str(model_name or "").strip().lower()
    if not normalized:
        return False
    if any(token in normalized for token in _VISION_EXCLUDED_MODEL_HINTS):
        return False
    return any(token in normalized for token in _VISION_NATIVE_MODEL_HINTS)


def _convert_tools_for_openai(tools: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert Float tool definitions into the shape expected by OpenAI APIs."""
    converted: List[Dict[str, Any]] = []
    native_passthrough_types = {
        "computer",
        "computer_use_preview",
        "shell",
        "apply_patch",
        "mcp",
    }
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        tool_type = str(tool.get("type") or "").strip().lower()
        if tool_type in native_passthrough_types and not tool.get("name"):
            native_payload = dict(tool)
            native_payload.pop("native", None)
            native_payload.pop("metadata", None)
            converted.append(native_payload)
            continue
        native_payload = tool.get("native")
        if isinstance(native_payload, dict):
            native_type = str(native_payload.get("type") or "").strip().lower()
            if native_type in native_passthrough_types:
                converted.append(dict(native_payload))
                continue
        name_raw = tool.get("name")
        try:
            name = str(name_raw).strip()
        except Exception:
            name = ""
        if not name:
            continue
        description = tool.get("description")
        if isinstance(description, str):
            description = description.strip()
        else:
            description = None
        params = tool.get("parameters") or {}
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except Exception:
                params = {}
        if not isinstance(params, dict):
            params = {}
        if "type" not in params:
            params = {
                "type": "object",
                "properties": dict(params) if isinstance(params, dict) else {},
            }
        tool_entry: Dict[str, Any] = {
            "type": "function",
            "function": {
                "name": name,
                "parameters": params or {"type": "object", "properties": {}},
            },
        }
        if description:
            tool_entry["function"]["description"] = description
        converted.append(tool_entry)
    return converted


def _contains_native_openai_tool(tool_definitions: Sequence[Dict[str, Any]]) -> bool:
    native_types = {
        "computer",
        "computer_use_preview",
        "shell",
        "apply_patch",
        "mcp",
    }
    for tool in tool_definitions or []:
        if not isinstance(tool, dict):
            continue
        tool_type = str(tool.get("type") or "").strip().lower()
        if tool_type in native_types and "function" not in tool:
            return True
    return False


def _native_tool_name_from_output_type(output_type: str) -> Optional[str]:
    mapping = {
        "computer_call": "computer.act",
        "shell_call": "shell.exec",
        "apply_patch_call": "patch.apply",
        "mcp_call": "mcp.call",
    }
    return mapping.get(str(output_type or "").strip().lower())


def _normalize_native_tool_args(payload: Dict[str, Any]) -> Dict[str, Any]:
    output_type = str(payload.get("type") or "").strip().lower()
    if output_type == "computer_call":
        action = payload.get("action")
        if isinstance(action, dict):
            return {
                "session_id": str(
                    payload.get("session_id")
                    or payload.get("call_id")
                    or payload.get("id")
                    or ""
                ).strip(),
                "actions": [dict(action)],
                "native_call_id": payload.get("call_id") or payload.get("id"),
            }
        if isinstance(payload.get("actions"), list):
            return {
                "session_id": str(
                    payload.get("session_id")
                    or payload.get("call_id")
                    or payload.get("id")
                    or ""
                ).strip(),
                "actions": [
                    dict(item)
                    for item in payload.get("actions") or []
                    if isinstance(item, dict)
                ],
                "native_call_id": payload.get("call_id") or payload.get("id"),
            }
    if output_type == "shell_call":
        return {
            "command": payload.get("command") or payload.get("input") or "",
            "timeout_seconds": payload.get("timeout_seconds") or 20,
            "cwd": payload.get("cwd") or "",
            "native_call_id": payload.get("call_id") or payload.get("id"),
        }
    if output_type == "apply_patch_call":
        return {
            "path": payload.get("path") or "",
            "content": payload.get("patch") or payload.get("content") or "",
            "mode": payload.get("mode") or "replace",
            "native_call_id": payload.get("call_id") or payload.get("id"),
        }
    if output_type == "mcp_call":
        return {
            "server": payload.get("server") or "",
            "method": payload.get("method") or payload.get("name") or "",
            "arguments": payload.get("arguments")
            if isinstance(payload.get("arguments"), dict)
            else {},
            "native_call_id": payload.get("call_id") or payload.get("id"),
        }
    return {}


def _extract_native_responses_tool_calls(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    tools_used: List[Dict[str, Any]] = []
    output = data.get("output")
    if not isinstance(output, list):
        response_obj = data.get("response")
        if isinstance(response_obj, dict):
            output = response_obj.get("output")
    if not isinstance(output, list):
        return tools_used
    for item in output:
        if not isinstance(item, dict):
            continue
        name = _native_tool_name_from_output_type(item.get("type") or "")
        if not name:
            continue
        args = _normalize_native_tool_args(item)
        if name == "computer.act" and not args.get("actions"):
            continue
        if name in {"shell.exec", "patch.apply"} and not args:
            continue
        tools_used.append({"name": name, "args": args, "native": dict(item)})
    return tools_used


def _resolve_hf_snapshot_path(
    models_root: "os.PathLike[str]", model_name: str
) -> Optional[Path]:
    root = Path(models_root)
    try:
        for candidate in root.glob(f"models--*--{model_name}"):
            if not candidate.is_dir():
                continue
            refs = candidate / "refs" / "main"
            snap_root = candidate / "snapshots"
            if refs.exists():
                try:
                    commit = refs.read_text().strip()
                    snap = snap_root / commit
                    if snap.exists() and snap.is_dir():
                        return snap
                except Exception:
                    pass
            try:
                snaps = [p for p in snap_root.iterdir() if p.is_dir()]
                if snaps:
                    snaps.sort(
                        key=lambda p: getattr(p.stat(), "st_mtime", 0), reverse=True
                    )
                    return snaps[0]
            except Exception:
                pass
    except Exception:
        pass
    return None


def _resolve_local_model_dir(
    search_roots: List["os.PathLike[str]"], model_name: str
) -> Optional[Path]:
    registered = resolve_registered_model_path(model_name, for_loading=True)
    if registered is not None:
        return registered
    for root in map(Path, search_roots):
        direct = root / model_name
        try:
            if direct.exists() and direct.is_dir():
                return direct
        except Exception:
            pass
        snap = _resolve_hf_snapshot_path(root, model_name)
        if snap is not None:
            return snap
    return None


class ModelContext:
    def __init__(
        self,
        system_prompt: str = "",
        messages: List[Dict[str, str]] = None,
        tools: List[Dict[str, Any]] = None,
        metadata: Dict[str, Any] = None,
    ):
        self.system_prompt = system_prompt
        self.messages = messages or []
        self.tools = tools or []
        self.metadata = metadata or {}

    def add_message(
        self, role: str, content: str, metadata: Optional[Dict[str, Any]] = None
    ):
        self.messages.append(
            {"role": role, "content": content, "metadata": metadata or {}}
        )

    def add_tool(
        self,
        name: str,
        description: str,
        parameters: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.tools.append(
            {
                "name": name,
                "description": description,
                "parameters": parameters,
                "metadata": metadata or {},
            }
        )

    def set_metadata(self, key: str, value: Any):
        self.metadata[key] = value

    def get_metadata(self, key: str) -> Optional[Any]:
        return self.metadata.get(key)

    def add_image(self, path: str, score: float) -> None:
        """Store an image reference with an associated *score*.

        Images are recorded under ``metadata['images']`` so they can be
        injected into a model prompt or inspected later.  Each entry is a
        mapping with ``path`` and ``score`` keys.
        """
        images = self.metadata.setdefault("images", [])
        images.append({"path": path, "score": score})

    def clear(self):
        self.messages = []
        self.tools = []
        self.metadata = {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "system_prompt": self.system_prompt,
            "messages": self.messages,
            "tools": self.tools,
            "metadata": self.metadata,
        }


logger = logging.getLogger(__name__)
MAX_INLINE_ATTACHMENTS = 3


class LLMService:
    def __init__(self, mode="api", config=None):
        """
        Initialize the LLM service.
        Modes:
        - api:     First‑party API (e.g., OpenAI default)
        - server:  OpenAI‑compatible server (e.g., LM Studio/self‑hosted)
        - local:   Local inference (Transformers)
        - dynamic: Legacy experimental server mode (deprecated)
        """
        self.mode = mode
        # Load configuration, allowing for overwrite via passed config
        self.config = config or app_config.load_config()
        self.dynamic_process = None
        self.local_model = None
        self.local_tokenizer = None
        default_prompt = self.config.get("system_prompt", "")
        self.contexts: Dict[str, ModelContext] = {
            "default": ModelContext(system_prompt=default_prompt)
        }
        # Default request timeout (seconds)
        self.timeout = self._parse_timeout_config()
        self.timeout_backoff = self._parse_timeout_backoff()
        self.stream_idle_timeout = self._parse_stream_idle_timeout()
        self.allow_remote_code = bool(self.config.get("allow_remote_code", True))
        # Inference tuning options
        self.use_kv_cache = self.config.get("enable_kv_cache", True)
        self.max_context_length = self.config.get("max_context_length", 2048)
        self.enable_ram_swap = self.config.get("enable_ram_swap", False)
        self._kv_cache: Dict[str, Any] = {}
        self._last_memory_plan: Optional[Dict[str, int]] = None
        self._last_memory_snapshot: Optional[Dict[str, Any]] = None
        self._local_quant_method: Optional[str] = None
        self._local_backend_active: Optional[str] = "transformers"
        self._local_load_state: str = "idle"
        self._local_load_error: Optional[str] = None
        self._local_load_started_at: Optional[float] = None
        self._local_load_finished_at: Optional[float] = None

    def set_context(self, context: ModelContext, session_id: str = "default"):
        """Set the model context for a session."""
        self.contexts[session_id] = context

    def get_context(self, session_id: str = "default") -> ModelContext:
        """Get the model context for a session."""
        default_prompt = self.config.get("system_prompt", "")
        return self.contexts.setdefault(
            session_id, ModelContext(system_prompt=default_prompt)
        )

    def branch_context(self, from_id: str, new_id: str) -> ModelContext:
        """Create a copy of an existing context under a new ID."""
        source = self.get_context(from_id)
        branched = ModelContext(
            system_prompt=source.system_prompt,
            messages=list(source.messages),
            tools=list(source.tools),
            metadata=dict(source.metadata),
        )
        self.contexts[new_id] = branched
        return branched

    def clear_context(self, session_id: str = "default"):
        """Clear the context for the specified session."""
        self.get_context(session_id).clear()

    def _config_str(self, key: str) -> Optional[str]:
        value = self.config.get(key)
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return None

    def _local_model_name(self, override_model_name: Optional[str] = None) -> str:
        return str(
            override_model_name
            or self.config.get("local_model")
            or self.config.get("transformer_model")
            or "gpt2"
        )

    @staticmethod
    def _format_bytes(value: Optional[int]) -> str:
        if value is None:
            return "n/a"
        units = ["B", "KiB", "MiB", "GiB", "TiB"]
        size = float(value)
        idx = 0
        while size >= 1024 and idx < len(units) - 1:
            size /= 1024
            idx += 1
        return f"{size:.2f}{units[idx]}"

    def _resolve_torch_dtype(self, value: Optional[str]):
        if not torch or not value:
            return None
        alias_map = {
            "fp16": "float16",
            "f16": "float16",
            "float16": "float16",
            "bf16": "bfloat16",
            "bfloat16": "bfloat16",
            "fp32": "float32",
            "f32": "float32",
            "float32": "float32",
            "fp64": "float64",
            "f64": "float64",
            "float64": "float64",
            "int8": "int8",
            "i8": "int8",
            "uint8": "uint8",
            "u8": "uint8",
        }
        dtype_name = alias_map.get(value.strip().lower(), value.strip())
        return getattr(torch, dtype_name, None)

    def _dtype_itemsize(self, dtype) -> int:
        if not torch or dtype is None:
            return 0
        try:
            return torch.tensor([], dtype=dtype).element_size()
        except Exception:
            return int(getattr(dtype, "itemsize", 0) or 0)

    def _configure_torch_threads(self) -> None:
        if not torch:
            return
        threads = self.config.get("local_cpu_thread_count", 0)
        if isinstance(threads, int) and threads > 0:
            try:
                torch.set_num_threads(threads)
            except Exception:
                logger.debug("Failed to set torch num threads", exc_info=True)
            try:
                torch.set_num_interop_threads(threads)
            except Exception:
                logger.debug("Failed to set torch num interop threads", exc_info=True)

    def _should_use_gpu(self, device_map: Any) -> bool:
        if not (torch and torch.cuda.is_available()):
            return False
        if device_map is None:
            strategy = self._config_str("local_device_map_strategy")
            if strategy:
                strategy = strategy.lower()
                if strategy in {
                    "auto",
                    "balanced",
                    "balanced_low_0",
                    "balanced_high_0",
                    "sequential",
                }:
                    return True
                if strategy.startswith("cuda"):
                    return True
                return False
            device_id = self._preferred_device_id()
            return not device_id or device_id.startswith("cuda")
        if isinstance(device_map, str):
            normalized = device_map.lower()
            if normalized in {
                "auto",
                "balanced",
                "balanced_low_0",
                "balanced_high_0",
                "sequential",
            }:
                return True
            return normalized.startswith("cuda")
        if isinstance(device_map, dict):
            return any(str(val).startswith("cuda") for val in device_map.values())
        return False

    def _preferred_device_id(self) -> Optional[str]:
        device = self.config.get("inference_device")
        if isinstance(device, str) and device.strip():
            return device.strip()
        default = self.config.get("default_inference_device")
        if isinstance(default, dict):
            return default.get("id") or default.get("name")
        return None

    def _apply_device_preferences(self, model_kwargs: Dict[str, Any]) -> None:
        existing = model_kwargs.get("device_map")
        if existing not in (None, "auto"):
            return

        strategy = self._config_str("local_device_map_strategy")
        if strategy:
            normalized = strategy.lower()
            if normalized in {
                "auto",
                "balanced",
                "balanced_low_0",
                "balanced_high_0",
                "sequential",
            }:
                model_kwargs["device_map"] = normalized
                return
            if normalized in {"cpu", "mps"} or normalized.startswith("cuda"):
                model_kwargs["device_map"] = {"": normalized}
                return

        device_id = self._preferred_device_id()
        if isinstance(device_id, str) and device_id.strip().lower() == "auto":
            device_id = None

        if not device_id:
            if self.enable_ram_swap:
                model_kwargs.setdefault("device_map", "auto")
            return

        try:
            lowered = device_id.lower()
            if lowered.startswith("cuda"):
                if torch and torch.cuda.is_available():
                    target = device_id if ":" in device_id else device_id
                    model_kwargs["device_map"] = {"": target}
                else:
                    logger.warning(
                        "Configured CUDA device %s unavailable; falling back to CPU",
                        device_id,
                    )
            elif lowered.startswith("mps"):
                model_kwargs["device_map"] = {"": "mps"}
            elif lowered == "cpu":
                model_kwargs["device_map"] = {"": "cpu"}
        except Exception:
            logger.debug("Failed to apply device preferences", exc_info=True)

    def _apply_attention_preferences(self, model_kwargs: Dict[str, Any]) -> None:
        attn_impl = self._config_str("local_attention_implementation")
        if attn_impl:
            model_kwargs["attn_implementation"] = attn_impl
            return
        if not self.config.get("local_flash_attention"):
            return
        if is_flash_attn_2_available():
            model_kwargs["attn_implementation"] = "flash_attention_2"
        elif is_torch_sdpa_available():
            model_kwargs["attn_implementation"] = "sdpa"
        else:
            logger.warning(
                "Flash attention requested but dependencies are unavailable; using default attention implementation"
            )

    def _apply_dtype_preferences(self, model_kwargs: Dict[str, Any]) -> None:
        if not torch:
            return
        dtype_override = self._resolve_torch_dtype(
            self.config.get("local_weight_dtype")
        )
        using_gpu = self._should_use_gpu(model_kwargs.get("device_map"))
        if dtype_override is not None:
            model_kwargs.setdefault("torch_dtype", dtype_override)
            return
        if not using_gpu:
            return
        if (self._local_quant_method or "").lower() == "mxfp4":
            if not _mxfp4_kernels_available():
                try:
                    model_kwargs.setdefault("torch_dtype", getattr(torch, "float16"))
                    logger.warning(
                        "MXFP4 kernels missing; forcing float16 weights to avoid dtype mismatches."
                    )
                    return
                except Exception:
                    logger.debug(
                        "Unable to set torch dtype to float16 for MXFP4 fallback",
                        exc_info=True,
                    )
            bf16 = getattr(torch, "bfloat16", None)
            if bf16 is not None:
                bf16_supported = True
                if torch.cuda.is_available():
                    try:
                        bf16_supported = torch.cuda.is_bf16_supported()
                    except Exception:
                        bf16_supported = True
                    if bf16_supported is False:
                        try:
                            major, _minor = torch.cuda.get_device_capability()
                            if major >= 8:
                                bf16_supported = True
                        except Exception:
                            pass
                if bf16_supported:
                    model_kwargs.setdefault("torch_dtype", bf16)
                    return
            logger.warning(
                "MXFP4 checkpoint prefers bfloat16 but bf16 is unavailable; "
                "falling back to float16."
            )
        try:
            model_kwargs.setdefault("torch_dtype", getattr(torch, "float16"))
        except Exception:
            logger.debug("Unable to set torch dtype to float16", exc_info=True)

    def _apply_memory_strategy(self, model_kwargs: Dict[str, Any]) -> None:
        if model_kwargs.get("max_memory") is not None:
            return
        self._last_memory_plan = None
        plan = self._compute_max_memory_plan(model_kwargs.get("device_map"))
        if not plan:
            return
        self._last_memory_plan = plan
        model_kwargs["max_memory"] = plan
        if model_kwargs.get("device_map") in (None, "auto"):
            model_kwargs["device_map"] = "auto"
        if self.enable_ram_swap:
            models_folder = self.config.get("models_folder")
            if models_folder:
                model_kwargs.setdefault("offload_folder", str(models_folder))
        if self.config.get("local_low_cpu_mem_usage", True):
            if (self._local_quant_method or "").lower() == "mxfp4":
                # MXFP4 checkpoints can misbehave with low_cpu_mem_usage during offload.
                model_kwargs.setdefault("low_cpu_mem_usage", False)
            else:
                model_kwargs.setdefault("low_cpu_mem_usage", True)
        human_plan = {
            key: self._format_bytes(int(value)) for key, value in plan.items()
        }
        logger.info(
            "Using max_memory plan for local model load",
            extra={"max_memory_plan": human_plan},
        )

    def _normalize_memory_plan_key(self, entry: Dict[str, Any]) -> int | str | None:
        """Convert hardware snapshot identifiers into accelerate-compatible keys."""
        idx = entry.get("index")
        if isinstance(idx, int):
            return idx

        device_id = entry.get("id")
        if isinstance(device_id, str):
            lowered = device_id.lower()
            for prefix in ("cuda:", "gpu:", "xpu:"):
                if lowered.startswith(prefix):
                    suffix = lowered.split(":", 1)[1]
                    try:
                        return int(suffix)
                    except (ValueError, IndexError):
                        return None
            if lowered in {"cuda", "gpu", "xpu"} and isinstance(idx, int):
                return idx
            if lowered in {"cpu", "mps", "disk"}:
                return lowered
        if isinstance(device_id, int):
            return device_id
        return None

    def _compute_max_memory_plan(
        self, device_map: Any
    ) -> Optional[Dict[int | str, int]]:
        gpu_fraction = float(self.config.get("local_max_gpu_mem_fraction") or 0.0)
        gpu_fraction = min(max(gpu_fraction, 0.0), 1.0)
        margin_mb = int(self.config.get("local_gpu_memory_margin_mb") or 0)
        margin_bytes = max(0, margin_mb) * (1024**2)
        gpu_limit_gb = float(self.config.get("local_gpu_mem_limit_gb") or 0.0)
        gpu_limit_bytes = int(gpu_limit_gb * (1024**3)) if gpu_limit_gb > 0 else None
        cpu_fraction = float(self.config.get("local_cpu_offload_fraction") or 0.0)
        cpu_fraction = min(max(cpu_fraction, 0.0), 1.0)
        cpu_limit_gb = float(self.config.get("local_cpu_offload_limit_gb") or 0.0)
        cpu_limit_bytes = int(cpu_limit_gb * (1024**3)) if cpu_limit_gb > 0 else None

        plan: Dict[int | str, int] = {}
        gpu_allowed = self._should_use_gpu(device_map)

        gpu_snapshots = gpu_memory_snapshot() if gpu_allowed else []
        system_snapshot = system_memory_snapshot()
        self._last_memory_snapshot = {"gpu": gpu_snapshots, "system": system_snapshot}

        if gpu_allowed and gpu_snapshots:
            for entry in gpu_snapshots:
                total = entry.get("total_bytes")
                if not isinstance(total, (int, float)) or total <= 0:
                    continue
                total_bytes = int(total)
                candidate = total_bytes
                if gpu_fraction > 0:
                    candidate = min(candidate, int(total_bytes * gpu_fraction))
                free = entry.get("free_bytes")
                if isinstance(free, (int, float)):
                    free_candidate = int(free)
                    if margin_bytes:
                        free_candidate -= margin_bytes
                    if free_candidate > 0:
                        candidate = min(candidate, free_candidate)
                if gpu_limit_bytes is not None:
                    candidate = min(candidate, gpu_limit_bytes)
                if candidate <= 0:
                    continue
                device_key = self._normalize_memory_plan_key(entry)
                if device_key is None:
                    continue
                plan[device_key] = candidate

        include_cpu = self.enable_ram_swap or not gpu_allowed or not plan
        if not include_cpu and self.enable_ram_swap and cpu_fraction > 0:
            include_cpu = True
        if include_cpu:
            available = (
                system_snapshot.get("available_bytes")
                if isinstance(system_snapshot, dict)
                else None
            )
            total = (
                system_snapshot.get("total_bytes")
                if isinstance(system_snapshot, dict)
                else None
            )
            base = None
            if isinstance(available, (int, float)) and available > 0:
                base = int(available)
            elif isinstance(total, (int, float)) and total > 0:
                base = int(total)
            if base:
                candidate = base
                if cpu_fraction > 0 and self.enable_ram_swap:
                    candidate = int(candidate * cpu_fraction)
                reserve_bytes = 2 * (1024**3)
                if isinstance(available, (int, float)) and available > reserve_bytes:
                    candidate = min(candidate, int(available) - reserve_bytes)
                if cpu_limit_bytes is not None:
                    candidate = min(candidate, cpu_limit_bytes)
                if candidate > 0:
                    plan["cpu"] = max(candidate, 512 * (1024**2))

        return plan or None

    def _configure_generation_features(self) -> None:
        model = self.local_model
        if model is None:
            return
        gen_config = getattr(model, "generation_config", None)
        if gen_config is None:
            return

        cache_impl = self._config_str("local_kv_cache_implementation")
        if cache_impl:
            try:
                gen_config.cache_implementation = cache_impl
            except Exception:
                logger.warning(
                    "Failed to set cache implementation to %s",
                    cache_impl,
                    exc_info=True,
                )

        quant_backend = self._config_str("local_kv_cache_quant_backend")
        if cache_impl == "quantized" and quant_backend:
            backend = quant_backend.strip()
            module_name = "quanto" if backend.lower() == "quanto" else "hqq"
            try:
                import importlib.util as _importlib_util

                if _importlib_util.find_spec(module_name) is None:
                    logger.warning(
                        "Requested KV cache quantization backend %s is not installed; skipping",
                        backend,
                    )
                else:
                    try:
                        gen_config.quantized_cache_backend = backend
                    except Exception:
                        logger.warning(
                            "Failed to set quantized cache backend to %s",
                            backend,
                            exc_info=True,
                        )
            except Exception:
                logger.warning(
                    "Unable to validate KV cache quantization backend %s",
                    backend,
                    exc_info=True,
                )

        cache_dtype = self._config_str("local_kv_cache_dtype")
        dtype = self._resolve_torch_dtype(cache_dtype)
        if dtype is not None:
            try:
                gen_config.kv_cache_dtype = dtype
            except Exception:
                logger.warning(
                    "Failed to apply KV cache dtype override %s",
                    cache_dtype,
                    exc_info=True,
                )

        keep_device = self._config_str("local_kv_cache_keep_on_device")
        if keep_device:
            try:
                setattr(gen_config, "cache_device", keep_device)
            except Exception:
                logger.debug(
                    "Failed to set cache_device on generation config", exc_info=True
                )

        cache_enabled = bool(self.use_kv_cache)
        try:
            gen_config.use_cache = cache_enabled
        except Exception:
            setattr(gen_config, "use_cache", cache_enabled)

        if hasattr(model.config, "use_cache"):
            try:
                model.config.use_cache = cache_enabled
            except Exception:
                logger.debug(
                    "Failed to propagate use_cache to model config", exc_info=True
                )

    def verify_local_model(self, model_name: Optional[str] = None) -> Dict[str, Any]:
        """Return a summary of the local model folder (presence, shards, bytes)."""

        target_name = model_name or (
            self.config.get("local_model") or self.config.get("transformer_model")
        )
        if not target_name:
            return {
                "model": model_name,
                "found": False,
                "path": None,
                "total_bytes": 0,
                "safetensor_shards": 0,
                "bin_files": 0,
                "metadata_files": [],
            }
        search_dirs = app_config.model_search_dirs(self.config.get("models_folder"))
        directory = _resolve_local_model_dir(search_dirs, target_name)
        summary: Dict[str, Any] = {
            "model": target_name,
            "found": bool(directory),
            "path": str(directory) if directory else None,
            "total_bytes": 0,
            "safetensor_shards": 0,
            "bin_files": 0,
            "metadata_files": [],
        }
        if not directory:
            return summary

        total = 0
        safetensors: List[Dict[str, Any]] = []
        bins = 0
        metadata: List[str] = []
        try:
            for path in directory.rglob("*"):
                if not path.is_file():
                    continue
                size = getattr(path.stat(), "st_size", 0)
                total += size
                suffix = path.suffix.lower()
                if suffix == ".safetensors":
                    safetensors.append({"file": path.name, "bytes": size})
                elif suffix == ".bin":
                    bins += 1
                elif suffix in {".json", ".model", ".config"}:
                    metadata.append(path.name)
        except Exception:
            pass

        summary.update(
            total_bytes=total,
            safetensor_shards=len(safetensors),
            safetensor_files=safetensors,
            bin_files=bins,
            metadata_files=metadata,
        )
        if not safetensors and not bins:
            summary["warning"] = "No model shard files (.safetensors/.bin) were found."
        return summary

    def _parse_timeout_config(self) -> float:
        """Resolve the HTTP request timeout setting in seconds."""

        def _candidate_values():
            env_keys = (
                "LLM_REQUEST_TIMEOUT",
                "LLM_TIMEOUT",
                "FLOAT_REQUEST_TIMEOUT",
            )
            for key in env_keys:
                value = os.getenv(key)
                if value and str(value).strip():
                    yield value
            if isinstance(self.config, dict):
                config_keys = (
                    "request_timeout",
                    "llm_request_timeout",
                    "timeout",
                    "api_timeout",
                )
                for key in config_keys:
                    value = self.config.get(key)
                    if value is not None and str(value).strip():
                        yield value

        for raw in _candidate_values():
            try:
                timeout = float(raw)
            except (TypeError, ValueError):
                continue
            if timeout > 0:
                return timeout
        return 30.0

    def _parse_timeout_backoff(self) -> Tuple[float, float]:
        """Parse exponential backoff bounds (start, max) for retries."""

        default = (self.timeout, max(self.timeout * 4, self.timeout))

        def _coerce(raw) -> Optional[Tuple[float, float]]:
            values: List[float] = []
            if isinstance(raw, (int, float)):
                values = [float(raw)]
            elif isinstance(raw, (list, tuple)):
                for item in raw:
                    try:
                        values.append(float(item))
                    except (TypeError, ValueError):
                        continue
            elif isinstance(raw, str):
                separators = [",", ";", "|", ":"]
                cleaned = raw
                for sep in separators[1:]:
                    cleaned = cleaned.replace(sep, separators[0])
                parts = [p.strip() for p in cleaned.split(separators[0]) if p.strip()]
                for part in parts:
                    try:
                        values.append(float(part))
                    except ValueError:
                        continue
            if not values:
                return None
            start = max(values[0], 0.0)
            if len(values) == 1:
                end = start
            else:
                end = max(values[1], start)
            if start <= 0 or end <= 0:
                return None
            return (start, end)

        candidates = []
        env_value = os.getenv("LLM_TIMEOUT_BACKOFF") or os.getenv(
            "FLOAT_TIMEOUT_BACKOFF"
        )
        if env_value:
            candidates.append(env_value)
        if isinstance(self.config, dict):
            for key in ("timeout_backoff", "timeout_backoff_range"):
                raw = self.config.get(key)
                if raw:
                    candidates.append(raw)

        for raw in candidates:
            parsed = _coerce(raw)
            if parsed:
                return parsed
        return default

    def _parse_stream_idle_timeout(self) -> float:
        """Resolve idle timeout (seconds) while consuming streamed responses."""

        candidates = (
            self.config.get("stream_idle_timeout"),
            os.getenv("LLM_STREAM_IDLE_TIMEOUT"),
            os.getenv("FLOAT_STREAM_IDLE_TIMEOUT"),
            600,
        )
        for raw in candidates:
            if raw is None:
                continue
            try:
                value = float(raw)
                if value > 0:
                    return value
            except (TypeError, ValueError):
                continue
        return 120.0

    def _compose_timeout(
        self, streaming: bool, attempt: int = 0
    ) -> Tuple[float, float]:
        """Compose connect/read timeout tuple suitable for requests.post."""

        try:
            connect = float(self.timeout)
        except (TypeError, ValueError):
            connect = 30.0
        connect = max(connect, 1.0)
        if streaming:
            try:
                read = float(self.stream_idle_timeout)
            except (TypeError, ValueError):
                read = 120.0
            read = max(read, connect)
            return (connect, read)

        backoff_start, backoff_end = self.timeout_backoff
        backoff_attempt = max(attempt, 0)
        progressive = min(
            max(connect, backoff_start * (2**backoff_attempt)),
            backoff_end,
        )
        return (connect, progressive)

    @staticmethod
    def _strip_inline_tool_objects(text: str) -> str:
        if not text:
            return text
        flt = InlineToolStreamFilter()
        return flt.filter(text)

    @staticmethod
    def _inline_tool_placeholder(index: int) -> str:
        return f"[[tool_call:{index}]]"

    @staticmethod
    def _iter_json_objects(raw: str) -> Iterator[Tuple[int, int, str]]:
        """Yield balanced JSON object substrings from raw text.

        Some models emit concatenated tool calls such as
        `{"tool":"recall",...}{"tool":"recall",...}` without newlines. The
        older `find("{")` / `rfind("}")` strategy would capture both objects
        and fail JSON parsing. This scanner extracts each balanced object.
        """

        start_idx: Optional[int] = None
        depth = 0
        in_string = False
        escape = False
        for idx, ch in enumerate(raw):
            if in_string:
                if escape:
                    escape = False
                    continue
                if ch == "\\":
                    escape = True
                    continue
                if ch == '"':
                    in_string = False
                continue

            if ch == '"':
                in_string = True
                continue
            if ch == "{":
                if depth == 0:
                    start_idx = idx
                depth += 1
                continue
            if ch == "}":
                if depth == 0:
                    continue
                depth -= 1
                if depth == 0 and start_idx is not None:
                    yield start_idx, idx + 1, raw[start_idx : idx + 1]
                    start_idx = None
                continue

    @staticmethod
    def _strip_harmony_envelope(text: str) -> str:
        if not text:
            return text
        cleaned = re.sub(r"<\|[^|>]+?\|>", " ", text)
        cleaned = re.sub(
            r"\b(?:channel|commentary|analysis|constrain|message|json)\b",
            " ",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"\bto=[^\s]+", " ", cleaned)
        return " ".join(cleaned.split())

    @staticmethod
    def _extract_inline_tool_calls(
        text: str,
    ) -> Tuple[str, List[Dict[str, Any]]]:
        if not text:
            return text, []

        candidates: List[Dict[str, Any]] = []
        replacements: List[Tuple[int, int, str]] = []
        for start, end, obj in LLMService._iter_json_objects(text):
            candidate_obj = obj.strip()
            if not candidate_obj or '"tool"' not in candidate_obj:
                continue
            try:
                payload = json.loads(candidate_obj)
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            tool_name = payload.get("tool")
            params = (
                payload.get("params")
                or payload.get("arguments")
                or payload.get("args")
                or {}
            )
            if not tool_name or not isinstance(params, dict):
                continue
            idx = len(candidates)
            candidates.append(
                {
                    "name": str(tool_name),
                    "args": params,
                    "raw": candidate_obj,
                }
            )
            replacements.append((start, end, LLMService._inline_tool_placeholder(idx)))

        if not replacements:
            return text, []

        replacements.sort(key=lambda item: item[0])
        out: List[str] = []
        last_idx = 0
        for start, end, repl in replacements:
            if start < last_idx:
                continue
            out.append(text[last_idx:start])
            out.append(repl)
            last_idx = end
        out.append(text[last_idx:])
        return "".join(out), candidates

    @staticmethod
    def _extract_harmony_tool_calls(
        text: str,
    ) -> Tuple[str, List[Dict[str, Any]]]:
        if not text or "to=" not in text:
            return text, []

        match = re.search(r"\bto=([A-Za-z_][\w-]*)", text)
        if not match:
            return text, []
        tool_name = match.group(1).strip()
        if not tool_name:
            return text, []

        message_tag = "<|message|>"
        tag_idx = text.find(message_tag)
        search_start = tag_idx + len(message_tag) if tag_idx != -1 else match.end()

        candidates: List[Dict[str, Any]] = []
        replacements: List[Tuple[int, int, str]] = []
        for start, end, obj in LLMService._iter_json_objects(text[search_start:]):
            candidate_obj = obj.strip()
            if not candidate_obj:
                continue
            try:
                payload = json.loads(candidate_obj)
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            idx = len(candidates)
            candidates.append(
                {
                    "name": tool_name,
                    "args": payload,
                    "raw": candidate_obj,
                    "kind": "harmony",
                }
            )
            replacements.append(
                (
                    search_start + start,
                    search_start + end,
                    LLMService._inline_tool_placeholder(idx),
                )
            )

        if not replacements:
            return text, []

        replacements.sort(key=lambda item: item[0])
        out: List[str] = []
        last_idx = 0
        for start, end, repl in replacements:
            if start < last_idx:
                continue
            out.append(text[last_idx:start])
            out.append(repl)
            last_idx = end
        out.append(text[last_idx:])
        cleaned = LLMService._strip_harmony_envelope("".join(out))
        return cleaned, candidates

    @staticmethod
    def _parse_inline_tool_calls(text: str) -> List[Dict[str, Any]]:
        _cleaned, candidates = LLMService._extract_inline_tool_calls(text)
        return candidates

    def _parse_inline_tool_call(self, text: str) -> Optional[Dict[str, Any]]:
        calls = self._parse_inline_tool_calls(text)
        return calls[0] if calls else None

    @staticmethod
    def _utf8_mojibake_score(text: str) -> int:
        if not text:
            return 0
        score = text.count("\ufffd") * 3
        for marker in (
            "Ã",
            "Â",
            "â€",
            "â€™",
            "â€œ",
            "â€",
            "â€“",
            "â€”",
            "ðŸ",
            "ð",
            "ï¿½",
        ):
            score += text.count(marker)
        return score

    @classmethod
    def _repair_mojibake_utf8(cls, text: str) -> str:
        """Best-effort fix for UTF-8 text that was decoded as latin-1/cp1252."""
        if not text:
            return text
        baseline_score = cls._utf8_mojibake_score(text)
        if baseline_score <= 0:
            return text
        try:
            repaired = text.encode("latin-1").decode("utf-8")
        except Exception:
            return text
        if cls._utf8_mojibake_score(repaired) < baseline_score:
            return repaired
        return text

    def _decode_stream_line(
        self,
        raw_line: Any,
        *,
        response_encoding: Optional[str],
        force_utf8: bool,
    ) -> str:
        if isinstance(raw_line, bytes):
            if force_utf8:
                try:
                    return raw_line.decode("utf-8")
                except Exception:
                    return raw_line.decode("utf-8", errors="replace")
            charset = response_encoding or "utf-8"
            try:
                return raw_line.decode(charset)
            except Exception:
                return raw_line.decode("utf-8", errors="replace")
        line = str(raw_line)
        if force_utf8:
            line = self._repair_mojibake_utf8(line)
        return line

    def _consume_streaming_response(
        self,
        url: str,
        headers: Dict[str, Any],
        payload: Dict[str, Any],
        session_id: str,
        stream_consumer: Optional[Callable[[Dict[str, Any]], None]],
        stream_message_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        """Consume a streamed chat completion and return accumulated output."""

        headers = dict(headers)
        headers.setdefault("Accept", "text/event-stream")

        try:
            response = http_session.post(
                url,
                headers=headers,
                json=payload,
                timeout=self._compose_timeout(streaming=True),
                stream=True,
            )
            # Some OpenAI-compatible servers (notably LM Studio) omit charset
            # headers; Requests may default to latin-1, producing mojibake.
            if getattr(self, "mode", None) == "server":
                try:
                    response.encoding = "utf-8"
                except Exception:
                    pass
            response.raise_for_status()
        except requests.exceptions.HTTPError as exc:
            resp = getattr(exc, "response", None)
            status = getattr(resp, "status_code", None)
            try:
                if resp is not None and getattr(self, "mode", None) == "server":
                    try:
                        resp.encoding = "utf-8"
                    except Exception:
                        pass
                body_preview = (resp.text or "")[:2000] if resp is not None else ""
            except Exception:
                body_preview = ""
            if getattr(self, "mode", None) == "server":
                try:
                    forwarded_response_format = None
                    try:
                        rf_payload = payload.get("response_format")
                        if isinstance(rf_payload, dict):
                            forwarded_response_format = rf_payload.get("type")
                    except Exception:
                        forwarded_response_format = None
                    log_llm_server_event(
                        "stream_http_error",
                        {
                            "session_id": session_id,
                            "message_id": stream_message_id,
                            "endpoint": url,
                            "model": payload.get("model"),
                            "response_format_forwarded": forwarded_response_format,
                            "status_code": status,
                            "body_preview": body_preview,
                        },
                    )
                except Exception:
                    pass
            if body_preview:
                logger.warning(
                    "LLMService: streaming HTTP error (status=%s) body=%s",
                    status,
                    body_preview,
                )
            else:
                logger.warning(
                    "LLMService: streaming HTTP error (status=%s): %s",
                    status,
                    str(exc),
                )
            raise
        except requests.exceptions.RequestException as exc:
            if getattr(self, "mode", None) == "server":
                try:
                    forwarded_response_format = None
                    try:
                        rf_payload = payload.get("response_format")
                        if isinstance(rf_payload, dict):
                            forwarded_response_format = rf_payload.get("type")
                    except Exception:
                        forwarded_response_format = None
                    log_llm_server_event(
                        "stream_request_failed",
                        {
                            "session_id": session_id,
                            "message_id": stream_message_id,
                            "endpoint": url,
                            "model": payload.get("model"),
                            "response_format_forwarded": forwarded_response_format,
                            "error": str(exc),
                        },
                    )
                except Exception:
                    pass
            logger.warning(
                "LLMService: streaming request failed: %s", str(exc), exc_info=True
            )
            raise

        text_parts: List[str] = []
        analysis_trace: List[Dict[str, Any]] = []
        tool_calls_accum: List[Dict[str, Any]] = []
        finish_reason: Optional[str] = None
        usage: Optional[Dict[str, Any]] = None
        last_message: Optional[Dict[str, Any]] = None
        response_model: Optional[str] = None
        saw_thought = False
        last_chunk_at = time.time()
        stream_error: Optional[str] = None
        idle_elapsed: Optional[float] = None
        output_source: Optional[str] = None
        current_source: Optional[str] = None

        def _append_text(text: str) -> None:
            nonlocal output_source
            if not text:
                return
            if current_source:
                if output_source and output_source != current_source:
                    return
                if output_source is None:
                    output_source = current_source
            text_parts.append(text)
            _emit_content(text)

        def _emit_thought(text: str) -> None:
            nonlocal saw_thought
            idx = len(analysis_trace)
            analysis_trace.append({"index": idx, "text": text})
            saw_thought = True
            if stream_consumer and text:
                event = {
                    "type": "thought",
                    "content": text,
                    "offset": idx,
                    "session_id": session_id,
                    "message_id": stream_message_id,
                }
                try:
                    stream_consumer(event)
                except Exception:
                    pass

        def _emit_content(text: str) -> None:
            if not stream_consumer or not text:
                return
            event = {
                "type": "content",
                "content": text,
                "session_id": session_id,
                "message_id": stream_message_id,
            }
            try:
                stream_consumer(event)
            except Exception:
                pass

        def _emit_tool_delta(
            name: Optional[str],
            arguments: Optional[str],
            index: Optional[int],
            fragment: Optional[str],
        ) -> None:
            if not stream_consumer:
                return
            event = {
                "type": "tool_call_delta",
                "name": name,
                "arguments": arguments,
                "fragment": fragment,
                "call_index": index,
                "session_id": session_id,
                "message_id": stream_message_id,
            }
            try:
                stream_consumer(event)
            except Exception:
                pass

        def _emit_status(status: str, detail: Optional[Dict[str, Any]] = None) -> None:
            if not stream_consumer:
                return
            event: Dict[str, Any] = {
                "type": "stream_status",
                "status": status,
                "session_id": session_id,
                "message_id": stream_message_id,
            }
            if detail is not None:
                event["detail"] = detail
            try:
                stream_consumer(event)
            except Exception:
                pass

        def _is_reasoning_type(value: Optional[str]) -> bool:
            if not value:
                return False
            lowered = str(value).lower()
            reasoning_tokens = (
                "analysis",
                "reasoning",
                "thought",
                "chain_of_thought",
                "critique",
            )
            return any(token in lowered for token in reasoning_tokens)

        def _process_stream_payload(
            payload: Any,
            default_thought: bool = False,
            _seen: Optional[Set[Any]] = None,
        ) -> None:
            if payload is None:
                return
            if isinstance(payload, (bytes, bytearray)):
                try:
                    payload = payload.decode()
                except Exception:
                    return
            if isinstance(payload, str):
                text_value = payload.strip("\x00")
                if not text_value:
                    return
                if _seen is None:
                    _seen = set()
                text_marker = ("text", bool(default_thought), text_value)
                if text_marker in _seen:
                    return
                _seen.add(text_marker)
                handler = _emit_thought if default_thought else _append_text
                handler(text_value)
                return
            if isinstance(payload, (int, float)):
                return
            if _seen is None:
                _seen = set()
            if isinstance(payload, dict):
                obj_marker = ("obj", id(payload))
                if obj_marker in _seen:
                    return
                _seen.add(obj_marker)
                payload_type = payload.get("type")
                payload_channel = payload.get("channel")
                thought_default = (
                    True
                    if _is_reasoning_type(payload_type)
                    or _is_reasoning_type(payload_channel)
                    else default_thought
                )
                for key in ("text", "output_text", "value"):
                    value = payload.get(key)
                    if isinstance(value, str):
                        _process_stream_payload(value, thought_default, _seen)
                content = payload.get("content")
                if isinstance(content, (list, dict, str)):
                    _process_stream_payload(content, thought_default, _seen)
                delta = payload.get("delta")
                if isinstance(delta, (list, dict, str)):
                    _process_stream_payload(delta, thought_default, _seen)
                message = payload.get("message")
                if isinstance(message, (list, dict, str)):
                    _process_stream_payload(message, thought_default, _seen)
                reasoning = payload.get("reasoning")
                if isinstance(reasoning, (list, dict, str)):
                    _process_stream_payload(reasoning, True, _seen)
                data_field = payload.get("data")
                if isinstance(data_field, (list, dict, str)):
                    _process_stream_payload(data_field, thought_default, _seen)
                return
            if isinstance(payload, list):
                for item in payload:
                    _process_stream_payload(item, default_thought, _seen)
                return

        saw_response_stream = False

        force_utf8_decode = getattr(self, "mode", None) == "server"
        decode_unicode = not force_utf8_decode
        try:
            for raw_line in response.iter_lines(decode_unicode=decode_unicode):
                last_chunk_at = time.time()
                if not raw_line:
                    continue
                line = self._decode_stream_line(
                    raw_line,
                    response_encoding=getattr(response, "encoding", None),
                    force_utf8=force_utf8_decode,
                )
                line = line.strip()
                if not line:
                    continue
                if line.startswith("data:"):
                    line = line[5:].strip()
                if line in {"[DONE]", "[done]"}:
                    break
                try:
                    chunk = json.loads(line)
                except Exception:
                    continue
                if response_model is None and isinstance(chunk, dict):
                    model_value = chunk.get("model")
                    if isinstance(model_value, str) and model_value.strip():
                        response_model = model_value.strip()
                handled_response_payload = False
                chunk_type = str(chunk.get("type") or chunk.get("object") or "")
                if chunk_type:
                    lowered = chunk_type.lower()
                    is_reasoning = _is_reasoning_type(lowered)
                    is_response = lowered.startswith("response.")
                    if is_response:
                        saw_response_stream = True
                    is_delta = lowered.endswith(".delta")
                    if is_response or is_reasoning:
                        # Avoid double-counting Responses API "done" payloads after deltas.
                        if is_reasoning or is_delta or not text_parts:
                            current_source = "responses"
                            try:
                                _process_stream_payload(
                                    chunk,
                                    default_thought=is_reasoning,
                                )
                            finally:
                                current_source = None
                        handled_response_payload = True
                if saw_response_stream:
                    choices_iter = []
                else:
                    raw_choices = chunk.get("choices")
                    if isinstance(raw_choices, dict):
                        choices_iter = [raw_choices]
                    elif isinstance(raw_choices, list):
                        choices_iter = raw_choices
                    else:
                        choices_iter = []
                for choice in choices_iter:
                    if not isinstance(choice, dict):
                        continue
                    finish_reason = choice.get("finish_reason") or finish_reason
                    message = choice.get("message")
                    if isinstance(message, dict):
                        last_message = message
                    delta = choice.get("delta") or {}
                    if isinstance(delta, dict):
                        content_items = delta.get("content")
                        if isinstance(content_items, list):
                            for item in content_items:
                                if isinstance(item, dict):
                                    text = str(
                                        item.get("text")
                                        or item.get("content")
                                        or item.get("value")
                                        or ""
                                    )
                                    channel = item.get("channel") or item.get("type")
                                else:
                                    text = str(item)
                                    channel = None
                                if not text:
                                    continue
                                if _is_reasoning_type(channel):
                                    _emit_thought(text)
                                else:
                                    current_source = "chat"
                                    try:
                                        _append_text(text)
                                    finally:
                                        current_source = None
                        elif isinstance(content_items, dict):
                            text = str(
                                content_items.get("text")
                                or content_items.get("content")
                                or ""
                            )
                            if text:
                                channel = content_items.get(
                                    "channel"
                                ) or content_items.get("type")
                                if _is_reasoning_type(channel):
                                    _emit_thought(text)
                                else:
                                    current_source = "chat"
                                    try:
                                        _append_text(text)
                                    finally:
                                        current_source = None
                        elif isinstance(content_items, str):
                            current_source = "chat"
                            try:
                                _append_text(content_items)
                            finally:
                                current_source = None
                        reasoning = delta.get("reasoning")
                        if isinstance(reasoning, dict):
                            r_content = reasoning.get("content")
                            if isinstance(r_content, list):
                                for item in r_content:
                                    if isinstance(item, dict):
                                        text = str(item.get("text", "") or "")
                                    else:
                                        text = str(item)
                                    if text:
                                        _emit_thought(text)
                            elif isinstance(reasoning.get("text"), str):
                                _emit_thought(str(reasoning.get("text")))
                        elif isinstance(reasoning, list):
                            for item in reasoning:
                                if isinstance(item, dict):
                                    text = str(item.get("text", "") or "")
                                else:
                                    text = str(item)
                                if text:
                                    _emit_thought(text)
                        elif isinstance(reasoning, str):
                            _emit_thought(reasoning)
                        tool_deltas = delta.get("tool_calls") or []
                        if isinstance(tool_deltas, list):
                            for call in tool_deltas:
                                if not isinstance(call, dict):
                                    continue
                                idx = call.get("index")
                                target: Dict[str, Any]
                                if isinstance(idx, int) and idx >= 0:
                                    while len(tool_calls_accum) <= idx:
                                        tool_calls_accum.append(
                                            {"function": {"arguments": ""}}
                                        )
                                    target = tool_calls_accum[idx]
                                else:
                                    target = {"function": {"arguments": ""}}
                                    tool_calls_accum.append(target)
                                if call.get("id"):
                                    target["id"] = call["id"]
                                if call.get("type"):
                                    target["type"] = call["type"]
                                func = call.get("function")
                                if isinstance(func, dict):
                                    entry_func = target.setdefault("function", {})
                                    name = func.get("name")
                                    if name:
                                        entry_func["name"] = name
                                    args = func.get("arguments")
                                    if args:
                                        current = entry_func.get("arguments", "")
                                        entry_func["arguments"] = f"{current}{args}"
                                        _emit_tool_delta(
                                            entry_func.get("name"),
                                            entry_func.get("arguments"),
                                            idx if isinstance(idx, int) else None,
                                            str(args),
                                        )
                    top_reasoning = chunk.get("reasoning")
                    if not handled_response_payload:
                        if isinstance(top_reasoning, list):
                            for item in top_reasoning:
                                if isinstance(item, dict):
                                    text = str(item.get("text", "") or "")
                                else:
                                    text = str(item)
                                if text:
                                    _emit_thought(text)
                        elif isinstance(top_reasoning, dict):
                            text = str(
                                top_reasoning.get("text")
                                or top_reasoning.get("content")
                                or ""
                            )
                            if text:
                                _emit_thought(text)
                        elif isinstance(top_reasoning, str):
                            _emit_thought(top_reasoning)
                    usage = chunk.get("usage") or usage
                    if not handled_response_payload:
                        chunk_output = chunk.get("output") or chunk.get("data")
                        if isinstance(chunk_output, str):
                            current_source = "chat"
                            try:
                                _append_text(chunk_output)
                            finally:
                                current_source = None
                        elif isinstance(chunk_output, dict):
                            text = str(
                                chunk_output.get("text")
                                or chunk_output.get("content")
                                or chunk_output.get("output_text")
                                or ""
                            )
                            if text:
                                current_source = "chat"
                                try:
                                    _append_text(text)
                                finally:
                                    current_source = None
                        elif isinstance(chunk_output, list):
                            for item in chunk_output:
                                if isinstance(item, dict):
                                    text = str(item.get("text", "") or "")
                                else:
                                    text = str(item)
                                if text:
                                    current_source = "chat"
                                    try:
                                        _append_text(text)
                                    finally:
                                        current_source = None
        except requests.exceptions.ReadTimeout as exc:
            stream_error = "stream_idle_timeout"
            idle_elapsed = time.time() - last_chunk_at
            _emit_status(
                "timeout",
                {
                    "reason": stream_error,
                    "idle_seconds": idle_elapsed,
                    "detail": str(exc),
                },
            )

        if last_message:
            msg_content = last_message.get("content")
            if isinstance(msg_content, list):
                for item in msg_content:
                    if not isinstance(item, dict):
                        continue
                    text = str(item.get("text", "") or "")
                    channel = item.get("channel") or item.get("type")
                    if not text:
                        continue
                    if _is_reasoning_type(channel):
                        if not saw_thought:
                            _emit_thought(text)
                    elif not text_parts:
                        text_parts.append(text)
            elif isinstance(msg_content, str) and not text_parts:
                text_parts.append(msg_content)
            if not saw_thought:
                lm_reasoning = last_message.get("reasoning")
                if isinstance(lm_reasoning, list):
                    for item in lm_reasoning:
                        if isinstance(item, dict):
                            text = str(item.get("text", "") or "")
                        else:
                            text = str(item)
                        if text:
                            _emit_thought(text)
                elif isinstance(lm_reasoning, dict):
                    text = str(
                        lm_reasoning.get("text") or lm_reasoning.get("content") or ""
                    )
                    if text:
                        _emit_thought(text)
                elif isinstance(lm_reasoning, str):
                    _emit_thought(lm_reasoning)
            lm_tool_calls = last_message.get("tool_calls")
            if isinstance(lm_tool_calls, list) and not tool_calls_accum:
                tool_calls_accum = lm_tool_calls

        text = "".join(text_parts).strip()
        thought = "".join(part.get("text", "") for part in analysis_trace).strip()

        tools_used: List[Dict[str, Any]] = []
        for call in tool_calls_accum or []:
            if not isinstance(call, dict):
                continue
            func = call.get("function")
            if isinstance(func, dict):
                name = func.get("name")
                args = func.get("arguments")
                parsed_args: Any = args
                if isinstance(args, str):
                    try:
                        parsed_args = json.loads(args)
                    except Exception:
                        parsed_args = args
                if name:
                    tools_used.append({"name": name, "args": parsed_args})

        inline_tool_payloads: List[str] = []
        if not tools_used and text:
            cleaned_text, inline_candidates = self._extract_inline_tool_calls(text)
            if inline_candidates:
                for candidate in inline_candidates:
                    tools_used.append(
                        {"name": candidate["name"], "args": candidate["args"]}
                    )
                    raw_payload = candidate.get("raw")
                    if isinstance(raw_payload, str) and raw_payload:
                        inline_tool_payloads.append(raw_payload)
                text = cleaned_text.strip()
            else:
                harmony_text, harmony_candidates = self._extract_harmony_tool_calls(
                    text
                )
                if harmony_candidates:
                    for candidate in harmony_candidates:
                        tools_used.append(
                            {"name": candidate["name"], "args": candidate["args"]}
                        )
                    text = harmony_text.strip()

        if (not text or not text.strip()) and tools_used:
            text = " ".join(
                self._inline_tool_placeholder(idx) for idx in range(len(tools_used))
            )

        if not text and not thought and not tools_used:
            return None

        metadata: Dict[str, Any] = {}
        if finish_reason:
            metadata["finish_reason"] = finish_reason
        if usage:
            metadata["usage"] = usage
        if analysis_trace:
            metadata["thought_trace_length"] = len(analysis_trace)
        if inline_tool_payloads:
            metadata["inline_tool_payload"] = inline_tool_payloads[0]
            if len(inline_tool_payloads) > 1:
                metadata["inline_tool_payloads"] = inline_tool_payloads
        if stream_error:
            metadata["error"] = stream_error
            if idle_elapsed is not None:
                metadata["idle_seconds"] = idle_elapsed
        requested_model = payload.get("model")
        if isinstance(requested_model, str):
            requested_model = requested_model.strip() or None
        if isinstance(response_model, str):
            response_model = response_model.strip() or None
        if requested_model and response_model:
            metadata["model_requested"] = requested_model
            metadata["model_received"] = response_model
            if response_model != requested_model:
                metadata["model_mismatch"] = True
                metadata.setdefault(
                    "warning",
                    f"Model mismatch: requested '{requested_model}', received '{response_model}'.",
                )
                if getattr(self, "mode", None) == "server":
                    try:
                        log_llm_server_event(
                            "model_mismatch",
                            {
                                "session_id": session_id,
                                "message_id": stream_message_id,
                                "endpoint": url,
                                "requested": requested_model,
                                "received": response_model,
                            },
                        )
                    except Exception:
                        pass

        if getattr(self, "mode", None) == "server":
            try:
                log_llm_server_event(
                    "stream_summary",
                    {
                        "session_id": session_id,
                        "message_id": stream_message_id,
                        "endpoint": url,
                        "requested_model": requested_model,
                        "received_model": response_model,
                        "finish_reason": finish_reason,
                        "stream_error": stream_error,
                        "idle_seconds": idle_elapsed,
                        "text_chars": len(text or ""),
                        "thought_chars": len(thought or ""),
                        "tools_used_count": len(tools_used),
                        "thought_trace_length": len(analysis_trace),
                    },
                )
            except Exception:
                pass

        return {
            "text": text,
            "thought": thought,
            "tools_used": tools_used,
            "metadata": metadata,
            "thought_trace": analysis_trace,
        }

    # ------------------------------------------------------------------
    # Context helpers
    # ------------------------------------------------------------------
    def add_image_to_context(
        self, path: str, score: float, session_id: str = "default"
    ) -> None:
        """Attach an image reference to the session's context."""
        ctx = self.get_context(session_id)
        ctx.add_image(path, score)

    def generate(
        self,
        prompt: str | Sequence[Message] | Sequence[Dict[str, Any]],
        context: Optional[ModelContext] = None,
        session_id: str = "default",
        response_format: Optional[str] = None,
        stream_consumer: Optional[Callable[[Dict[str, Any]], None]] = None,
        stream_message_id: Optional[str] = None,
        **kwargs,
    ):
        """Generate text using the selected mode and context."""
        if context:
            self.set_context(context, session_id)

        ctx = self.get_context(session_id)

        if self.mode == "api":
            return self._generate_via_api(
                prompt,
                ctx,
                session_id=session_id,
                response_format=response_format,
                stream_consumer=stream_consumer,
                stream_message_id=stream_message_id,
                **kwargs,
            )
        elif self.mode == "server":
            # Reuse API flow but target server_url and relax API key requirement
            return self._generate_via_api(
                prompt,
                ctx,
                session_id=session_id,
                response_format=response_format,
                stream_consumer=stream_consumer,
                stream_message_id=stream_message_id,
                **kwargs,
            )
        elif self.mode == "local":
            self._load_local_model(override_model_name=kwargs.get("model"))
            return self._generate_via_local(
                prompt,
                ctx,
                session_id=session_id,
                response_format=response_format,
                **kwargs,
            )
        elif self.mode == "dynamic":
            return self._generate_via_dynamic(
                prompt, ctx, response_format=response_format, **kwargs
            )
        else:
            raise ValueError(f"Invalid mode: {self.mode}")

    def _prepare_payload(
        self, prompt: str, ctx: ModelContext, **kwargs
    ) -> Dict[str, Any]:
        """Prepare the payload for API requests with context."""
        context_dict = ctx.to_dict()
        return {
            "prompt": prompt,
            "system_prompt": context_dict["system_prompt"],
            "messages": context_dict["messages"],
            "tools": context_dict["tools"],
            "metadata": context_dict["metadata"],
            **kwargs,
        }

    def _normalize_server_url(self, url: str) -> str:
        """Normalize a configured server URL to an OpenAI-compatible endpoint."""
        stripped = (url or "").strip()
        if not stripped:
            return stripped
        try:
            parsed = urlparse(stripped)
        except Exception:
            return stripped
        path = parsed.path or ""
        trimmed_path = path.rstrip("/")
        lower = trimmed_path.lower()
        suffixes = (
            "/v1/chat/completions",
            "/v1/completions",
            "/v1/responses",
            "/chat/completions",
            "/completions",
            "/responses",
        )
        if any(lower.endswith(suffix) for suffix in suffixes):
            normalized_path = trimmed_path or "/"
        elif lower.endswith("/v1"):
            normalized_path = f"{trimmed_path}/chat/completions"
        elif not trimmed_path:
            normalized_path = "/v1/chat/completions"
        else:
            normalized_path = trimmed_path or "/"
        normalized = parsed._replace(path=normalized_path)
        return urlunparse(normalized)

    def _generate_via_api(
        self,
        prompt: str | Sequence[Message] | Sequence[Dict[str, Any]],
        ctx: ModelContext,
        session_id: str = "default",
        response_format: Optional[str] = None,
        stream_consumer: Optional[Callable[[Dict[str, Any]], None]] = None,
        stream_message_id: Optional[str] = None,
        **kwargs,
    ):
        """
        Generate text via an OpenAI-style Chat Completion API.
        """
        attachments_param = kwargs.pop("attachments", None) or []
        if isinstance(attachments_param, dict):
            attachments_param = [attachments_param]
        attachment_queue: List[Dict[str, Any]] = [
            att for att in attachments_param if isinstance(att, dict)
        ]
        reasoning = kwargs.pop("reasoning", None)
        vision_workflow = _normalize_vision_workflow(
            kwargs.pop("vision_workflow", None)
        )
        vision_fallback_details: List[Dict[str, Any]] = []
        vision_fallback_seen: Set[str] = set()
        vision_captioner: Optional[VisionCaptioner] = None
        configured_vision_model = str(self.config.get("vision_model") or "").strip()
        if not configured_vision_model or "clip" in configured_vision_model.lower():
            configured_vision_model = (
                str(os.getenv("VISION_CAPTION_MODEL") or "").strip()
                or "google/paligemma2-3b-pt-224"
            )

        def _coerce_iso_timestamp(metadata: Any) -> Optional[str]:
            if not isinstance(metadata, dict):
                return None
            iso_value = metadata.get("iso_timestamp") or metadata.get("iso_time")
            if isinstance(iso_value, str) and iso_value.strip():
                return iso_value.strip()
            ts_value = metadata.get("timestamp") or metadata.get("time")
            try:
                ts_float = float(ts_value)
            except (TypeError, ValueError):
                ts_float = None
            if ts_float is None:
                return None
            try:
                return (
                    datetime.fromtimestamp(ts_float, tz=timezone.utc)
                    .replace(microsecond=0)
                    .isoformat()
                    .replace("+00:00", "Z")
                )
            except Exception:
                return None

        def _inject_timestamp_content(
            message: Dict[str, Any], iso_text: Optional[str]
        ) -> Dict[str, Any]:
            if not iso_text:
                return message
            stamp = f"[time: {iso_text}]"
            content = message.get("content")
            if isinstance(content, list):
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    part_type = part.get("type")
                    if part_type in {"text", "input_text", "output_text"}:
                        existing = part.get("text", "") or ""
                        if isinstance(existing, str) and existing.strip().startswith(
                            stamp
                        ):
                            return message
                        part["text"] = f"{stamp}\n{existing}".strip()
                        return message
                content.insert(0, {"type": "text", "text": stamp})
                return message
            if isinstance(content, str):
                if content.strip().startswith(stamp):
                    return message
                message["content"] = f"{stamp}\n{content}".strip()
                return message
            message["content"] = f"{stamp}\n{str(content)}".strip()
            return message

        def _extract_hash_from_url(url_value: Any) -> Optional[str]:
            if not url_value:
                return None
            try:
                parsed = urlparse(str(url_value))
                path = parsed.path or ""
            except Exception:
                path = str(url_value)
            segments = [segment for segment in path.split("/") if segment]
            for idx, segment in enumerate(segments):
                if segment == "attachments" and idx + 1 < len(segments):
                    return segments[idx + 1]
            return None

        def _ensure_list_content(raw_content: Any) -> List[Dict[str, Any]]:
            if isinstance(raw_content, list):
                normalized: List[Dict[str, Any]] = []
                for part in raw_content:
                    if isinstance(part, dict):
                        normalized.append(dict(part))
                    elif isinstance(part, str):
                        normalized.append({"type": "text", "text": part})
                    else:
                        normalized.append({"type": "text", "text": str(part)})
                return normalized
            if raw_content is None:
                return []
            if isinstance(raw_content, str):
                return [{"type": "text", "text": raw_content}]
            return [{"type": "text", "text": str(raw_content)}]

        def _inline_image_part(
            att: Dict[str, Any], mime_hint: Optional[str]
        ) -> Optional[Dict[str, Any]]:
            content_hash = att.get("content_hash") or _extract_hash_from_url(
                att.get("url")
            )
            if not content_hash:
                return None
            try:
                raw = load_blob(content_hash)
            except Exception:
                logger.debug(
                    "LLMService: failed to load attachment %s",
                    content_hash,
                    exc_info=True,
                )
                return None
            mime = (
                mime_hint
                or att.get("type")
                or mimetypes.guess_type(att.get("name") or "")[0]
                or "image/png"
            )
            try:
                encoded = base64.b64encode(raw).decode("ascii")
            except Exception:
                return None
            return {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{encoded}"},
            }

        def _get_vision_captioner() -> VisionCaptioner:
            nonlocal vision_captioner
            if vision_captioner is None:
                vision_captioner = VisionCaptioner(model=configured_vision_model)
            return vision_captioner

        def _local_vision_fallback_part(att: Dict[str, Any]) -> Dict[str, Any]:
            label = att.get("name") or "image"
            ref = att.get("url") or att.get("remoteUrl") or ""
            content_hash = att.get("content_hash") or _extract_hash_from_url(ref)
            raw: Optional[bytes] = None
            if content_hash:
                try:
                    raw = load_blob(content_hash)
                except Exception:
                    raw = None
            caption = ""
            placeholder = False
            caption_model = configured_vision_model or "vision-captioner"
            if raw:
                try:
                    result = _get_vision_captioner().run(raw)
                    caption_model = getattr(vision_captioner, "model", caption_model)
                    if isinstance(result, str):
                        caption = result
                        placeholder = is_placeholder_caption(caption)
                    elif isinstance(result, dict):
                        caption = str(result.get("image_caption") or "")
                        placeholder = bool(
                            result.get("placeholder", is_placeholder_caption(caption))
                        )
                except Exception:
                    caption = ""
                    placeholder = False
            if not caption:
                ref_key = (content_hash or "")[:8] or hashlib.sha256(
                    label.encode("utf-8")
                ).hexdigest()[:8]
                caption = placeholder_caption(ref_key)
                placeholder = True
            detail_key = str(content_hash or label).strip().lower()
            existing_detail = next(
                (
                    item
                    for item in vision_fallback_details
                    if str(item.get("content_hash") or item.get("name") or "")
                    .strip()
                    .lower()
                    == detail_key
                ),
                None,
            )
            entry_index = (
                int(existing_detail.get("index"))
                if isinstance(existing_detail, dict)
                and isinstance(existing_detail.get("index"), int)
                else len(vision_fallback_details) + 1
            )
            if detail_key and detail_key not in vision_fallback_seen:
                vision_fallback_seen.add(detail_key)
                vision_fallback_details.append(
                    {
                        "index": entry_index,
                        "name": label,
                        "content_hash": content_hash,
                        "caption": caption,
                        "placeholder": placeholder,
                        "caption_model": caption_model,
                    }
                )
            prefix = {
                "ocr": "Local vision fallback summary. Read visible text cautiously:",
                "compare": "Local vision fallback summary for comparison:",
                "caption": "Local vision fallback caption:",
            }.get(vision_workflow, "Local vision fallback summary:")
            text = f"{prefix} Image {entry_index} ({label}): {caption}"
            if ref:
                text = f"{text} [{ref}]"
            return {"type": "text", "text": text}

        def _attachment_to_part(
            att: Dict[str, Any], allow_inline_image: bool
        ) -> Optional[Dict[str, Any]]:
            att_type = (att.get("type") or "").lower()
            if not att_type:
                guessed = mimetypes.guess_type(att.get("name") or "")[0]
                if guessed:
                    att_type = guessed.lower()
            if att_type.startswith("image/"):
                if allow_inline_image and supports_native_images:
                    inline = _inline_image_part(att, att_type)
                    if inline:
                        return inline
                return _local_vision_fallback_part(att)
            label = att.get("name") or "attachment"
            ref = att.get("url") or att.get("remoteUrl") or ""
            text = f"[Attachment: {label}]".strip()
            if ref:
                text = f"{text} {ref}"
            return {"type": "text", "text": text}

        def _build_attachment_parts(
            att_list: Sequence[Dict[str, Any]] | None,
            seen_keys: Set[str],
            inline_slots: Optional[int],
        ) -> List[Dict[str, Any]]:
            if not att_list:
                return []
            slots = inline_slots
            parts: List[Dict[str, Any]] = []
            for att in att_list:
                if not isinstance(att, dict):
                    continue
                key = att.get("content_hash") or att.get("url") or att.get("name")
                if key:
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                allow_inline = slots is None or slots > 0
                part = _attachment_to_part(att, allow_inline_image=allow_inline)
                if not part:
                    continue
                if part.get("type") == "image_url" and slots is not None and slots > 0:
                    slots -= 1
                parts.append(part)
            return parts

        def _merge_attachments(
            message_dict: Dict[str, Any],
            attachment_list: Sequence[Dict[str, Any]] | None,
            seen_keys: Optional[Set[str]] = None,
        ) -> Tuple[Dict[str, Any], Set[str]]:
            seen = seen_keys or set()
            existing_content = _ensure_list_content(message_dict.get("content"))
            inline_count = sum(
                1
                for item in existing_content
                if isinstance(item, dict) and item.get("type") == "image_url"
            )
            remaining_slots = max(0, MAX_INLINE_ATTACHMENTS - inline_count)
            parts = _build_attachment_parts(attachment_list, seen, remaining_slots)
            if parts:
                existing_content.extend(parts)
                message_dict["content"] = existing_content
            elif isinstance(message_dict.get("content"), list):
                message_dict["content"] = existing_content
            return message_dict, seen

        # Choose endpoint and authentication depending on mode.
        # Allow per-request overrides so callers can route managed local providers
        # through the shared OpenAI-compatible transport without mutating global config.
        server_url_override = kwargs.pop("server_url", None)
        api_key_override = kwargs.pop("api_key", None)
        use_server = self.mode == "server"
        raw_server_url = (
            server_url_override
            if use_server and isinstance(server_url_override, str)
            else (self.config.get("server_url") or "")
            if use_server
            else ""
        )
        api_url = (
            self.config.get("api_url") or app_config.DEFAULT_OPENAI_API_URL or ""
        ).strip()
        if use_server:
            server_url = raw_server_url.strip()
            if not server_url:
                return {
                    "text": "",
                    "thought": "",
                    "tools_used": [],
                    "metadata": {
                        "error": "Missing server URL",
                        "category": "config_missing",
                        "endpoint": server_url,
                        "hint": "Set a Server URL in Settings when using server mode.",
                    },
                }
            configured_url = self._normalize_server_url(server_url)
        else:
            configured_url = api_url
        api_key = (
            api_key_override
            if isinstance(api_key_override, str)
            else self.config.get("api_key")
        )
        if not configured_url:
            return {
                "text": "",
                "thought": "",
                "tools_used": [],
                "metadata": {
                    "error": "Missing API URL",
                    "category": "config_missing",
                    "endpoint": configured_url,
                },
            }
        configured_url = configured_url.rstrip("/")
        if self.mode == "api" and not api_key:
            # Strictly require key for first‑party API mode
            return {
                "text": f"You said: {prompt}",
                "thought": "",
                "tools_used": [],
                "metadata": {
                    "warning": "No API key configured",
                    "category": "api_key_missing",
                    "endpoint": configured_url,
                },
            }
        override_model = kwargs.pop("model", None)
        if isinstance(override_model, str):
            override_model = override_model.strip() or None
        config_model = self.config.get("api_model")
        if isinstance(config_model, str):
            config_model = config_model.strip() or None
        model = override_model or config_model
        if use_server:
            model = resolve_model_alias(model)
        if isinstance(model, str):
            model = model.strip()
        if not model:
            return {
                "text": f"You said: {prompt}",
                "thought": "",
                "tools_used": [],
                "metadata": {
                    "error": "No model configured",
                    "category": "model_missing",
                    "endpoint": configured_url,
                },
            }
        supports_native_images = _model_supports_native_images(model)
        # Prepare HTTP request
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        # Optional organization routing for enterprise keys
        try:
            org = (
                os.getenv("OPENAI_ORG")
                or os.getenv("OPENAI_ORGANIZATION")
                or os.getenv("OPENAI_ORG_ID")
            )
            if org:
                headers["OpenAI-Organization"] = org
        except Exception:
            pass
        # Build messages for chat API using Harmony envelope
        messages: List[Dict[str, Any]] = []
        if ctx.system_prompt:
            messages.append(
                Message.from_role_and_content(Role.SYSTEM, ctx.system_prompt).to_dict()
            )
        prompt_metadata: Optional[Dict[str, Any]] = None
        for idx, msg in enumerate(ctx.messages):
            try:
                role = msg.get("role", "user")
                content = msg.get("content", "")
            except Exception:
                role, content = "user", str(msg)
            try:
                msg_dict = Message.from_role_and_content(role, content).to_dict()
            except Exception:
                # Fallback to a simple role/content dict if Harmony is unavailable
                msg_dict = {"role": role, "content": content}
            metadata = msg.get("metadata") if isinstance(msg, dict) else None
            meta_attachments = (
                metadata.get("attachments") if isinstance(metadata, dict) else None
            )
            msg_dict, _ = _merge_attachments(msg_dict, meta_attachments)
            iso_text = _coerce_iso_timestamp(metadata)
            msg_dict = _inject_timestamp_content(msg_dict, iso_text)
            messages.append(msg_dict)
            if (
                prompt_metadata is None
                and isinstance(metadata, dict)
                and isinstance(content, str)
                and isinstance(prompt, str)
                and content == prompt
                and idx == len(ctx.messages) - 1
            ):
                prompt_metadata = metadata
        if isinstance(prompt, Sequence) and not isinstance(prompt, (str, bytes)):
            for msg in prompt:
                try:
                    if hasattr(msg, "to_dict"):
                        messages.append(msg.to_dict())
                    elif isinstance(msg, dict):
                        messages.append(msg)
                    else:
                        # best effort stringify
                        messages.append({"role": "user", "content": str(msg)})
                except Exception:
                    continue
            if attachment_queue:
                prompt_entry = Message.from_role_and_content(Role.USER, "").to_dict()
                prompt_entry, _ = _merge_attachments(prompt_entry, attachment_queue)
                attachment_queue = []
                messages.append(prompt_entry)
        else:
            prompt_entry = Message.from_role_and_content(Role.USER, prompt).to_dict()
            if attachment_queue:
                prompt_entry, _ = _merge_attachments(prompt_entry, attachment_queue)
                attachment_queue = []
            prompt_entry = _inject_timestamp_content(
                prompt_entry, _coerce_iso_timestamp(prompt_metadata)
            )
            messages.append(prompt_entry)

        # If images are present in context and the selected model likely supports them,
        # attach top few to a trailing user message. Keep conservative to avoid errors.
        try:
            images = (
                list(ctx.metadata.get("images", []))
                if isinstance(ctx.metadata, dict)
                else []
            )
        except Exception:
            images = []
        requested_response_format: Optional[str] = None
        if isinstance(response_format, str):
            requested_response_format = response_format.strip().lower() or None

        send_response_format = response_format
        if isinstance(send_response_format, str):
            fmt_lower = send_response_format.strip().lower()
            # Only forward formats understood by OpenAI-style servers.
            # Note: "harmony" is an internal formatting mode for GPT-OSS and
            # should not be sent upstream as `response_format`.
            allowed_formats = {"text", "json_object", "json_schema"}
            if not fmt_lower or fmt_lower not in allowed_formats:
                send_response_format = None
            else:
                send_response_format = fmt_lower

        def _truncate_log_text(value: Any, limit: int = 300) -> Optional[str]:
            if value is None:
                return None
            try:
                text_value = str(value).strip()
            except Exception:
                return None
            if not text_value:
                return None
            if len(text_value) > limit:
                return text_value[: max(0, limit - 3)].rstrip() + "..."
            return text_value

        if images and supports_native_images:
            # Prefer highest scored first if scores are provided
            try:
                images.sort(key=lambda x: float(x.get("score", 0)), reverse=True)
            except Exception:
                pass
            parts: List[Dict[str, Any]] = []
            # Start with a small instruction
            parts.append(
                {"type": "text", "text": "Consider these images in your answer."}
            )
            for entry in images[:3]:
                p = Path(str(entry.get("path", "")))
                if not p.exists() or not p.is_file():
                    continue
                try:
                    raw = p.read_bytes()
                    mime, _ = mimetypes.guess_type(p.name)
                    if not mime:
                        # default safe type
                        mime = "image/png"
                    b64 = base64.b64encode(raw).decode("ascii")
                    url = f"data:{mime};base64,{b64}"
                    parts.append({"type": "image_url", "image_url": {"url": url}})
                except Exception:
                    # As a fallback, include the file name
                    parts.append({"type": "text", "text": f"Image: {p.name}"})
            if len(parts) > 1:
                messages.append({"role": "user", "content": parts})
        normalized_messages: List[Dict[str, Any]] = []
        for msg in messages:
            if isinstance(msg, dict):
                msg_copy = dict(msg)
                msg_copy["role"] = _normalize_chat_role(msg_copy.get("role"))
                normalized_messages.append(msg_copy)
            else:
                normalized_messages.append({"role": "user", "content": str(msg)})
        messages = normalized_messages
        tool_definitions = _convert_tools_for_openai(ctx.tools)

        structured_content_present = any(
            isinstance(m.get("content"), list) for m in messages
        )
        # Decide endpoint: Chat Completions vs Responses API
        suffix_chat = "/chat/completions"
        suffix_resp = "/responses"
        try:
            lower_model = (model or "").lower() if isinstance(model, str) else ""
        except Exception:
            lower_model = ""
        # Prefer Responses API for GPT‑5; honor explicit /responses in URL
        use_responses = configured_url.endswith(suffix_resp) or lower_model.startswith(
            "gpt-5"
        )
        # Derive final URL if switching from chat to responses
        url = configured_url
        if use_responses and not configured_url.endswith(suffix_resp):
            base = configured_url
            if configured_url.endswith(suffix_chat):
                base = configured_url[: -len(suffix_chat)]
            url = base.rstrip("/") + suffix_resp

        # Build payload according to endpoint
        if url.endswith(suffix_resp):
            # Responses API: prefer structured content when available
            def _collapse(msgs: List[Dict[str, Any]]) -> str:
                lines: List[str] = []
                for m in msgs:
                    role = _normalize_chat_role(m.get("role"))
                    content = m.get("content")
                    if isinstance(content, list):
                        text = "".join(
                            (p.get("text", "") if isinstance(p, dict) else str(p))
                            for p in content
                        )
                    else:
                        text = str(content or "")
                    if text:
                        lines.append(f"{role}: {text}")
                return "\n".join(lines)

            def _map_part_for_responses(part: Any, role: str) -> Dict[str, Any]:
                role_normalized = role.lower()
                base_text_type = (
                    "output_text"
                    if role_normalized in {"assistant", "tool"}
                    else "input_text"
                )

                if not isinstance(part, dict):
                    return {"type": base_text_type, "text": str(part)}
                ptype = part.get("type")
                if ptype in {"text", "input_text", "output_text"}:
                    return {"type": base_text_type, "text": part.get("text", "")}
                if ptype == "tool_call":
                    item: Dict[str, Any] = {
                        "type": "tool_call",
                        "id": part.get("id"),
                        "name": part.get("name"),
                    }
                    arguments = part.get("arguments", part.get("args"))
                    if isinstance(arguments, (dict, list)):
                        try:
                            item["arguments"] = json.dumps(arguments)
                        except Exception:
                            item["arguments"] = json.dumps(arguments, default=str)
                    elif arguments is not None:
                        item["arguments"] = str(arguments)
                    return item
                if ptype == "image_url":
                    image_url_payload = part.get("image_url")
                    image_url: Optional[str] = None
                    if isinstance(image_url_payload, str):
                        image_url = image_url_payload
                    elif isinstance(image_url_payload, dict):
                        candidate = (
                            image_url_payload.get("url")
                            or image_url_payload.get("uri")
                            or image_url_payload.get("href")
                        )
                        if candidate:
                            image_url = str(candidate)
                    if image_url:
                        return {"type": "input_image", "image_url": image_url}
                    image_base64 = part.get("image_base64")
                    if image_base64:
                        item: Dict[str, Any] = {
                            "type": "input_image",
                            "image_base64": image_base64,
                        }
                        mime = part.get("mime_type") or (
                            image_url_payload.get("mime_type")
                            if isinstance(image_url_payload, dict)
                            else None
                        )
                        if mime:
                            item["mime_type"] = mime
                        return item
                    return {"type": base_text_type, "text": part.get("text") or ""}
                return {"type": base_text_type, "text": part.get("text") or ""}

            def _convert_messages_for_responses(
                msgs: List[Dict[str, Any]]
            ) -> List[Dict[str, Any]]:
                converted: List[Dict[str, Any]] = []
                for m in msgs:
                    role = _normalize_chat_role(m.get("role"))
                    content = m.get("content")
                    parts: List[Dict[str, Any]]
                    if isinstance(content, list):
                        parts = [
                            _map_part_for_responses(part, role)
                            for part in content
                            if part is not None
                        ]
                    else:
                        part_type = (
                            "output_text"
                            if role in {"assistant", "tool"}
                            else "input_text"
                        )
                        parts = [{"type": part_type, "text": str(content or "")}]
                    entry: Dict[str, Any] = {"role": role, "content": parts}
                    for key in ("id", "tool_call_id", "name"):
                        if key in m and m[key] is not None:
                            entry[key] = m[key]
                    metadata_payload = m.get("metadata")
                    if isinstance(metadata_payload, dict) and metadata_payload:
                        entry["metadata"] = metadata_payload
                    converted.append(entry)
                return converted

            if structured_content_present:
                payload = {
                    "model": model,
                    "input": _convert_messages_for_responses(messages),
                }
            else:
                transcript = _collapse(messages)
                payload = {
                    "model": model,
                    "input": transcript,
                }
            if reasoning is not None:
                payload["reasoning"] = reasoning
            if send_response_format:
                payload["response_format"] = {"type": send_response_format}
            if tool_definitions:
                payload["tools"] = tool_definitions
            # Map a few common generation params if present
            for k in (
                "temperature",
                "max_output_tokens",
                "top_p",
                "max_completion_tokens",
                "metadata",
                "stop",
                "frequency_penalty",
                "presence_penalty",
                "seed",
                "tool_choice",
            ):
                if k in kwargs and kwargs[k] is not None:
                    payload[k] = kwargs[k]
        else:
            payload = {
                "model": model,
                "messages": messages,
                **kwargs,
            }
            if send_response_format:
                payload["response_format"] = {"type": send_response_format}
            if tool_definitions and "tools" not in payload:
                payload["tools"] = tool_definitions

        allow_responses_stream = self.config.get("enable_responses_stream", True)
        allow_responses_stream = (
            True if allow_responses_stream is None else bool(allow_responses_stream)
        )
        native_tools_present = _contains_native_openai_tool(tool_definitions)
        streaming_enabled = bool(stream_consumer) and (
            allow_responses_stream or not url.endswith(suffix_resp)
        )
        if native_tools_present and url.endswith(suffix_resp):
            # Keep native Responses tools on the non-streaming path until output-item
            # streaming is normalized into Float's proposal lifecycle.
            streaming_enabled = False
        if use_server:
            try:
                log_llm_server_event(
                    "request_dispatch",
                    {
                        "session_id": session_id,
                        "message_id": stream_message_id,
                        "endpoint": url,
                        "model": model,
                        "api_shape": (
                            "responses"
                            if url.endswith(suffix_resp)
                            else "chat_completions"
                        ),
                        "response_format_requested": requested_response_format,
                        "response_format_forwarded": send_response_format,
                        "has_tools": bool(tool_definitions),
                        "stream_requested": streaming_enabled,
                    },
                )
            except Exception:
                pass
        if streaming_enabled:
            payload_stream = dict(payload)
            payload_stream["stream"] = True
            if use_server:
                try:
                    log_llm_server_event(
                        "stream_attempt",
                        {
                            "session_id": session_id,
                            "message_id": stream_message_id,
                            "endpoint": url,
                            "model": model,
                            "response_format_forwarded": send_response_format,
                            "idle_timeout_seconds": self.stream_idle_timeout,
                        },
                    )
                except Exception:
                    pass
            try:
                streamed = self._consume_streaming_response(
                    url,
                    headers,
                    payload_stream,
                    session_id,
                    stream_consumer,
                    stream_message_id,
                )
                if streamed is not None:
                    if use_server:
                        try:
                            streamed_meta = (
                                streamed.get("metadata")
                                if isinstance(streamed, dict)
                                else {}
                            )
                            if not isinstance(streamed_meta, dict):
                                streamed_meta = {}
                            log_llm_server_event(
                                "stream_success",
                                {
                                    "session_id": session_id,
                                    "message_id": stream_message_id,
                                    "endpoint": url,
                                    "model": model,
                                    "finish_reason": streamed_meta.get("finish_reason"),
                                    "stream_error": streamed_meta.get("error"),
                                    "text_chars": len(str(streamed.get("text") or "")),
                                    "thought_chars": len(
                                        str(streamed.get("thought") or "")
                                    ),
                                    "tools_used_count": len(
                                        streamed.get("tools_used") or []
                                    ),
                                },
                            )
                        except Exception:
                            pass
                    return streamed
            except requests.exceptions.RequestException as exc:
                if use_server:
                    try:
                        log_llm_server_event(
                            "stream_fallback",
                            {
                                "session_id": session_id,
                                "message_id": stream_message_id,
                                "endpoint": url,
                                "model": model,
                                "reason": "request_exception",
                                "error": _truncate_log_text(exc),
                            },
                        )
                    except Exception:
                        pass
                logger.debug(
                    "LLMService: streaming request failed; falling back to non-streaming",
                    exc_info=True,
                )
            except Exception as exc:
                if use_server:
                    try:
                        log_llm_server_event(
                            "stream_fallback",
                            {
                                "session_id": session_id,
                                "message_id": stream_message_id,
                                "endpoint": url,
                                "model": model,
                                "reason": "unexpected_exception",
                                "error": _truncate_log_text(exc),
                            },
                        )
                    except Exception:
                        pass
                logger.exception(
                    "LLMService: error while consuming streaming response; falling back"
                )

        logger.debug("LLMService: sending request to %s with payload: %s", url, payload)
        # Best-effort retry with backoff for transient errors
        retries = 0
        delay = 0.5
        try:
            retries = int(os.getenv("LLM_API_RETRIES", "1"))
            delay = float(os.getenv("LLM_API_RETRY_DELAY", "0.5"))
        except Exception:
            retries = 1
            delay = 0.5
        attempt = 0
        last_err = None
        while True:
            try:
                response = http_session.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=self._compose_timeout(streaming=False, attempt=attempt),
                )
                if getattr(self, "mode", None) == "server":
                    try:
                        response.encoding = "utf-8"
                    except Exception:
                        pass
                logger.debug(
                    "LLMService: received status %s, body: %s",
                    response.status_code,
                    response.text,
                )
                response.raise_for_status()
                if use_server:
                    try:
                        log_llm_server_event(
                            "request_success",
                            {
                                "session_id": session_id,
                                "message_id": stream_message_id,
                                "endpoint": url,
                                "model": model,
                                "status_code": response.status_code,
                                "attempt": attempt + 1,
                                "stream_requested": streaming_enabled,
                                "response_format_requested": requested_response_format,
                                "response_format_forwarded": send_response_format,
                            },
                        )
                    except Exception:
                        pass
                break
            except requests.exceptions.RequestException as e:
                last_err = e
                attempt += 1
                if attempt > max(1, retries):
                    logger.exception("LLMService: API request failed after retries")
                    # Classify error for client UX
                    category = "http_error"
                    status_code = None
                    response_body = None
                    response_json = None
                    request_id = None
                    try:
                        import requests as _rq

                        if isinstance(e, _rq.exceptions.Timeout):
                            category = "timeout"
                        elif isinstance(e, _rq.exceptions.ConnectionError):
                            category = "connection_error"
                    except Exception:
                        pass
                    try:
                        resp = getattr(e, "response", None)
                        if resp is not None:
                            request_id = resp.headers.get(
                                "OpenAI-Request-Id"
                            ) or resp.headers.get("x-request-id")
                            if hasattr(resp, "status_code"):
                                status_code = resp.status_code
                                if status_code == 401:
                                    category = "unauthorized"
                                elif status_code == 404:
                                    category = "endpoint_not_found"
                                elif status_code == 429:
                                    category = "rate_limited"
                                elif status_code >= 500:
                                    category = "server_error"
                            try:
                                response_json = resp.json()
                            except Exception:
                                try:
                                    body_text = resp.text
                                except Exception:
                                    body_text = ""
                                response_body = body_text[:2000] if body_text else None
                    except Exception:
                        pass
                    meta = {
                        "error": str(e),
                        "attempts": attempt,
                        "status_code": status_code,
                        "category": category,
                        "endpoint": url,
                        "hint": (
                            "Provider error or timeout. Verify API key and endpoint in /api/settings, "
                            "or switch to local mode."
                        ),
                    }
                    fallback_text = f"You said: {prompt}"
                    if request_id:
                        meta["request_id"] = request_id
                    provider_message = None
                    if response_json:
                        meta["provider_error"] = response_json
                        try:
                            err_obj = (
                                response_json.get("error")
                                if isinstance(response_json, dict)
                                else None
                            )
                            if isinstance(err_obj, dict):
                                provider_message = err_obj.get(
                                    "message"
                                ) or err_obj.get("code")
                        except Exception:
                            pass
                    elif response_body:
                        meta["provider_error_text"] = response_body
                        provider_message = response_body
                    if provider_message:
                        try:
                            provider_message = str(provider_message).strip()
                        except Exception:
                            provider_message = (
                                str(provider_message)
                                if provider_message is not None
                                else None
                            )
                    if provider_message:
                        truncated = provider_message[:200]
                        if len(provider_message) > 200:
                            truncated = truncated.rstrip() + "..."
                        meta["provider_message"] = truncated
                        fallback_text = f"Provider error: {truncated}"
                    if use_server:
                        try:
                            log_llm_server_event(
                                "request_failed",
                                {
                                    "session_id": session_id,
                                    "message_id": stream_message_id,
                                    "endpoint": url,
                                    "model": model,
                                    "status_code": status_code,
                                    "category": category,
                                    "request_id": request_id,
                                    "stream_requested": streaming_enabled,
                                    "response_format_requested": requested_response_format,
                                    "response_format_forwarded": send_response_format,
                                    "provider_message": meta.get("provider_message"),
                                    "provider_error": meta.get("provider_error"),
                                    "provider_error_text": meta.get(
                                        "provider_error_text"
                                    ),
                                },
                            )
                        except Exception:
                            pass
                    return {
                        "text": fallback_text,
                        "thought": "",
                        "tools_used": [],
                        "metadata": meta,
                    }
                try:
                    time.sleep(delay * (2 ** (attempt - 1)))
                except Exception:
                    pass
        # Parse JSON
        data = response.json()
        response_model: Optional[str] = None
        if isinstance(data, dict):
            model_value = data.get("model")
            if isinstance(model_value, str) and model_value.strip():
                response_model = model_value.strip()
            if response_model is None:
                response_obj = data.get("response")
                if isinstance(response_obj, dict):
                    model_value = response_obj.get("model")
                    if isinstance(model_value, str) and model_value.strip():
                        response_model = model_value.strip()
        # Extract assistant reply and any reasoning/thought content
        thought = ""
        message: Dict[str, Any] = {}
        if url.endswith(suffix_resp):
            # OpenAI Responses API style payload
            text = data.get("output_text", "")
            if not text:
                output = data.get("output")
                if isinstance(output, list):
                    collected: list[str] = []
                    for item in output:
                        if not isinstance(item, dict):
                            continue
                        content = item.get("content")
                        if isinstance(content, list):
                            for part in content:
                                if (
                                    isinstance(part, dict)
                                    and part.get("type") == "output_text"
                                ):
                                    collected.append(str(part.get("text", "")))
                                elif (
                                    isinstance(part, dict)
                                    and part.get("type") == "text"
                                ):
                                    collected.append(str(part.get("text", "")))
                        elif isinstance(content, str):
                            collected.append(content)
                    text = "".join(collected)
            if not text:
                response_obj = data.get("response")
                if isinstance(response_obj, dict):
                    try:
                        text = response_obj.get("output_text", "") or "".join(
                            part.get("text", "")
                            for part in response_obj.get("content", [])
                            if isinstance(part, dict)
                        )
                    except Exception:
                        pass
        else:
            # Chat Completions style payload
            choice = (
                data.get("choices", [])[0]
                if isinstance(data.get("choices"), list)
                else {}
            )
            message = choice.get("message", {}) if isinstance(choice, dict) else {}
            content = message.get("content", "") if isinstance(message, dict) else ""
            text = ""
            if isinstance(content, list):
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    if part.get("channel") == "analysis":
                        thought += part.get("text", "")
                    else:
                        text += part.get("text", "")
            else:
                text = content

        # Reasoning best-effort
        if not thought:
            reasoning = data.get("reasoning")
            if isinstance(reasoning, dict):
                rc = reasoning.get("content")
                if isinstance(rc, str):
                    thought = rc
                elif isinstance(rc, list):
                    thought = "".join(
                        part.get("text", "") for part in rc if isinstance(part, dict)
                    )
            elif isinstance(reasoning, list):
                thought = "".join(
                    part.get("text", "") if isinstance(part, dict) else str(part)
                    for part in reasoning
                )

        tools_used: List[Dict[str, Any]] = []

        if url.endswith(suffix_resp):
            tools_used.extend(_extract_native_responses_tool_calls(data))

        # Safely parse tool calls if present in chat-completions style responses
        tool_calls = message.get("tool_calls", []) if isinstance(message, dict) else []
        if isinstance(tool_calls, list):
            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                func = call.get("function") or {}
                name = func.get("name")
                args = func.get("arguments")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        pass
                if name:
                    tools_used.append({"name": name, "args": args})

        # Legacy function_call field used by some providers
        function_call = (
            message.get("function_call") if isinstance(message, dict) else None
        )
        if isinstance(function_call, dict):
            name = function_call.get("name")
            args = function_call.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    pass
            if name:
                tools_used.append({"name": name, "args": args})

        inline_tool_payloads: List[str] = []
        if not tools_used and text:
            cleaned_text, inline_candidates = self._extract_inline_tool_calls(text)
            if inline_candidates:
                for candidate in inline_candidates:
                    tools_used.append(
                        {"name": candidate["name"], "args": candidate["args"]}
                    )
                    raw_payload = candidate.get("raw")
                    if isinstance(raw_payload, str) and raw_payload:
                        inline_tool_payloads.append(raw_payload)
                text = cleaned_text.strip()
            else:
                harmony_text, harmony_candidates = self._extract_harmony_tool_calls(
                    text
                )
                if harmony_candidates:
                    for candidate in harmony_candidates:
                        tools_used.append(
                            {"name": candidate["name"], "args": candidate["args"]}
                        )
                    text = harmony_text.strip()

        if (not text or not text.strip()) and tools_used:
            text = " ".join(
                self._inline_tool_placeholder(idx) for idx in range(len(tools_used))
            )

        trace = [{"index": 0, "text": thought}] if thought else []
        result = {
            "text": text,
            "thought": thought,
            "tools_used": tools_used,
            "metadata": {},
            "thought_trace": trace,
        }
        requested_model = model if isinstance(model, str) else None
        if requested_model:
            requested_model = requested_model.strip() or None
        if response_model:
            result["metadata"]["model_received"] = response_model
        if requested_model:
            result["metadata"]["model_requested"] = requested_model
        if requested_model and response_model and response_model != requested_model:
            result["metadata"]["model_mismatch"] = True
            result["metadata"].setdefault(
                "warning",
                f"Model mismatch: requested '{requested_model}', received '{response_model}'.",
            )
            if getattr(self, "mode", None) == "server":
                try:
                    log_llm_server_event(
                        "model_mismatch",
                        {
                            "session_id": session_id,
                            "endpoint": url,
                            "requested": requested_model,
                            "received": response_model,
                        },
                    )
                except Exception:
                    pass
        if inline_tool_payloads:
            result["metadata"]["inline_tool_payload"] = inline_tool_payloads[0]
            if len(inline_tool_payloads) > 1:
                result["metadata"]["inline_tool_payloads"] = inline_tool_payloads
        native_image_parts = 0
        try:
            for msg in messages:
                content = msg.get("content") if isinstance(msg, dict) else None
                if not isinstance(content, list):
                    continue
                native_image_parts += sum(
                    1
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "image_url"
                )
        except Exception:
            native_image_parts = 0
        if native_image_parts or vision_fallback_details or vision_workflow != "auto":
            vision_meta: Dict[str, Any] = {
                "workflow": vision_workflow,
                "native_image_input": native_image_parts > 0,
                "fallback_used": bool(vision_fallback_details),
                "fallback_images": len(vision_fallback_details),
            }
            if vision_fallback_details:
                vision_meta["fallback_attachments"] = [
                    {
                        "name": item.get("name"),
                        "content_hash": item.get("content_hash"),
                        "caption": item.get("caption"),
                        "placeholder": item.get("placeholder"),
                        "caption_model": item.get("caption_model"),
                    }
                    for item in vision_fallback_details
                ]
            result["metadata"]["vision"] = vision_meta
        return result

    def _load_local_model(self, override_model_name: str | None = None) -> None:
        """Lazily load a local transformers model and tokenizer."""
        if self.local_model is not None and self.local_tokenizer is not None:
            self._local_load_state = "ready"
            self._local_backend_active = "transformers"
            return
        AutoModelForCausalLM, AutoTokenizer = _get_transformers_components()
        if AutoTokenizer is None or AutoModelForCausalLM is None:
            self._local_load_state = "error"
            self._local_load_error = "transformers library is required for local mode"
            self._local_load_finished_at = time.time()
            raise ImportError("transformers library is required for local mode")

        self._local_load_state = "loading"
        self._local_load_error = None
        self._local_load_started_at = time.time()
        self._local_load_finished_at = None
        try:
            self._configure_torch_threads()
            if self.enable_ram_swap:
                os.environ.setdefault(
                    "PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:true"
                )

            model_name = override_model_name or (
                self.config.get("local_model")
                or self.config.get("transformer_model")
                or "gpt2"
            )

            search_dirs = app_config.model_search_dirs(self.config.get("models_folder"))
            resolved_dir = _resolve_local_model_dir(search_dirs, model_name)
            load_target = str(resolved_dir) if resolved_dir is not None else model_name

            tokenizer_kwargs: Dict[str, Any] = {"local_files_only": True}
            if self.max_context_length:
                tokenizer_kwargs["model_max_length"] = self.max_context_length
            if self.allow_remote_code:
                tokenizer_kwargs["trust_remote_code"] = True

            tokenizer_errors: List[Exception] = []

            def _try_load_tokenizer(use_fast: Optional[bool]) -> Optional[Any]:
                kwargs = dict(tokenizer_kwargs)
                if use_fast is not None:
                    kwargs["use_fast"] = use_fast
                try:
                    return AutoTokenizer.from_pretrained(load_target, **kwargs)
                except (
                    Exception
                ) as exc:  # pragma: no cover - converts raise various errors
                    tokenizer_errors.append(exc)
                    return None

            self.local_tokenizer = _try_load_tokenizer(None)
            if self.local_tokenizer is None:
                self.local_tokenizer = _try_load_tokenizer(False)
            if self.local_tokenizer is None:
                logger.exception("Failed to load tokenizer for %s", load_target)
                searched = ", ".join(str(p) for p in search_dirs)
                last_exc = (
                    tokenizer_errors[-1]
                    if tokenizer_errors
                    else RuntimeError("unknown tokenizer error")
                )
                raise RuntimeError(
                    f"Failed to load tokenizer for '{model_name}' from '{load_target}'. "
                    f"Ensure the model files exist locally (searched: {searched}). Original error: {last_exc}"
                ) from last_exc

            quant_method: Optional[str] = None
            available_variants: List[str] = []
            if resolved_dir is not None:
                config_path = resolved_dir / "config.json"
                if config_path.exists():
                    try:
                        with config_path.open("r", encoding="utf-8") as handle:
                            config_data = json.load(handle)
                        quant_cfg = config_data.get("quantization_config")
                        if isinstance(quant_cfg, dict):
                            method = quant_cfg.get("quant_method")
                            if isinstance(method, str):
                                quant_method = method.lower().strip() or None
                    except Exception:
                        logger.debug(
                            "Failed to read quantization config from %s",
                            config_path,
                            exc_info=True,
                        )
                try:
                    available_variants = sorted(
                        entry.name for entry in resolved_dir.iterdir() if entry.is_dir()
                    )
                except Exception:
                    available_variants = []

            self._local_quant_method = quant_method

            model_kwargs: Dict[str, Any] = {"local_files_only": True}
            if self.allow_remote_code:
                model_kwargs["trust_remote_code"] = True
            if self.enable_ram_swap:
                model_kwargs.setdefault(
                    "offload_folder", str(self.config.get("models_folder"))
                )
            self._apply_device_preferences(model_kwargs)
            self._apply_attention_preferences(model_kwargs)
            self._apply_memory_strategy(model_kwargs)
            self._apply_dtype_preferences(model_kwargs)

            if quant_method == "mxfp4" and not (torch and torch.cuda.is_available()):
                hint_parts = []
                original_variant = next(
                    (name for name in available_variants if name.lower() == "original"),
                    None,
                )
                if original_variant:
                    hint_parts.append(
                        f"Re-download the '{model_name}' weights using the '{original_variant}' variant "
                        "or point Float to that subfolder for CPU inference."
                    )
                hint_parts.append(
                    "Install a CUDA-enabled PyTorch build and ensure the NVIDIA drivers are available "
                    "if you plan to keep using the MXFP4 checkpoint."
                )
                hint = " ".join(hint_parts)
                logger.error(
                    "MXFP4 checkpoint requires CUDA but no CUDA device is available for %s",
                    model_name,
                )
                raise RuntimeError(
                    (
                        f"Model '{model_name}' uses MXFP4 quantized weights that require a CUDA-capable GPU, "
                        "but no CUDA device is available in this environment. "
                        f"{hint} "
                        "Use local/lmstudio or local/ollama for managed quantized C++ runtime fallback."
                    )
                )

            try:
                self.local_model = AutoModelForCausalLM.from_pretrained(
                    load_target, **model_kwargs
                )
            except Exception as exc:
                logger.exception("Failed to load local model %s", load_target)
                try:
                    summary = self.verify_local_model(model_name)
                    logger.error(
                        "Model integrity summary: found=%s total_bytes=%s safetensors=%s bin_files=%s path=%s",
                        summary.get("found"),
                        summary.get("total_bytes"),
                        summary.get("safetensor_shards"),
                        summary.get("bin_files"),
                        summary.get("path"),
                    )
                except Exception:
                    pass
                searched = ", ".join(str(p) for p in search_dirs)
                raise RuntimeError(
                    f"Failed to load model '{model_name}' from '{load_target}'. "
                    f"Verify the checkpoint is complete (searched: {searched}). Original error: {exc}"
                ) from exc
            logger.info("Using cached model", extra={"model": load_target})
            self._configure_generation_features()
            try:
                self.local_model.eval()
            except Exception:  # pragma: no cover - some models may not implement eval
                pass
            self._local_backend_active = "transformers"
            self._local_load_state = "ready"
            self._local_load_finished_at = time.time()
        except Exception as exc:
            message = str(exc)
            wrapped_exc = None
            lowered = message.lower()
            if any(
                key in lowered
                for key in (
                    "mxfp4",
                    "bf16",
                    "bfloat16",
                    "cuda",
                    "cublas",
                    "cutlass",
                )
            ):
                message = (
                    f"{message} "
                    "If CUDA or bf16/mxfp4 issues persist on this GPU, switch to a "
                    "managed quantized C++ runtime via local/lmstudio or local/ollama."
                ).strip()
                wrapped_exc = RuntimeError(message)
            self._local_load_state = "error"
            self._local_load_error = message
            self._local_load_finished_at = time.time()
            if wrapped_exc is not None:
                raise wrapped_exc from exc
            raise

    def local_runtime_status(self) -> Dict[str, Any]:
        """Return local inference status and memory usage snapshot."""
        mode_value = self.mode
        if (
            self.local_model is not None
            or self.local_tokenizer is not None
            or self._local_load_state in {"loading", "ready", "error"}
        ):
            mode_value = "local"
        model_name = self._local_model_name()
        active_backend = self._local_backend_active or "transformers"
        loaded = bool(self.local_model and self.local_tokenizer)
        status: Dict[str, Any] = {
            "mode": mode_value,
            "model": model_name,
            "loaded": loaded,
            "active_backend": active_backend,
            "load_state": self._local_load_state,
            "load_error": self._local_load_error,
            "load_started_at": self._local_load_started_at,
            "load_finished_at": self._local_load_finished_at,
            "quant_method": self._local_quant_method,
            "model_dtype": None,
            "model_device": None,
            "config_weight_dtype": self._config_str("local_weight_dtype"),
            "kv_cache_dtype": self._config_str("local_kv_cache_dtype"),
            "cuda_available": bool(torch and torch.cuda.is_available()),
            "cuda_bf16_supported": None,
            "ram_swap_enabled": bool(self.enable_ram_swap),
        }
        if torch and torch.cuda.is_available():
            try:
                status["cuda_bf16_supported"] = torch.cuda.is_bf16_supported()
            except Exception:
                status["cuda_bf16_supported"] = None
        if self.local_model is not None:
            try:
                dtype = getattr(self.local_model, "dtype", None)
                if dtype is not None:
                    status["model_dtype"] = str(dtype).replace("torch.", "")
            except Exception:
                pass
            try:
                device = getattr(self.local_model, "device", None)
                if device is not None:
                    status["model_device"] = str(device)
            except Exception:
                pass

        use_torch_snapshot = False
        if torch and torch.cuda.is_available():
            try:
                use_torch_snapshot = bool(self.local_model) or bool(
                    torch.cuda.is_initialized()
                )
            except Exception:
                use_torch_snapshot = bool(self.local_model)
        status["memory"] = {
            "gpu": gpu_memory_snapshot(use_torch=use_torch_snapshot),
            "system": system_memory_snapshot(),
        }
        return status

    def unload_local_model(self) -> Dict[str, Any]:
        """Release local model weights and clear cached state."""
        released = bool(self.local_model or self.local_tokenizer)
        self.local_model = None
        self.local_tokenizer = None
        self._kv_cache.clear()
        self._last_memory_snapshot = None
        self._local_quant_method = None
        self._local_backend_active = None
        self._local_load_state = "idle"
        self._local_load_error = None
        self._local_load_started_at = None
        self._local_load_finished_at = time.time()
        if torch and torch.cuda.is_available():
            try:
                torch.cuda.empty_cache()
            except Exception:
                logger.debug("Failed to clear CUDA cache", exc_info=True)
        try:
            gc.collect()
        except Exception:
            logger.debug("Failed to run garbage collection", exc_info=True)
        return {"released": released}

    def load_local_model(
        self, override_model_name: str | None = None
    ) -> Dict[str, Any]:
        """Explicitly load the local model without running a generation."""
        self._load_local_model(override_model_name=override_model_name)
        return {
            "loaded": bool(
                self.local_model is not None and self.local_tokenizer is not None
            ),
            "backend": "transformers",
        }

    def _generate_via_local(
        self, prompt: str, ctx: ModelContext, session_id: str = "default", **kwargs
    ):
        """Generate a response using a locally loaded transformers model."""
        # Allow per‑request override of model selection
        override = kwargs.get("model")
        self._load_local_model(override_model_name=override)
        context_prompt = ctx.system_prompt + "\n" if ctx.system_prompt else ""
        for msg in ctx.messages:
            role = msg.get("role")
            content = msg.get("content")
            context_prompt += f"{role}: {content}\n"
        # Fallback: if images attached in context, add textual stubs so local text-only models
        # are aware that images exist. Real captioning handled by vision worker in future.
        try:
            images = (
                list(ctx.metadata.get("images", []))
                if isinstance(ctx.metadata, dict)
                else []
            )
            for entry in images[:3]:
                p = Path(str(entry.get("path", ""))).name
                sc = entry.get("score")
                if sc is not None:
                    context_prompt += f"image: {p} (score {sc})\n"
                else:
                    context_prompt += f"image: {p}\n"
        except Exception:
            pass
        full_prompt = context_prompt + prompt
        inputs = self.local_tokenizer(
            full_prompt,
            return_tensors="pt",
            truncation=True,
            max_length=self.max_context_length,
        )
        if torch and self.local_model is not None:
            try:
                target_device = getattr(self.local_model, "device", None)
                if (
                    target_device is not None
                    and getattr(target_device, "type", None) != "meta"
                ):
                    inputs = inputs.to(target_device)
            except Exception:
                logger.debug(
                    "Failed to move local inference inputs to model device",
                    exc_info=True,
                )
        generate_kwargs: Dict[str, Any] = {
            "max_new_tokens": kwargs.get("max_new_tokens", 128)
        }
        if self.use_kv_cache:
            generate_kwargs["use_cache"] = True
            generate_kwargs["return_dict_in_generate"] = True
            if session_id in self._kv_cache:
                generate_kwargs["past_key_values"] = self._kv_cache[session_id]
        try:
            output = self.local_model.generate(**inputs, **generate_kwargs)
        except Exception as exc:
            # Some transformers stacks reject combining internal cache init args
            # with caller-supplied past_key_values. Retry once without stale cache.
            msg = str(exc).lower()
            cache_conflict = (
                "past_key_values" in msg and "cache_implementation" in msg
            ) or ("past_key_values" in msg and "cache implementation" in msg)
            if not (self.use_kv_cache and cache_conflict):
                raise
            generate_kwargs.pop("past_key_values", None)
            self._kv_cache.pop(session_id, None)
            output = self.local_model.generate(**inputs, **generate_kwargs)
        if self.use_kv_cache:
            pkv = getattr(output, "past_key_values", None)
            if pkv is not None:
                self._kv_cache[session_id] = pkv
        sequences = getattr(output, "sequences", output)
        sequence = sequences[0]
        prompt_token_count = 0
        try:
            input_ids = None
            if isinstance(inputs, dict):
                input_ids = inputs.get("input_ids")
            else:
                input_ids = getattr(inputs, "input_ids", None)
            if input_ids is not None:
                if hasattr(input_ids, "shape"):
                    shape = getattr(input_ids, "shape", None)
                    if shape:
                        prompt_token_count = int(shape[-1] or 0)
                elif isinstance(input_ids, list) and input_ids:
                    first = input_ids[0]
                    if isinstance(first, (list, tuple)):
                        prompt_token_count = len(first)
                    else:
                        prompt_token_count = len(input_ids)
        except Exception:
            prompt_token_count = 0

        decode_ids = sequence
        if prompt_token_count > 0:
            try:
                candidate = sequence[prompt_token_count:]
                candidate_len = len(candidate) if hasattr(candidate, "__len__") else 0
                if candidate_len > 0:
                    decode_ids = candidate
            except Exception:
                pass

        text = self.local_tokenizer.decode(decode_ids, skip_special_tokens=True)
        return {"text": text, "thought": "", "tools_used": [], "metadata": {}}

    def estimate_vram(self, context_length: int) -> float:
        """Estimate VRAM usage in megabytes for a given context length."""
        if self.local_model is None:
            return 0.0

        gpu_weight_bytes = 0
        if torch and self.local_model is not None:
            try:
                for param in self.local_model.parameters():
                    device = getattr(param, "device", None)
                    if device is not None and getattr(device, "type", "") == "cuda":
                        gpu_weight_bytes += param.numel() * param.element_size()
                for buffer in self.local_model.buffers():
                    device = getattr(buffer, "device", None)
                    if device is not None and getattr(device, "type", "") == "cuda":
                        gpu_weight_bytes += buffer.numel() * buffer.element_size()
            except Exception:
                gpu_weight_bytes = 0

        if gpu_weight_bytes == 0 and self._last_memory_plan:
            for device, value in self._last_memory_plan.items():
                if isinstance(device, str) and device.startswith("cuda"):
                    try:
                        gpu_weight_bytes += int(value)
                    except Exception:
                        continue
                elif isinstance(device, str) and device.isdigit():
                    try:
                        gpu_weight_bytes += int(value)
                    except Exception:
                        continue
                elif isinstance(device, int):
                    try:
                        gpu_weight_bytes += int(value)
                    except Exception:
                        continue

        if gpu_weight_bytes == 0 and torch and torch.cuda.is_available():
            try:
                gpu_weight_bytes = getattr(
                    self.local_model, "get_memory_footprint", lambda: 0
                )()
            except Exception:
                gpu_weight_bytes = 0

        kv_bytes = 0
        if self.use_kv_cache and self.local_model is not None:
            gen_config = getattr(self.local_model, "generation_config", None)
            cache_device = None
            cache_impl = None
            quant_backend = None
            kv_dtype = None
            if gen_config is not None:
                cache_device = getattr(gen_config, "cache_device", None)
                cache_impl = getattr(gen_config, "cache_implementation", None)
                quant_backend = getattr(gen_config, "quantized_cache_backend", None)
                kv_dtype = getattr(gen_config, "kv_cache_dtype", None)
            if isinstance(cache_device, str):
                cache_device = cache_device.lower()
            if cache_device not in {"cpu", "disk"}:
                dtype = kv_dtype
                if dtype is None and torch:
                    dtype = getattr(self.local_model, "dtype", None)
                item_size = self._dtype_itemsize(dtype)
                if item_size == 0 and torch:
                    try:
                        item_size = torch.tensor([], dtype=torch.float32).element_size()
                    except Exception:
                        item_size = 0
                hidden = getattr(self.local_model.config, "hidden_size", None)
                layers = getattr(self.local_model.config, "num_hidden_layers", None)
                if hidden and layers and item_size:
                    kv_bytes = context_length * hidden * layers * 2 * item_size
                    cache_impl_lower = (
                        str(cache_impl).lower() if isinstance(cache_impl, str) else ""
                    )
                    if cache_impl_lower == "quantized":
                        kv_bytes = int(kv_bytes * 0.5)
                    elif isinstance(quant_backend, str):
                        backend_lower = quant_backend.lower()
                        if backend_lower in {"quanto", "hqq"}:
                            kv_bytes = int(kv_bytes * 0.5)

        total_bytes = max(gpu_weight_bytes + kv_bytes, 0)
        return float(total_bytes) / (1024**2)

    def _generate_via_dynamic(self, prompt: str, ctx: ModelContext, **kwargs):
        """Generate using a dynamically managed GPT-OSS server."""
        if not self.dynamic_process:
            self.start_dynamic_server()
        url = self.config.get("dynamic_url")
        if not url:
            port = self.config.get("dynamic_port", 8000)
            url = f"http://localhost:{port}/generate"
        payload = self._prepare_payload(prompt, ctx, **kwargs)
        try:
            response = requests.post(
                url, json=payload, stream=True, timeout=self.timeout
            )
            response.raise_for_status()
            chunks: List[str] = []
            for line in response.iter_lines(decode_unicode=True):
                if line:
                    chunks.append(line)
            text = "".join(chunks)
            return {"text": text, "thought": "", "tools_used": [], "metadata": {}}
        except (
            requests.exceptions.RequestException
        ) as e:  # pragma: no cover - network errors
            logger.exception("LLMService: dynamic server request failed")
            return {
                "text": "",
                "thought": "",
                "tools_used": [],
                "metadata": {"error": str(e)},
            }

    def start_dynamic_server(self):
        """Launch a GPT-OSS server if it's not already running."""
        if self.dynamic_process:
            return
        cmd = self.config.get("dynamic_server_cmd", "gpt-oss")
        model = self.config.get("dynamic_model", "20B")
        port = self.config.get("dynamic_port", 8000)
        args = [cmd, "--model", model, "--port", str(port)]
        try:
            self.dynamic_process = Popen(args)
            # Give the server a moment to start
            sleep(2)
        except Exception as exc:  # pragma: no cover - subprocess errors
            logger.exception("Failed to start dynamic server: %s", exc)
            self.dynamic_process = None

    def stop_dynamic_server(self):
        """Terminate the GPT-OSS server if it is running."""
        if not self.dynamic_process:
            return
        try:
            self.dynamic_process.terminate()
            self.dynamic_process.wait(timeout=10)
        except Exception:  # pragma: no cover - subprocess errors
            pass
        finally:
            self.dynamic_process = None


# Simple stub memory and RAG handler classes for POC
_UNSET = object()


MemoryLifecycleKind = Literal["evergreen", "reviewable", "prunable"]
MemoryReviewDecision = Literal["preserve", "rewrite", "prune"]


class MemoryLifecycleSpec(TypedDict, total=False):
    lifecycle: MemoryLifecycleKind
    grounded_at: Optional[float]
    occurs_at: Optional[float]
    review_at: Optional[float]
    decay_at: Optional[float]


class MemoryManager:
    """
    Simple in-memory memory manager stub for POC.
    """

    review_interval_seconds = 90 * 24 * 60 * 60
    prunable_review_fallback_seconds = 24 * 60 * 60
    prunable_decay_grace_seconds = 24 * 60 * 60
    prunable_decay_fallback_seconds = 7 * 24 * 60 * 60

    def __init__(self, config):
        self.store = {}
        self.tools = {}
        self._review_executor: Optional[
            Callable[[str, Dict[str, Any], str], Optional[Dict[str, Any] | str]]
        ] = None
        self._pending_review_keys: set[str] = set()
        # Optional Fernet for secret encryption
        self._fernet = None
        try:
            from cryptography.fernet import Fernet  # type: ignore

            key = str(config.get("mem_key") or "").strip()
            if key:
                self._fernet = Fernet(
                    key.encode("utf-8") if not key.startswith("gAAAA") else key
                )
        except Exception:
            self._fernet = None
        self.lifecycle_kinds = ("evergreen", "reviewable", "prunable")
        self.sensitivity_levels = [
            "mundane",
            "public",
            "personal",
            "protected",
            "secret",
        ]
        self._persist_lock = threading.Lock()
        self._store_path: Optional[Path] = None
        self._graph_store: Optional[GraphStore] = None
        raw_path = config.get("memory_store_path") or config.get("memory_store_file")
        if raw_path:
            try:
                self._store_path = memory_store.resolve_path(raw_path)
            except Exception:
                logger.exception(
                    "MemoryManager: invalid memory storage path: %s", raw_path
                )
                self._store_path = None
        if self._store_path:
            try:
                self._graph_store = GraphStore(self._store_path)
            except Exception:
                logger.exception(
                    "MemoryManager: failed to initialize graph store at %s",
                    self._store_path,
                )
        if self._store_path:
            self._load_persisted_store()
        self._action_history_service = None
        self.sweep_lifecycle()

    def _load_persisted_store(self) -> None:
        if not self._store_path:
            return
        try:
            persisted = memory_store.load(self._store_path)
        except Exception:
            logger.exception(
                "MemoryManager: failed to load persisted memory store from %s",
                self._store_path,
            )
            return
        if not isinstance(persisted, dict):
            logger.warning(
                "MemoryManager: ignoring malformed memory store (expected dict, got %s)",
                type(persisted).__name__,
            )
            return
        for key, raw in persisted.items():
            try:
                key_str = str(key)
            except Exception:
                continue
            self.store[key_str] = self._coerce_store_item(key_str, raw)

    def _prepare_snapshot_item(self, item: Any):
        if isinstance(item, dict):
            clone = copy.deepcopy(item)
            clone.pop("decrypt_error", None)
            if clone.get("sensitivity") == "secret" and not clone.get("encrypted"):
                try:
                    self._maybe_encrypt_item(clone)
                except Exception:
                    pass
            return clone
        return {"value": item}

    def _persist(self) -> None:
        if not self._store_path:
            return
        snapshot: Dict[str, Any] = {}
        for key, value in self.store.items():
            try:
                key_str = str(key)
            except Exception:
                continue
            snapshot[key_str] = self._prepare_snapshot_item(value)
        with self._persist_lock:
            try:
                memory_store.save(snapshot, self._store_path)
            except Exception:
                logger.exception(
                    "MemoryManager: failed to persist memory store to %s",
                    self._store_path,
                )

    def _value_preview(self, value: Any, limit: int = 200) -> str:
        if isinstance(value, str):
            text = value
        else:
            try:
                text = json.dumps(value, ensure_ascii=False)
            except Exception:
                text = str(value)
        text = text.replace("\n", " ").strip()
        if len(text) <= limit:
            return text
        return text[: limit - 3].rstrip() + "..."

    def _emit_memory_hook(self, key: str, source: str, item: dict) -> None:
        if not isinstance(item, dict):
            return
        payload = {
            "importance": item.get("importance"),
            "sensitivity": item.get("sensitivity"),
            "updated_at": item.get("updated_at"),
            "lifecycle": item.get("lifecycle"),
            "review_at": item.get("review_at"),
            "decay_at": item.get("decay_at"),
            "pruned_at": item.get("pruned_at"),
            "preview": self._value_preview(item.get("value")),
        }
        event = hooks.MemoryWriteEvent(key=str(key), source=source, payload=payload)
        try:
            hooks.emit(hooks.MEMORY_WRITE_EVENT, event)
        except Exception:
            logger.debug("memory hook emit failed", exc_info=True)

    def _normalize_timestamp(self, value: Any) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return float(text)
        except Exception:
            pass
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except Exception:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()

    def _normalize_importance(self, value: Any) -> float:
        try:
            importance = float(value)
        except (TypeError, ValueError):
            importance = 1.0
        return max(0.0, importance)

    def _normalize_importance_floor(self, floor) -> float | None:
        if floor is None:
            return None
        try:
            value = float(floor)
        except (TypeError, ValueError):
            return None
        if value < 0:
            return 0.0
        return value

    def _normalize_sensitivity(self, level: str | None) -> str:
        if not level:
            return "mundane"
        lvl = str(level).strip().lower()
        return lvl if lvl in self.sensitivity_levels else "mundane"

    def _normalize_lifecycle(
        self, lifecycle: str | None, default: MemoryLifecycleKind = "evergreen"
    ) -> MemoryLifecycleKind:
        text = str(lifecycle or "").strip().lower()
        if text in self.lifecycle_kinds:
            return text  # type: ignore[return-value]
        return default

    def _normalize_relative_value(
        self, value: Any, grounded_at: float
    ) -> tuple[Any, float | None, float | None]:
        if not isinstance(value, str):
            return value, None, None
        text = value.strip()
        if not text:
            return value, None, None
        (
            normalized,
            occurs_at,
            review_at,
            _timezone_name,
        ) = normalize_temporal_references(
            text,
            grounded_at=grounded_at,
        )
        return normalized, occurs_at, review_at

    def _lifecycle_state(self, item: dict[str, Any], now: float | None = None) -> str:
        current_time = time.time() if now is None else float(now)
        if self._normalize_timestamp(item.get("pruned_at")) is not None:
            return "pruned"
        decay_at = self._normalize_timestamp(item.get("decay_at"))
        if decay_at is not None and decay_at <= current_time:
            return "decayed"
        review_at = self._normalize_timestamp(item.get("review_at"))
        if review_at is not None and review_at <= current_time:
            return "review_due"
        return "active"

    def lifecycle_multiplier(
        self, item: dict[str, Any], *, now: float | None = None
    ) -> float:
        state = self._lifecycle_state(item, now=now)
        if state in {"pruned", "decayed"}:
            return 0.0
        lifecycle = self._normalize_lifecycle(item.get("lifecycle"))
        if state == "review_due":
            if lifecycle == "prunable":
                return 0.25
            if lifecycle == "reviewable":
                return 0.6
        return 1.0

    def _copy_item_for_read(self, item: Any) -> dict | None:
        if not isinstance(item, dict):
            if item is None:
                return None
            return {"value": item, "importance": 1.0}
        result = copy.deepcopy(item)
        try:
            self._maybe_decrypt_item(result)
        except Exception:
            pass
        return result

    def _coerce_store_item(
        self, key: str, raw: Any, *, now: float | None = None
    ) -> dict:
        current_time = time.time() if now is None else float(now)
        item = dict(raw) if isinstance(raw, dict) else {"value": raw}

        created_at = self._normalize_timestamp(item.get("created_at")) or current_time
        updated_at = self._normalize_timestamp(item.get("updated_at")) or created_at
        last_accessed_at = self._normalize_timestamp(
            item.get("last_accessed_at") or item.get("last_accessed")
        )
        last_confirmed_at = (
            self._normalize_timestamp(item.get("last_confirmed_at")) or updated_at
        )
        grounded_at = self._normalize_timestamp(item.get("grounded_at")) or created_at
        occurs_at = self._normalize_timestamp(item.get("occurs_at"))
        review_at = self._normalize_timestamp(item.get("review_at"))
        decay_at = self._normalize_timestamp(item.get("decay_at"))
        pruned_at = self._normalize_timestamp(item.get("pruned_at"))

        lifecycle = self._normalize_lifecycle(
            item.get("lifecycle"), default="evergreen"
        )
        legacy_end_time = self._normalize_timestamp(item.get("end_time"))
        legacy_archived = bool(item.get("archived"))
        if item.get("lifecycle") is None:
            legacy_evergreen = item.get("evergreen")
            if legacy_archived:
                lifecycle = "prunable"
                occurs_at = occurs_at or legacy_end_time
                review_at = review_at or legacy_end_time or updated_at
                decay_at = decay_at or review_at
                pruned_at = (
                    pruned_at
                    or self._normalize_timestamp(item.get("archived_at"))
                    or updated_at
                )
            elif legacy_end_time is not None:
                lifecycle = "prunable"
                occurs_at = occurs_at or legacy_end_time
                review_at = review_at or legacy_end_time
                decay_at = decay_at or (review_at + self.prunable_decay_grace_seconds)
            elif legacy_evergreen is False:
                lifecycle = "reviewable"
                review_at = review_at or (updated_at + self.review_interval_seconds)
            else:
                lifecycle = "evergreen"

        if lifecycle == "evergreen":
            review_at = None
            decay_at = None
            if item.get("occurs_at") is None:
                occurs_at = None
        elif lifecycle == "reviewable":
            review_at = review_at or (updated_at + self.review_interval_seconds)
            decay_at = None if item.get("decay_at") is None else decay_at
        else:
            if occurs_at is not None:
                review_at = review_at or occurs_at
                decay_at = decay_at or (review_at + self.prunable_decay_grace_seconds)
            else:
                review_at = review_at or (
                    updated_at + self.prunable_review_fallback_seconds
                )
                decay_at = decay_at or (
                    review_at + self.prunable_decay_fallback_seconds
                )
            if decay_at is not None and review_at is not None and decay_at < review_at:
                decay_at = review_at

        normalized: dict[str, Any] = {
            "value": item.get("value"),
            "importance": self._normalize_importance(item.get("importance", 1.0)),
            "created_at": created_at,
            "updated_at": updated_at,
            "last_accessed_at": last_accessed_at,
            "last_confirmed_at": last_confirmed_at,
            "grounded_at": grounded_at,
            "occurs_at": occurs_at,
            "review_at": review_at,
            "decay_at": decay_at,
            "pruned_at": pruned_at,
            "lifecycle": lifecycle,
            "sensitivity": self._normalize_sensitivity(item.get("sensitivity")),
            "encrypted": bool(item.get("encrypted", False)),
            "hint": item.get("hint") or None,
            "pinned": bool(item.get("pinned", False)),
            "importance_floor": self._normalize_importance_floor(
                item.get("importance_floor")
            ),
            "vectorize": bool(
                item.get("vectorize")
                or item.get("vectorized_at")
                or item.get("rag_doc_id")
            ),
            "vectorized_at": self._normalize_timestamp(item.get("vectorized_at")),
            "rag_doc_id": item.get("rag_doc_id"),
        }
        if item.get("rag_excluded") is not None:
            normalized["rag_excluded"] = bool(item.get("rag_excluded"))
        if pruned_at is not None:
            normalized["vectorize"] = False
            normalized["vectorized_at"] = None
            normalized["rag_doc_id"] = None
        return normalized

    def _build_lifecycle_spec(
        self,
        *,
        value: Any,
        now: float,
        existing: Optional[dict[str, Any]] = None,
        lifecycle: str | None = None,
        grounded_at: float | None = None,
        occurs_at: float | None = None,
        review_at: float | None = None,
        decay_at: float | None = None,
        evergreen: bool | None = None,
        end_time: float | None = None,
        archived: bool | None = None,
    ) -> tuple[Any, MemoryLifecycleSpec]:
        grounded_ts = self._normalize_timestamp(grounded_at)
        lifecycle_hints_used = any(
            value is not None
            for value in (
                lifecycle,
                grounded_at,
                occurs_at,
                review_at,
                decay_at,
                evergreen,
                end_time,
                archived,
            )
        )
        existing_public = existing or {}
        if grounded_ts is None:
            grounded_ts = (
                self._normalize_timestamp(existing_public.get("grounded_at")) or now
            )

        (
            normalized_value,
            inferred_occurs_at,
            inferred_review_at,
        ) = self._normalize_relative_value(value, grounded_ts)
        if (
            existing_public
            and not lifecycle_hints_used
            and normalized_value == existing_public.get("value")
        ):
            preserved: MemoryLifecycleSpec = {
                "lifecycle": self._normalize_lifecycle(
                    existing_public.get("lifecycle")
                ),
                "grounded_at": self._normalize_timestamp(
                    existing_public.get("grounded_at")
                )
                or grounded_ts,
                "occurs_at": self._normalize_timestamp(
                    existing_public.get("occurs_at")
                ),
                "review_at": self._normalize_timestamp(
                    existing_public.get("review_at")
                ),
                "decay_at": self._normalize_timestamp(existing_public.get("decay_at")),
            }
            return normalized_value, preserved

        occurs_ts = self._normalize_timestamp(occurs_at) or inferred_occurs_at
        review_ts = self._normalize_timestamp(review_at) or inferred_review_at
        decay_ts = self._normalize_timestamp(decay_at)

        resolved_lifecycle = self._normalize_lifecycle(lifecycle, default="evergreen")
        if lifecycle is None:
            if archived is True:
                resolved_lifecycle = "prunable"
            elif occurs_ts is not None or decay_ts is not None:
                resolved_lifecycle = "prunable"
            elif review_ts is not None:
                resolved_lifecycle = "reviewable" if decay_ts is None else "prunable"
            elif end_time is not None:
                resolved_lifecycle = "prunable"
            elif evergreen is False:
                resolved_lifecycle = "reviewable" if end_time is None else "prunable"
            else:
                resolved_lifecycle = "evergreen"

        if resolved_lifecycle == "evergreen":
            if lifecycle is None and occurs_ts is not None and review_ts is not None:
                resolved_lifecycle = "prunable"
            else:
                occurs_ts = self._normalize_timestamp(occurs_at)
                review_ts = self._normalize_timestamp(review_at)
                decay_ts = self._normalize_timestamp(decay_at)

        if resolved_lifecycle == "reviewable":
            review_ts = review_ts or (now + self.review_interval_seconds)
            if decay_at is None:
                decay_ts = None
        elif resolved_lifecycle == "prunable":
            explicit_end_time = self._normalize_timestamp(end_time)
            if occurs_ts is None and explicit_end_time is not None:
                occurs_ts = explicit_end_time
            if occurs_ts is not None:
                review_ts = review_ts or occurs_ts
                decay_ts = decay_ts or (review_ts + self.prunable_decay_grace_seconds)
            else:
                review_ts = review_ts or (now + self.prunable_review_fallback_seconds)
                decay_ts = decay_ts or (
                    review_ts + self.prunable_decay_fallback_seconds
                )
            if decay_ts is not None and review_ts is not None and decay_ts < review_ts:
                decay_ts = review_ts
        else:
            review_ts = None if review_at is None else review_ts
            decay_ts = None if decay_at is None else decay_ts

        return normalized_value, {
            "lifecycle": resolved_lifecycle,
            "grounded_at": grounded_ts,
            "occurs_at": occurs_ts,
            "review_at": review_ts,
            "decay_at": decay_ts,
        }

    def _queue_review(self, key: str) -> bool:
        if key in self._pending_review_keys:
            return False
        self._pending_review_keys.add(key)
        return True

    def update_memory(self, update_request: dict):
        key = update_request.get("key")
        value = update_request.get("value")
        self.upsert_item(str(key or ""), value)
        return {key: value}

    # ---- richer memory helpers ----
    def list_items(self, *, include_pruned: bool = False) -> list[str]:
        return [key for key, _item in self.iter_items(include_pruned=include_pruned)]

    def iter_items(
        self,
        *,
        include_pruned: bool = False,
        touch: bool = False,
    ) -> list[tuple[str, dict]]:
        current_time = time.time()
        self.sweep_lifecycle(current_time)
        out: list[tuple[str, dict]] = []
        dirty = False
        for key in sorted(self.store.keys()):
            item = self._coerce_store_item(key, self.store.get(key), now=current_time)
            if item != self.store.get(key):
                self.store[key] = item
                dirty = True
            state = self._lifecycle_state(item, current_time)
            if state == "review_due":
                self._queue_review(key)
            if not include_pruned and state in {"pruned", "decayed"}:
                continue
            if touch:
                item["last_accessed_at"] = current_time
                self.store[key] = item
                dirty = True
            public_item = self._copy_item_for_read(item)
            if public_item is None:
                continue
            out.append((key, public_item))
        if dirty:
            self._persist()
        return out

    def get_item(
        self,
        key: str,
        *,
        include_pruned: bool = False,
        touch: bool = True,
    ) -> dict | None:
        current_time = time.time()
        self.sweep_lifecycle(current_time)
        raw_item = self.store.get(key)
        if raw_item is None:
            return None
        item = self._coerce_store_item(key, raw_item, now=current_time)
        self.store[key] = item
        state = self._lifecycle_state(item, current_time)
        if state == "review_due":
            self._queue_review(key)
        if not include_pruned and state in {"pruned", "decayed"}:
            return None
        if touch:
            item["last_accessed_at"] = current_time
            self.store[key] = item
        return self._copy_item_for_read(item)

    def set_review_executor(
        self,
        executor: Optional[
            Callable[[str, Dict[str, Any], str], Optional[Dict[str, Any] | str]]
        ],
    ) -> None:
        self._review_executor = executor

    def _drop_vector_record(self, key: str) -> None:
        try:
            from app.services.rag_provider import get_rag_service

            service = get_rag_service(raise_http=False)
        except Exception:
            service = None
        if not service:
            return
        try:
            service.delete_source(f"memory:{key}")
        except Exception:
            logger.debug("memory vector delete failed for %s", key, exc_info=True)

    def _prune_item(
        self,
        key: str,
        item: dict[str, Any],
        *,
        now: float | None = None,
        reason: str = "prune",
    ) -> dict[str, Any]:
        current_time = time.time() if now is None else float(now)
        item["pruned_at"] = current_time
        item["updated_at"] = current_time
        item["vectorize"] = False
        item["vectorized_at"] = None
        item["rag_doc_id"] = None
        self._pending_review_keys.discard(key)
        self._drop_vector_record(key)
        return item

    def preserve_item(
        self,
        key: str,
        *,
        review_at: float | None = None,
        decay_at: float | object = _UNSET,
        now: float | None = None,
    ) -> dict | None:
        current_time = time.time() if now is None else float(now)
        raw_item = self.store.get(key)
        if raw_item is None:
            return None
        item = self._coerce_store_item(key, raw_item, now=current_time)
        item["pruned_at"] = None
        item["last_confirmed_at"] = current_time
        lifecycle = self._normalize_lifecycle(item.get("lifecycle"))
        next_review = self._normalize_timestamp(review_at)
        next_decay = (
            self._normalize_timestamp(decay_at) if decay_at is not _UNSET else None
        )
        if lifecycle == "reviewable":
            item["review_at"] = next_review or (
                current_time + self.review_interval_seconds
            )
            item["decay_at"] = next_decay if decay_at is not _UNSET else None
        elif lifecycle == "prunable":
            item["review_at"] = next_review or (
                current_time + self.prunable_review_fallback_seconds
            )
            if decay_at is _UNSET:
                span = (
                    self.prunable_decay_grace_seconds
                    if item.get("occurs_at") is not None
                    else self.prunable_decay_fallback_seconds
                )
                item["decay_at"] = item["review_at"] + span
            else:
                item["decay_at"] = next_decay
        else:
            item["review_at"] = next_review
            item["decay_at"] = next_decay if decay_at is not _UNSET else None
        item["updated_at"] = current_time
        self.store[key] = item
        self._pending_review_keys.discard(key)
        self._persist()
        self._emit_memory_hook(key, "preserve_item", item)
        return self._copy_item_for_read(item)

    def _run_review_executor(
        self,
        key: str,
        item: dict[str, Any],
        *,
        reason: str,
        now: float,
    ) -> bool:
        if not callable(self._review_executor):
            return False
        try:
            decision = self._review_executor(key, copy.deepcopy(item), reason)
        except Exception:
            logger.warning("memory review executor failed for %s", key, exc_info=True)
            return False
        if decision is None:
            return False
        if isinstance(decision, str):
            action = decision.strip().lower()
            payload: dict[str, Any] = {}
        elif isinstance(decision, dict):
            action = (
                str(decision.get("action") or decision.get("decision") or "")
                .strip()
                .lower()
            )
            payload = dict(decision)
        else:
            return False
        if action == "preserve":
            self.preserve_item(
                key,
                review_at=payload.get("review_at"),
                decay_at=payload.get("decay_at", _UNSET),
                now=now,
            )
            return True
        if action == "rewrite":
            self.upsert_item(
                key,
                payload.get("value", item.get("value")),
                payload.get("importance", item.get("importance")),
                None,
                None,
                None,
                payload.get("sensitivity", item.get("sensitivity")),
                payload.get("hint", item.get("hint")),
                payload.get("pinned", item.get("pinned", False)),
                payload.get("importance_floor", item.get("importance_floor")),
                payload.get("lifecycle", item.get("lifecycle")),
                payload.get("grounded_at", item.get("grounded_at")),
                payload.get("occurs_at", item.get("occurs_at")),
                payload.get("review_at", item.get("review_at")),
                payload.get("decay_at", item.get("decay_at")),
            )
            return True
        if action == "prune":
            updated = self._prune_item(key, item, now=now, reason=reason)
            self.store[key] = updated
            self._persist()
            self._emit_memory_hook(key, f"review_{reason}", updated)
            return True
        return False

    def sweep_lifecycle(self, now: float | None = None) -> dict[str, int]:
        current_time = time.time() if now is None else float(now)
        dirty = False
        updated_count = 0
        review_queued = 0
        pruned_count = 0
        for key in list(self.store.keys()):
            existing_raw = self.store.get(key)
            normalized = self._coerce_store_item(key, existing_raw, now=current_time)
            if normalized != self.store.get(key):
                self.store[key] = normalized
                dirty = True
                updated_count += 1
            state = self._lifecycle_state(normalized, current_time)
            if state == "review_due" and self._queue_review(key):
                review_queued += 1
            if state == "pruned":
                if isinstance(existing_raw, dict) and (
                    existing_raw.get("vectorize") or existing_raw.get("rag_doc_id")
                ):
                    self._drop_vector_record(key)
                    self.store[key]["vectorize"] = False
                    self.store[key]["vectorized_at"] = None
                    self.store[key]["rag_doc_id"] = None
                    dirty = True
                continue
            if state != "decayed":
                continue
            self._run_review_executor(key, normalized, reason="decay", now=current_time)
            refreshed = self._coerce_store_item(
                key, self.store.get(key), now=current_time
            )
            if self._lifecycle_state(refreshed, current_time) != "decayed":
                if refreshed != self.store.get(key):
                    self.store[key] = refreshed
                    dirty = True
                continue
            self.store[key] = self._prune_item(
                key, refreshed, now=current_time, reason="decay"
            )
            dirty = True
            pruned_count += 1
        if dirty:
            self._persist()
        return {
            "updated": updated_count,
            "review_queued": review_queued,
            "pruned": pruned_count,
        }

    def upsert_item(
        self,
        key: str,
        value,
        importance: float | None = None,
        evergreen: bool | None = None,
        end_time: float | None = None,
        archived: bool | None = None,
        sensitivity: str | None = None,
        hint: str | None = None,
        pinned: bool | None | object = _UNSET,
        importance_floor: float | None | object = _UNSET,
        lifecycle: str | None = None,
        grounded_at: float | None = None,
        occurs_at: float | None = None,
        review_at: float | None = None,
        decay_at: float | None = None,
    ) -> dict:
        now = time.time()
        existing_raw = self.store.get(key)
        existing = (
            self._copy_item_for_read(
                self._coerce_store_item(key, existing_raw, now=now)
            )
            if existing_raw is not None
            else None
        )
        normalized_value, lifecycle_spec = self._build_lifecycle_spec(
            value=value,
            now=now,
            existing=existing,
            lifecycle=lifecycle,
            grounded_at=grounded_at,
            occurs_at=occurs_at,
            review_at=review_at,
            decay_at=decay_at,
            evergreen=evergreen,
            end_time=end_time,
            archived=archived,
        )
        item = (
            self._coerce_store_item(key, existing_raw, now=now)
            if existing_raw is not None
            else self._coerce_store_item(
                key,
                {
                    "value": normalized_value,
                    "importance": importance if importance is not None else 1.0,
                    "created_at": now,
                    "updated_at": now,
                    "grounded_at": lifecycle_spec.get("grounded_at") or now,
                    "lifecycle": lifecycle_spec.get("lifecycle", "evergreen"),
                },
                now=now,
            )
        )

        item["value"] = normalized_value
        item["updated_at"] = now
        if existing is None:
            item["created_at"] = now
        if importance is not None:
            item["importance"] = self._normalize_importance(importance)
        elif item.get("importance") is None:
            item["importance"] = 1.0
        item["lifecycle"] = lifecycle_spec["lifecycle"]
        item["grounded_at"] = lifecycle_spec.get("grounded_at")
        item["occurs_at"] = lifecycle_spec.get("occurs_at")
        item["review_at"] = lifecycle_spec.get("review_at")
        item["decay_at"] = lifecycle_spec.get("decay_at")
        item["last_confirmed_at"] = now
        item["pruned_at"] = (
            self._normalize_timestamp(item.get("pruned_at"))
            if archived is not False
            else None
        )
        if archived is True:
            item = self._prune_item(key, item, now=now, reason="archive")
        else:
            if archived is False and item.get("pruned_at") is not None:
                item["pruned_at"] = None
                if item.get("lifecycle") == "prunable" and (
                    item.get("decay_at") is None
                    or self._normalize_timestamp(item.get("decay_at")) <= now
                ):
                    item["review_at"] = now
                    span = (
                        self.prunable_decay_grace_seconds
                        if item.get("occurs_at") is not None
                        else self.prunable_decay_fallback_seconds
                    )
                    item["decay_at"] = now + span
        if sensitivity is not None:
            item["sensitivity"] = self._normalize_sensitivity(sensitivity)
        if hint is not None:
            item["hint"] = hint or None
        if pinned is not _UNSET:
            item["pinned"] = bool(pinned)
        if importance_floor is not _UNSET:
            item["importance_floor"] = self._normalize_importance_floor(
                importance_floor
            )
        if item.get("pruned_at") is not None:
            item["vectorize"] = False
            item["vectorized_at"] = None
            item["rag_doc_id"] = None
        self.store[key] = item
        # Apply encryption if secret
        try:
            if item.get("sensitivity") == "secret":
                self._maybe_encrypt_item(item)
                self.store[key] = item
        except Exception:
            pass
        self._persist()
        self._emit_memory_hook(key, "upsert_item", item)
        return item

    def delete_item(self, key: str) -> bool:
        if key in self.store:
            self._drop_vector_record(key)
        removed = self.store.pop(key, None) is not None
        if removed:
            self._persist()
        return removed

    def decay(self, rate: float = 0.95) -> dict[str, int]:
        """Compatibility shim for the retired importance-decay API."""
        return self.sweep_lifecycle()

    def archive_item(self, key: str, archived: bool = True) -> dict | None:
        now = time.time()
        raw_item = self.store.get(key)
        if raw_item is None:
            return None
        item = self._coerce_store_item(key, raw_item, now=now)
        if archived:
            item = self._prune_item(key, item, now=now, reason="archive")
        else:
            item["pruned_at"] = None
            if item.get("lifecycle") == "prunable" and (
                item.get("decay_at") is None
                or self._normalize_timestamp(item.get("decay_at")) <= now
            ):
                item["review_at"] = now
                span = (
                    self.prunable_decay_grace_seconds
                    if item.get("occurs_at") is not None
                    else self.prunable_decay_fallback_seconds
                )
                item["decay_at"] = now + span
        item["updated_at"] = now
        self.store[key] = item
        self._persist()
        self._emit_memory_hook(key, "archive_item", item)
        return self._copy_item_for_read(item)

    def update_item_fields(self, key: str, updates: dict[str, Any]) -> dict | None:
        """Patch auxiliary fields on a memory item without changing its value.

        Used for bookkeeping fields like RAG vectorization status.
        """
        raw_item = self.store.get(key)
        if raw_item is None:
            return None
        now = time.time()
        item = self._coerce_store_item(key, raw_item, now=now)
        for field, value in dict(updates or {}).items():
            if field in {
                "grounded_at",
                "occurs_at",
                "review_at",
                "decay_at",
                "pruned_at",
            }:
                item[field] = self._normalize_timestamp(value)
            elif field == "lifecycle":
                item[field] = self._normalize_lifecycle(value)
            elif field == "importance":
                item[field] = self._normalize_importance(value)
            elif field == "importance_floor":
                item[field] = self._normalize_importance_floor(value)
            elif field in {"vectorize", "rag_excluded"}:
                item[field] = bool(value)
            else:
                item[field] = value
        item["updated_at"] = now
        if self._normalize_timestamp(item.get("pruned_at")) is not None:
            item = self._prune_item(key, item, now=now, reason="update_item_fields")
        elif item.get("vectorize") is False:
            item["vectorized_at"] = None
            item["rag_doc_id"] = None
            self._drop_vector_record(key)
        self.store[key] = item
        self._persist()
        self._emit_memory_hook(key, "update_item_fields", item)
        return copy.deepcopy(item)

    def _allowed_for_external(
        self, item: dict, *, allow_protected: bool = False
    ) -> bool:
        lvl = str(item.get("sensitivity", "mundane")).lower()
        if lvl == "secret":
            return False
        if lvl == "protected" and not allow_protected:
            return False
        return True

    def export_items(
        self, *, for_external: bool = False, allow_protected: bool = False
    ) -> dict[str, dict]:
        """Return a mapping of key->item for safe use.

        If for_external is True, items with sensitivity 'protected' (unless
        allow_protected=True) and 'secret' are omitted. For secret items, we
        never export values; callers may choose to surface only metadata.
        """
        out: dict[str, dict] = {}
        for k, it in self.iter_items(include_pruned=False, touch=False):
            if it is None:
                continue
            entry = dict(it)
            # Ensure sensitivity field present
            entry["sensitivity"] = self._normalize_sensitivity(entry.get("sensitivity"))
            if for_external and not self._allowed_for_external(
                entry, allow_protected=allow_protected
            ):
                continue
            # We never expose secret values in any export
            if entry.get("sensitivity") == "secret":
                entry = {k2: v for k2, v in entry.items() if k2 != "value"}
                entry["redacted"] = True
            out[k] = entry
        return out

    # ---------- encryption helpers ----------
    def _maybe_encrypt_item(self, item: dict) -> None:
        if not isinstance(item, dict):
            return
        if item.get("sensitivity") != "secret":
            return
        if self._fernet is None:
            # cannot encrypt; mark as plaintext
            item["encrypted"] = False
            return
        try:
            # serialize value to JSON and encrypt
            raw = json.dumps(item.get("value")).encode("utf-8")
            token = self._fernet.encrypt(raw).decode("utf-8")
            item["value"] = token
            item["encrypted"] = True
        except Exception:
            item["encrypted"] = False

    def _maybe_decrypt_item(self, item: dict) -> None:
        if not isinstance(item, dict):
            return
        if not item.get("encrypted"):
            return
        if self._fernet is None:
            # mark that decryption is unavailable; hint can be shown in UI
            item["decrypt_error"] = True
            return
        try:
            token = str(item.get("value") or "").encode("utf-8")
            data = self._fernet.decrypt(token)
            item["value"] = json.loads(data.decode("utf-8"))
            item["encrypted"] = False  # decrypted in memory view
        except Exception:
            # keep ciphertext if decryption fails and flag error
            item["decrypt_error"] = True

    def list_tools(self):
        return list(self.tools.keys())

    def register_tool(self, name: str, func):
        self.tools[name] = func

    def set_action_history_service(self, service) -> None:
        self._action_history_service = service

    def invoke_tool(
        self,
        name: str,
        *,
        user: str,
        signature: str | None,
        _action_context: dict | None = None,
        **kwargs,
    ):
        """Invoke a registered tool after verifying its signature.

        All user-facing tool functions require ``user`` and ``signature`` keyword
        arguments. A :class:`PermissionError` is raised if the signature is
        missing or invalid. Signatures must be generated with
        :func:`app.utils.generate_signature` using sanitized arguments.
        """
        if name not in self.tools:
            raise KeyError(f"Tool '{name}' is not registered")
        # Tool modules like `app.tools.memory` rely on a global manager binding;
        # keep it synced so tests/routes invoking tools always have a manager.
        try:
            from app.tools import memory as memory_tools  # type: ignore

            memory_tools.set_manager(self)
        except Exception:
            pass
        verify_signature(signature, user, name, kwargs)
        action_service = getattr(self, "_action_history_service", None)
        action_token = None
        should_journal = False
        if action_service is not None:
            try:
                tool_entry = get_tool_catalog_entry(name)
                persistence = (
                    tool_entry.get("persistence")
                    if isinstance(tool_entry.get("persistence"), dict)
                    else {}
                )
                should_journal = (
                    bool(persistence.get("writes_state")) and name != "revert_actions"
                )
            except Exception:
                should_journal = False
        if should_journal:
            try:
                action_token = action_service.prepare_tool_action(
                    name,
                    kwargs,
                    context=_action_context,
                    manager=self,
                )
            except Exception:
                action_token = None
        try:
            result = self.tools[name](user=user, signature=signature, **kwargs)
        except Exception as exc:
            if action_token is not None:
                try:
                    action_service.finalize_tool_action(
                        action_token,
                        result={"error": str(exc)},
                        status="error",
                    )
                except Exception:
                    pass
            raise
        if action_token is not None:
            try:
                action_service.finalize_tool_action(
                    action_token,
                    result=result,
                    status="invoked",
                )
            except Exception:
                pass
        return result


class RAGHandler:
    """
    Stub RAG handler for POC.
    """

    def __init__(self, config):
        pass


# Provide access to RAGService in submodule if available
try:
    from importlib import import_module

    RAGService = import_module("app.services.rag_service").RAGService
except Exception:  # pragma: no cover - optional
    RAGService = None
