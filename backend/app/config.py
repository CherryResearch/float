import os
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

# Resolve the repository root from this file location so config loading is stable
# even when the backend is launched from inside `backend/`.
REPO_ROOT = Path(__file__).resolve().parents[2]


def _resolve_repo_relative_path(value: str) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = REPO_ROOT / candidate
    try:
        return candidate.resolve()
    except Exception:
        return candidate


def get_dotenv_path() -> Path:
    """Return the dotenv path used for persisted settings.

    Defaults to `<repo_root>/.env`. Override with `FLOAT_ENV_FILE` (absolute or
    repo-relative) to keep secrets out of the repository tree entirely.
    """

    env_file = os.getenv("FLOAT_ENV_FILE")
    if env_file:
        return _resolve_repo_relative_path(env_file)
    return (REPO_ROOT / ".env").resolve()


DOTENV_PATH = get_dotenv_path()
LEGACY_DOTENV_PATH = (REPO_ROOT / "backend" / ".env").resolve()
LEGACY_DOTENV_LOADED = False

load_dotenv(dotenv_path=DOTENV_PATH, override=False)
# Backwards compatibility: some installs stored settings in `backend/.env` when
# running the API from that working directory. Load it as a non-overriding
# fallback when no explicit `FLOAT_ENV_FILE` is set so users can migrate by
# saving settings once.
if not os.getenv("FLOAT_ENV_FILE"):
    try:
        if LEGACY_DOTENV_PATH.exists() and LEGACY_DOTENV_PATH != DOTENV_PATH:
            load_dotenv(dotenv_path=LEGACY_DOTENV_PATH, override=False)
            LEGACY_DOTENV_LOADED = True
    except Exception:
        pass

OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_OPENAI_API_URL = OPENAI_RESPONSES_URL
DEFAULT_OPENAI_MODEL = "gpt-5"

DEFAULT_DATA_DIR = (REPO_ROOT / "data").resolve()
DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_DATABASES_DIR = (DEFAULT_DATA_DIR / "databases").resolve()
DEFAULT_DATABASES_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_MEMORY_FILE = (DEFAULT_DATABASES_DIR / "memory.sqlite3").resolve()
DEFAULT_FILES_DIR = (DEFAULT_DATA_DIR / "files").resolve()
DEFAULT_FILES_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_WORKSPACE_DIR = (DEFAULT_DATA_DIR / "workspace").resolve()
DEFAULT_WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_MODELS_DATA_DIR = (DEFAULT_DATA_DIR / "models").resolve()
DEFAULT_MODELS_DATA_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_CHROMA_DIR = (DEFAULT_DATABASES_DIR / "chroma").resolve()
DEFAULT_CHROMA_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_CALENDAR_EVENTS_DIR = (DEFAULT_DATABASES_DIR / "calendar_events").resolve()
DEFAULT_CALENDAR_EVENTS_DIR.mkdir(parents=True, exist_ok=True)

# Default directory for storing conversations (migrated from repo-root `conversations/`)
DEFAULT_CONV_DIR = (DEFAULT_DATA_DIR / "conversations").resolve()
DEFAULT_CONV_DIR.mkdir(parents=True, exist_ok=True)


# Candidate directories for storing models. If the user already has a Hugging
# Face cache on their machine, prefer that location so previously downloaded
# models are discovered automatically. A custom path can also be supplied and
# is appended to the search list.
def model_search_dirs(custom_path: Optional[str] = None) -> List[Path]:
    """Return candidate directories to search for models.

    Directories are returned in the following priority order:
    1. A user-specified custom models directory (if provided).
    2. The repository's bundled ``models`` directory.
    3. The ``HF_HOME`` environment variable (used by huggingface-hub).
    4. ``~/.cache/huggingface/hub`` under the current user's home directory.
    """

    dirs: List[Path] = []

    if custom_path:
        custom = Path(custom_path)
        if custom.exists():
            resolved = custom.resolve()
            if resolved not in dirs:
                dirs.append(resolved)

    repo_models = DEFAULT_MODELS_DATA_DIR
    repo_models.mkdir(parents=True, exist_ok=True)
    dirs.append(repo_models.resolve())

    legacy_repo_models = Path(__file__).resolve().parents[2] / "models"
    if legacy_repo_models.exists():
        dirs.append(legacy_repo_models.resolve())

    env_hf_home = os.getenv("HF_HOME")
    if env_hf_home:
        candidate = Path(env_hf_home) / "hub"
        if candidate.exists():
            dirs.append(candidate.resolve())

    hf_cache = Path.home() / ".cache" / "huggingface" / "hub"
    if hf_cache.exists():
        dirs.append(hf_cache.resolve())

    return dirs


def _detect_models_dir() -> Path:
    """Return the highest-priority existing models directory."""

    for candidate in model_search_dirs():
        if candidate.exists():
            return candidate
    return (Path(__file__).resolve().parents[2] / "models").resolve()


DEFAULT_MODELS_DIR = _detect_models_dir()


def _env_or_default(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return value.strip().lower() in {"1", "true", "yes", "on"}
    except Exception:
        return default


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _env_str(name: str) -> Optional[str]:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _normalize_rag_clip_model(value: str) -> str:
    """Normalize CLIP model names and reject non-CLIP vision-model drift."""
    cleaned = str(value or "").strip()
    if not cleaned:
        return "ViT-B-32"
    aliases = {
        "clip-vit-base-patch32": "ViT-B-32",
    }
    normalized = aliases.get(cleaned, cleaned)
    lowered = normalized.lower()
    if "paligemma" in lowered or "pixtral" in lowered:
        return "ViT-B-32"
    return normalized


def load_config():
    harmony_format = os.getenv("HARMONY_FORMAT", "false").lower() == "true"
    dev_mode = os.getenv("FLOAT_DEV_MODE", "false").lower() == "true"
    data_dir_env = os.getenv("FLOAT_DATA_DIR")
    data_dir = Path(data_dir_env).expanduser() if data_dir_env else DEFAULT_DATA_DIR
    if data_dir_env:
        try:
            if not data_dir.is_absolute():
                data_dir = (REPO_ROOT / data_dir).resolve()
            else:
                data_dir = data_dir.resolve()
        except Exception:
            pass
    data_dir.mkdir(parents=True, exist_ok=True)
    if dev_mode:
        default_conv = data_dir / "test_conversations"
    else:
        default_conv = data_dir / "conversations"
    databases_dir = data_dir / "databases"
    databases_dir.mkdir(parents=True, exist_ok=True)
    if dev_mode:
        default_memory_file = databases_dir / "test_memory.sqlite3"
    else:
        default_memory_file = databases_dir / "memory.sqlite3"
    memory_store_path = os.getenv("FLOAT_MEMORY_FILE", str(default_memory_file))
    conv_folder = os.getenv("FLOAT_CONV_DIR", str(default_conv))
    models_folder = os.getenv("FLOAT_MODELS_DIR", str(DEFAULT_MODELS_DIR))
    try:
        if models_folder and not Path(models_folder).exists():
            models_folder = str(DEFAULT_MODELS_DIR)
    except Exception:
        models_folder = str(DEFAULT_MODELS_DIR)
    return {
        # Telemetry & logging
        "service_name": os.getenv("FLOAT_SERVICE_NAME", "float-backend"),
        "service_version": os.getenv("FLOAT_SERVICE_VERSION", "0.1.0-alpha.0"),
        "environment": os.getenv("FLOAT_ENV", "development"),
        "log_level": os.getenv("FLOAT_LOG_LEVEL", "INFO"),
        # Log format: 'console' (human-friendly) or 'json'
        "log_format": os.getenv("FLOAT_LOG_FORMAT", "console"),
        "telemetry_enabled": (
            os.getenv("FLOAT_TELEMETRY_ENABLED", "true").lower() == "true"
        ),
        # Metrics
        "metrics_enabled": (
            os.getenv("FLOAT_METRICS_ENABLED", "true").lower() == "true"
        ),
        # OpenTelemetry OTLP config (optional)
        "otlp_endpoint": os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", ""),
        "otlp_headers": os.getenv("OTEL_EXPORTER_OTLP_HEADERS", ""),
        # OpenAI Responses API endpoint (preferred over legacy Chat Completions)
        "api_url": _env_or_default("EXTERNAL_API_URL", DEFAULT_OPENAI_API_URL),
        # Use OPENAI_API_KEY if set, otherwise fall back to API_KEY
        "api_key": os.getenv("OPENAI_API_KEY") or os.getenv("API_KEY", ""),
        # Hugging Face token for gated model downloads
        "hf_token": os.getenv("HUGGINGFACE_HUB_TOKEN") or os.getenv("HF_TOKEN", ""),
        # Default OpenAI model for chat
        "api_model": os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
        "local_url": os.getenv(
            "LOCAL_LLM_URL", "http://localhost:11433"
        ),  # maybe 1234 unless its proxied
        "dynamic_model": os.getenv("DYNAMIC_MODEL", "mistral"),
        "dynamic_port": int(os.getenv("DYNAMIC_PORT", "11434")),
        "transformer_model": os.getenv("TRANSFORMER_MODEL", "gpt-oss-20b"),
        "local_provider": os.getenv("LOCAL_PROVIDER", "lmstudio").strip().lower()
        or "lmstudio",
        "local_provider_mode": os.getenv("LOCAL_PROVIDER_MODE", "local-managed")
        .strip()
        .lower()
        or "local-managed",
        "local_provider_base_url": os.getenv("LOCAL_PROVIDER_BASE_URL", "").strip(),
        "local_provider_host": os.getenv("LOCAL_PROVIDER_HOST", "127.0.0.1").strip()
        or "127.0.0.1",
        "local_provider_port": _env_int(
            "LOCAL_PROVIDER_PORT",
            11434
            if (os.getenv("LOCAL_PROVIDER", "lmstudio").strip().lower() == "ollama")
            else 1234,
        ),
        "lmstudio_path": os.getenv("LMSTUDIO_PATH", "").strip(),
        "local_provider_api_token": os.getenv("LOCAL_PROVIDER_API_TOKEN", "").strip(),
        "local_provider_auto_start": _env_bool("LOCAL_PROVIDER_AUTO_START", True),
        "local_provider_preferred_model": os.getenv(
            "LOCAL_PROVIDER_PREFERRED_MODEL", ""
        ).strip(),
        "local_provider_default_context_length": _env_int(
            "LOCAL_PROVIDER_DEFAULT_CONTEXT_LENGTH",
            0,
        ),
        "local_provider_show_server_logs": _env_bool(
            "LOCAL_PROVIDER_SHOW_SERVER_LOGS", True
        ),
        "local_provider_enable_cors": _env_bool("LOCAL_PROVIDER_ENABLE_CORS", False),
        "local_provider_allow_lan": _env_bool("LOCAL_PROVIDER_ALLOW_LAN", False),
        "static_model": os.getenv("STATIC_MODEL", "gpt-4o-mini"),
        # Local inference tuning
        "max_context_length": int(os.getenv("MAX_CONTEXT_LENGTH", "2048")),
        "enable_kv_cache": (
            os.getenv("KV_CACHE_ENABLED", "true").lower() == "true"
        ),  # noqa: E501
        "enable_ram_swap": (
            os.getenv("RAM_SWAP_ENABLED", "false").lower() == "true"
        ),  # noqa: E501
        "local_device_map_strategy": _env_str("LOCAL_DEVICE_MAP_STRATEGY") or "auto",
        "local_max_gpu_mem_fraction": _env_float("LOCAL_MAX_GPU_MEM_FRACTION", 0.9),
        "local_gpu_memory_margin_mb": _env_int("LOCAL_GPU_MEMORY_MARGIN_MB", 512),
        "local_gpu_mem_limit_gb": _env_float("LOCAL_GPU_MEM_LIMIT_GB", 0.0),
        "local_cpu_offload_fraction": _env_float("LOCAL_CPU_OFFLOAD_FRACTION", 0.85),
        "local_cpu_offload_limit_gb": _env_float("LOCAL_CPU_OFFLOAD_LIMIT_GB", 0.0),
        "local_low_cpu_mem_usage": _env_bool("LOCAL_LOW_CPU_MEM_USAGE", True),
        "local_flash_attention": _env_bool("LOCAL_FLASH_ATTENTION", False),
        "local_attention_implementation": _env_str("LOCAL_ATTN_IMPLEMENTATION"),
        "local_kv_cache_implementation": _env_str("LOCAL_KV_CACHE_IMPLEMENTATION"),
        "local_kv_cache_quant_backend": _env_str("LOCAL_KV_CACHE_QUANT_BACKEND"),
        "local_kv_cache_dtype": _env_str("LOCAL_KV_CACHE_DTYPE"),
        "local_kv_cache_keep_on_device": _env_str("LOCAL_KV_CACHE_DEVICE"),
        "local_weight_dtype": _env_str("LOCAL_MODEL_DTYPE"),
        "local_cpu_thread_count": _env_int("LOCAL_CPU_THREADS", 0),
        "inference_device": os.getenv("LLM_DEVICE", "").strip() or None,
        "stream_idle_timeout": _env_int(
            "LLM_STREAM_IDLE_TIMEOUT", _env_int("FLOAT_STREAM_IDLE_TIMEOUT", 240)
        ),
        "allow_remote_code": _env_bool("ALLOW_TRANSFORMERS_REMOTE_CODE", True),
        "harmony_format": harmony_format,
        "server_url": os.getenv("SERVER_URL", ""),
        "mcp_url": os.getenv("MCP_SERVER_URL"),
        "mcp_token": os.getenv("MCP_API_TOKEN", ""),
        "livekit_url": os.getenv("LIVEKIT_URL", "ws://localhost:7880"),
        "livekit_api_key": os.getenv("LIVEKIT_API_KEY", ""),
        "livekit_secret": os.getenv("LIVEKIT_SECRET", ""),
        # Speech-to-text, text-to-speech, and vision model identifiers/paths
        # Prefer concrete versions for defaults to reduce ambiguity
        "stt_model": os.getenv("STT_MODEL", "whisper-large-v3-turbo"),
        # Default OpenAI TTS voice. 'nova' was not a valid voice name.
        "voice_model": os.getenv("VOICE_MODEL", "alloy"),
        "stream_backend": os.getenv("FLOAT_STREAM_BACKEND", "api"),
        "realtime_model": os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime"),
        "realtime_voice": os.getenv(
            "OPENAI_REALTIME_VOICE",
            os.getenv("VOICE_MODEL", "alloy"),
        ),
        "realtime_base_url": os.getenv(
            "OPENAI_REALTIME_URL", "https://api.openai.com/v1/realtime/client_secrets"
        ),
        "realtime_connect_url": os.getenv(
            "OPENAI_REALTIME_CONNECT_URL",
            "https://api.openai.com/v1/realtime/calls",
        ),
        "tts_model": os.getenv("TTS_MODEL", "tts-1"),
        "vision_model": os.getenv("VISION_MODEL", "google/paligemma2-3b-pt-224"),
        # Vector store / RAG configuration
        "rag_backend": os.getenv("FLOAT_RAG_BACKEND", "chroma"),
        "chroma_persist_dir": os.getenv("CHROMA_PERSIST_DIR", str(DEFAULT_CHROMA_DIR)),
        "weaviate_url": (
            os.getenv("FLOAT_WEAVIATE_URL")
            or os.getenv("WEAVIATE_URL")
            or "http://localhost:8080"
        ),
        "weaviate_grpc_host": (
            os.getenv("FLOAT_WEAVIATE_GRPC_HOST")
            or os.getenv("WEAVIATE_GRPC_HOST")
            or None
        ),
        "weaviate_grpc_port": _env_int(
            "FLOAT_WEAVIATE_GRPC_PORT",
            _env_int("WEAVIATE_GRPC_PORT", 50051),
        ),
        "auto_start_weaviate": _env_bool("FLOAT_AUTO_START_WEAVIATE", False),
        "rag_embedding_model": os.getenv(
            "RAG_EMBEDDING_MODEL", "local:all-MiniLM-L6-v2"
        ),
        # Optional CLIP model for multimodal (image) indexing.
        # Stored in a dedicated vector index to avoid dimension conflicts with text embeddings.
        "rag_clip_model": _normalize_rag_clip_model(
            os.getenv("RAG_CLIP_MODEL", "ViT-B-32")
        ),
        # Chat retrieval controls (prompt + response metadata).
        "rag_chat_top_k": _env_int("RAG_CHAT_TOP_K", 3),
        # Keep CLIP retrieval opt-in for chat to avoid irrelevant image matches on text-only prompts.
        "rag_chat_clip_top_k": _env_int("RAG_CHAT_CLIP_TOP_K", 0),
        # Minimum similarity (0..1) required for a retrieved snippet to be injected
        # into the chat prompt/context. Helps avoid polluting context with 0-score items.
        "rag_chat_min_similarity": _env_float("RAG_CHAT_MIN_SIMILARITY", 0.3),
        # Experimental SAE steering + retrieval path stubs.
        "sae_threads_signal_mode": (
            os.getenv("SAE_THREADS_SIGNAL_MODE", "hybrid").strip().lower() or "hybrid"
        ),
        "sae_threads_signal_blend": _env_float("SAE_THREADS_SIGNAL_BLEND", 0.7),
        "sae_model_combo": (
            os.getenv(
                "SAE_MODEL_COMBO", "openai/gpt-oss-20b :: future SAE pack"
            ).strip()
        ),
        "sae_embeddings_fallback": _env_bool("SAE_EMBEDDINGS_FALLBACK", True),
        "sae_steering_enabled": _env_bool("SAE_STEERING_ENABLED", False),
        "sae_steering_layer": _env_int("SAE_STEERING_LAYER", 12),
        "sae_steering_features": (
            os.getenv("SAE_STEERING_FEATURES", "123:+0.8,91:-0.4").strip()
        ),
        "sae_steering_token_positions": (
            os.getenv("SAE_STEERING_TOKEN_POSITIONS", "last").strip() or "last"
        ),
        "sae_steering_dry_run": _env_bool("SAE_STEERING_DRY_RUN", True),
        "sae_live_inspect_console": _env_bool("SAE_LIVE_INSPECT_CONSOLE", False),
        # Cap the amount of retrieved text stored in chat metadata/history.
        "rag_chat_match_chars": _env_int("RAG_CHAT_MATCH_CHARS", 1200),
        # Cap per-match snippet length in the prompt-injected RAG system message.
        "rag_chat_prompt_snippet_chars": _env_int("RAG_CHAT_PROMPT_SNIPPET_CHARS", 240),
        # Cap the total prompt-injected RAG system message size.
        "rag_chat_prompt_max_chars": _env_int("RAG_CHAT_PROMPT_MAX_CHARS", 2200),
        # Memory encryption (optional). If provided, used for encrypting
        # 'secret' sensitivity items at rest (Fernet key, urlsafe base64)
        "mem_key": os.getenv("FLOAT_MEM_KEY", ""),
        # Web Push (VAPID). Provide via environment for production.
        "vapid_public_key": os.getenv("VAPID_PUBLIC_KEY", ""),
        "vapid_private_key": os.getenv("VAPID_PRIVATE_KEY", ""),
        "vapid_subject": os.getenv(
            "VAPID_SUBJECT",
            "mailto:admin@example.com",
        ),
        # Conversations storage directory.  In dev mode this points to a test
        # folder to avoid overwriting real conversation history.
        "dev_mode": dev_mode,
        "conv_folder": conv_folder,
        "models_folder": models_folder,
        "data_dir": str(data_dir),
        "memory_store_path": memory_store_path,
        # Default system prompt describing Float's current tool/runtime behavior.
        "system_prompt": os.getenv(
            "SYSTEM_PROMPT",
            (
                "You are float, an agentic AI system. "
                "float's personality is light, clever, and helpful. "
                "Do not rely on memory or stale examples when describing the runtime, tools, or limits. "
                "The built-ins currently exposed here are: crawl (fetch one URL), search_web (structured search results), "
                "open_url (stub only), list_dir, read_file, write_file, create_task, generate_threads, "
                "read_threads_summary, remember, recall, list_actions, read_action_diff, revert_actions, tool_help, and tool_info. "
                "Use tool_help to list or verify available tools and tool_info to inspect one tool's purpose, arguments, "
                "sandbox, and limits. "
                "If you are not already certain a capability exists, call tool_help before saying it does not exist. "
                "If the user asks for reminders, tasks, events, or scheduled follow-ups, prefer create_task instead of "
                "claiming scheduling is unavailable. "
                "For local files, use list_dir to discover paths first, then use read_file with narrow windows. "
                "read_file is limited to paths under data/, returns bounded excerpts via start_line, line_count, and "
                "max_chars, and should not be used to pull whole large files into context. "
                "list_dir discovers directories without reading file contents, and write_file only writes under "
                "data/workspace/. "
                "For tracked local writes, use list_actions to inspect revertible actions, read_action_diff to inspect one stored diff, "
                "and revert_actions to undo one action or a batch from the same response or conversation. "
                "Treat CSV as only one example of a broader artifact-analysis pattern: for tables, JSON, logs, or mixed "
                "local collections, prefer typed working summaries and stable handles over replaying raw rows or chunks "
                "whenever the available tools support that flow. "
                "If a plan would benefit from code execution or a Python-like REPL, verify that a sandboxed runtime is "
                "actually present in tool_help/tool_info output before assuming it exists; if it does exist, respect "
                "its sandbox, venv/project, and persistence limits. "
                "Invoke tools using structured JSON, or following the harmony format e.g. "
                '{"tool":"remember","args":{"key":"lab", "value":"1234"}}. '
                "Use remember to store important details, recall to search memories (call recall with no key for "
                "suggestions). "
                "Keep intermediate narration brief between tool calls unless the user asks for detailed commentary. "
                "Summaries should stay grounded in retrieved data, note any gaps or offline services, and propose next "
                "actions only when they are actionable inside this environment. "
                "Threads are semantic groupings of conversations for purposes of reading and generating. "
                "Tool workflows can be multi-step; after tool outcomes (including errors or denials), continue the "
                "response or propose corrected calls. "
                "When the user explicitly asks you to use tools and the required inputs are already available or can be "
                "inferred safely from local context, call the tools instead of restating the plan. "
                "When calling tools, you can attach intermediate messages with the tool or just the tools before "
                "providing the final output; if you have questions about the proposed call, ask in the response."
            ),
        ),
    }
